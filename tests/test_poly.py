"""KeyPoly: a MIDI keyboard plays four voices through the effect's Key1–Key4 knobs.

Run against the simulated devices from sim.py. KeyPoly's Key knobs are parameters 2–5, each in
KeySynth's Key format (0 = gate off, n = 10-cent steps above C0); the bridge gives every note one
of them. The effect id below is made up for the tests; the real one is in KeyPoly's manifest.
"""
import json
import unittest

import bridge
import zoom_sysex as zs
from tests.sim import ID, KEYBOARD_PORT, World, cc, learned, mapping, note_off, note_on
from tests.test_synth import EVERYTHING, KEYBOARD_ONLY, KEYSYNTH, PRESET, STOCK, SynthCase, control, wheel

KEYPOLY = 0x07000F62
SYNTH = {"keyboard": KEYBOARD_PORT, "channel": 1, "effect_id": KEYSYNTH, "poly_effect_id": KEYPOLY}
K = zs.key_for                                  # the Key value of a MIDI note


def voices(world: World, slot: int = 0) -> list:
    """(voice 1–4, Key value) of every write to a Key knob of the KeyPoly in that slot."""
    return [(param - 1, value) for _, where, param, value in world.params
            if where == slot and zs.KEY_PARAM <= param < zs.KEY_PARAM + zs.MAX_VOICES]


def chord(seconds: float, *notes: int, on: bool = True) -> list:
    """Script entries: the keys of a chord pressed (or released) together."""
    return [(seconds, (note_on if on else note_off)(note)) for note in notes]


class Messages(unittest.TestCase):
    def test_the_four_key_knobs(self):
        self.assertEqual(zs.to_hex(zs.build_set_key(ID, 0, 331, voice=0)), "F0 52 00 6E 64 20 00 00 02 4B 02 00 00 00 F7")
        self.assertEqual(zs.to_hex(zs.build_set_key(ID, 2, 1000, voice=3)), "F0 52 00 6E 64 20 00 02 05 68 07 00 00 00 F7")
        for voice in (-1, 4, 10):
            with self.subTest(voice), self.assertRaises(ValueError):
                zs.build_set_key(ID, 0, 331, voice=voice)

    def test_the_allowlist_takes_the_knobs_of_the_effect_in_that_slot(self):
        for voice in range(4):
            message = zs.build_set_key(ID, 1, K(45), voice=voice)
            self.assertEqual(zs.classify(message, ID, zs.synth_targets(1, zs.KEYPOLY)), "set_param")
        with self.assertRaises(zs.NotAllowed):          # on KeySynth parameter 5 is Wave2, 0–4
            zs.classify(zs.build_set_key(ID, 1, K(45), voice=3), ID, zs.synth_targets(1, zs.KEYSYNTH))
        self.assertEqual([knob for knob, _ in zs.KEYPOLY.knobs],
                         ["Key1", "Key2", "Key3", "Key4", "Level", "Wave", "Cutoff", "Reso", "Atk", "Rel", "LFO", "Rate"])
        self.assertEqual(list(zs.KEYPOLY.controls),
                         ["Level", "Wave", "Cutoff", "Reso", "Atk", "Rel", "LFO", "Rate", "Vib", "Trm"])

    def test_which_notes_four_voices_sound(self):
        keys = bridge.Keys()
        for note in (40, 43, 47, 50, 52):
            keys.note_on(note)
        self.assertEqual(keys.notes(4), [52, 50, 47, 43])          # the oldest gives way
        keys.pedal(True)
        keys.note_off(52); keys.note_off(50)
        self.assertEqual(keys.notes(4), [47, 43, 40, 50])          # keys that are down come first
        keys.note_on(55)
        self.assertEqual(keys.notes(4), [55, 47, 43, 40])
        self.assertEqual((keys.note, keys.notes(1)), (55, [55]))


