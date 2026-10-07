"""A simulated MS-60B+ and MIDI controller, and a test case that runs the project against them.

The simulated pedal behaves the way the real one was measured (see README.md): it answers the
identity request, acknowledges edit enable/disable, reports its preset, and acknowledges a
"set parameter" message only when the value changes. It answers the patch query with a dump of
its effect chain, built the way real dumps of the MS-60B+ look (tests/test_synth.py has a real one).
"""
from __future__ import annotations

import binascii
import contextlib
import io
import json
import shutil
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import mido
import yaml

import bridge
import probe
import ui
import zoom_sysex as zs

PROJECT = Path(__file__).resolve().parent.parent
ID = 0x6E
ZOOM_PORT, CONTROLLER_PORT = "ZOOM MS Plus Series", "SINCO"
KEYBOARD_PORT = "KOMPLETE KONTROL A61"
IDENTITY_REPLY = bytes.fromhex("F07E000602526E002700312E3230F7")
ACK = bytes([0xF0, 0x52, 0x00, ID, 0x00, 0x00, 0xF7])


def cc(value: int, control: int = 25, channel: int = 0) -> mido.Message:
    """An expression-pedal message from the controller."""
    return mido.Message("control_change", channel=channel, control=control, value=value)


def sysex(raw: bytes) -> mido.Message:
    return mido.Message("sysex", data=raw[1:-1])


def keyboard(message: mido.Message):
    """A script action: the MIDI keyboard sends the message."""
    return lambda world: world.keyboard_queue.append(message)


def note_on(note: int, velocity: int = 100, channel: int = 0):
    return keyboard(mido.Message("note_on", channel=channel, note=note, velocity=velocity))


def note_off(note: int, channel: int = 0):
    return keyboard(mido.Message("note_off", channel=channel, note=note))


def pack_7bit(data: bytes) -> bytes:
    """Seven data bytes become eight on the wire: first a byte with their top bits."""
    packed = bytearray()
    for start in range(0, len(data), 7):
        group = data[start:start + 7]
        packed.append(sum((byte >> 7) << (6 - index) for index, byte in enumerate(group)))
        packed += bytes(byte & 0x7F for byte in group)
    return bytes(packed)


def patch_dump(effects, name: str = "USER-001") -> bytes:
    """The pedal's reply to the patch query, for a chain of (effect id, value of the first knob)."""
    patch = (b"PTCF" + (168).to_bytes(4, "little") + (2).to_bytes(4, "little")
             + len(effects).to_bytes(4, "little") + (0x80000).to_bytes(4, "little") + bytes(6)
             + name.ljust(10).encode("ascii")
             + b"".join(effect_id.to_bytes(4, "little") for effect_id, _ in effects)
             + b"TXJ1" + bytes(4) + b"TXE1" + bytes(4)
             + b"EDTB" + (24 * len(effects)).to_bytes(4, "little")
             + b"".join((1 | effect_id << 1 | first << 30).to_bytes(24, "little") for effect_id, first in effects)
             + b"PRM2" + (32).to_bytes(4, "little") + bytes(32))
    crc = binascii.crc32(patch) ^ 0xFFFFFFFF
    return (bytes([0xF0, 0x52, 0x00, ID, 0x64, 0x12, 0x01, len(patch) & 0x7F, len(patch) >> 7])
            + pack_7bit(patch) + bytes((crc >> 7 * index) & 0x7F for index in range(5)) + b"\xF7")


def mapping(slot: int = 0, param: int = 2, low: int = 0, high: int = 100, **extra) -> dict:
    """An assignment as it is stored in config.yaml."""
    return dict({"slot": slot, "param": param, "min": low, "max": high,
                 "invert": False, "curve": "linear", "name": ""}, **extra)


def learned(*ranges: tuple) -> dict:
    """Learned ranges of one preset, from (slot, param, min, max) tuples."""
    return {f"{slot}/{param}": {"slot": slot, "param": param, "min": low, "max": high,
                                "values": [low, high]} for slot, param, low, high in ranges}


