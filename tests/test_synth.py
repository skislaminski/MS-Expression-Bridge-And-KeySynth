"""The keyboard synth: a MIDI keyboard plays the KeySynth effect through its Key knob.

Run against the simulated devices from sim.py. KeySynth is a DIY effect (id 07000f61 here); its
first knob is gate and pitch: 0 = gate off, n = 10-cent steps above C0 (K(note) below).
"""
import json
import unittest
from unittest import mock

import mido
import yaml

import bridge
import zoom_sysex as zs
from tests.sim import (ID, CONTROLLER_PORT, KEYBOARD_PORT, ZOOM_PORT, SimulatedCase, World, cc,
                       keyboard, learned, mapping, note_off, note_on, patch_dump)

KEYSYNTH, STOCK = 0x07000F61, 0x01000020
SYNTH = {"keyboard": KEYBOARD_PORT, "channel": 1, "effect_id": KEYSYNTH}
EVERYTHING = lambda seconds: [ZOOM_PORT, CONTROLLER_PORT, KEYBOARD_PORT]
KEYBOARD_ONLY = lambda seconds: [ZOOM_PORT, KEYBOARD_PORT]
PATCH_QUERY, DISABLE = "F0 52 00 6E 64 13 F7", "F0 52 00 6E 51 F7"
PRESET = "0/94"
K = zs.key_for                                  # the Key value of a MIDI note