class Voices(SynthCase):
    def run_poly(self, script, chain=(KEYPOLY,), synth=SYNTH, **world):
        world = World(script, ports=KEYBOARD_ONLY, chain=list(chain), **world)
        self.install(synth=synth)
        self.run_bridge(world)
        return world

    def test_a_chord_takes_one_voice_per_note_and_a_released_key_leaves_the_others_alone(self):
        script = (chord(1.0, 48, 52, 55) + [(1.3, note_off(52)), (1.5, note_on(59))]
                  + chord(1.7, 48, 55, 59, on=False))
        world = self.run_poly(script)
        self.assertEqual(voices(world), [(1, K(48)), (2, K(52)), (3, K(55)),
                                         (2, 0),                  # E released: C and G untouched
                                         (4, K(59)),              # B takes the voice free longest
                                         (1, 0), (3, 0), (4, 0)])
        for seconds, *_ in world.params[:3]:
            self.assertLess(seconds - 1.0, 0.02)                  # the chord goes out at once
        self.assertIn("KeyPoly is effect 1 – the keyboard plays it with 4 voices", self.output)
        self.assertIn("C3 ( 48) → Key1  361   ack", self.output)
        self.assertIn("G3 ( 55) → Key3  431   ack", self.output)
        for _, text in world.sent:                                # everything sent is on the allowlist
            zs.classify(bytes.fromhex(text.replace(" ", "")), ID, zs.synth_targets(0, zs.KEYPOLY))

    def test_a_fifth_note_takes_the_oldest_voice_and_gives_it_back(self):
        script = ([(1.0 + 0.05 * i, note_on(note)) for i, note in enumerate((48, 50, 52, 53, 55))]
                  + [(1.4, note_off(55))] + chord(1.6, 48, 50, 52, 53, on=False))
        world = self.run_poly(script)
        self.assertEqual(voices(world), [(1, K(48)), (2, K(50)), (3, K(52)), (4, K(53)),
                                         (1, K(55)),              # C was the oldest
                                         (1, K(48)),              # C is still down and comes back
                                         (1, 0), (2, 0), (3, 0), (4, 0)])

    def test_free_voices_take_turns_and_a_note_comes_back_to_its_own_voice(self):
        """A release fades out undisturbed while other voices are free; the same note played
        again takes over its own fading voice instead of sounding twice."""
        script = []
        for i, note in enumerate((60, 62, 64, 65, 67, 64)):
            script += [(1.0 + 0.1 * i, note_on(note)), (1.05 + 0.1 * i, note_off(note))]
        world = self.run_poly(script)
        self.assertEqual(voices(world), [(1, K(60)), (1, 0), (2, K(62)), (2, 0), (3, K(64)), (3, 0),
                                         (4, K(65)), (4, 0), (1, K(67)), (1, 0),
                                         (3, K(64)), (3, 0)])    # not voice 2, though it is free longer

    def test_the_sustain_pedal_holds_a_chord(self):
        script = (chord(1.0, 48, 52) + [(1.1, control(64, 127))] + chord(1.2, 48, 52, on=False)
                  + [(1.3, note_on(55)), (1.4, note_off(55)), (1.5, control(64, 0))])
        world = self.run_poly(script)
        self.assertEqual(voices(world), [(1, K(48)), (2, K(52)), (3, K(55)), (1, 0), (2, 0), (3, 0)])

    def test_all_notes_off_closes_every_voice(self):
        world = self.run_poly(chord(1.0, 48, 52, 55, 59) + [(1.2, control(123, 0))])
        self.assertEqual(voices(world)[4:], [(1, 0), (2, 0), (3, 0), (4, 0)])

    def test_the_pitch_wheel_bends_every_voice_in_small_steps(self):
        script = chord(1.0, 48, 55) + [(1.1, wheel(8191)), (1.4, wheel(0))] + chord(1.7, 48, 55, on=False)
        world = self.run_poly(script)
        # two semitones are 20 clicks; the two voices take turns, at most 9 clicks a step
        self.assertEqual(voices(world), [(1, 361), (2, 431),
                                         (1, 370), (2, 440), (1, 379), (2, 449), (1, 381), (2, 451),
                                         (1, 372), (2, 442), (1, 363), (2, 433), (1, 361), (2, 431),
                                         (1, 0), (2, 0)])
        times = [seconds for seconds, *_ in world.params][2:14]
        self.assertGreaterEqual(min(later - earlier for earlier, later in zip(times, times[1:])), 0.009)
        self.assertLess(times[5] - 1.1, 0.08)                      # both voices are there within 60 ms
        self.assertEqual(self.output.count("→ Key"), 4)            # the notes and gate-offs, no line per step

    def test_a_note_played_with_the_wheel_up_starts_bent(self):
        world = self.run_poly([(1.0, wheel(4096))] + chord(1.1, 48, 52) + chord(1.3, 48, 52, on=False))
        self.assertEqual(voices(world), [(1, K(48, 1)), (2, K(52, 1)), (1, 0), (2, 0)])

    def test_a_lost_note_of_a_chord_is_repeated_on_its_own_voice(self):
        lost = []

        def accept(slot, param, value):
            if param == 3 and not lost:
                lost.append(True)                 # the pedal misses the first write to Key2
                return False
            return True
        world = World(chord(1.0, 48, 52, 55) + chord(1.4, 48, 52, 55, on=False), ports=KEYBOARD_ONLY,
                      chain=[KEYPOLY])
        world.accept = accept
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(voices(world)[:4], [(1, K(48)), (2, K(52)), (3, K(55)), (2, K(52))])
        first, again = world.params[1][0], world.params[3][0]
        self.assertTrue(0.05 <= again - first <= 0.1, again - first)
        self.assertNotIn("does not confirm notes", self.output)

    def test_on_stop_every_sounding_voice_is_closed_and_unused_ones_are_left_alone(self):
        world = self.run_poly(chord(1.0, 48, 52), length=1.3)
        self.assertEqual(voices(world), [(1, K(48)), (2, K(52)), (1, 0), (2, 0)])   # Key3, Key4 never written
        self.assertEqual(world.sent[-1][1], "F0 52 00 6E 51 F7")

    def test_a_key_knob_turned_on_the_pedal_is_closed_at_the_next_key(self):
        script = [(1.0, lambda world: world.knob(0, 4, 400)),      # Key3 turned by hand
                  (1.1, note_on(60)), (1.2, note_off(60))]
        world = self.run_poly(script)
        self.assertEqual(voices(world), [(1, K(60)), (3, 0), (1, 0)])

    def test_keypoly_alone_and_without_the_effect(self):
        only = {"keyboard": KEYBOARD_PORT, "poly_effect_id": KEYPOLY}
        world = self.run_poly(chord(1.0, 48, 52) + chord(1.1, 48, 52, on=False), chain=(STOCK, KEYPOLY), synth=only)
        self.assertEqual(voices(world, slot=1), [(1, K(48)), (2, K(52)), (1, 0), (2, 0)])

        world = self.run_poly(chord(1.0, 48, 52), chain=(STOCK, KEYSYNTH), synth=only)   # KeySynth is not set up
        self.assertEqual(world.params, [])
        self.assertIn("No KeyPoly effect in this preset", self.output)
        world = self.run_poly(chord(1.0, 48, 52), chain=(STOCK,))
        self.assertIn("No KeySynth or KeyPoly effect in this preset", self.output)


