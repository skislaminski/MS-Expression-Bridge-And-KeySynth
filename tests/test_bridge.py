"""The bridge and its interface, run against the simulated devices from sim.py.

In these tests the pedal is on preset 095, which the simulation reports as bank 0, program 94.
"""
import json
import unittest

import mido
import yaml

import bridge
import zoom_sysex as zs
from tests.sim import ID, CONTROLLER_PORT, ZOOM_PORT, SimulatedCase, World, cc, learned, mapping

PRESET = "0/94"
HOLD = {PRESET: [mapping()]}                              # effect 1, parameter 1, 0–100
HOLD_LEARNED = {PRESET: learned((0, 2, 0, 100))}

IDENTITY, ENABLE, DISABLE, QUERY = ("F0 7E 7F 06 01 F7", "F0 52 00 6E 50 F7",
                                    "F0 52 00 6E 51 F7", "F0 52 00 6E 33 F7")


def values(world: World) -> list:
    return [value for *_, value in world.params]


def gaps(world: World) -> list:
    times = [seconds for seconds, *_ in world.params]
    return [later - earlier for earlier, later in zip(times, times[1:])]


class MappingMath(unittest.TestCase):
    def test_range_direction_and_curve(self):
        plain = bridge.Mapping(0, 2, 20, 80)
        self.assertEqual([plain.target(position) for position in (0, 0.5, 1)], [20, 50, 80])
        inverted = bridge.Mapping(0, 2, 20, 80, invert=True)
        self.assertEqual([inverted.target(position) for position in (0, 0.5, 1)], [80, 50, 20])
        self.assertGreater(bridge.Mapping(0, 2, 0, 100, curve="log").target(0.5), 50)
        self.assertLess(bridge.Mapping(0, 2, 0, 100, curve="exp").target(0.5), 50)

    def test_preset_numbers(self):
        self.assertEqual(bridge.display((9, 4)), "095")
        self.assertEqual(bridge.parse_key("9/4"), (9, 4))


class Sending(SimulatedCase):
    def test_a_fast_sweep_is_throttled_and_ends_on_the_last_value(self):
        sweep = list(range(128)) + list(range(127, -1, -1))             # one CC every 2 ms
        world = World([(0.8 + 0.002 * i, cc(value)) for i, value in enumerate(sweep)])
        self.install(HOLD, HOLD_LEARNED)
        self.run_bridge(world)

        sent = values(world)
        peak = sent.index(max(sent))
        self.assertLess(len(sent), len(sweep) / 2)                      # values were merged
        self.assertGreaterEqual(max(sent), 95)                          # the peak lasts 2 ms and may merge
        self.assertEqual(sent[-1], 0)
        self.assertEqual(sent[:peak + 1], sorted(sent[:peak + 1]))
        self.assertEqual(sent[peak:], sorted(sent[peak:], reverse=True))
        self.assertGreaterEqual(min(gaps(world)), 0.009)                # 10 ms apart, minus jitter
        self.assertEqual([text for _, text in world.sent[:3]], [IDENTITY, ENABLE, QUERY])
        self.assertEqual(world.sent[-1][1], DISABLE)

    def test_four_parameters_take_turns(self):
        four = {PRESET: [mapping(slot, 2 + slot, invert=bool(slot % 2)) for slot in range(4)]}
        measured = {PRESET: learned(*[(slot, 2 + slot, 0, 100) for slot in range(4)])}
        script = [(0.8 + 0.033 * i, cc(value)) for i, value in enumerate(list(range(0, 128, 4)) + [127])]
        world = World(script)                                           # 30 CCs per second
        self.install(four, measured)
        self.run_bridge(world)

        per_parameter = {}
        for _, slot, param, value in world.params:
            per_parameter.setdefault((slot, param), []).append(value)
        self.assertEqual({key: sent[-1] for key, sent in per_parameter.items()},
                         {(0, 2): 100, (1, 3): 0, (2, 4): 100, (3, 5): 0})
        # 4 parameters × 30 CCs/s would be 120 messages/s, 100 are allowed: some values merge
        self.assertGreaterEqual(min(len(sent) for sent in per_parameter.values()), 0.6 * len(script))
        self.assertGreaterEqual(min(gaps(world)), 0.009)