# A real reply of the MS-60B+ (firmware 1.20) to the patch query, 2026-10-07: six effects,
# KeySynth first with Key at 0, then BaOctaver, AMPG SVT, BrghtRoom, BaVinFLNG, TriChorus.
REAL_DUMP = bytes.fromhex(
    "F052006E641201500600505443463401000000020000000600000000000008000000000000000055530045522D3030332000"
    "20610F0007100000000720000005200000000B50000006003000000654584A00310000000054580045310000000045084454"
    "421000000042431E000E0018400200080009001001407000000F0A0000000000002100000E000D1440024001000000000000"
    "00000000000000000000411200000A071C010C0600000A342020080000000000000000000000410000160128244001700000"
    "000000000000000000020000000000210024004C0B1C0004040000000000000000000000000000000000006100000C196410"
    "001910010000000000000000000000000000000050524D0032200000000000000000000000000048000C4206000000000000"
    "000008000000000000000000004000074E414D45200000000055534552002D30303320202000202020202020200020202020"
    "2020200020202000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "00000000000000000000000000000000000000000000000000000000000B1D5B350AF7")


def sent(world: World) -> list:
    return [(slot, param, value) for _, slot, param, value in world.params]


def control(number: int, value: int):
    return keyboard(mido.Message("control_change", control=number, value=value))


class PatchDumps(unittest.TestCase):
    def test_a_real_dump_gives_the_effects_in_slot_order(self):
        dump = zs.parse_patch_dump(zs.zoom_body(REAL_DUMP, ID))
        self.assertEqual(dump.name, "USER-003")
        self.assertEqual([f"{effect.id:08x}" for effect in dump.effects],
                         ["07000f61", "07000010", "05000020", "0b000020", "06000050", "06000030"])
        self.assertEqual(dump.effects[0], zs.PatchEffect(KEYSYNTH, True, 0))
        self.assertEqual([effect.first_param for effect in dump.effects], [0, 52, 30, 4, 47, 100])

    def test_a_damaged_dump_is_not_trusted(self):
        for position in (9, 40, 500, len(REAL_DUMP) - 3):
            with self.subTest(position):
                damaged = bytearray(REAL_DUMP)
                damaged[position] ^= 0x01
                self.assertIsNone(zs.parse_patch_dump(zs.zoom_body(bytes(damaged), ID)))
        self.assertIsNone(zs.parse_patch_dump(zs.zoom_body(REAL_DUMP[:400] + b"\xF7", ID)))
        self.assertIsNone(zs.parse_patch_dump(bytes.fromhex("64 20 01 00 02 32 00 00 00 00")))
        self.assertIsNone(zs.parse_patch_dump(None))

    def test_the_simulated_dump_reads_like_a_real_one(self):
        dump = zs.parse_patch_dump(zs.zoom_body(patch_dump([(STOCK, 80), (KEYSYNTH, 128)]), ID))
        self.assertEqual(dump.effects, (zs.PatchEffect(STOCK, True, 80), zs.PatchEffect(KEYSYNTH, True, 128)))
        self.assertEqual(len(patch_dump([(KEYSYNTH, 0)])), len(patch_dump([])) + 32)


class KeyMessages(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(zs.to_hex(zs.build_set_key(ID, 0, 58)), "F0 52 00 6E 64 20 00 00 02 3A 00 00 00 00 F7")
        self.assertEqual(zs.to_hex(zs.build_set_key(ID, 5, 128)), "F0 52 00 6E 64 20 00 05 02 00 01 00 00 00 F7")
        self.assertEqual(zs.to_hex(zs.build_set_key(ID, 3, 0)), "F0 52 00 6E 64 20 00 03 02 00 00 00 00 00 F7")
        self.assertEqual(zs.to_hex(zs.build_set_key(ID, 0, 1000)), "F0 52 00 6E 64 20 00 00 02 68 07 00 00 00 F7")

    def test_notes_and_bends_as_key_values(self):
        self.assertEqual([K(12), K(13), K(45), K(111)], [1, 11, 331, 991])
        self.assertEqual([K(45, 0.5), K(45, -2), K(45, 2), K(45, 0.04), K(45, 0.06)], [336, 311, 351, 331, 332])
        self.assertEqual([K(12, -2), K(111, 2)], [1, 1000])            # a bend ends where the knob ends
        for note in (11, 112, -1, 127):
            with self.subTest(note), self.assertRaises(ValueError):
                K(note)
        self.assertEqual([zs.key_pitch(key) for key in (1, 331, 334, 337, 991, 1000)],
                         [(12, 0), (45, 0), (45, 30), (46, -40), (111, 0), (112, -10)])
        self.assertEqual((zs.KEY_LOW_NOTE, zs.KEY_HIGH_NOTE), (12, 111))

    def test_only_the_key_knob_of_an_effect_slot_can_be_built(self):
        for slot, key in ((0, 1001), (0, -1), (6, 5), (-1, 5), (0x64, 5), (0x5F, 5)):
            with self.subTest(slot=slot, key=key), self.assertRaises(ValueError):
                zs.build_set_key(ID, slot, key)

    def test_the_allowlist_needs_the_key_target_of_that_slot(self):
        message = zs.build_set_key(ID, 2, 128)
        self.assertEqual(zs.classify(message, ID, zs.synth_targets(2)), "set_param")
        for targets in ([], zs.synth_targets(1), [zs.ParamTarget(2, 2, 0, 100)]):
            with self.subTest(targets), self.assertRaises(zs.NotAllowed):
                zs.classify(message, ID, targets)


class WhichNoteSounds(unittest.TestCase):
    def test_the_last_note_wins_and_a_held_one_comes_back(self):
        keys = bridge.Keys()
        self.assertEqual(keys.note, None)
        keys.note_on(40); keys.note_on(43); keys.note_on(47)
        self.assertEqual(keys.note, 47)
        keys.note_off(47)
        self.assertEqual(keys.note, 43)
        keys.note_off(40)                       # not the sounding one: nothing changes
        self.assertEqual(keys.note, 43)
        keys.note_off(43)
        self.assertEqual(keys.note, None)
        keys.note_off(43)                       # a note off without a note on is harmless
        self.assertEqual(keys.note, None)

    def test_pressing_a_note_again_makes_it_the_last(self):
        keys = bridge.Keys()
        keys.note_on(40); keys.note_on(43); keys.note_on(40)
        self.assertEqual((keys.note, keys.held), (40, [43, 40]))

    def test_the_sustain_pedal_holds_the_note(self):
        keys = bridge.Keys()
        keys.note_on(40); keys.pedal(True); keys.note_off(40)
        self.assertEqual(keys.note, 40)
        keys.note_on(45); keys.note_off(45)
        self.assertEqual(keys.note, 45)          # the last one released
        keys.note_on(40)                        # pressed again: a held key beats a sustained one
        self.assertEqual(keys.note, 40)
        keys.note_off(40); keys.pedal(False)
        self.assertEqual(keys.note, None)

    def test_sustain_can_be_switched_off_and_all_notes_off_clears(self):
        keys = bridge.Keys(sustain_enabled=False)
        keys.note_on(40); keys.pedal(True); keys.note_off(40)
        self.assertEqual(keys.note, None)
        keys = bridge.Keys()
        keys.note_on(40); keys.pedal(True); keys.note_on(41); keys.note_off(41); keys.clear()
        self.assertEqual(keys.note, None)

    def test_note_names(self):
        self.assertEqual([bridge.note_name(note) for note in (0, 36, 57, 60, 127)], ["C-1", "C2", "A3", "C4", "G9"])


class SynthCase(SimulatedCase):
    def assign(self, **controls) -> None:
        """Writes controls.json: which controller sets what, as the interface would have stored it."""
        bridge.CONTROLS.write_text(json.dumps({"controls": controls}), encoding="utf-8")

    def setUp(self) -> None:
        super().setUp()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        patcher = mock.patch.object(bridge, "SYNTH_QUERY_SECONDS", 0.2)
        patcher.start()
        self.addCleanup(patcher.stop)


class Playing(SynthCase):
    def test_notes_set_the_key_knob_at_once(self):
        script = [(1.0, note_on(36)), (1.2, note_off(36)),
                  (1.3, note_on(48)), (1.35, note_on(52)), (1.4, note_off(52)), (1.5, note_off(48)),
                  (1.6, note_on(111)), (1.65, keyboard(mido.Message("note_on", note=111, velocity=0)))]
        times = [seconds for seconds, _ in script]
        state = {}
        world = World(script + [(1.45, lambda world: state.update(self.instance.state()))],
                      ports=EVERYTHING, chain=[STOCK, KEYSYNTH])
        self.install(synth=SYNTH)
        self.run_bridge(world)

        self.assertEqual(sent(world), [(1, 2, K(36)), (1, 2, 0), (1, 2, K(48)), (1, 2, K(52)), (1, 2, K(48)),
                                       (1, 2, 0), (1, 2, K(111)), (1, 2, 0)])
        for due, (seconds, *_) in zip(times, world.params):
            self.assertLess(seconds - due, 0.02)                      # no spacing, no waiting
        self.assertEqual(world.count(PATCH_QUERY), 1)                 # once at the start is enough
        self.assertIn("KeySynth is effect 2", self.output)
        self.assertIn("C2 ( 36) → Key  241   ack", self.output)
        self.assertEqual(world.sent[-1][1], DISABLE)
        self.assertEqual({key: state["synth"][key] for key in ("keyboard", "expression", "slot", "key")},
                         {"keyboard": True, "expression": True, "slot": 1, "key": K(48)})
        self.assertEqual(state["synth"]["totals"]["acked"], 5)
        for _, text in world.sent:                                    # everything sent is on the allowlist
            zs.classify(bytes.fromhex(text.replace(" ", "")), ID, zs.synth_targets(1))

    def test_sustain_channel_and_note_range(self):
        script = [(1.0, note_on(40)), (1.05, control(64, 127)), (1.1, note_off(40)),     # held by the pedal
                  (1.2, control(64, 0)),                                                  # released
                  (1.3, note_on(50, channel=1)), (1.35, note_off(50, channel=1)),         # another channel
                  (1.4, note_on(20)), (1.45, note_off(20)), (1.5, note_on(90)),           # outside the range
                  (1.6, note_on(60)), (1.65, control(123, 0)),                            # all notes off
                  (1.7, keyboard(mido.Message("pitchwheel", pitch=4000)))]                # no note sounds: nothing to bend
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH])
        self.install(synth=dict(SYNTH, low_note=28, high_note=72))
        self.run_bridge(world)
        self.assertEqual(sent(world), [(0, 2, K(40)), (0, 2, 0), (0, 2, K(60)), (0, 2, 0)])

    def test_a_note_still_down_at_the_end_is_switched_off(self):
        world = World([(1.0, note_on(45))], ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=1.3)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(sent(world), [(0, 2, K(45)), (0, 2, 0)])
        self.assertEqual(world.sent[-1][1], DISABLE)

    def test_a_key_value_left_on_the_pedal_is_cleared_by_the_first_note_off(self):
        world = World([(1.0, note_on(45)), (1.1, note_off(45))], ports=KEYBOARD_ONLY, chain=[KEYSYNTH])
        world.values[(0, 2)] = K(45)            # the knob already stands on that note
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(sent(world), [(0, 2, 0)])     # the note on changes nothing, so it is not sent