class World:
    """The simulated devices, driven by a timeline of (seconds, action) pairs. An action is a
    message from the controller or a callable that takes the world."""

    def __init__(self, script=(), length=None, acks=True, program=94, ports=None, chain=()):
        self.script = sorted(script, key=lambda item: item[0])
        self.length = length if length is not None else (self.script[-1][0] if self.script else 0) + 0.6
        self.acks = acks                      # False: the pedal never acknowledges a parameter
        self.program = program                # preset 095 is bank 0, program 94 in this simulation
        self.accept = lambda slot, param, value: True
        self.ports = ports or (lambda seconds: [ZOOM_PORT, CONTROLLER_PORT])
        self.chain = list(chain)              # effect ids in slot order, for the patch query
        self.edit_mode = False                # parameters are confirmed only in edit mode
        self.zoom_queue, self.controller_queue, self.keyboard_queue = [], [], []
        self.sent = []                        # (seconds, hex) of every SysEx sent to the pedal
        self.channel_messages = []            # everything else sent to the pedal
        self.values = {}                      # (slot, param) → value the pedal holds
        self.started = time.monotonic()
        self.stopped = False

    def now(self) -> float:
        return time.monotonic() - self.started

    def advance(self) -> None:
        """Plays every action that is due; ends the run once, like a single Ctrl+C."""
        while self.script and self.script[0][0] <= self.now():
            _, action = self.script.pop(0)
            if isinstance(action, mido.Message):
                self.controller_queue.append(action)
            else:
                action(self)
        if self.now() > self.length and not self.stopped:
            self.stopped = True
            raise KeyboardInterrupt

    def report_preset(self) -> None:
        self.zoom_queue += [mido.Message("control_change", control=0, value=0),
                            mido.Message("control_change", control=32, value=0),
                            mido.Message("program_change", program=self.program)]

    def preset_change(self, program: int) -> None:
        """The player selects another preset on the pedal."""
        self.program = program
        self.zoom_queue.append(sysex(bytes([0xF0, 0x52, 0, ID, 0x64, 0x26, 0, 0, 0, 0, program, 0, 0xF7])))
        self.zoom_queue.append(sysex(bytes.fromhex("F052006E64200064027800000000F7")))   # tempo
        self.report_preset()

    def replug(self) -> None:
        """The pedal's USB cable is pulled and plugged back in so quickly that the port list
        never shows it missing. As measured on the real pedal: afterwards it answers queries
        but has left edit mode and confirms no parameter."""
        self.edit_mode = False

    def knob(self, slot: int, param: int, value: int) -> None:
        """The player turns a knob on the pedal."""
        self.zoom_queue.append(sysex(zs.build_set_param(ID, slot, param, value)))

    def receive(self, message: mido.Message) -> None:
        if message.type != "sysex":
            self.channel_messages.append(message)
            return
        raw = bytes(message.bytes())
        self.sent.append((self.now(), zs.to_hex(raw)))
        if raw == zs.IDENTITY_REQUEST:
            self.zoom_queue.append(sysex(IDENTITY_REPLY))
        elif raw[4] in (0x50, 0x51):
            self.edit_mode = raw[4] == 0x50
            self.zoom_queue.append(sysex(ACK))
        elif raw[4] == 0x33:
            self.report_preset()
        elif raw[4:6] == b"\x64\x13":
            self.zoom_queue.append(sysex(patch_dump(
                [(effect_id, self.values.get((slot, 2), 0)) for slot, effect_id in enumerate(self.chain)])))
        elif raw[4:7] == b"\x64\x20\x00":
            key, value = (raw[7], raw[8]), raw[9] | raw[10] << 7
            if not self.accept(*key, value):
                return
            if self.acks and self.edit_mode and value != self.values.get(key):
                self.zoom_queue.append(sysex(raw[:6] + b"\x01" + raw[7:]))
            self.values[key] = value

    @property
    def params(self) -> list:
        """(seconds, slot, param, value) of every "set parameter" message sent to the pedal."""
        found = []
        for seconds, text in self.sent:
            raw = bytes.fromhex(text.replace(" ", ""))
            if raw[4:7] == b"\x64\x20\x00":
                found.append((seconds, raw[7], raw[8], raw[9] | raw[10] << 7))
        return found

    def count(self, text: str) -> int:
        return sum(1 for _, sent in self.sent if sent == text)


class Port:
    """Stands in for a mido port."""

    def __init__(self, world: World, queue):
        self.world, self.queue = world, queue

    def iter_pending(self):
        self.world.advance()
        while self.queue:
            yield self.queue.pop(0)

    def send(self, message: mido.Message) -> None:
        self.world.receive(message)

    def close(self) -> None:
        pass