class Presets(SimulatedCase):
    def test_a_preset_without_assignment_is_left_alone(self):
        script = [(0.8, cc(64)), (1.0, lambda world: world.preset_change(5)),
                  (1.1, cc(10)), (1.15, cc(20)), (1.2, cc(30)),
                  (1.4, lambda world: world.preset_change(94)), (1.5, cc(127))]
        world = World(script)
        self.install(HOLD, HOLD_LEARNED)
        self.run_bridge(world)
        self.assertEqual(values(world), [50, 100])

    def test_a_passed_through_program_change_waits_for_the_pedal(self):
        change = mido.Message("program_change", program=3)
        world = World([(0.8, change), (0.9, cc(127)), (1.4, cc(10))])
        self.install(HOLD, HOLD_LEARNED, passthrough=True)
        self.run_bridge(world)
        self.assertEqual(world.channel_messages, [change])
        self.assertEqual(values(world), [8])     # 127 arrives before the pedal has reported its preset

    def test_program_changes_are_not_passed_through_by_default(self):
        world = World([(0.8, mido.Message("program_change", program=3))])
        self.install(HOLD, HOLD_LEARNED)
        self.run_bridge(world)
        self.assertEqual(world.channel_messages, [])


class Robustness(SimulatedCase):
    def test_missing_acks_slow_a_learned_parameter_down(self):
        sweep = list(range(0, 128, 2)) + [127]
        world = World([(0.8 + 0.02 * i, cc(value)) for i, value in enumerate(sweep)], acks=False, length=3.5)
        self.install(HOLD, HOLD_LEARNED)
        self.run_bridge(world)
        self.assertGreater(max(gaps(world)), 0.05)
        self.assertIn("WARNING: acks are missing", self.output)
        self.assertEqual(values(world)[-1], 100)          # slowed down, never stopped

    def test_the_controller_can_be_unplugged_and_replugged(self):
        ports = lambda seconds: [ZOOM_PORT] + ([] if 1.0 < seconds < 1.6 else [CONTROLLER_PORT])
        world = World([(0.8, cc(64)), (2.6, cc(127))], ports=ports)
        self.install(HOLD, HOLD_LEARNED)
        self.run_bridge(world)
        self.assertEqual(values(world), [50, 100])
        self.assertEqual((world.count(ENABLE), world.count(DISABLE)), (2, 2))
        self.assertIn("Connection lost", self.output)

    def test_a_quick_replug_of_the_pedal_is_healed(self):
        """Seen on the real pedal: after its USB cable was out for two seconds it confirmed nothing."""
        sweep = [(0.8 + 0.02 * i, cc(value)) for i, value in enumerate(list(range(0, 128, 2)) + [127])]
        world = World(sweep + [(1.2, lambda world: world.replug())], length=3.0)
        self.install(HOLD, HOLD_LEARNED)
        self.run_bridge(world)
        self.assertEqual(world.count(ENABLE), 2)                       # at the start, and once to heal
        self.assertEqual(values(world)[-1], 100)
        self.assertNotIn("Connection lost", self.output)

    def test_parameters_that_were_not_learned(self):
        accepted, partly, never = mapping(0, 3), mapping(1, 2), mapping(5, 13)
        state = {}
        script = [(0.8 + 0.033 * i, cc(value)) for i, value in enumerate(list(range(0, 128, 2)) + [127])]
        script.append((script[-1][0] + 0.4, lambda world: state.update(self.instance.state())))
        world = World(script)
        world.accept = lambda slot, param, value: (slot, param) == (0, 3) or (
            (slot, param) == (1, 2) and value <= 50)
        self.install({PRESET: [accepted, partly, never]})
        self.run_bridge(world)

        sent = {}
        for _, slot, param, value in world.params:
            sent.setdefault((slot, param), []).append(value)
        self.assertEqual(sent[(0, 3)][-1], 100)                              # runs normally
        self.assertGreater(len(sent[(0, 3)]), 40)
        self.assertLessEqual(len(sent[(5, 13)]), bridge.UNCONFIRMED_LIMIT)   # given up after a few tries
        self.assertEqual([target["stopped"] for target in state["targets"]], [False, False, True])
        self.assertEqual([target["unconfirmed"] for target in state["targets"]][:2], [False, True])
        self.assertIn("the pedal does not accept effect 6, parameter 12", self.output)


