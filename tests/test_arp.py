"""The arpeggiator: its note logic, and the bridge playing it on its own clock or the MIDI clock.

Run against the simulated devices from sim.py. Most cases use 1/16 at 150 BPM, so a step is
100 ms and, with gate 50, a note sounds 50 ms.
"""
import contextlib
import io
import json
import unittest

import mido
import yaml

import arpeggiator
import bridge
import config_schema as cs
import zoom_sysex as zs
from tests.sim import World, keyboard, note_off, note_on
from tests.test_poly import KEYPOLY, SYNTH as POLY_SYNTH, chord, voices
from tests.test_synth import DISABLE, KEYBOARD_ONLY, KEYSYNTH, SYNTH, SynthCase, control, wheel

K = zs.key_for
FAST = {"rate": "1/16", "tempo": 150}           # 100 ms per step


def clock(kind: str = "clock"):
    return keyboard(mido.Message(kind))


def ticks(start: float, count: int, period: float = 0.02) -> list:
    """Script entries: MIDI clocks at a steady tempo (20 ms apart is 125 BPM)."""
    return [(start + period * index, clock()) for index in range(count)]


def keys(world: World) -> list:
    """(seconds, value) of every write to the KeySynth's Key knob in slot 0."""
    return [(seconds, value) for seconds, slot, param, value in world.params if (slot, param) == (0, 2)]


class Notes(unittest.TestCase):
    def arp(self, **settings) -> arpeggiator.Arpeggiator:
        return arpeggiator.Arpeggiator(dict(arpeggiator.DEFAULTS, **settings), seed=1)

    def run_steps(self, arp, played, steps):
        return [arp.next_note(played) for _ in range(steps)]

    def test_the_modes_over_two_octaves(self):
        played = [52, 48, 55]                    # E, C, G, in the order the keys were pressed
        expected = {
            "up": [48, 52, 55, 60, 64, 67, 48, 52],
            "down": [67, 64, 60, 55, 52, 48, 67, 64],
            "updown": [48, 52, 55, 60, 64, 67, 64, 60, 55, 52, 48, 52],
            "played": [52, 48, 55, 64, 60, 67, 52, 48],
        }
        for mode, notes in expected.items():
            with self.subTest(mode):
                self.assertEqual(self.run_steps(self.arp(mode=mode, octaves=2), played, len(notes)), notes)

    def test_a_single_note_and_the_turning_points_of_updown(self):
        self.assertEqual(self.run_steps(self.arp(mode="updown"), [60], 3), [60, 60, 60])
        self.assertEqual(self.run_steps(self.arp(mode="updown"), [60, 64], 5), [60, 64, 60, 64, 60])

    def test_a_chord_that_arrives_key_by_key_does_not_start_over(self):
        arp = self.arp()
        self.assertEqual(arp.next_note([52]), 52)          # E came a few ms before C and G
        self.assertEqual(self.run_steps(arp, [52, 48, 55], 4), [55, 48, 52, 55])

    def test_random_stays_in_the_chord_and_never_repeats_a_note(self):
        notes = self.run_steps(self.arp(mode="random", octaves=2), [48, 52, 55], 200)
        self.assertTrue(set(notes) <= {48, 52, 55, 60, 64, 67})
        self.assertEqual(len(set(notes)), 6)
        self.assertTrue(all(a != b for a, b in zip(notes, notes[1:])))

    def test_notes_above_the_highest_playable_one_are_left_out(self):
        arp = arpeggiator.Arpeggiator(dict(arpeggiator.DEFAULTS, octaves=3), high_note=80)
        self.assertEqual(self.run_steps(arp, [60, 67], 6), [60, 67, 72, 79, 60, 67])

    def test_nothing_to_play_starts_over(self):
        arp = self.arp()
        self.assertEqual(self.run_steps(arp, [48, 52, 55], 2), [48, 52])
        self.assertIsNone(arp.next_note([]))
        self.assertEqual(arp.next_note([48, 52, 55]), 48)

    def test_latch_keeps_the_chord_until_a_new_one_is_played(self):
        settings = dict(arpeggiator.DEFAULTS, latch=True)
        arp = arpeggiator.Arpeggiator(settings)
        arp.pressed(48, alone=True)
        arp.pressed(52, alone=False)
        self.assertEqual(arp.chord([]), [48, 52])          # the keys are up again
        arp.pressed(60, alone=True)                        # a new chord
        self.assertEqual(arp.chord([]), [60])
        settings["latch"] = False
        self.assertEqual(arp.chord([]), [])
        settings["latch"] = True
        arp.clear()                                         # all notes off
        self.assertEqual(arp.chord([]), [])

    def test_settings(self):
        self.assertEqual(arpeggiator.problems(dict(arpeggiator.DEFAULTS)), [])
        self.assertEqual(arpeggiator.problems({"tempo": 92.5, "gate": 100, "octaves": 4}), [])
        wrong = {"mode": "sideways", "rate": "1/3", "octaves": 5, "gate": 4, "latch": "yes", "clock": "usb",
                 "tempo": 301, "swing": 50}
        found = " ".join(arpeggiator.problems(wrong))
        for word in ("mode", "rate", "octaves", "gate", "latch", "clock", "tempo", "unknown setting “swing”"):
            self.assertIn(word, found)
        self.assertEqual(arpeggiator.problems({"octaves": True}), ["arp: octaves must be a number from 1 to 4."])
        self.assertEqual(arpeggiator.read({"mode": "down", "tempo": 999, "swing": 1}),
                         dict(arpeggiator.DEFAULTS, mode="down"))
        self.assertEqual(arpeggiator.read(None), arpeggiator.DEFAULTS)
        self.assertEqual(arpeggiator.changed(dict(arpeggiator.DEFAULTS, gate=80)), {"gate": 80})