class BothEffects(SynthCase):
    def test_a_preset_change_switches_between_keysynth_and_keypoly(self):
        def to_poly(world):
            world.chain = [STOCK, KEYPOLY]
            world.preset_change(5)

        def back(world):
            world.chain = [KEYSYNTH]
            world.preset_change(94)
        script = (chord(1.0, 48, 52) + chord(1.1, 52, 48, on=False) + [(1.2, to_poly)]
                  + chord(1.6, 48, 52) + chord(1.7, 52, 48, on=False) + [(1.8, back)]
                  + chord(2.2, 48, 52) + chord(2.3, 52, 48, on=False))
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=2.6)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        # KeySynth plays the last key of the chord, and the first when that is released; KeyPoly both
        self.assertEqual([(slot, param, value) for _, slot, param, value in world.params],
                         [(0, 2, K(48)), (0, 2, K(52)), (0, 2, K(48)), (0, 2, 0),
                          (1, 2, K(48)), (1, 3, K(52)), (1, 3, 0), (1, 2, 0),
                          (0, 2, K(48)), (0, 2, K(52)), (0, 2, K(48)), (0, 2, 0)])
        self.assertIn("KeySynth is effect 1 – the keyboard plays it\n", self.output)
        self.assertIn("KeyPoly is effect 2 – the keyboard plays it with 4 voices", self.output)

    def test_the_first_synth_effect_in_the_chain_is_played(self):
        world = World(chord(1.0, 48, 52) + chord(1.1, 48, 52, on=False), ports=KEYBOARD_ONLY,
                      chain=[STOCK, KEYPOLY, KEYSYNTH])
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual({slot for _, slot, *_ in world.params}, {1})


