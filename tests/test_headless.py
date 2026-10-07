"""The bridge on a computer without screen (Raspberry Pi): settings from one file, a status light
instead of a display, and a safe mode in which nothing is sent."""
import contextlib
import io
import json
import time
import unittest
from pathlib import Path
from unittest import mock

import yaml

import bridge
import config_schema as cs
import export
import status_led
import zoom_sysex as zs
from tests.sim import (CONTROLLER_PORT, KEYBOARD_PORT, ZOOM_PORT, SimulatedCase, World, cc, keyboard,
                       learned, mapping, note_off, note_on)

import mido

PRESET = "0/94"
KEYSYNTH, STOCK = 0x07000F61, 0x01000020
SYNTH = {"keyboard": KEYBOARD_PORT, "channel": 1, "effect_id": KEYSYNTH}
EVERYTHING = lambda seconds: [ZOOM_PORT, CONTROLLER_PORT, KEYBOARD_PORT]
K = zs.key_for


class RecordingLed:
    """Stands in for the status light and notes every state it was asked to show."""

    def __init__(self):
        self.states, self.problem, self.closed = [], None, False

    def show(self, state: str) -> None:
        if not self.states or self.states[-1] != state:
            self.states.append(state)

    def close(self) -> None:
        self.closed = True


class HeadlessCase(SimulatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.card = self.folder / "card"
        self.card.mkdir()
        self.led = RecordingLed()
        self.journal = ""
        for target, name, value in ((bridge, "HEADLESS", False), (bridge, "_wait_forever", lambda: None),
                                    (bridge, "SYNTH_QUERY_SECONDS", 0.2)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(status_led.StatusLed, "find", classmethod(lambda cls: self.led))
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_card(self) -> dict:
        """Sets an installation up and exports it, as the user does on the computer."""
        self.install({PRESET: [mapping()]}, {PRESET: learned((0, 2, 0, 100))}, synth=SYNTH)
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        bridge.CONTROLS.write_text(json.dumps({"controls": {"Rate": 1}}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            export.main(["--to", str(self.card)])
        return yaml.safe_load((self.card / cs.FILE_NAME).read_text(encoding="utf-8"))

    def forget_the_installation(self) -> None:
        """On the Pi there is only the settings file: none of the computer's own files exist."""
        for path in (bridge.CONFIG, bridge.APPROVALS, bridge.MEASUREMENTS, bridge.CONTROLS):
            path.unlink(missing_ok=True)

    def run_headless(self, world: World) -> None:
        journal = io.StringIO()
        with self.devices(world), contextlib.redirect_stderr(journal):
            bridge.run_headless(self.card)
        self.journal = journal.getvalue()


class FromTheSettingsFile(HeadlessCase):
    def test_it_plays_as_on_the_computer_and_stores_nothing(self):
        self.write_card()
        self.forget_the_installation()
        before = sorted(path.name for path in self.folder.iterdir())
        world = World([(0.9, cc(127)), (1.0, note_on(45)), (1.1, keyboard(mido.Message("control_change", control=1, value=127))),
                       (1.2, note_off(45))], ports=EVERYTHING, chain=[STOCK, KEYSYNTH])
        self.run_headless(world)

        self.assertEqual([(slot, param, value) for _, slot, param, value in world.params],
                         [(0, 2, 100), (1, 2, K(45)), (1, 13, 100), (1, 2, 0)])
        self.assertEqual(self.led.states, ["waiting", "ready"])
        self.assertTrue(self.led.closed)
        self.assertEqual(world.sent[-1][1], "F0 52 00 6E 51 F7")        # edit mode off at the end
        # everything goes to the console (the service's journal), every message as hex
        self.assertIn("Settings exported 20", self.journal)
        self.assertIn("TX  F0 52 00 6E 64 20 00 01 02", self.journal)
        self.assertIn("RX  F0 52 00 6E 64 20 01", self.journal)
        self.assertIn("Ready:", self.journal)
        self.assertEqual(self.output, "")                               # nothing is printed a second time
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), before)   # no file was made

    def test_the_settings_cannot_be_changed_there(self):
        settings = self.write_card()
        instance = bridge.Bridge(settings)
        for change in (lambda: instance.save_mapping(PRESET, {"targets": [mapping(low=10)]}),
                       lambda: instance.delete_mapping(PRESET),
                       lambda: instance.learn("start"),
                       lambda: instance.synth_control("clear", "Rate")):
            with self.assertRaises(bridge.UserError) as refused:
                change()
            self.assertIn("come from a settings file", str(refused.exception))
        self.assertEqual(instance.controls, {"Rate": 1})

    def test_the_previous_file_is_used_when_the_current_one_is_broken(self):
        self.write_card()
        with contextlib.redirect_stdout(io.StringIO()):
            export.main(["--to", str(self.card)])                        # the first one is now the previous one
        (self.card / cs.FILE_NAME).write_text("mappings: [oops", encoding="utf-8")
        world = World([(1.0, note_on(45)), (1.1, note_off(45))], ports=EVERYTHING, chain=[KEYSYNTH])
        self.run_headless(world)
        self.assertEqual([value for *_, value in world.params], [K(45), 0])
        self.assertEqual(self.led.states, ["waiting", "fallback"])
        self.assertIn(f"Settings: {cs.FILE_NAME} is not a readable settings file.", self.journal)
        self.assertIn("this is the PREVIOUS file", self.journal)


class SafeMode(HeadlessCase):
    def assert_safe(self, world: World, reason: str, sent=()) -> None:
        self.assertEqual([text for _, text in world.sent], list(sent))
        self.assertEqual(self.led.states[-1], "safe")
        self.assertTrue(self.led.closed)
        self.assertIn("SAFE MODE – nothing is sent to the pedal", self.journal)
        self.assertIn(reason, self.journal)

    def test_without_a_settings_file_nothing_is_sent(self):
        world = World(ports=EVERYTHING, chain=[KEYSYNTH])
        self.run_headless(world)
        self.assert_safe(world, "there is no usable settings file")
        self.assertIn(f"Settings: {cs.FILE_NAME} cannot be read", self.journal)

    def test_settings_that_break_a_rule_are_not_used(self):
        cases = {
            "leaves its learned range": lambda data: data["mappings"][PRESET][0].update(max=127),
            "not approved: set_param": lambda data: data["approvals"]["messages"].pop("set_param"),
            "the backup of the patches has not been confirmed": lambda data: data["approvals"].update(backup_confirmed=None),
            "is not a message kind of the bridge": lambda data: data["approvals"]["messages"].update(erase="2026-10-07 10:00:00"),
            "looks like raw bytes": lambda data: data["mappings"][PRESET][0].update(name="F0 52 00 6E 50 F7"),
            "Unknown setting": lambda data: data["bridge"].update(turbo=True),
        }
        for reason, change in cases.items():
            with self.subTest(reason):
                data = self.write_card()
                change(data)
                (self.card / cs.FILE_NAME).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
                self.led.states.clear()
                world = World(ports=EVERYTHING, chain=[KEYSYNTH])
                self.run_headless(world)
                self.assert_safe(world, reason)

    def test_another_pedal_than_the_settings_are_for(self):
        data = self.write_card()
        data["zoom"]["device_id"] = 0x6D                                 # the simulated pedal answers with 6E
        (self.card / cs.FILE_NAME).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        world = World([(1.0, note_on(45))], ports=EVERYTHING, chain=[KEYSYNTH])
        self.run_headless(world)
        self.assert_safe(world, "Device ID 6E does not match", sent=["F0 7E 7F 06 01 F7"])   # the question only


class Light(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self.leds = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.leds, ignore_errors=True))

    def make(self, name: str = "ACT") -> Path:
        folder = self.leds / name
        folder.mkdir()
        (folder / "trigger").write_text("none rc-feedback [mmc0] timer heartbeat\n")
        (folder / "brightness").write_text("0\n")
        return folder

    def test_it_blinks_and_gives_the_led_back(self):
        folder = self.make()
        levels = []

        class Watched(status_led.StatusLed):
            def _write(self, level):
                levels.append(level)
                super()._write(level)

        led = Watched.find(self.leds)
        self.assertEqual((led.folder, (folder / "trigger").read_text()), (folder, "none"))
        led.show("safe")
        time.sleep(0.7)
        led.close()
        self.assertGreaterEqual(levels.count(1), 2)
        self.assertGreaterEqual(levels.count(0), 2)
        self.assertEqual((folder / "trigger").read_text(), "mmc0")      # what it did before
        with self.assertRaises(ValueError):
            led.show("disco")

    def test_older_systems_call_the_led_led0(self):
        folder = self.make("led0")
        led = status_led.StatusLed.find(self.leds)
        self.assertEqual(led.folder, folder)
        led.close()

    def test_without_an_led_nothing_happens(self):
        led = status_led.StatusLed.find(self.leds)
        self.assertIsNone(led.folder)
        led.show("ready")
        led.close()

    def test_an_led_that_may_not_be_written_is_left_alone(self):
        folder = self.make()
        (folder / "trigger").chmod(0o444)
        self.addCleanup((folder / "trigger").chmod, 0o644)
        led = status_led.StatusLed(folder)
        self.assertIsNone(led.folder)
        self.assertIn("ACT", led.problem)
        led.show("ready")
        led.close()