class ArpCase(SynthCase):
    def settings(self, controls=None, **arp) -> None:
        """controls.json: the controllers and the arpeggiator's settings."""
        bridge.CONTROLS.write_text(json.dumps({"controls": controls or {}, "arp": arp}), encoding="utf-8")

    def on(self, seconds: float) -> tuple:
        return seconds, lambda world: self.instance.switch_arp(True)

    def assert_times(self, events, expected, tolerance=0.015):
        self.assertEqual([value for _, value in events], [value for _, value in expected])
        for (seconds, _), (due, _) in zip(events, expected):
            self.assertLess(abs(seconds - due), tolerance, (seconds, due))


class OwnClock(ArpCase):
    def test_a_held_chord_is_played_note_by_note_with_gaps(self):
        self.settings(**FAST)
        world = World([self.on(0.9)] + chord(1.0, 48, 52, 55) + chord(1.45, 48, 52, 55, on=False),
                      ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=1.9)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assert_times(keys(world), [(1.0, K(48)), (1.05, 0), (1.1, K(52)), (1.15, 0), (1.2, K(55)), (1.25, 0),
                                        (1.3, K(48)), (1.35, 0), (1.4, K(52)), (1.45, 0)])
        self.assertNotIn("→ Key", self.output)                       # an arpeggio is not printed note by note
        self.assertIn("Arpeggiator on", self.output)

    def test_gate_100_ties_the_notes_and_the_last_ends_with_the_next_step(self):
        self.settings(gate=100, **FAST)
        world = World([self.on(0.9)] + chord(1.0, 48, 52) + chord(1.35, 48, 52, on=False),
                      ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=1.7)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assert_times(keys(world), [(1.0, K(48)), (1.1, K(52)), (1.2, K(48)), (1.3, K(52)), (1.4, 0)])

    def test_it_starts_with_the_key_and_again_after_a_pause(self):
        self.settings(**FAST)
        script = ([self.on(0.9), (1.0, note_on(60)), (1.12, note_off(60)),     # stops with the step at 1.2
                   (1.47, note_on(62)), (1.52, note_off(62))])
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=1.9)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assert_times(keys(world), [(1.0, K(60)), (1.05, 0), (1.1, K(60)), (1.15, 0), (1.47, K(62)), (1.52, 0)])

    def test_latch_plays_on_until_a_new_chord(self):
        self.settings(latch=True, **FAST)
        script = ([self.on(0.9)] + chord(1.0, 48, 52) + chord(1.05, 48, 52, on=False)
                  + [(1.25, note_on(60)), (1.27, note_off(60)), (1.45, lambda world: self.instance.switch_arp(False))])
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=1.8)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assert_times(keys(world), [(1.0, K(48)), (1.05, 0), (1.1, K(52)), (1.15, 0), (1.2, K(48)), (1.25, 0),
                                        (1.3, K(60)), (1.35, 0), (1.4, K(60)), (1.45, 0)])

    def test_the_sustain_pedal_keeps_notes_in_the_chord(self):
        self.settings(**FAST)
        script = ([self.on(0.9), (0.95, control(64, 127))] + chord(1.0, 48, 52) + chord(1.02, 48, 52, on=False)
                  + [(1.32, control(64, 0))])
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=1.7)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual([value for _, value in keys(world)], [K(48), 0, K(52), 0, K(48), 0, K(52), 0])

    def test_all_notes_off_stops_it_even_with_latch(self):
        self.settings(latch=True, gate=100, **FAST)
        script = [self.on(0.9)] + chord(1.0, 48, 52) + chord(1.05, 48, 52, on=False) + [(1.15, control(123, 0))]
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=1.6)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assert_times(keys(world), [(1.0, K(48)), (1.1, K(52)), (1.15, 0)])

    def test_the_pitch_wheel_bends_the_arpeggio(self):
        self.settings(gate=100, **FAST)
        script = [self.on(0.9), (0.95, wheel(4096))] + chord(1.0, 48, 52) + chord(1.15, 48, 52, on=False)
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=1.5)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual([value for _, value in keys(world)], [K(48, 1), K(52, 1), 0])

    def test_stopping_the_bridge_closes_the_gate(self):
        self.settings(gate=100, **FAST)
        world = World([self.on(0.9)] + chord(1.0, 48, 52), ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=1.25)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual([value for _, value in keys(world)], [K(48), K(52), K(48), 0])
        self.assertEqual(world.sent[-1][1], DISABLE)

    def test_on_keypoly_every_note_takes_the_next_voice(self):
        self.settings(**FAST)
        world = World([self.on(0.9)] + chord(1.0, 48, 52, 55) + chord(1.32, 48, 52, 55, on=False),
                      ports=KEYBOARD_ONLY, chain=[KEYPOLY], length=1.7)
        self.install(synth=POLY_SYNTH)
        self.run_bridge(world)
        # the release of each note is left to fade; C comes back to its own voice
        self.assertEqual(voices(world), [(1, K(48)), (1, 0), (2, K(52)), (2, 0), (3, K(55)), (3, 0),
                                         (1, K(48)), (1, 0)])