class Configuration(SimulatedCase):
    def test_assignments_that_are_not_acceptable_stop_the_start(self):
        refused = {
            "outside the learned range": [mapping(high=120)],
            "is outside effects 1–6 / parameters 1–12": [mapping(slot=6)],
            "parameters 1–12 and has not been learned": [mapping(param=14)],
            "is not a valid range": [mapping(slot=1, high=16384)],
            "assigned twice": [mapping(), mapping()],
            "1 to 4 parameters": [mapping(slot, 3) for slot in range(5)],
            "Unknown curve": [mapping(curve="s-shape")],
        }
        for expected, targets in refused.items():
            with self.subTest(expected):
                self.install({PRESET: targets}, HOLD_LEARNED)
                self.assertIn(expected, self.start_fails())

    def test_the_last_selectable_parameter_is_accepted(self):
        self.install({PRESET: [mapping(slot=5, param=13)]})
        self.run_bridge(World())
        self.assertEqual(self.instance.state()["limits"],
                         {"targets": 4, "effects": 6, "params": 12, "value": 16383, "per_bank": 10})

    def test_a_missing_approval_stops_the_start(self):
        self.install(HOLD, HOLD_LEARNED)
        self.approve("identity_request", "edit_enable", "edit_disable", "query_program")
        self.assertIn("Approval missing: set_param", self.start_fails())
        self.approve(*bridge.REQUIRED_KINDS, backup=False)
        self.assertIn("Approval missing: backup", self.start_fails())

    def test_a_missing_config_stops_the_start(self):
        self.assertIn("config.yaml is missing", self.start_fails())


A = mapping(2, 5, 20, 100, invert=True, name="Depth #1")
B = mapping(0, 2, 0, 60, name="Hold")