class NotesAndExpression(SynthCase):
    HOLD = {PRESET: [mapping()]}                       # expression → effect 1, parameter 1
    HOLD_LEARNED = {PRESET: learned((0, 2, 0, 100))}

    def test_notes_are_not_held_up_by_an_expression_sweep(self):
        sweep = [(0.9 + 0.002 * i, cc(value)) for i, value in enumerate(list(range(128)) * 2)]
        notes = [(1.0, note_on(40)), (1.1, note_off(40)), (1.2, note_on(52)), (1.3, note_off(52))]
        world = World(sweep + notes, ports=EVERYTHING, chain=[STOCK, KEYSYNTH])
        self.install(self.HOLD, self.HOLD_LEARNED, synth=SYNTH)
        self.run_bridge(world)

        keys = [(seconds, value) for seconds, slot, _, value in world.params if slot == 1]
        expression = [seconds for seconds, slot, *_ in world.params if slot == 0]
        self.assertEqual([value for _, value in keys], [K(40), 0, K(52), 0])
        for (due, _), (seconds, _) in zip(notes, keys):
            self.assertLess(seconds - due, 0.02)
        self.assertGreater(len(expression), 20)
        self.assertGreaterEqual(min(later - earlier for earlier, later in zip(expression, expression[1:])), 0.009)
        for seconds, _ in keys:                                       # expression keeps its distance from a note
            self.assertFalse([moment for moment in expression if 0 < moment - seconds < 0.009])

    def test_expression_is_never_sent_to_the_key_knob(self):
        sweep = [(0.9 + 0.01 * i, cc(value)) for i, value in enumerate(range(0, 128, 4))]
        world = World(sweep + [(1.1, note_on(40)), (1.2, note_off(40)),
                               (1.25, lambda world: world.knob(0, 2, 77)),      # the Key knob turned on the pedal
                               (1.3, cc(100)), (1.35, cc(20))],
                      ports=EVERYTHING, chain=[KEYSYNTH])
        self.install(self.HOLD, self.HOLD_LEARNED, synth=SYNTH)
        self.run_bridge(world)
        # the two notes, and at the end the gate is closed because the knob was left on 77;
        # none of the expression values (0–100 from the sweep, 79 and 16 afterwards) went out
        self.assertEqual(sent(world), [(0, 2, K(40)), (0, 2, 0), (0, 2, 0)])
        self.assertIn("that is the KeySynth's Key knob", self.output)

    def test_expression_alone_still_works_while_the_keyboard_is_missing(self):
        world = World([(0.9, cc(64)), (1.0, cc(127))], chain=[STOCK, KEYSYNTH])     # no keyboard port
        self.install(self.HOLD, self.HOLD_LEARNED, synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(sent(world), [(0, 2, 50), (0, 2, 100)])


class FindingTheEffect(SynthCase):
    def test_without_the_effect_the_keyboard_is_ignored(self):
        world = World([(1.0, note_on(40)), (1.05, note_off(40)), (1.4, note_on(41)), (1.45, note_off(41))],
                      ports=KEYBOARD_ONLY, chain=[STOCK], length=2.0)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(sent(world), [])
        self.assertEqual(self.output.count("No KeySynth effect in this preset"), 1)
        self.assertLessEqual(world.count(PATCH_QUERY), 3)             # at the start and once per held key

    def test_an_effect_added_while_a_key_is_down_is_found(self):
        def insert(world):
            world.chain = [KEYSYNTH, STOCK]
        world = World([(1.0, note_on(40)), (1.5, insert), (2.2, note_off(40))],
                      ports=KEYBOARD_ONLY, chain=[STOCK], length=2.6)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(sent(world), [(0, 2, K(40)), (0, 2, 0)])
        first = world.params[0][0]
        self.assertTrue(1.5 <= first <= 1.5 + 0.2 + 0.1, first)       # found at the next look, 0.2 s apart here
        self.assertLessEqual(world.count(PATCH_QUERY), 2 + 7)          # only while the key was down
        self.assertIn("KeySynth is effect 1", self.output)

    def test_a_preset_change_makes_the_bridge_look_again(self):
        def to_plain(world):
            world.chain = [STOCK]
            world.preset_change(5)

        def back(world):
            world.chain = [STOCK, STOCK, KEYSYNTH]
            world.preset_change(94)
        world = World([(1.0, note_on(40)), (1.1, note_off(40)), (1.2, to_plain),
                       (1.6, note_on(41)), (1.65, note_off(41)), (1.8, back),
                       (2.3, note_on(42)), (2.4, note_off(42))],
                      ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=2.8)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(sent(world), [(0, 2, K(40)), (0, 2, 0), (2, 2, K(42)), (2, 2, 0)])
        self.assertIn("KeySynth is effect 3", self.output)

    def test_without_approval_of_the_patch_query_the_keyboard_is_ignored(self):
        self.approve(*bridge.REQUIRED_KINDS)
        world = World([(1.0, note_on(40)), (1.1, note_off(40)), (1.2, cc(64))], ports=EVERYTHING, chain=[STOCK, KEYSYNTH])
        self.install({PRESET: [mapping()]}, {PRESET: learned((0, 2, 0, 100))}, synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(world.count(PATCH_QUERY), 0)
        self.assertEqual(sent(world), [(0, 2, 50)])                   # expression is unaffected
        self.assertIn("python probe.py approve query_patch", self.output)

    def test_unconfirmed_notes_are_repeated_and_make_the_bridge_look_again(self):
        script = [(1.0 + 0.6 * i, action) for i, action in enumerate(
            [note_on(40), note_off(40), note_on(41), note_off(41)])]
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH], acks=False, length=3.6)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        tries = 1 + bridge.KEY_RETRIES
        self.assertEqual(sent(world), [(0, 2, K(40))] * tries + [(0, 2, 0)] * tries
                         + [(0, 2, K(41))] * tries + [(0, 2, 0)] * tries)
        self.assertIn("the pedal does not confirm notes", self.output)
        self.assertGreaterEqual(world.count(PATCH_QUERY), 2)

    def test_while_notes_stay_unconfirmed_the_pedal_is_not_flooded(self):
        """Seen on the real pedal: turning a knob or browsing effects keeps it from answering."""
        script = [(1.0 + 0.1 * i, note_on(40 + i % 3) if i % 2 == 0 else note_off(40 + (i - 1) % 3))
                  for i in range(40)]
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH], acks=False, length=5.4)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(self.output.count("the pedal does not confirm notes"), 1)     # once in 5 s
        self.assertLessEqual(world.count(PATCH_QUERY), 2)                              # the start and one more
        self.assertLessEqual(world.count("F0 52 00 6E 50 F7"), 1 + 3)                  # edit enable every 2 s at most

    def test_a_quick_replug_of_the_pedal_is_healed(self):
        """Seen on the real pedal: after its USB cable was out for two seconds it confirmed nothing."""
        state = {}
        script = [(1.0 + 0.1 * i, note_on(40 + i % 3) if i % 2 == 0 else note_off(40 + (i - 1) % 3))
                  for i in range(30)]
        script += [(1.55, lambda world: world.replug()),
                   (3.9, lambda world: state.update(self.instance.state()))]
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=4.2)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(world.count("F0 52 00 6E 50 F7"), 2)          # at the start, and once to heal
        again = [seconds for seconds, text in world.sent if text == "F0 52 00 6E 50 F7"][1]
        self.assertLess(again - 1.55, 0.6)                             # three notes later at most
        totals = state["synth"]["totals"]
        self.assertGreaterEqual(totals["acked"], 30 - 6)               # only the few in between are lost
        self.assertNotIn("Connection lost", self.output)

    def test_a_lost_gate_off_is_repeated_quickly(self):
        """Seen on the real pedal: a gate-off sent while the player was in the effect menu got lost."""
        lost = []

        def accept(slot, param, value):
            if value == 0 and not lost:
                lost.append(True)                # the pedal misses the first gate-off
                return False
            return True
        world = World([(1.0, note_on(40)), (1.2, note_off(40))], ports=KEYBOARD_ONLY, chain=[KEYSYNTH])
        world.accept = accept
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(sent(world), [(0, 2, K(40)), (0, 2, 0), (0, 2, 0)])
        first, again = world.params[1][0], world.params[2][0]
        self.assertTrue(0.05 <= again - first <= 0.1, again - first)   # not a quarter of a second later
        self.assertNotIn("does not confirm notes", self.output)

    def test_a_newer_note_is_never_overtaken_by_a_repeat(self):
        script = [(1.0, note_on(40)), (1.03, note_on(43)), (1.06, note_off(43)), (1.09, note_off(40))]
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH], acks=False, length=1.8)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        values = [value for *_, value in sent(world)]
        self.assertEqual(values[:4], [K(40), K(43), K(40), 0])                  # no repeat in between: each is superseded
        self.assertEqual(values[4:], [0] * bridge.KEY_RETRIES)         # only the final gate-off is repeated