class MidiClock(ArpCase):
    def test_it_steps_with_the_clock_from_start(self):
        self.settings(clock="midi", rate="1/16")
        script = ([self.on(0.9)] + chord(0.95, 48, 52, 55) + [(1.0, clock("start"))] + ticks(1.0, 30)
                  + [(1.6, clock("stop"))])
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=1.9)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        # nothing before Start; then a note every 6 clocks (120 ms), sounding half of it
        self.assert_times(keys(world), [(1.0, K(48)), (1.06, 0), (1.12, K(52)), (1.18, 0), (1.24, K(55)), (1.30, 0),
                                        (1.36, K(48)), (1.42, 0), (1.48, K(52)), (1.54, 0)], tolerance=0.02)
        self.assertNotIn("→ Key", self.output)

    def test_stop_and_continue(self):
        self.settings(clock="midi", rate="1/16", gate=100)
        script = ([self.on(0.9)] + chord(0.95, 48, 52, 55) + [(1.0, clock("start"))] + ticks(1.0, 13)
                  + [(1.25, clock("stop"))] + ticks(1.26, 7)           # some DAWs keep the clock running
                  + [(1.40, clock("continue"))] + ticks(1.40, 8))
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=1.7)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        # the 14th clock after Start is the 6th after Continue; after Stop it starts at the first note
        self.assert_times(keys(world), [(1.0, K(48)), (1.12, K(52)), (1.24, K(55)), (1.25, 0), (1.50, K(48)), (1.7, 0)],
                          tolerance=0.02)

    def test_a_clock_that_just_stops_silences_it(self):
        self.settings(clock="midi", rate="1/16", gate=100)
        world = World([self.on(0.9)] + chord(0.95, 48, 52) + ticks(1.0, 16), ports=KEYBOARD_ONLY,
                      chain=[KEYSYNTH], length=2.1)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        values = keys(world)
        self.assertEqual([value for _, value in values], [K(48), K(52), K(48), 0])
        self.assertTrue(1.3 + bridge.CLOCK_TIMEOUT - 0.01 <= values[-1][0] <= 1.3 + bridge.CLOCK_TIMEOUT + 0.05,
                        values[-1][0])

    def test_without_a_clock_nothing_plays_and_the_interface_says_so(self):
        state = {}
        self.settings(clock="midi")
        world = World([self.on(0.9)] + chord(1.0, 48, 52)
                      + [(1.4, lambda world: state.update(self.instance.state()["synth"]["arp"]))],
                      ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=1.6)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(keys(world), [])
        self.assertEqual((state["on"], state["note"], state["clock_bpm"]), (True, None, None))

    def test_the_interface_shows_the_tempo_of_the_clock(self):
        state = {}
        self.settings(clock="midi")
        world = World(ticks(1.0, 30) + [(1.55, lambda world: state.update(self.instance.state()["synth"]["arp"]))],
                      ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=1.7)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertLess(abs(state["clock_bpm"] - 125), 3)