class Interface(SimulatedCase):
    def test_learning_saving_and_exporting(self):
        results = {}

        def client(http):
            http.at(0.9)
            results["page"] = http.call("/")
            results["start state"] = http.call("/api/state")[1]
            results["foreign"] = http.call("/api/learn", {"action": "start"}, {"Origin": "http://evil.example"})
            http.call("/api/learn", {"action": "start"})
            http.at(1.5)
            results["learning"] = http.call("/api/state")[1]["learning"]
            results["first"] = http.call("/api/learn", {"action": "stop"})[1]
            http.at(1.9)
            http.call("/api/learn", {"action": "start"})
            http.at(2.3)
            results["second"] = http.call("/api/learn", {"action": "stop"})[1]
            results["saved"] = http.call("/api/mapping", {"key": PRESET, "targets": [A, B]})
            http.at(2.9)
            results["refused"] = {
                "outside the learned range": http.call("/api/mapping", {"key": PRESET, "targets": [dict(A, max=151)]}),
                "is outside effects 1–6": http.call("/api/mapping", {"key": PRESET, "targets": [dict(A, slot=6)]}),
                "is not a valid range": http.call("/api/mapping", {"key": "0/5", "targets": [dict(A, max=16384)]}),
                "assigned twice": http.call("/api/mapping", {"key": PRESET, "targets": [A, A]}),
                "1 to 4 parameters": http.call("/api/mapping", {"key": PRESET, "targets": [A, B, A, B, A]}),
                "incomplete": http.call("/api/mapping", {"key": PRESET, "targets": [{"slot": 1}]}),
                "No knob movement": (http.call("/api/learn", {"action": "start"}),
                                     http.call("/api/learn", {"action": "stop"}))[1],
                "no assignment for this preset": http.call("/api/export?key=0/5"),
            }
            results["config"] = bridge.CONFIG.read_text(encoding="utf-8")
            results["state"] = http.call("/api/state")[1]
            results["export all"] = http.call("/api/export")
            results["export one"] = http.call(f"/api/export?key={PRESET}")
            http.at(3.3)
            results["deleted"] = http.call("/api/delete", {"key": PRESET})

        turn = lambda start, slot, param, top: [
            (start + 0.01 * i, lambda world, value=value: world.knob(slot, param, value))
            for i, value in enumerate(range(0, top + 1, 10))]
        script = (turn(1.1, 2, 5, 150) + turn(2.0, 0, 2, 100)
                  + [(1.15, lambda world: world.knob(0x64, 2, 120)),      # tempo: not a knob
                     (1.16, lambda world: world.knob(1, 0, 1)),            # effect on/off: not a knob
                     (1.2, cc(64)),                                        # while learning: nothing is sent
                     (2.6, cc(127)), (2.8, cc(0)),
                     (3.5, cc(90))])                                       # after deleting: nothing is sent
        world = World(script, length=3.9)
        self.install()
        self.run_bridge(world, client)

        self.assertEqual(results["page"][0], 200)
        self.assertIn(b"Expression Bridge", results["page"][1])
        self.assertTrue(results["start state"]["connected"])
        self.assertEqual(results["start state"]["patch"], {"key": PRESET, "display": "095"})
        self.assertEqual(results["foreign"][0], 403)
        self.assertEqual(results["learning"], [{"slot": 2, "param": 5, "min": 0, "max": 150, "count": 16}])
        self.assertEqual(results["first"]["result"],
                         {"slot": 2, "param": 5, "min": 0, "max": 150, "added": True, "known": False})
        self.assertEqual(results["second"]["result"],
                         {"slot": 0, "param": 2, "min": 0, "max": 100, "added": True, "known": False})
        self.assertEqual(results["saved"][0], 200)
        for expected, (status, body, _) in results["refused"].items():
            with self.subTest(expected):
                self.assertEqual(status, 400)
                self.assertIn(expected, body["error"])

        self.assertEqual(results["state"]["mappings"][0]["targets"], [A, B])
        self.assertEqual(len(results["state"]["learned"][PRESET]), 2)
        self.assertEqual(yaml.safe_load(results["config"])["mappings"][PRESET], [A, B])
        # pedal fully down: A is inverted → 20, B → 60; pedal back: A → 100, B → 0
        sent = [(slot, param, value) for _, slot, param, value in world.params]
        self.assertEqual(sorted(sent[:2]), [(0, 2, 60), (2, 5, 20)])
        self.assertEqual(sorted(sent[2:]), [(0, 2, 0), (2, 5, 100)])

        exported = results["export all"][1]
        self.assertEqual(exported["format"], "expression-bridge/1")
        self.assertEqual(exported["presets"][PRESET]["targets"], [A, B])
        self.assertIn("expression-bridge-all-presets.json", results["export all"][2]["Content-Disposition"])
        self.assertIn("expression-bridge-preset-095.json", results["export one"][2]["Content-Disposition"])
        self.assertEqual(results["export one"][1], exported)
        self.assertEqual(results["deleted"][0], 200)
        self.assertTrue(bridge.CONFIG.read_text(encoding="utf-8").endswith("\nmappings: {}\n"))

    def test_importing_into_an_empty_installation(self):
        ranges = [{"slot": 2, "param": 5, "min": 0, "max": 150}, {"slot": 0, "param": 2, "min": 0, "max": 100}]
        export = lambda targets, learned_ranges=ranges: {
            "format": "expression-bridge/1", "presets": {PRESET: {"targets": targets, "learned": learned_ranges}}}
        results = {}

        def client(http):
            http.at(0.9)
            results["refused"] = {
                "not an Expression Bridge export file": http.call("/api/import", {"foo": 1}),
                "incomplete or damaged": http.call("/api/import", {
                    "format": "expression-bridge/1", "presets": {PRESET: {"targets": [A]}}}),
                "outside the learned range": http.call("/api/import", export([dict(A, max=200)])),
                "invalid range in the file": http.call("/api/import", export(
                    [], [{"slot": 0, "param": 1, "min": 0, "max": 5}])),
            }
            results["untouched"] = (bridge.MEASUREMENTS.read_text(encoding="utf-8"),
                                    http.call("/api/state")[1]["mappings"])
            results["imported"] = http.call("/api/import", export([A, B]))
            results["state"] = http.call("/api/state")[1]

        world = World([(1.4, cc(127))], length=2.0)
        self.install()
        self.run_bridge(world, client)

        for expected, (status, body, _) in results["refused"].items():
            with self.subTest(expected):
                self.assertEqual(status, 400)
                self.assertIn(expected, body["error"])
        self.assertEqual(json.loads(results["untouched"][0]), {"patches": {}})   # refused imports change nothing
        self.assertEqual(results["untouched"][1], [])
        self.assertEqual(results["imported"][:2], (200, {"result": ["095"]}))
        self.assertEqual(results["state"]["mappings"][0]["targets"], [A, B])
        self.assertEqual(sorted((slot, param, value) for _, slot, param, value in world.params),
                         [(0, 2, 60), (2, 5, 20)])
        with self.devices(World()):                       # config and measurements load again
            reloaded = bridge.Bridge()
        self.assertEqual([bridge.asdict(target) for target in reloaded.mappings[(0, 94)]], [A, B])


class EverythingSentIsOnTheAllowlist(SimulatedCase):
    def test_messages_of_a_run_pass_the_allowlist(self):
        world = World([(0.8 + 0.01 * i, cc(value)) for i, value in enumerate(range(0, 128, 8))])
        self.install(HOLD, HOLD_LEARNED)
        self.run_bridge(world)
        for _, text in world.sent:
            zs.classify(bytes.fromhex(text.replace(" ", "")), ID, [zs.ParamTarget(0, 2, 0, 100)])


if __name__ == "__main__":
    unittest.main()