class Controllers(SynthCase):
    def test_the_keyboard_plays_without_the_expression_controller(self):
        world = World([(1.0, note_on(40)), (1.1, note_off(40))], ports=KEYBOARD_ONLY, chain=[KEYSYNTH])
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(sent(world), [(0, 2, K(40)), (0, 2, 0)])
        self.assertIn(f"Ready: {KEYBOARD_PORT} → {ZOOM_PORT}", self.output)

    def test_without_any_controller_the_bridge_waits(self):
        ports = lambda seconds: [ZOOM_PORT] + ([KEYBOARD_PORT] if seconds > 0.5 else [])
        world = World([(1.6, note_on(40)), (1.7, note_off(40))], ports=ports, chain=[KEYSYNTH], length=2.2)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertIn("Waiting for devices: no controller connected", self.output)
        self.assertEqual(sent(world), [(0, 2, K(40)), (0, 2, 0)])

    def test_the_keyboard_can_join_and_leave_while_expression_keeps_running(self):
        ports = lambda seconds: [ZOOM_PORT, CONTROLLER_PORT] + ([KEYBOARD_PORT] if 1.0 < seconds < 1.8 else [])
        world = World([(0.9, cc(64)), (1.4, note_on(40)), (2.2, cc(127))], ports=ports,
                      chain=[STOCK, KEYSYNTH], length=2.6)
        self.install({PRESET: [mapping()]}, {PRESET: learned((0, 2, 0, 100))}, synth=SYNTH)
        self.run_bridge(world)
        # the held note is switched off when the keyboard disappears; expression goes on
        self.assertEqual(sent(world), [(0, 2, 50), (1, 2, K(40)), (1, 2, 0), (0, 2, 100)])
        self.assertIn(f"{KEYBOARD_PORT} connected", self.output)
        self.assertIn(f"{KEYBOARD_PORT} is gone", self.output)
        self.assertNotIn("Connection lost", self.output)
        self.assertEqual(world.count("F0 52 00 6E 50 F7"), 1)          # one session throughout

    def test_without_a_synth_section_the_expression_controller_stays_required(self):
        def ports(seconds):                     # no port is ever opened, so the run has to end here
            if seconds > 1.0:
                raise KeyboardInterrupt
            return [ZOOM_PORT, KEYBOARD_PORT]
        world = World([(0.5, note_on(40))], ports=ports, chain=[KEYSYNTH])
        self.install()
        self.run_bridge(world)
        self.assertIn("Waiting for devices", self.output)
        self.assertEqual(world.sent, [])