class Client:
    """Calls the bridge's HTTP interface at given moments of the simulation."""

    def __init__(self, world: World, port: int):
        self.world, self.port = world, port

    def at(self, seconds: float) -> None:
        time.sleep(max(0.0, self.world.started + seconds - time.monotonic()))

    def call(self, path: str, body=None, headers=None) -> tuple:
        """Returns (status, body, headers); JSON bodies of /api paths are decoded."""
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {},
                                         data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                raw = response.read()
                return response.status, json.loads(raw) if path.startswith("/api") else raw, dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read()), {}


class SimulatedCase(unittest.TestCase):
    """A temporary installation: its own config, approvals, measurements and logs."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        paths = {"ROOT": self.folder, "CONFIG": self.folder / "config.yaml",
                 "APPROVALS": self.folder / "approvals.json",
                 "MEASUREMENTS": self.folder / "measurements.json"}
        for module, values in ((bridge, dict(paths, RETRY_SECONDS=0.2, PORT_CHECK_SECONDS=0.05,
                                             CONTROLS=self.folder / "controls.json")),
                               (probe, paths)):
            for name, value in values.items():
                patcher = mock.patch.object(module, name, value)
                patcher.start()
                self.addCleanup(patcher.stop)
        self.approve(*bridge.REQUIRED_KINDS)
        zs.setup_logging(self.folder / "logs", console=False)
        self.addCleanup(self._close_log)
        bridge.EVENTS.clear()
        self.instance = None
        self.output = ""

    @staticmethod
    def _close_log() -> None:
        for handler in list(zs.log.handlers):
            handler.close()
            zs.log.removeHandler(handler)

    def approve(self, *kinds: str, backup: bool = True) -> None:
        """Replaces the approvals of the installation."""
        bridge.APPROVALS.unlink(missing_ok=True)
        approvals = zs.Approvals(bridge.APPROVALS)
        if backup:
            approvals.confirm_backup()
        for kind in kinds:
            approvals.approve(kind)

    def install(self, mappings=None, measured=None, synth=None, **bridge_settings) -> None:
        """Writes config.yaml (from the example) and measurements.json."""
        cfg = yaml.safe_load((PROJECT / "config.example.yaml").read_text(encoding="utf-8"))
        cfg["ports"]["chocolate"] = CONTROLLER_PORT
        if synth is not None:
            cfg["synth"] = synth
        cfg["zoom"].update(device_id=ID, firmware="1.20")
        cfg["expression"].update(cc=25, channel=1)
        cfg["bridge"].update(bridge_settings)
        cfg["mappings"] = mappings or {}
        bridge.CONFIG.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        bridge.MEASUREMENTS.write_text(json.dumps({"patches": measured or {}}), encoding="utf-8")

    @contextlib.contextmanager
    def devices(self, world: World):
        """Replaces the MIDI ports with the simulated world and captures what is printed."""
        names = lambda: world.ports(world.now())
        queues = {ZOOM_PORT: world.zoom_queue, KEYBOARD_PORT: world.keyboard_queue}
        opened = lambda name: Port(world, queues.get(name, world.controller_queue))
        with mock.patch.object(mido, "get_input_names", names), \
                mock.patch.object(mido, "get_output_names", names), \
                mock.patch.object(mido, "open_input", opened), \
                mock.patch.object(mido, "open_output", lambda name: Port(world, None)), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            try:
                yield
            finally:
                self.output = printed.getvalue()

    def run_bridge(self, world: World, client=None) -> "bridge.Bridge":
        """Runs the bridge until the world ends. client(Client) runs alongside in a thread."""
        failures = []

        def guarded(connection: Client) -> None:
            try:
                client(connection)
            except Exception as error:   # reported in the main thread below
                failures.append(error)

        with self.devices(world):
            self.instance = bridge.Bridge()
            thread = None
            if client is not None:
                with socket.socket() as probe_socket:
                    probe_socket.bind(("127.0.0.1", 0))
                    port = probe_socket.getsockname()[1]
                ui.serve(self.instance, {"port": port})
                thread = threading.Thread(target=guarded, args=(Client(world, port),), daemon=True)
                thread.start()
            self.instance.run()
            if thread is not None:
                thread.join(5)
        if failures:
            raise failures[0]
        return self.instance

    def start_fails(self) -> str:
        """The message with which the bridge refuses to start."""
        with self.devices(World()), self.assertRaises(SystemExit) as refused:
            bridge.Bridge()
        return str(refused.exception)