class KnobsOfKeyPoly(SynthCase):
    def test_controllers_set_the_knobs_keypoly_has(self):
        state = {}
        bridge.CONTROLS.write_text(json.dumps({"controls": {"Cutoff": 14, "Level": 7, "Glide": 15, "Vib": 1}}),
                                   encoding="utf-8")
        script = [(1.0, control(14, 127)), (1.1, control(7, 64)), (1.2, control(15, 127)), (1.3, control(1, 127)),
                  (1.35, lambda world: state.update(self.instance.state()["synth"]))]
        world = World(script, ports=KEYBOARD_ONLY, chain=[STOCK, KEYPOLY])
        self.install(synth=SYNTH)
        self.run_bridge(world)
        # Cutoff, Level and the LFO's vibrato half; Glide is a KeySynth knob and sets nothing here
        self.assertEqual([(slot, param, value) for _, slot, param, value in world.params],
                         [(1, 8, 100), (1, 6, 50), (1, 12, 0)])
        self.assertEqual([knob["name"] for knob in state["knobs"]], list(zs.KEYPOLY.controls))
        self.assertEqual((state["effect"], state["slot"]), ("KeyPoly", 1))

    def test_a_controller_can_be_learned_for_a_knob_only_keypoly_has(self):
        script = [(1.0, lambda world: self.instance.synth_control("learn", "Reso")), (1.1, control(21, 5)),
                  (1.2, control(21, 127))]
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYPOLY])
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(self.instance.controls, {"Reso": 21})
        self.assertIn("KeyPoly: controller 21 now sets Reso", self.output)
        self.assertEqual([(slot, param, value) for _, slot, param, value in world.params], [(0, 9, 100)])

    def test_expression_is_never_sent_to_a_key_knob(self):
        world = World([(0.9, cc(127)), (1.0, cc(64))], ports=EVERYTHING, chain=[KEYPOLY])
        self.install({PRESET: [mapping(param=4), mapping(param=9)]},
                     {PRESET: learned((0, 4, 0, 100), (0, 9, 0, 100))}, synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual([(slot, param, value) for _, slot, param, value in world.params], [(0, 9, 100), (0, 9, 50)])
        self.assertIn("that is the KeyPoly's Key3 knob", self.output)

    def test_without_an_effect_the_interface_lists_the_knobs_of_both(self):
        state = {}
        world = World([(1.0, lambda world: state.update(self.instance.state()["synth"]))],
                      ports=KEYBOARD_ONLY, chain=[STOCK])
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual([knob["name"] for knob in state["knobs"]], list(zs.ALL_CONTROLS))
        self.assertIn("Cutoff", zs.ALL_CONTROLS)
        self.assertIn("Glide", zs.ALL_CONTROLS)
        self.assertEqual((state["effect"], state["voices"]), (None, []))


class PolySettings(SynthCase):
    def test_settings_that_are_not_acceptable_stop_the_start(self):
        refused = {
            "two different ids": dict(SYNTH, poly_effect_id=KEYSYNTH),
            "a value is not a number": dict(SYNTH, poly_effect_id="KeyPoly"),
            "effect_id is missing": {"keyboard": KEYBOARD_PORT, "effect_id": None, "poly_effect_id": None},
        }
        for expected, synth in refused.items():
            with self.subTest(expected):
                self.install(synth=synth)
                self.assertIn(expected, self.start_fails())

    def test_the_interface_shows_which_note_each_voice_plays(self):
        state = {}
        world = World(chord(1.0, 48, 52, 55) + [(1.1, note_off(52)),
                                                (1.2, lambda world: state.update(self.instance.state()["synth"]))],
                      ports=KEYBOARD_ONLY, chain=[KEYPOLY])
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(state["voices"], ["C3", None, "G3", None])
        self.assertEqual(state["key"], K(48))


if __name__ == "__main__":
    unittest.main()