class Switching(ArpCase):
    def test_a_controller_switches_it_on_and_off(self):
        self.settings(controls={"Arp": 20}, gate=100, **FAST)
        script = (chord(1.0, 48, 52) + [(1.1, control(20, 127)), (1.35, control(20, 0))]
                  + chord(1.45, 48, 52, on=False))
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=1.7)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        # the keys first (last note wins), then the arpeggio from the bottom, then the keys again
        self.assert_times(keys(world), [(1.0, K(48)), (1.0, K(52)), (1.1, K(48)), (1.2, K(52)), (1.3, K(48)),
                                        (1.35, K(52)), (1.45, 0)])
        self.assertIn("Arpeggiator on", self.output)
        self.assertIn("Arpeggiator off", self.output)

    def test_its_switch_is_learned_like_a_knob(self):
        script = [(1.0, lambda world: self.instance.synth_control("learn", "Arp")), (1.1, control(21, 127))]
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=1.3)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(self.instance.controls, {"Arp": 21})
        self.assertFalse(self.instance.arp_on)                      # the move that was learned switches nothing
        self.assertIn("Arpeggiator: controller 21 now switches it on and off", self.output)
        self.assertEqual(json.loads(bridge.CONTROLS.read_text(encoding="utf-8")), {"controls": {"Arp": 21}})

    def test_the_interface_changes_and_stores_the_settings(self):
        seen = {}

        def client(connection):
            connection.at(1.0)
            seen["change"] = connection.call("/api/arp", {"mode": "down", "octaves": 2, "tempo": 92.5})[:2]
            seen["wrong"] = [connection.call("/api/arp", body)[:2] for body in
                             ({"tempo": 999}, {"swing": 50}, {"on": "yes"}, {"gate": 50.5})]
            seen["on"] = connection.call("/api/arp", {"on": True})[:2]
            seen["state"] = connection.call("/api/state")[1]["synth"]["arp"]
            seen["page"] = connection.call("/")[1]
        world = World(ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=1.5)
        self.install(synth=SYNTH)
        self.run_bridge(world, client)
        self.assertEqual(seen["change"], (200, {"result": None}))
        self.assertEqual(seen["wrong"], [(400, {"error": "tempo must be a number from 40 to 300."}),
                                         (400, {"error": "unknown setting “swing”."}),
                                         (400, {"error": "on must be true or false."}),
                                         (400, {"error": "gate must be a number from 5 to 100 (percent)."})])
        self.assertEqual(seen["on"], (200, {"result": None}))
        self.assertEqual({key: seen["state"][key] for key in ("on", "mode", "octaves", "tempo", "rate")},
                         {"on": True, "mode": "down", "octaves": 2, "tempo": 92.5, "rate": "1/16"})
        self.assertEqual(json.loads(bridge.CONTROLS.read_text(encoding="utf-8")),
                         {"controls": {}, "arp": {"mode": "down", "octaves": 2, "tempo": 92.5}})
        self.assertIn(b"Arpeggiator", seen["page"])

    def test_it_is_off_when_the_bridge_starts_and_bad_stored_settings_take_their_default(self):
        self.settings(mode="down", tempo=5000)
        self.install(synth=SYNTH)
        self.run_bridge(World(ports=KEYBOARD_ONLY, chain=[KEYSYNTH]))
        self.assertFalse(self.instance.arp_on)
        self.assertEqual(self.instance.arp_settings, dict(arpeggiator.DEFAULTS, mode="down"))


class SettingsFile(ArpCase):
    def test_the_settings_travel_to_the_pi(self):
        import export
        self.install(synth=SYNTH)
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        self.settings(controls={"Arp": 20}, mode="updown", gate=75)
        settings = export.gather()
        self.assertEqual(cs.validate(settings), [])
        self.assertEqual((settings["arp"], settings["controls"]), ({"mode": "updown", "gate": 75}, {"Arp": 20}))
        settings["arp"]["tempo"] = 999
        self.assertIn("arp: tempo must be a number from 40 to 300.", cs.validate(settings))

        settings["arp"]["tempo"] = 150
        instance = bridge.Bridge(settings)                          # as on the Pi
        self.assertEqual(instance.arp_settings, dict(arpeggiator.DEFAULTS, mode="updown", gate=75, tempo=150))
        with self.assertRaises(bridge.UserError):
            instance.arp({"mode": "down"})                          # the settings cannot be changed there
        with contextlib.redirect_stdout(io.StringIO()):
            instance.arp({"on": True})                              # but it can be switched
        self.assertTrue(instance.arp_on)
        dumped = yaml.safe_load(cs.dump(settings))
        self.assertEqual(dumped["arp"], {"mode": "updown", "gate": 75, "tempo": 150})


if __name__ == "__main__":
    unittest.main()