class SynthSettings(SynthCase):
    def test_settings_that_are_not_acceptable_stop_the_start(self):
        refused = {
            "unknown setting slot": dict(SYNTH, slot=0),
            "effect_id is missing": {"keyboard": KEYBOARD_PORT},
            "channel must be 0–16": dict(SYNTH, channel=17),
            "low_note ≤ high_note": dict(SYNTH, low_note=60, high_note=40),
            "a value is not a number": dict(SYNTH, effect_id="KeySynth"),
            "bend_range 0–12 semitones": dict(SYNTH, bend_range=13),
        }
        for expected, synth in refused.items():
            with self.subTest(expected):
                self.install(synth=synth)
                self.assertIn(expected, self.start_fails())

    def test_keyboard_only_needs_no_expression_settings(self):
        """Someone without an expression controller leaves its port empty and never measures a CC."""
        def without_expression(port: str) -> None:
            cfg = yaml.safe_load(bridge.CONFIG.read_text(encoding="utf-8"))
            cfg["ports"]["chocolate"] = port
            cfg["expression"].update(cc=None, channel=None)
            bridge.CONFIG.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

        self.install(synth=SYNTH)
        without_expression(CONTROLLER_PORT)      # a controller is named, so its CC has to be known
        self.assertIn("config.yaml is incomplete", self.start_fails())
        self.install()
        without_expression("")                   # no synth either: nothing could ever be controlled
        self.assertIn("config.yaml is incomplete", self.start_fails())

        self.install(synth=SYNTH)
        without_expression("")
        world = World([(1.0, note_on(40)), (1.1, note_off(40))], ports=KEYBOARD_ONLY, chain=[KEYSYNTH])
        self.run_bridge(world)
        self.assertEqual(sent(world), [(0, 2, K(40)), (0, 2, 0)])
        self.assertIn(f"Ready: {KEYBOARD_PORT} → {ZOOM_PORT}", self.output)

    def test_a_section_without_a_keyboard_switches_the_synth_off(self):
        self.install(synth={"keyboard": "", "effect_id": KEYSYNTH})
        self.run_bridge(World())
        self.assertIsNone(self.instance.state()["synth"])


def wheel(pitch: int):
    return keyboard(mido.Message("pitchwheel", pitch=pitch))


def keys_sent(world: World, slot: int = 0) -> list:
    return [value for _, where, param, value in world.params if (where, param) == (slot, 2)]


def call(function, *arguments, into=None):
    """A script action that runs a bridge method in the main loop, the way the interface does."""
    def action(world):
        try:
            function(*arguments)
        except bridge.UserError as error:
            if into is None:
                raise
            into.append(str(error))
    return action


