"""The setup script probe.py, run against the simulated pedal from sim.py."""
import io
import json
import types
import unittest
from unittest import mock

import probe
import zoom_sysex as zs
from tests.sim import SimulatedCase, World

IDENTITY, ENABLE, DISABLE, QUERY = ("F0 7E 7F 06 01 F7", "F0 52 00 6E 50 F7",
                                    "F0 52 00 6E 51 F7", "F0 52 00 6E 33 F7")
ALL_KINDS = ("identity_request", "edit_enable", "edit_disable", "set_param", "query_program")
FOREVER = 3600   # these runs end by themselves, not by the simulated Ctrl+C


class Probe(SimulatedCase):
    def setUp(self):
        super().setUp()
        self.install()
        patcher = mock.patch.object(probe.sys, "stdin", io.StringIO())   # not a terminal: no questions asked
        patcher.start()
        self.addCleanup(patcher.stop)

    def command(self, world, function, **arguments):
        with self.devices(world):
            function(types.SimpleNamespace(**arguments))
        return [text for _, text in world.sent]

    def test_nothing_is_sent_without_approval(self):
        self.approve()                                          # backup confirmed, no message kind approved
        world = World(length=FOREVER)
        with self.assertRaises(SystemExit) as refused:
            self.command(world, probe.cmd_identity)
        self.assertIn("python probe.py approve identity_request", str(refused.exception))
        self.assertEqual(world.sent, [])

    def test_identity_reports_the_device(self):
        sent = self.command(World(length=FOREVER), probe.cmd_identity)
        self.assertEqual(sent, [IDENTITY])
        self.assertIn("Device ID 6E", self.output)
        self.assertIn("firmware 1.20", self.output)

    def test_learn_records_the_range_for_the_current_preset(self):
        turn = [(0.1 + 0.01 * i, lambda world, value=value: world.knob(2, 3, value))
                for i, value in enumerate((0, 40, 100, 127, 128, 150))]
        sent = self.command(World(turn, length=FOREVER), probe.cmd_learn, seconds=0.5)
        self.assertEqual(sent, [IDENTITY, QUERY, ENABLE, DISABLE])
        stored = json.loads(probe.MEASUREMENTS.read_text(encoding="utf-8"))["patches"]["0/94"]["2/3"]
        self.assertEqual((stored["min"], stored["max"]), (0, 150))
        self.assertIn("LSB + 128·MSB fits the sequence", self.output)

    def test_verify_sends_only_values_the_pedal_reported(self):
        probe.MEASUREMENTS.write_text(json.dumps({"patches": {"0/94": {
            "2/3": {"slot": 2, "param": 3, "min": 0, "max": 100, "values": [0, 50, 100]}}}}), encoding="utf-8")
        world = World(length=FOREVER)
        with self.assertRaises(SystemExit) as refused:
            self.command(world, probe.cmd_verify, slot=None, param=None, value=70, pause=0, listen=0)
        self.assertIn("Value 70 was not measured", str(refused.exception))
        self.assertEqual(world.params, [])

        world = World(length=FOREVER)
        sent = self.command(world, probe.cmd_verify, slot=None, param=None, value=None, pause=0, listen=0)
        self.assertEqual([(slot, param, value) for _, slot, param, value in world.params], [(2, 3, 50)])
        self.assertEqual(sent[-1], DISABLE)
        self.assertIn("The pedal confirmed the value", self.output)

    def test_approve_records_kinds(self):
        self.approve()
        with self.devices(World(length=FOREVER)):
            probe.cmd_approve(types.SimpleNamespace(items=list(ALL_KINDS)))
        approvals = zs.Approvals(probe.APPROVALS)
        self.assertTrue(all(approvals.is_approved(kind) for kind in ALL_KINDS))


if __name__ == "__main__":
    unittest.main()