class PitchBend(SynthCase):
    def test_the_wheel_walks_the_key_knob_in_small_steps(self):
        script = [(1.0, note_on(45)), (1.1, wheel(8191)), (1.4, wheel(0)), (1.7, note_off(45))]
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH])
        self.install(synth=SYNTH)
        self.run_bridge(world)
        # two semitones are 20 clicks; the effect takes more than 9 at once for a new note
        self.assertEqual(keys_sent(world), [331, 340, 349, 351, 342, 333, 331, 0])
        times = [seconds for seconds, *_ in world.params]
        self.assertGreaterEqual(min(later - earlier for earlier, later in zip(times[1:7], times[2:7])), 0.009)
        self.assertLess(times[3] - 1.1, 0.06)                          # the wheel is followed without delay
        self.assertEqual(self.output.count("→ Key"), 2)                # the note and the gate-off, no line per step
        self.assertIn("A2 ( 45) → Key  331   ack", self.output)

    def test_a_note_played_with_the_wheel_up_starts_bent(self):
        script = [(1.0, wheel(4096)), (1.1, note_on(50)), (1.2, note_on(52)), (1.3, note_off(52)),
                  (1.35, note_off(50))]
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH])
        self.install(synth=SYNTH)
        self.run_bridge(world)
        # the wheel alone sends nothing; every note goes out at once, a whole semitone up
        self.assertEqual(keys_sent(world), [K(50, 1), K(52, 1), K(50, 1), 0])
        for due, (seconds, *_) in zip((1.1, 1.2, 1.3, 1.35), world.params):
            self.assertLess(seconds - due, 0.02)
        self.assertIn("D#3 ( 51) → Key  391   ack", self.output)   # the line names what sounds

    def test_bend_range_0_switches_the_wheel_off(self):
        world = World([(1.0, note_on(45)), (1.1, wheel(8191)), (1.3, note_off(45))],
                      ports=KEYBOARD_ONLY, chain=[KEYSYNTH])
        self.install(synth=dict(SYNTH, bend_range=0))
        self.run_bridge(world)
        self.assertEqual(keys_sent(world), [331, 0])

    def test_a_wide_bend_still_moves_in_small_steps(self):
        world = World([(1.0, note_on(45)), (1.1, wheel(8191)), (1.6, note_off(45))],
                      ports=KEYBOARD_ONLY, chain=[KEYSYNTH])
        self.install(synth=dict(SYNTH, bend_range=12))
        self.run_bridge(world)
        values = keys_sent(world)[:-1]
        self.assertEqual((values[0], values[-1]), (331, 451))
        self.assertTrue(all(0 < later - earlier <= zs.KEY_BEND_STEP for earlier, later in zip(values, values[1:])))

    def test_the_end_of_a_bend_is_repeated_if_the_pedal_misses_it(self):
        missed = []

        def accept(slot, param, value):
            if value == 351 and not missed:
                missed.append(True)
                return False
            return True
        world = World([(1.0, note_on(45)), (1.1, wheel(8191))], ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=1.5)
        world.accept = accept
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(keys_sent(world), [331, 340, 349, 351, 351, 0])

    def test_a_bend_is_not_slowed_down_by_a_parameter_the_pedal_ignores(self):
        """Seen with the real devices: an expression assignment pointed at an effect that had been
        replaced, the pedal confirmed none of its values, and sending slowed down to 40 ms."""
        sweep = [(0.9 + 0.01 * i, cc(value)) for i, value in enumerate(range(0, 128, 3))]
        world = World(sweep + [(1.8, note_on(45)), (1.9, wheel(8191))], ports=EVERYTHING,
                      chain=[STOCK, KEYSYNTH], length=2.3)
        world.accept = lambda slot, param, value: slot != 0          # nothing in slot 0 answers
        self.install({PRESET: [mapping()]}, {PRESET: learned((0, 2, 0, 100))}, synth=SYNTH)
        self.run_bridge(world)
        self.assertIn("acks are missing – minimum interval now 40 ms", self.output)
        bend = [seconds for seconds, slot, _, value in world.params if slot == 1 and value in (340, 349, 351)]
        self.assertEqual(len(bend), 3)
        self.assertLess(bend[-1] - bend[0], 0.035)                    # 10 ms apart, not 40

    def test_a_note_replaced_within_a_millisecond_is_not_counted_as_lost(self):
        """Seen on the real pedal: of two messages 1 ms apart it confirms only the second."""
        dropped, state = [], {}

        def accept(slot, param, value):
            if value == K(40) and not dropped:
                dropped.append(True)
                return False
            return True
        world = World([(1.0, note_on(40)), (1.0, note_on(43)), (1.2, note_off(43)), (1.3, note_off(40)),
                       (1.4, lambda world: state.update(self.instance.state()))],
                      ports=KEYBOARD_ONLY, chain=[KEYSYNTH])
        world.accept = accept
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(keys_sent(world), [K(40), K(43), K(40), 0])   # the first is not sent again
        totals = state["synth"]["totals"]
        self.assertEqual((totals["overtaken"], totals["missed"], totals["acked"]), (1, 0, 3))
        log = (self.folder / "logs" / "sysex.log").read_text(encoding="utf-8")
        self.assertIn(f"Key {K(40)} was overtaken", log)
        self.assertNotIn("no ack for Key", log)


class KeyboardControllers(SynthCase):
    def test_nothing_is_assigned_until_the_user_does_it(self):
        state = {}
        world = World([(1.0, control(1, 100)), (1.1, control(14, 100)),
                       (1.2, lambda world: state.update(self.instance.state()))],
                      ports=KEYBOARD_ONLY, chain=[KEYSYNTH])
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(sent(world), [])
        self.assertEqual(self.instance.controls, {})
        self.assertTrue(all(knob["cc"] is None for knob in state["synth"]["knobs"]))
        self.assertFalse(bridge.CONTROLS.exists())

    def test_an_assigned_controller_sets_its_knob(self):
        state = {}
        self.assign(Rate=1)
        script = [(1.0, control(1, 0)), (1.05, control(1, 64)), (1.1, control(1, 127)), (1.2, control(14, 100)),
                  (1.3, lambda world: state.update(self.instance.state()))]
        world = World(script, ports=KEYBOARD_ONLY, chain=[STOCK, KEYSYNTH])
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(sent(world), [(1, 13, 0), (1, 13, 50), (1, 13, 100)])   # controller 14 sets nothing
        knobs = {knob["name"]: knob for knob in state["synth"]["knobs"]}
        self.assertEqual(list(knobs), ["Level", "Wave1", "Wave2", "Pitch", "Dtune", "Mix", "Glide", "Atk", "Rel",
                                       "LFO", "Rate", "Vib", "Trm"])
        self.assertEqual(knobs["Rate"], {"name": "Rate", "knob": "Rate", "rest": 0, "full": 100, "cc": 1})
        self.assertEqual(knobs["LFO"], {"name": "LFO", "knob": "LFO", "rest": 0, "full": 100, "cc": None})
        self.assertEqual(knobs["Vib"], {"name": "Vib", "knob": "LFO", "rest": 50, "full": 0, "cc": None})
        self.assertEqual(state["targets"], [])                         # these are not expression assignments
        for _, text in world.sent:                                     # everything sent is on the allowlist
            zs.classify(bytes.fromhex(text.replace(" ", "")), ID, zs.synth_targets(1))

    def test_assignments_come_from_controls_json(self):
        bridge.CONTROLS.write_text(json.dumps({"controls": {
            "Glide": 14, "LFO": 15, "Wave1": 16,
            "Key": 17, "Rate": 64, "Mix": 14, "Bogus": 3, "Atk": "9", "Rel": 200}}), encoding="utf-8")
        script = [(1.0, control(14, 127)), (1.1, control(15, 64)), (1.2, control(16, 127)), (1.3, control(1, 90)),
                  (1.35, control(17, 50))]
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH])
        self.install(synth=SYNTH)
        self.run_bridge(world)
        # Key cannot be assigned, 64 is the sustain pedal, 14 is taken, the rest is not a knob or
        # not a controller number; the mod wheel is free because the file says so
        self.assertEqual(self.instance.controls, {"Glide": 14, "LFO": 15, "Wave1": 16})
        self.assertEqual(sent(world), [(0, 9, 100), (0, 12, 50), (0, 4, 3)])

    def test_a_wheel_can_set_the_lfo_depth_from_off(self):
        """The LFO knob is Vib50 … Off … Trm50. "Vib" and "Trm" are its halves: at rest the LFO is off."""
        self.assertEqual([zs.control_value("Vib", value) for value in (0, 2, 3, 64, 125, 127)], [50, 50, 49, 25, 1, 0])
        self.assertEqual([zs.control_value("Trm", value) for value in (0, 64, 127)], [50, 75, 100])
        self.assertEqual([zs.control_value("LFO", value) for value in (0, 64, 127)], [0, 50, 100])
        bridge.CONTROLS.write_text(json.dumps({"controls": {"Vib": 1, "Trm": 22, "LFO": 23}}), encoding="utf-8")
        script = [(1.0, control(1, 127)), (1.1, control(1, 64)), (1.2, control(1, 0)),      # the mod wheel
                  (1.3, control(22, 127)), (1.4, control(23, 0))]                          # all three write one knob
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH])
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(sent(world), [(0, 12, 0), (0, 12, 25), (0, 12, 50), (0, 12, 100), (0, 12, 0)])

    def test_a_sweep_is_thinned_out_and_ends_on_the_last_value(self):
        sweep = [(1.0 + 0.001 * value, control(1, value)) for value in range(128)]
        world = World(sweep, ports=KEYBOARD_ONLY, chain=[KEYSYNTH])
        self.assign(Rate=1)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        times, values = [seconds for seconds, *_ in world.params], [value for *_, value in sent(world)]
        self.assertLess(len(values), 20)
        self.assertEqual(values[-1], 100)
        self.assertEqual(values, sorted(values))
        self.assertGreaterEqual(min(later - earlier for earlier, later in zip(times, times[1:])), 0.009)

    def test_without_the_effect_controllers_do_nothing(self):
        world = World([(1.0, control(1, 100)), (1.1, wheel(8191))], ports=KEYBOARD_ONLY, chain=[STOCK])
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(sent(world), [])

    def test_expression_and_a_controller_can_share_a_knob(self):
        world = World([(0.9, cc(127)), (1.1, control(1, 0)), (1.2, cc(64))], ports=EVERYTHING, chain=[KEYSYNTH])
        self.assign(Rate=1)
        self.install({PRESET: [mapping(param=13)]}, {PRESET: learned((0, 13, 0, 100))}, synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(sent(world), [(0, 13, 100), (0, 13, 0), (0, 13, 50)])

    def test_an_expression_range_wider_than_the_knob_is_not_sent(self):
        """A range learned with another effect in that place, or with an older KeySynth."""
        world = World([(0.9, cc(127)), (1.0, cc(10))], ports=EVERYTHING, chain=[KEYSYNTH])
        self.install({PRESET: [mapping(param=4)]}, {PRESET: learned((0, 4, 0, 100))}, synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(sent(world), [])
        self.assertIn("the KeySynth's Wave1 knob only goes up to 3 – learn it again", self.output)


class LearningControllers(SynthCase):
    def test_the_next_controller_moved_is_assigned(self):
        state = {}
        script = [(1.0, call(lambda: self.instance.synth_control("learn", "Glide"))),
                  (1.05, lambda world: state.update(waiting=self.instance.state()["synth"]["learning"])),
                  (1.1, control(64, 127)), (1.15, control(64, 0)),    # the sustain pedal stays the sustain pedal
                  (1.2, control(20, 5)),                               # learned, not sent as a value
                  (1.3, control(20, 127)),                             # from now on it sets Glide
                  (1.4, lambda world: state.update(self.instance.state()["synth"]))]
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH])
        self.assign(Rate=1)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(sent(world), [(0, 9, 100)])
        self.assertEqual((state["waiting"], state["learning"]), ("Glide", None))
        self.assertEqual(json.loads(bridge.CONTROLS.read_text(encoding="utf-8")), {"controls": {"Rate": 1, "Glide": 20}})
        self.assertIn("KeySynth: controller 20 now sets Glide", self.output)

    def test_learning_one_knob_leaves_the_others_alone(self):
        """Seen with the real devices: after every learned controller the remark about a blocked
        expression assignment was printed again, and unchanged values went out a second time."""
        script = [(0.9, control(1, 127)),
                  (1.0, call(lambda: self.instance.synth_control("learn", "Glide"))), (1.1, control(20, 5)),
                  (1.2, call(lambda: self.instance.synth_control("learn", "Mix"))), (1.3, control(21, 5)),
                  (1.4, control(1, 127))]                               # the mod wheel has not moved
        world = World(script, ports=EVERYTHING, chain=[KEYSYNTH])
        self.assign(Rate=1)
        self.install({PRESET: [mapping(param=4)]}, {PRESET: learned((0, 4, 0, 100))}, synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(sent(world), [(0, 13, 100)])                   # once, not again after each learning
        self.assertEqual(self.output.count("only goes up to 3 – learn it again"), 1)

    def test_a_controller_sets_one_knob_only(self):
        script = [(1.0, call(lambda: self.instance.synth_control("learn", "Glide"))), (1.1, control(1, 30)),
                  (1.2, control(1, 127))]
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH])
        self.assign(Rate=1)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(self.instance.controls, {"Glide": 1})         # the mod wheel moved from Rate to Glide
        self.assertEqual(sent(world), [(0, 9, 100)])

    def test_clear_and_cancel(self):
        script = [(1.0, call(lambda: self.instance.synth_control("clear", "Rate"))), (1.1, control(1, 127)),
                  (1.2, call(lambda: self.instance.synth_control("learn", "Mix"))),
                  (1.25, call(lambda: self.instance.synth_control("cancel"))), (1.3, control(30, 127))]
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH])
        self.assign(Rate=1)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(self.instance.controls, {})
        self.assertEqual(json.loads(bridge.CONTROLS.read_text(encoding="utf-8")), {"controls": {}})
        self.assertEqual(sent(world), [])

    def test_what_cannot_be_learned(self):
        errors = []
        script = [(1.0, call(lambda: self.instance.synth_control("learn", "Key"), into=errors)),
                  (1.05, call(lambda: self.instance.synth_control("learn", "Nope"), into=errors)),
                  (1.1, call(lambda: self.instance.synth_control("learn", "Glide"), into=errors)),
                  (1.15, call(lambda: self.instance.synth_control("shuffle", "Glide"), into=errors))]
        world = World(script, chain=[KEYSYNTH])                         # the keyboard is not connected
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(errors, ["Unknown knob.", "Unknown knob.", "The keyboard is not connected.",
                                  "Unknown action."])
        self.assertFalse(bridge.CONTROLS.exists())

    def test_the_interface_learns_and_clears_a_controller(self):
        seen = {}

        def client(connection):
            connection.at(1.0)
            seen["learn"] = connection.call("/api/synth", {"action": "learn", "knob": "Mix"})[:2]
            seen["waiting"] = connection.call("/api/state")[1]["synth"]["learning"]
            connection.at(1.3)
            knobs = connection.call("/api/state")[1]["synth"]["knobs"]
            seen["mix"] = next(knob for knob in knobs if knob["name"] == "Mix")
            seen["key"] = connection.call("/api/synth", {"action": "learn", "knob": "Key"})[:2]
            seen["odd"] = [connection.call("/api/synth", {"action": "learn", "knob": knob})[:2]
                           for knob in (["Mix"], {"Mix": 1}, 7, None)]
            seen["clear"] = connection.call("/api/synth", {"action": "clear", "knob": "Mix"})[:2]
            connection.at(1.5)
            seen["page"] = connection.call("/")[1]
        world = World([(1.15, control(21, 64)), (1.2, control(21, 127)), (1.45, control(21, 0))],
                      ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=1.8)
        self.install(synth=SYNTH)
        self.run_bridge(world, client)
        self.assertEqual(seen["learn"], (200, {"result": None}))
        self.assertEqual(seen["waiting"], "Mix")
        self.assertEqual(seen["mix"], {"name": "Mix", "knob": "Mix", "rest": 0, "full": 100, "cc": 21})
        self.assertEqual(seen["key"], (400, {"error": "Unknown knob."}))
        self.assertEqual(seen["odd"], [(400, {"error": "Unknown knob."})] * 4)   # and the bridge keeps running
        self.assertEqual(seen["clear"], (200, {"result": None}))
        self.assertEqual(sent(world), [(0, 8, 100)])                   # learned at 64, set at 127, cleared before 0
        self.assertEqual(self.instance.controls, {})
        self.assertIn(b"Keyboard synth", seen["page"])

    def test_a_damaged_controls_file_stops_the_start(self):
        bridge.CONTROLS.write_text("{not json", encoding="utf-8")
        self.install(synth=SYNTH)
        self.assertIn("controls.json is damaged", self.start_fails())


if __name__ == "__main__":
    unittest.main()
