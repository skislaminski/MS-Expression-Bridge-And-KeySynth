#!/usr/bin/env python3
"""Expression Bridge: CC from the Chocolate Plus → parameter SysEx to the MS-60B+, with a web UI.
Optionally a MIDI keyboard plays the KeySynth effect on the pedal (section `synth:` in config.yaml):
notes and pitch wheel become its Key knob, the mod wheel and any other controller can be assigned
to its other knobs (learned in the interface, kept in controls.json).

  python bridge.py           bridge + interface in the browser (the address is printed on start)
  python bridge.py --open    also opens the interface in the browser
  python bridge.py --no-ui   bridge only
  python bridge.py --settings FOLDER
                             for a computer without screen (Raspberry Pi): runs from the settings
                             file export.py wrote into FOLDER; no interface, nothing is stored

Runs until Ctrl+C; if a device is missing it keeps looking. Only approved message kinds are sent
(`python probe.py approve`), and only values within the ranges the pedal itself reported while
learning.
"""
from __future__ import annotations

import argparse
import json
import math
import queue
import signal
import statistics
import sys
import threading
import time
import webbrowser
from collections import deque
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import NamedTuple, Optional

import mido
import yaml

import config_schema as cs
import status_led
import zoom_sysex as zs

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.yaml"
APPROVALS = ROOT / "approvals.json"
MEASUREMENTS = ROOT / "measurements.json"
CONTROLS = ROOT / "controls.json"

REQUIRED_KINDS = ("identity_request", "edit_enable", "edit_disable", "set_param", "query_program")
IGNORED_TYPES = ("clock", "active_sensing")
RETRY_SECONDS = 2.0        # time between connection attempts
PORT_CHECK_SECONDS = 1.0   # how often to check that both devices are still there
REPORT_SECONDS = 5.0       # time between statistics lines while sending
SNAPSHOT_SECONDS = 0.1     # how often the state for the interface is refreshed
ACK_TIMEOUT = 0.25         # after this long a value counts as unconfirmed
QUERY_DELAY = 0.2          # wait after a passed-through program change before querying the patch
MISSES_BEFORE_SLOWDOWN = 3
ACKS_BEFORE_SPEEDUP = 200
MAX_INTERVAL = 0.1         # never throttle slower than 100 ms
PATCHES_PER_BANK = 10      # measured: display 095 = bank 9, program 4
MAX_TARGETS = 4            # how many parameters the expression pedal controls per preset at most
MAX_EFFECTS = 6            # effects 1–6 and parameters 1–12 can be assigned without learning them
MAX_PARAMS = 12
UNLEARNED_MAX = 0x3FFF     # value limit for a parameter whose range has not been learned
UNCONFIRMED_HINT = 3       # misses in a row before the interface flags a parameter
UNCONFIRMED_LIMIT = 5      # misses before sending stops for a parameter the pedal never confirmed
EXPORT_FORMAT = "expression-bridge/1"
SYNTH_QUERY_SECONDS = 1.0  # while looking for the KeySynth effect: at most one patch query per second
KEY_MISSES_BEFORE_QUERY = 3  # unconfirmed notes before the bridge looks where the effect is now
KEY_ACK_TIMEOUT = 0.06     # a note is confirmed after about 10 ms (slowest seen: 20 ms)
KEY_RETRIES = 3            # how often the last note or the gate-off is repeated while unconfirmed
KEY_TROUBLE_SECONDS = 5.0  # while notes stay unconfirmed: warn and look for the effect this often at most
EDIT_REENABLE_SECONDS = 2.0  # while nothing is confirmed: switch edit mode on again this often at most
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
MAX_BEND_RANGE = 12        # semitones the pitch wheel may be set to bend each way
# Controllers that cannot be assigned to a knob: bank select, sustain, the channel mode messages
RESERVED_CONTROLS = (0, 32, 64) + tuple(range(120, 128))

CURVES = {
    "linear": lambda x: x,
    "log": lambda x: math.log1p(9 * x) / math.log(10),   # changes quickly at the start
    "exp": lambda x: (10 ** x - 1) / 9,                   # changes slowly at the start
}

EVENTS: deque = deque(maxlen=8)   # latest status lines, for the interface
HEADLESS = False                  # run from a settings file: lines go to the log only (the service's journal)


class Disconnected(Exception):
    """A device is missing or not answering; the connection is rebuilt."""


class UserError(Exception):
    """A request from the interface cannot be carried out; the text is shown there."""


def say(text: str) -> None:
    line = f"{time.strftime('%H:%M:%S')}  {text}"
    if not HEADLESS:
        print(line, flush=True)
    EVENTS.append(line)
    zs.log.info("--  %s", text)


def parse_key(key) -> tuple[int, int]:
    """Key "bank/program" from config.yaml → (bank, program)."""
    bank, _, program = str(key).rpartition("/")
    patch = (int(bank or 0), int(program))
    if not (0 <= patch[0] <= 0x3FFF and 0 <= patch[1] <= 0x7F):
        raise ValueError(key)
    return patch


def display(patch: tuple[int, int]) -> str:
    """The number under which the pedal shows the patch."""
    return f"{patch[0] * PATCHES_PER_BANK + patch[1] + 1:03d}"


def note_name(note: int) -> str:
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 1}"


def read_synth(cfg: dict) -> Optional[dict]:
    """The optional `synth:` section of config.yaml, checked. None: no keyboard synth set up."""
    section = cfg.get("synth")
    if not section or not section.get("keyboard"):
        return None
    unknown = set(section) - {"keyboard", "channel", "effect_id", "low_note", "high_note", "sustain",
                              "bend_range"}
    if unknown:
        sys.exit(f"config.yaml, synth: unknown setting {', '.join(sorted(unknown))}")
    try:
        synth = {"keyboard": str(section["keyboard"]), "channel": int(section.get("channel", 0)),
                 "effect_id": int(section["effect_id"]), "low_note": int(section.get("low_note", 0)),
                 "high_note": int(section.get("high_note", 127)), "sustain": bool(section.get("sustain", True)),
                 "bend_range": float(section.get("bend_range", 2))}
    except (KeyError, TypeError, ValueError):
        sys.exit("config.yaml, synth: effect_id is missing or a value is not a number.")
    if not (0 <= synth["channel"] <= 16 and 0 < synth["effect_id"] <= 0xFFFFFFFF
            and 0 <= synth["low_note"] <= synth["high_note"] <= 127
            and 0 <= synth["bend_range"] <= MAX_BEND_RANGE):
        sys.exit("config.yaml, synth: channel must be 0–16 (0 = any), notes 0–127 with "
                 f"low_note ≤ high_note, bend_range 0–{MAX_BEND_RANGE} semitones, effect_id the id "
                 "of the KeySynth effect.")
    # The effect's Key knob reaches C0 to D#8; notes outside that are not played.
    synth["low_note"] = max(synth["low_note"], zs.KEY_LOW_NOTE)
    synth["high_note"] = min(synth["high_note"], zs.KEY_HIGH_NOTE)
    return synth


def read_controls(path: Path) -> dict:
    """Which controller of the keyboard sets what on the KeySynth: {name in zs.SYNTH_CONTROLS: CC number}.
    No file yet: nothing is assigned. Anything in the file that cannot be right is dropped."""
    if not path.exists():
        return {}
    try:
        stored = json.loads(path.read_text(encoding="utf-8")).get("controls", {})
    except (OSError, ValueError, AttributeError):
        sys.exit(f"{path.name} is damaged – delete it to start again without assignments.")
    controls: dict = {}
    for name, number in stored.items() if isinstance(stored, dict) else ():
        if (name in zs.SYNTH_CONTROLS and isinstance(number, int) and not isinstance(number, bool)
                and 0 <= number <= 127 and number not in RESERVED_CONTROLS
                and number not in controls.values()):
            controls[name] = number
    return controls


class Keys:
    """Which note a monophonic synth should sound: the last one pressed wins, and when it is
    released the one pressed before it comes back if it is still down."""

    def __init__(self, sustain_enabled: bool = True):
        self.sustain_enabled = sustain_enabled
        self.held: list = []          # notes that are down, oldest first
        self.sustained: list = []     # notes released while the sustain pedal was down
        self.pedal_down = False

    def note_on(self, note: int) -> None:
        self.note_off(note, sustain=False)
        self.held.append(note)

    def note_off(self, note: int, sustain: bool = True) -> None:
        if note in self.sustained:
            self.sustained.remove(note)
        if note in self.held:
            self.held.remove(note)
            if sustain and self.pedal_down:
                self.sustained.append(note)

    def pedal(self, down: bool) -> None:
        self.pedal_down = down and self.sustain_enabled
        if not self.pedal_down:
            self.sustained.clear()

    def clear(self) -> None:
        self.held.clear()
        self.sustained.clear()

    @property
    def note(self) -> Optional[int]:
        """The MIDI note that should sound, None for silence."""
        notes = self.held or self.sustained
        return notes[-1] if notes else None


@dataclass(frozen=True)
class Mapping:
    """A parameter controlled by the expression pedal. invert: heel = max, toe = min."""

    slot: int
    param: int
    min: int
    max: int
    invert: bool = False
    curve: str = "linear"
    name: str = ""

    def target(self, position: float) -> int:
        """Pedal position 0..1 → parameter value."""
        x = CURVES[self.curve](position)
        if self.invert:
            x = 1.0 - x
        return round(self.min + x * (self.max - self.min))

    def describe(self) -> str:
        low, high = (self.max, self.min) if self.invert else (self.min, self.max)
        return f"effect {self.slot + 1}, parameter {self.param - 1}, {low}–{high}"


def to_mapping(entry: dict) -> Mapping:
    """A mapping from interface input or an import file; raises on incomplete data."""
    return Mapping(int(entry["slot"]), int(entry["param"]), int(entry["min"]), int(entry["max"]),
                   invert=bool(entry.get("invert")), curve=str(entry.get("curve", "linear")),
                   name=str(entry.get("name", "")).strip()[:40])


class Sent(NamedTuple):
    value: int
    cc_time: float    # arrival of the CC that caused it
    send_time: float
    retry: bool


class KeySent(NamedTuple):
    value: int
    time: float       # arrival of the keyboard message that caused it
    send_time: float
    attempt: int      # 0 for the first try
    bend: bool        # a step of the pitch wheel, not a note


class Target:
    """An assigned parameter of the current preset and its send state."""

    def __init__(self, mapping: Mapping, learned: bool):
        self.mapping = mapping
        self.learned = learned                              # its range was reported by the pedal
        self.pending: Optional[tuple[int, float]] = None   # (target value, arrival of the CC)
        self.last_value: Optional[int] = None              # last value sent
        self.awaiting: deque = deque()                      # sent, ack outstanding
        self.misses_in_row = 0
        self.confirmed = False                              # the pedal has acknowledged or reported it
        self.stopped = False                                # not learned and never confirmed: nothing more is sent
        self.blocked = False                                # a KeySynth knob the expression pedal must not write
        self.expression = True                              # False: only a keyboard controller sets it


class Session:
    """One connection to both devices, from opening the ports to closing them."""

    def __init__(self, bridge: "Bridge"):
        cfg = bridge.cfg
        self.synth = bridge.synth                           # None: no keyboard synth set up
        inputs, outputs = mido.get_input_names(), mido.get_output_names()
        self.zoom_in_name = zs.find_port(inputs, cfg["ports"]["zoom"])
        self.zoom_out_name = zs.find_port(outputs, cfg["ports"]["zoom"])
        if self.synth is None:
            self.chocolate_name = zs.find_port(inputs, cfg["ports"]["chocolate"])
            self.keyboard_name = None
        else:   # two controllers: either one is enough to start, the other may join later
            self.chocolate_name = self._present(inputs, cfg["ports"]["chocolate"])
            self.keyboard_name = self._present(inputs, self.synth["keyboard"])
            if self.chocolate_name is None and self.keyboard_name is None:
                raise zs.PortError("no controller connected (neither the expression controller "
                                   f"nor the keyboard “{self.synth['keyboard']}”)")
        self.zoom_in = mido.open_input(self.zoom_in_name)
        self.zoom_out = mido.open_output(self.zoom_out_name)
        self.chocolate = mido.open_input(self.chocolate_name) if self.chocolate_name else None
        self.keyboard = mido.open_input(self.keyboard_name) if self.keyboard_name else None

        self.bridge = bridge
        self.device_id = cfg["zoom"]["device_id"]
        self.expression = cfg["expression"]
        self.deadband = cfg["bridge"].get("deadband", 0)
        self.passthrough = cfg["bridge"].get("passthrough", False)
        self.min_interval = cfg["bridge"]["min_interval_ms"] / 1000
        self.interval = self.min_interval
        self.sender = zs.ZoomSender(self.zoom_out, bridge.approvals)
        self.edit_enabled = self.ready = False

        self.bank_msb = self.bank_lsb = 0
        self.patch: Optional[tuple[int, int]] = None
        self.targets: list = []                             # one Target per assigned parameter of the preset
        self.turn = 0                                       # whose turn it is to send
        self.learning: Optional[dict] = None                # (slot, param) → reported values, while learning
        self.last_cc: Optional[int] = None
        self.last_send = 0.0
        self.misses_in_row = self.acks_in_row = 0
        self.sent = self.acked = self.missed = 0            # since the last statistics line
        self.totals = {"sent": 0, "acked": 0, "missed": 0}
        self.latencies: list = []
        self.recent_latencies: deque = deque(maxlen=50)
        self.next_report = self.next_port_check = 0.0
        self.query_at: Optional[float] = None               # when a patch query is due

        # KeySynth: the keyboard writes the effect's Key knob
        self.keys = Keys(self.synth["sustain"] if self.synth else True)
        self.bend = 0.0                                     # semitones, from the pitch wheel
        self.note_sent: Optional[int] = None                # the note the last Key value was for
        self.controls: dict = {}                            # CC number → (Target, name in zs.SYNTH_CONTROLS)
        self.told: set = set()                              # remarks made about the assignments of this preset
        self.synth_slot: Optional[int] = None               # where the effect sits in the current preset
        self.synth_known = False                            # a patch dump has told us whether it is there
        self.key_sent: Optional[int] = None                 # the Key value the pedal holds, as far as we know
        self.key_awaiting: deque = deque()                  # KeySent, ack outstanding
        self.key_misses = 0
        self.key_totals = {"sent": 0, "acked": 0, "missed": 0, "overtaken": 0}
        self.key_latencies: deque = deque(maxlen=50)
        self.patch_query_at: Optional[float] = None         # when to look for the effect (after a preset change)
        self.last_patch_query = float("-inf")
        self.last_key_trouble = float("-inf")
        self.last_reenable = float("-inf")
        self.can_query_patch = bridge.approvals.is_approved("query_patch")

    @staticmethod
    def _present(names, needle: str) -> Optional[str]:
        """The port for an optional controller, or None while it is not connected."""
        if not needle or not any(needle.lower() in name.lower() for name in names):
            return None
        return zs.find_port(names, needle)   # more than one match is still an error

    # --- Setup and teardown ---

    def start(self) -> None:
        self.sender.send(zs.build_identity_request())
        identity = self._wait(zs.parse_identity_reply, 2.0)
        if identity is None:
            raise Disconnected("no identity reply from the MS-60B+")
        if identity.device_id != self.device_id:
            sys.exit(f"Device ID {identity.device_id:02X} does not match config.yaml – nothing further sent.")
        self.sender.device_id = self.device_id

        self.sender.send(zs.build_edit_enable(self.device_id))
        self.edit_enabled = True
        if not self._wait(lambda raw: zs.is_ack(zs.zoom_body(raw, self.device_id)), 1.0):
            raise Disconnected("no ack for edit enable")

        self.sender.send(zs.build_query_program(self.device_id))
        self._wait(lambda raw: None, 0.5)   # the reply (bank select + program change) is handled by _on_zoom
        if self.patch is None:
            say("Current preset unknown – expression is ignored until the pedal reports a preset.")
        if self.synth is not None:
            if not self.can_query_patch:
                say("KeySynth: the patch query is not approved (`python probe.py approve query_patch`) "
                    "– the keyboard is ignored")
            else:
                self.patch_query_at = None
                self._look_for_synth(time.monotonic(), force=True)
                self._wait(lambda raw: self.synth_known, 1.0)
        self.ready = True
        controllers = " + ".join(name for name in (self.chocolate_name, self.keyboard_name) if name)
        say(f"Ready: {controllers} → {self.zoom_out_name}, firmware {identity.version}")

    def close(self) -> None:
        try:
            if self.edit_enabled:
                if self.synth_slot is not None and self.key_sent:   # never leave a note hanging
                    self.keys.clear()
                    self._send_key(0, time.monotonic())
                    self._wait(lambda raw: not self.key_awaiting, 0.3)
                self.sender.send(zs.build_edit_disable(self.device_id))
                self._wait(lambda raw: zs.is_ack(zs.zoom_body(raw, self.device_id)), 0.5)
        except OSError:
            pass   # the device is already gone
        finally:
            for port in (self.zoom_in, self.zoom_out, self.chocolate, self.keyboard):
                if port is not None:
                    port.close()

    def _wait(self, match, timeout: float):
        """Processes incoming Zoom messages until match(raw) returns something truthy for a SysEx."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for message in self.zoom_in.iter_pending():
                raw = self._on_zoom(message, time.monotonic())
                result = raw and match(raw)
                if result:
                    return result
            time.sleep(0.001)
        return None

    # --- One pass of the main loop ---

    def step(self, now: float) -> None:
        if self.keyboard is not None:           # notes first: they are never delayed or merged
            for message in self.keyboard.iter_pending():
                self._on_keyboard(message, now)
        if self.chocolate is not None:
            for message in self.chocolate.iter_pending():
                self._on_chocolate(message, now)
        for message in self.zoom_in.iter_pending():
            self._on_zoom(message, now)
        self._flush(now)
        self._check_timeouts(now)
        if self.query_at is not None and now >= self.query_at:
            self.query_at = None
            self.sender.send(zs.build_query_program(self.device_id))
        if self.patch_query_at is not None and now >= self.patch_query_at:
            self.patch_query_at = None
            # Known to be absent: only keep looking while a key is down (the player is waiting for it).
            if self.synth_slot is not None or not self.synth_known or self.keys.note is not None:
                self._look_for_synth(now, force=True)
        self._report(now)
        if now >= self.next_port_check:
            self._check_ports()
            self.next_port_check = now + PORT_CHECK_SECONDS

    def _check_ports(self) -> None:
        inputs, outputs = mido.get_input_names(), mido.get_output_names()
        for name, names in ((self.zoom_in_name, inputs), (self.zoom_out_name, outputs)):
            if name not in names:
                raise Disconnected(f"{name} is gone")
        if self.synth is None:
            if self.chocolate_name not in inputs:
                raise Disconnected(f"{self.chocolate_name} is gone")
            return
        # Two controllers: one may leave or join while the other keeps working.
        if self.chocolate_name is not None and self.chocolate_name not in inputs:
            say(f"{self.chocolate_name} is gone")
            self._drop("chocolate")
        if self.keyboard_name is not None and self.keyboard_name not in inputs:
            say(f"{self.keyboard_name} is gone")
            self._drop("keyboard")
            self.keys.clear()
            self.bend = 0.0
            self._play(time.monotonic())        # gate off
        if self.chocolate_name is None and self.keyboard_name is None:
            raise Disconnected("no controller left")
        for attribute, needle in (("chocolate", self.bridge.cfg["ports"]["chocolate"]),
                                  ("keyboard", self.synth["keyboard"])):
            if getattr(self, attribute) is None:
                name = self._present(inputs, needle)
                if name is not None:
                    setattr(self, attribute, mido.open_input(name))
                    setattr(self, attribute + "_name", name)
                    say(f"{name} connected")

    def _drop(self, attribute: str) -> None:
        port = getattr(self, attribute)
        setattr(self, attribute, None)
        setattr(self, attribute + "_name", None)
        try:
            port.close()
        except OSError:
            pass   # the device is already gone

    # --- Chocolate → target values ---

    def _on_chocolate(self, message, now: float) -> None:
        expression = self.expression
        if message.type == "control_change" and (message.control, message.channel + 1) == (
                expression["cc"], expression["channel"]):
            # While learning the user turns knobs on the pedal; nothing is sent against that.
            if self._accept(message.value) and self.learning is None:
                span = expression["max"] - expression["min"]
                position = min(1.0, max(0.0, (message.value - expression["min"]) / span))
                if expression["invert"]:
                    position = 1.0 - position
                for target in self.targets:
                    if target.expression and not (target.stopped or target.blocked):
                        target.pending = (target.mapping.target(position), now)   # an older value is dropped
        elif self.passthrough and (message.type == "program_change" or (
                message.type == "control_change" and message.control in (0, 32))):
            zs.log.info("TX  %s  (passed through)", message)
            self.zoom_out.send(message)
            if message.type == "program_change":
                # No parameter goes out until the pedal reports the new patch.
                self.patch = None
                self.refresh()
                self.query_at = now + QUERY_DELAY

    # --- Keyboard → the KeySynth's knobs ---

    def _on_keyboard(self, message, now: float) -> None:
        synth = self.synth
        if message.type in IGNORED_TYPES or not hasattr(message, "channel"):
            return
        if synth["channel"] and message.channel + 1 != synth["channel"]:
            return
        if message.type in ("note_on", "note_off"):
            if not synth["low_note"] <= message.note <= synth["high_note"]:
                return
            if message.type == "note_on" and message.velocity > 0:
                self.keys.note_on(message.note)
            else:                                   # note on with velocity 0 is a note off
                self.keys.note_off(message.note)
        elif message.type == "pitchwheel":
            # Only remembered here: _bend_step walks the Key knob there, a few clicks at a time.
            self.bend = message.pitch / 8192 * synth["bend_range"]
            return
        elif message.type == "control_change" and message.control == 64:
            self.keys.pedal(message.value >= 64)
        elif message.type == "control_change" and message.control in (120, 123):
            self.keys.clear()                       # all sound off, all notes off
        elif message.type == "control_change":
            self._on_control(message.control, message.value, now)
            return
        else:
            return
        self._play(now)

    def _on_control(self, number: int, value: int, now: float) -> None:
        """A controller of the keyboard other than sustain: while the interface is learning it is
        assigned to a knob, otherwise it sets the knob it is assigned to."""
        if self.bridge.control_learning is not None:
            self.bridge.learned_control(number)
            return
        target, name = self.controls.get(number, (None, None))
        if target is not None and not (target.stopped or target.blocked):
            target.pending = (zs.control_value(name, value), now)   # an older value is dropped

    def _wanted_key(self) -> int:
        """The Key value for the keys that are down and the pitch wheel; 0 = gate off."""
        note = self.keys.note
        return 0 if note is None else zs.key_for(note, self.bend)

    def _play(self, now: float) -> None:
        """Brings the pedal's Key knob in line with the keys that are down. A note goes out at
        once: it is not spaced, delayed or merged, only an unchanged value is skipped."""
        key, note = self._wanted_key(), self.keys.note
        if self.synth_slot is None:
            if key:                                 # the effect may have been added just now
                self._look_for_synth(now)
            return
        if note is not None and note == self.note_sent and self.key_sent:
            return                                  # the same note still sounds: _bend_step follows the wheel
        if key != self.key_sent:
            self._send_key(key, now)
        self.note_sent = note

    def _bend_step(self, now: float) -> bool:
        """While a note sounds, moves the Key knob towards where the pitch wheel wants it, at most
        KEY_BEND_STEP clicks per message. The effect takes a larger move for a new note and, with
        Glide on, would slide to it instead of following the wheel."""
        if self.synth_slot is None or not self.key_sent or self.keys.note != self.note_sent:
            return False
        wanted = self._wanted_key()
        if not wanted or wanted == self.key_sent:
            return False
        step = max(-zs.KEY_BEND_STEP, min(zs.KEY_BEND_STEP, wanted - self.key_sent))
        self._send_key(self.key_sent + step, now, bend=True)
        return True

    def _send_key(self, key: int, now: float, played: Optional[float] = None, attempt: int = 0,
                  bend: bool = False) -> None:
        self.sender.send(zs.build_set_key(self.device_id, self.synth_slot, key))
        self.key_sent = key
        self.last_send = time.monotonic()           # every other value keeps its distance from a note
        self.key_awaiting.append(KeySent(key, now if played is None else played, now, attempt, bend))
        self.key_totals["sent"] += 1

    def _on_key_ack(self, value: int, now: float) -> None:
        if all(sent.value != value for sent in self.key_awaiting):
            return
        while True:                                 # acks arrive in send order
            sent = self.key_awaiting.popleft()
            if sent.value == value:
                break
            # Seen on the real pedal: of two messages 1 ms apart (two keys almost together) only
            # the second is confirmed. The first was replaced before it could matter.
            zs.log.info("--  Key %d was overtaken by the next message", sent.value)
            self.key_totals["overtaken"] += 1
        self.key_misses = 0
        self.key_totals["acked"] += 1
        self.key_latencies.append(now - sent.time)
        if sent.bend or HEADLESS:
            return                                  # a moving wheel would flood the terminal
        if value:
            note, cents = zs.key_pitch(value)
            what = f"{note_name(note):>4} ({note:3d})" + (f" {cents:+d} c" if cents else "")
        else:
            what = "       off"
        print(f"{time.strftime('%H:%M:%S')}  {what} → Key {value:4d}   ack {1000 * (now - sent.time):5.1f} ms",
              flush=True)

    def _key_miss(self, sent: KeySent, now: float) -> None:
        """No ack for a note in time. Seen on the real pedal: while the player is in its effect
        menu a message can get lost."""
        zs.log.info("--  no ack for Key %d", sent.value)
        if (not self.key_awaiting and sent.value == self.key_sent == self._wanted_key()
                and sent.attempt < KEY_RETRIES):
            # The last note, the end of a bend or the gate-off must not get lost. Repeating is
            # harmless: the pedal ignores a value it already holds.
            self._send_key(sent.value, now, played=sent.time, attempt=sent.attempt + 1, bend=sent.bend)
        if sent.attempt:
            return          # counted once, with the first try
        self.key_totals["missed"] += 1
        self.key_misses += 1
        if self.key_misses >= KEY_MISSES_BEFORE_QUERY:
            self.key_misses = 0
            self._reenable_edit(now)
            # Usually the player is turning a knob or browsing effects on the pedal, which keeps
            # it from answering for a moment. Do not pile more work on it: ask where the effect
            # is only now and then.
            if now - self.last_key_trouble >= KEY_TROUBLE_SECONDS:
                self.last_key_trouble = now
                say("WARNING: the pedal does not confirm notes – switching edit mode on again and "
                    "looking for the KeySynth effect")
                self._look_for_synth(now)

    def _reenable_edit(self, now: float) -> None:
        """Parameters that stay unconfirmed can mean the pedal has left edit mode. Seen on the
        real pedal: its USB cable was pulled and plugged back in within two seconds, the port
        check did not notice, and afterwards the pedal answered queries but confirmed no
        parameter. Sending edit enable again is harmless."""
        if now - self.last_reenable < EDIT_REENABLE_SECONDS:
            return
        self.last_reenable = now
        self.sender.send(zs.build_edit_enable(self.device_id))

    def _look_for_synth(self, now: float, force: bool = False) -> None:
        """Asks the pedal for the current patch; _on_patch reads the answer. Unless forced, at
        most once per SYNTH_QUERY_SECONDS: a query that comes too early is put off, not dropped."""
        if not self.can_query_patch:
            return
        if not force and now - self.last_patch_query < SYNTH_QUERY_SECONDS:
            if self.patch_query_at is None:
                self.patch_query_at = self.last_patch_query + SYNTH_QUERY_SECONDS
            return
        self.last_patch_query = now
        self.sender.send(zs.build_query_patch(self.device_id))

    def _on_patch(self, dump: zs.PatchDump, now: float) -> None:
        slot = next((index for index, effect in enumerate(dump.effects)
                     if effect.id == self.synth["effect_id"]), None)
        changed = slot != self.synth_slot or not self.synth_known
        if slot != self.synth_slot:                 # what stood in the old place says nothing about the new one
            self.targets, self.told = [], set()
        self.synth_slot, self.synth_known = slot, True
        if slot is None:
            self.key_sent = self.note_sent = None
            self.key_awaiting.clear()
            if changed:
                say("No KeySynth effect in this preset – the keyboard is ignored")
        else:
            if not self.key_awaiting and dump.effects[slot].first_param != self.key_sent:
                self.key_sent = dump.effects[slot].first_param   # what the pedal's Key knob holds now
                self.note_sent = None
            if changed:
                say(f"KeySynth is effect {slot + 1} – the keyboard plays it")
        if changed:
            self.refresh()
        if self.keys.note is not None:
            self._play(now)

    def _accept(self, value: int) -> bool:
        """Deadband: ignore small changes, but always let the end stops through."""
        at_end = value in (self.expression["min"], self.expression["max"])
        if self.last_cc is None or abs(value - self.last_cc) > self.deadband or (
                at_end and value != self.last_cc):
            self.last_cc = value
            return True
        return False

    # --- Sending and acks ---

    def _flush(self, now: float) -> None:
        """At most one message per minimum interval; the parameters take turns."""
        # The pitch wheel comes before any other knob and keeps the configured pace: notes have
        # their own check for missing acks, and a parameter the pedal does not confirm (which
        # slows the others down) must not make a bend drag.
        if now - self.last_send >= self.min_interval and self._bend_step(now):
            return
        if now - self.last_send < self.interval:
            return
        for _ in self.targets:
            self.turn = (self.turn + 1) % len(self.targets)
            target = self.targets[self.turn]
            if target.pending is None:
                continue
            if not (target.learned or target.confirmed) and target.awaiting:
                continue   # unproven parameter: one message at a time until the pedal confirms it
            (value, cc_time), target.pending = target.pending, None
            if value != target.last_value:
                self._transmit(target, value, cc_time, now)
                return

    def _transmit(self, target: Target, value: int, cc_time: float, now: float,
                  retry: bool = False) -> None:
        mapping = target.mapping
        self.sender.send(zs.build_set_param(self.device_id, mapping.slot, mapping.param, value))
        target.last_value = value
        self.last_send = time.monotonic()   # the real send time, so a slow loop pass cannot shorten the spacing
        target.awaiting.append(Sent(value, cc_time, now, retry))
        self.sent += 1
        self.totals["sent"] += 1

    def _on_ack(self, target: Target, value: int, now: float) -> None:
        if all(sent.value != value for sent in target.awaiting):
            return
        while True:   # acks arrive in send order; anything skipped stayed unconfirmed
            sent = target.awaiting.popleft()
            if sent.value == value:
                break
            self._miss(target, sent, now)
        self.acked += 1
        self.totals["acked"] += 1
        self.latencies.append(now - sent.cc_time)
        self.recent_latencies.append(now - sent.cc_time)
        self.misses_in_row = target.misses_in_row = 0
        target.confirmed = True
        self.acks_in_row += 1
        if self.acks_in_row >= ACKS_BEFORE_SPEEDUP and self.interval > self.min_interval:
            self.interval = max(self.interval / 2, self.min_interval)
            self.acks_in_row = 0
            say(f"Acks are reliable again – minimum interval {self.interval * 1000:g} ms")

    def _check_timeouts(self, now: float) -> None:
        for target in self.targets:
            while target.awaiting and now - target.awaiting[0].send_time > ACK_TIMEOUT:
                self._miss(target, target.awaiting.popleft(), now)
        while self.key_awaiting and now - self.key_awaiting[0].send_time > KEY_ACK_TIMEOUT:
            self._key_miss(self.key_awaiting.popleft(), now)

    def _miss(self, target: Target, sent: Sent, now: float) -> None:
        """No ack. The pedal only confirms changes, so a single miss is normal."""
        self.missed += 1
        self.totals["missed"] += 1
        self.misses_in_row += 1
        target.misses_in_row += 1
        self.acks_in_row = 0
        zs.log.info("--  no ack for value %d", sent.value)
        if not (target.learned or target.confirmed) and target.misses_in_row >= UNCONFIRMED_LIMIT:
            # Wrong effect or parameter number, or a range the pedal rejects: stop sending to it.
            target.stopped, target.pending = True, None
            say(f"Preset {display(self.patch)}: the pedal does not accept {target.mapping.describe()} "
                "– sending stopped")
            return
        if self.misses_in_row >= MISSES_BEFORE_SLOWDOWN:
            self._reenable_edit(now)
        if self.misses_in_row >= MISSES_BEFORE_SLOWDOWN and self.interval < MAX_INTERVAL:
            self.interval = min(self.interval * 2, MAX_INTERVAL)
            self.misses_in_row = 0
            say(f"WARNING: acks are missing – minimum interval now {self.interval * 1000:g} ms")
        # Repeat the final value of a movement once, so the pedal does not stay on an old value
        if (not sent.retry and not target.awaiting and target.pending is None
                and sent.value == target.last_value):
            self._transmit(target, sent.value, sent.cc_time, now, retry=True)

    def _report(self, now: float) -> None:
        if now < self.next_report:
            return
        self.next_report = now + REPORT_SECONDS
        if not self.sent:
            return
        latency = ""
        if self.latencies:
            in_ms = [1000 * seconds for seconds in self.latencies]
            latency = f", latency CC→ack median {statistics.median(in_ms):.1f} ms / max {max(in_ms):.1f} ms"
        zs.log.info("--  %d sent, %d confirmed, %d unconfirmed%s", self.sent, self.acked, self.missed, latency)
        self.sent = self.acked = self.missed = 0
        self.latencies = []

    # --- MS-60B+ → patch, acks, knob movements ---

    def _on_zoom(self, message, now: float) -> Optional[bytes]:
        if message.type in IGNORED_TYPES:
            return None
        raw = zs.log_rx(message)
        if message.type == "control_change" and message.control == 0:
            self.bank_msb = message.value
        elif message.type == "control_change" and message.control == 32:
            self.bank_lsb = message.value
        elif message.type == "program_change":
            self._set_patch(zs.decode_value(self.bank_lsb, self.bank_msb), message.program)
        elif raw is not None:
            body = zs.zoom_body(raw, self.device_id)
            change, program_info = zs.parse_param(body), zs.parse_program_info(body)
            dump = zs.parse_patch_dump(body) if self.synth is not None else None
            if program_info:
                self._set_patch(*program_info)
            elif dump:
                self._on_patch(dump, now)
            elif change and self.synth_slot is not None and (change.slot, change.param) == (
                    self.synth_slot, zs.KEY_PARAM):
                if change.ack:
                    self._on_key_ack(change.value, now)
                else:
                    self.key_sent, self.note_sent = change.value, None   # the Key knob was turned on the pedal
            elif change:
                target = next((t for t in self.targets if (t.mapping.slot, t.mapping.param) == (
                    change.slot, change.param)), None)
                if not change.ack:
                    self._on_knob(change, target)
                elif target is not None:
                    self._on_ack(target, change.value, now)
        return raw

    def _on_knob(self, change: zs.ParamMessage, target: Optional[Target]) -> None:
        """A knob was turned on the pedal."""
        if self.learning is not None and zs.is_effect_param(change.slot, change.param):
            self.learning.setdefault((change.slot, change.param), []).append(change.value)
        if target is not None:
            target.last_value = None   # send the next target value in any case
            target.confirmed, target.stopped, target.misses_in_row = True, False, 0

    def _set_patch(self, bank: int, program: int) -> None:
        if (bank, program) == self.patch:
            return
        self.patch = (bank, program)
        if self.learning is not None:
            self.learning = None
            say("Learning cancelled: preset changed")
        if self.synth is not None:   # the effect may sit elsewhere now, or not be there at all
            self.synth_slot, self.synth_known, self.key_sent, self.note_sent = None, False, None, None
            self.key_awaiting.clear()
            self.patch_query_at = time.monotonic() + QUERY_DELAY
        self.targets, self.told = [], set()   # another preset: nothing is carried over, remarks are made afresh
        self.refresh()
        expression = [target for target in self.targets if target.expression]
        if not expression:
            say(f"Preset {display(self.patch)}: no assignment, expression is ignored")
        else:
            say(f"Preset {display(self.patch)}: expression → "
                + " and ".join(target.mapping.describe() for target in expression))

    def refresh(self) -> None:
        """Re-derive the assignments and allowlist targets for the current patch."""
        # A parameter that stays what it was keeps the value last sent to it, so that learning
        # a controller for one knob does not make all the others send their values again.
        before = {(target.mapping, target.expression): target for target in self.targets}

        def carry(target: Target) -> Target:
            old = before.get((target.mapping, target.expression))
            if old is not None:
                target.last_value, target.confirmed = old.last_value, old.confirmed
            return target

        def remark(text: str) -> None:              # once per preset and place of the effect
            if text not in self.told:
                self.told.add(text)
                say(text)

        self.targets, self.controls, allowed, knobs = [], {}, [], {}
        if self.synth_slot is not None:
            allowed += zs.synth_targets(self.synth_slot)
            knobs = {zs.MIN_PARAM + index: knob for index, knob in enumerate(zs.SYNTH_KNOBS)}
        for mapping in (self.bridge.mappings.get(self.patch, ()) if self.patch else ()):
            learned = self.bridge.measurements.get(self.patch, mapping.slot, mapping.param) is not None
            target = carry(Target(mapping, learned))
            self.targets.append(target)
            knob = knobs.get(mapping.param) if mapping.slot == self.synth_slot else None
            if knob is None:
                allowed.append(zs.ParamTarget(mapping.slot, mapping.param, mapping.min, mapping.max))
            elif mapping.param == zs.KEY_PARAM:
                target.blocked = True   # the keyboard owns the Key knob; expression must not write notes
                remark(f"Expression is not sent to {mapping.describe()}: that is the KeySynth's Key knob")
            elif mapping.max > knob[1]:
                target.blocked = True   # a range learned from another effect or an older KeySynth
                remark(f"Expression is not sent to {mapping.describe()}: the KeySynth's {knob[0]} knob "
                       f"only goes up to {knob[1]} – learn it again")
        if self.synth_slot is not None:   # the keyboard's controllers, one Target per knob
            for name, number in self.bridge.controls.items():
                param, top = zs.synth_knob(zs.SYNTH_CONTROLS[name][0])
                target = next((t for t in self.targets if (t.mapping.slot, t.mapping.param) == (
                    self.synth_slot, param)), None)
                if target is None:
                    target = Target(Mapping(self.synth_slot, param, 0, top), learned=True)
                    target.expression = False
                    self.targets.append(carry(target))
                self.controls[number] = (target, name)
        self.sender.targets = tuple(allowed)


class Bridge:
    """Configuration, assignments and the current connection. run() runs in the main thread; the
    interface reads state() and queues changes into the loop through call()."""

    def __init__(self, settings: Optional[dict] = None) -> None:
        """settings: the content of a settings file (config_schema.py), already validated. Then
        nothing is read from or written to this installation's own files."""
        self.fixed = settings is not None
        self.led: Optional[status_led.StatusLed] = None    # the status light of a bridge without screen
        self.fallback = False                              # running with the previous settings file
        if self.fixed:
            self.cfg = settings
            self.approvals = cs.Approvals(settings["approvals"])
            self.measurements = cs.Measurements(settings.get("learned") or {})
        else:
            if not CONFIG.exists():
                sys.exit("config.yaml is missing – see “First-time setup” in README.md.")
            self.cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
            self.approvals = zs.Approvals(APPROVALS)
            self.measurements = zs.Measurements(MEASUREMENTS)
        missing = [] if self.approvals.backup_confirmed else ["backup"]
        missing += [kind for kind in REQUIRED_KINDS if not self.approvals.is_approved(kind)]
        if missing:
            sys.exit(f"Approval missing: {', '.join(missing)} – see `python probe.py approve`.")
        self.synth = read_synth(self.cfg)
        # Someone who only plays the keyboard synth has no expression controller to measure.
        keyboard_only = self.synth is not None and not self.cfg["ports"].get("chocolate")
        if self.cfg["zoom"]["device_id"] is None or (self.cfg["expression"]["cc"] is None and not keyboard_only):
            sys.exit("config.yaml is incomplete – see “First-time setup” in README.md.")
        self.controls: dict = {}                      # what a keyboard controller sets → CC number
        if self.synth:
            self.controls = dict(settings.get("controls") or {}) if self.fixed else read_controls(CONTROLS)
        self.control_learning: Optional[str] = None   # the knob waiting for a controller to be moved

        self.mappings: dict = {}   # (bank, program) → up to MAX_TARGETS mappings
        for key, entries in (self.cfg.get("mappings") or {}).items():
            patch = parse_key(key)
            mappings = tuple(Mapping(**entry) for entry in (
                entries if isinstance(entries, list) else [entries]))
            problem = self.check(patch, mappings)
            if problem:
                sys.exit(f"Mapping {key}: {problem}")
            self.mappings[patch] = mappings

        self.session: Optional[Session] = None
        self.status = "Starting…"
        self.commands: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._snapshot: dict = {}
        self._publish()   # the interface gets a complete state from the very start

    def check(self, patch: tuple[int, int], mappings: tuple, incoming: Optional[dict] = None) -> Optional[str]:
        """The reason a preset's assignments are not acceptable, otherwise None.
        incoming: ranges per (slot, param) from an import file that are not applied yet."""
        if not 1 <= len(mappings) <= MAX_TARGETS:
            return f"A preset can have 1 to {MAX_TARGETS} parameters."
        if len({(mapping.slot, mapping.param) for mapping in mappings}) < len(mappings):
            return "The same parameter is assigned twice."
        for mapping in mappings:
            where = f"effect {mapping.slot + 1}, parameter {mapping.param - 1}"
            limits = (incoming or {}).get((mapping.slot, mapping.param)) or self.measurements.get(
                patch, mapping.slot, mapping.param)
            if limits is None:   # not learned: only the fixed grid, with a range the user states
                if not (0 <= mapping.slot < MAX_EFFECTS
                        and zs.MIN_PARAM <= mapping.param < zs.MIN_PARAM + MAX_PARAMS):
                    return (f"{where.capitalize()} is outside effects 1–{MAX_EFFECTS} / parameters "
                            f"1–{MAX_PARAMS} and has not been learned in preset {display(patch)}.")
                if not 0 <= mapping.min <= mapping.max <= UNLEARNED_MAX:
                    return f"{mapping.min}–{mapping.max} is not a valid range ({where})."
            elif not limits["min"] <= mapping.min <= mapping.max <= limits["max"]:
                return (f"{mapping.min}–{mapping.max} is outside the learned range "
                        f"{limits['min']}–{limits['max']} ({where}).")
            if mapping.curve not in CURVES:
                return f"Unknown curve {mapping.curve} (available: {', '.join(CURVES)})."
        return None

    # --- Main loop ---

    def run(self) -> None:
        next_try = next_snapshot = 0.0
        waiting = False
        try:
            while True:
                now = time.monotonic()
                self._run_commands()
                try:
                    if self.session is not None:
                        self.session.step(now)
                    elif now >= next_try:
                        try:
                            self.session = Session(self)
                        except zs.PortError as error:
                            if not waiting:
                                say(f"Waiting for devices: {error}")
                            waiting, self.status = True, f"Waiting for devices: {error}"
                            next_try = now + RETRY_SECONDS
                        else:
                            waiting = False
                            self.session.start()
                            self.status = "Ready"
                except (Disconnected, OSError) as error:
                    say(f"Connection lost: {error}")
                    self.status = f"Connection lost: {error}"
                    self._close_session()
                    next_try = time.monotonic() + RETRY_SECONDS
                if now >= next_snapshot:
                    self._publish()
                    if self.led is not None:
                        ready = self.session is not None and self.session.ready
                        self.led.show(("fallback" if self.fallback else "ready") if ready else "waiting")
                    next_snapshot = now + SNAPSHOT_SECONDS
                time.sleep(0.001 if self.session else 0.05)
        except KeyboardInterrupt:
            pass
        finally:
            self._close_session()
            self.status = "Stopped"
            self._publish()
            say("Stopped.")

    def _close_session(self) -> None:
        session, self.session = self.session, None
        if session is not None:
            session.close()

    # --- Access from the interface (another thread) ---

    def state(self) -> dict:
        with self._lock:
            return self._snapshot

    def call(self, function, timeout: float = 3.0) -> dict:
        """Runs function() in the main loop; returns {"result": …} or {"error": text}."""
        done, box = threading.Event(), {}
        self.commands.put((function, done, box))
        if not done.wait(timeout):
            return {"error": "The bridge is not responding right now."}
        return box

    def _run_commands(self) -> None:
        while True:
            try:
                function, done, box = self.commands.get_nowait()
            except queue.Empty:
                return
            try:
                box["result"] = function()
            except UserError as error:
                box["error"] = str(error)
            finally:
                self._publish()
                done.set()

    def _publish(self) -> None:
        session = self.session
        patch = session.patch if session else None
        latencies = [1000 * seconds for seconds in session.recent_latencies] if session else []
        learning = None
        if session and session.learning is not None:
            learning = [{"slot": slot, "param": param, "min": min(values), "max": max(values),
                         "count": len(values)} for (slot, param), values in session.learning.items()]
        state = {
            "connected": bool(session and session.ready),
            "status": self.status,
            "patch": {"key": f"{patch[0]}/{patch[1]}", "display": display(patch)} if patch else None,
            "pedal": session.last_cc if session else None,
            "targets": [{"value": target.last_value, "stopped": target.stopped or target.blocked,
                         "unconfirmed": target.misses_in_row >= UNCONFIRMED_HINT}
                        for target in session.targets if target.expression] if session else [],
            "synth": None if self.synth is None else {
                "keyboard": bool(session and session.keyboard is not None),
                "expression": bool(session and session.chocolate is not None),
                "slot": session.synth_slot if session else None,
                "key": session.key_sent if session else None,
                "bend_range": self.synth["bend_range"],
                "knobs": [{"name": name, "knob": knob, "rest": rest, "full": full, "cc": self.controls.get(name)}
                          for name, (knob, rest, full) in zs.SYNTH_CONTROLS.items()],
                "learning": self.control_learning,
                "totals": dict(session.key_totals) if session else None,
                "latency_ms": round(statistics.median(1000 * seconds for seconds in session.key_latencies), 1)
                if session and session.key_latencies else None,
            },
            "totals": dict(session.totals) if session else None,
            "latency_ms": round(statistics.median(latencies), 1) if latencies else None,
            "learning": learning,
            "mappings": [{"key": f"{p[0]}/{p[1]}", "display": display(p),
                          "targets": [asdict(mapping) for mapping in mappings]}
                         for p, mappings in sorted(self.mappings.items())],
            "learned": {key: self._choices(parse_key(key)) for key in self.measurements.patches},
            "limits": {"targets": MAX_TARGETS, "effects": MAX_EFFECTS, "params": MAX_PARAMS,
                       "value": UNLEARNED_MAX, "per_bank": PATCHES_PER_BANK},
            "events": list(EVENTS),
        }
        with self._lock:
            self._snapshot = state

    def _choices(self, patch: tuple[int, int]) -> list:
        """The learned parameters of a preset that the interface offers to choose from."""
        return [{"slot": entry["slot"], "param": entry["param"], "min": entry["min"], "max": entry["max"]}
                for entry in self.measurements.for_patch(patch)]

    # --- Changes from the interface (run in the main loop through call()) ---

    def learn(self, action: str) -> Optional[dict]:
        """start: record knob movements on the pedal. stop: take the most-moved knob for the
        current preset. cancel: discard."""
        self._changeable()
        session = self.session
        if session is None or not session.ready or session.patch is None:
            raise UserError("The MS-60B+ is not connected or has not reported a preset yet.")
        if action == "start":
            session.learning = {}
            for target in session.targets:
                target.pending = None
            return None
        seen, session.learning = session.learning, None
        if action == "cancel":
            return None
        if seen is None:
            raise UserError("Learning was cancelled because the preset changed.")
        if not seen:
            raise UserError("No knob movement received. Turn the knob on the MS-60B+ and try again.")
        (slot, param), values = max(seen.items(), key=lambda item: len(item[1]))
        if len(set(values)) < 2:
            raise UserError("The knob was barely moved. Please turn it once from minimum to maximum.")
        limits = self.measurements.add(session.patch, slot, param, values)
        current = self.mappings.get(session.patch, ())
        known = any((mapping.slot, mapping.param) == (slot, param) for mapping in current)
        added = not known and len(current) < MAX_TARGETS
        if added:   # a free place: assign it right away, with the whole learned range
            self.mappings[session.patch] = current + (Mapping(slot, param, limits["min"], limits["max"]),)
            self._store()
        elif known:   # already assigned: keep its range inside what the pedal just reported
            self.mappings[session.patch] = tuple(
                self._fit(mapping, limits) if (mapping.slot, mapping.param) == (slot, param) else mapping
                for mapping in current)
            self._store()
        say(f"Learned for preset {display(session.patch)}: effect {slot + 1}, parameter {param - 1}, "
            f"{limits['min']}–{limits['max']}")
        return {"slot": slot, "param": param, "min": limits["min"], "max": limits["max"],
                "added": added, "known": known}

    def synth_control(self, action: str, knob: Optional[str] = None) -> None:
        """learn: the next controller moved on the keyboard will set this KeySynth knob.
        cancel: stop waiting for one. clear: no controller sets the knob any more."""
        self._changeable()
        if self.synth is None:
            raise UserError("No keyboard synth is set up in config.yaml.")
        if action == "cancel":
            self.control_learning = None
            return
        if not isinstance(knob, str) or knob not in zs.SYNTH_CONTROLS:   # anything can arrive over HTTP
            raise UserError("Unknown knob.")
        if action == "clear":
            self.control_learning = None
            if self.controls.pop(knob, None) is not None:
                self._store_controls()
        elif action == "learn":
            if self.session is None or self.session.keyboard is None:
                raise UserError("The keyboard is not connected.")
            self.control_learning = knob
        else:
            raise UserError("Unknown action.")

    def learned_control(self, number: int) -> None:
        """The session saw a controller move while a knob was waiting for one."""
        knob = self.control_learning
        if knob is None or number in RESERVED_CONTROLS:
            return
        self.control_learning = None
        for other in [name for name, known in self.controls.items() if known == number]:
            del self.controls[other]              # one controller sets one thing
        self.controls[knob] = number
        self._store_controls()
        say(f"KeySynth: controller {number} now sets {knob}")

    def _changeable(self) -> None:
        if self.fixed:
            raise UserError("The settings come from a settings file; change them on the computer "
                            "they were exported from.")

    def _store_controls(self) -> None:
        self._changeable()
        CONTROLS.write_text(json.dumps({"controls": self.controls}, indent=2) + "\n", encoding="utf-8")
        if self.session is not None:
            self.session.refresh()

    @staticmethod
    def _fit(mapping: Mapping, limits: dict) -> Mapping:
        """The mapping narrowed to the learned range; the whole range if nothing is left."""
        low, high = max(mapping.min, limits["min"]), min(mapping.max, limits["max"])
        if low > high:
            low, high = limits["min"], limits["max"]
        return replace(mapping, min=low, max=high)

    def save_mapping(self, key: str, data: dict) -> None:
        self._changeable()
        try:
            patch = parse_key(key)
            mappings = tuple(to_mapping(entry) for entry in data["targets"])
        except (KeyError, TypeError, ValueError):
            raise UserError("The details are incomplete.") from None
        problem = self.check(patch, mappings)
        if problem:
            raise UserError(problem)
        self.mappings[patch] = mappings
        self._store()

    def delete_mapping(self, key: str) -> None:
        self._changeable()
        try:
            self.mappings.pop(parse_key(key), None)
        except ValueError:
            raise UserError("Unknown preset.") from None
        self._store()

    def export_data(self, key: Optional[str] = None) -> dict:
        """Assignments together with learned ranges, for all presets or for one."""
        try:
            chosen = sorted(self.mappings) if key is None else [parse_key(key)]
        except ValueError:
            raise UserError("Unknown preset.") from None
        if any(patch not in self.mappings for patch in chosen):
            raise UserError("There is no assignment for this preset.")
        return {
            "format": EXPORT_FORMAT,
            "firmware": self.cfg["zoom"]["firmware"],
            "presets": {f"{patch[0]}/{patch[1]}": {
                "display": display(patch),
                "targets": [asdict(mapping) for mapping in self.mappings[patch]],
                "learned": self._choices(patch),
            } for patch in chosen},
        }

    def import_data(self, data) -> list:
        """Applies an export file: its presets replace existing ones with the same number, and
        its ranges count as learned. Returns the display numbers of the presets."""
        self._changeable()
        if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT or not isinstance(
                data.get("presets"), dict) or not data["presets"]:
            raise UserError("That is not an Expression Bridge export file.")
        presets = []
        for key, preset in data["presets"].items():
            try:
                patch = parse_key(key)
                learned = [(int(e["slot"]), int(e["param"]), int(e["min"]), int(e["max"]))
                           for e in preset["learned"]]
                mappings = tuple(to_mapping(entry) for entry in preset["targets"])
            except (KeyError, TypeError, ValueError):
                raise UserError(f"Preset {key}: the file is incomplete or damaged.") from None
            for slot, param, low, high in learned:
                if not (zs.is_effect_param(slot, param) and 0 <= slot <= 0x7F and param <= 0x7F
                        and 0 <= low <= high <= 0x3FFF):
                    raise UserError(f"Preset {display(patch)}: invalid range in the file.")
            problem = self.check(patch, mappings, {(slot, param): {"min": low, "max": high}
                                                   for slot, param, low, high in learned})
            if problem:
                raise UserError(f"Preset {display(patch)}: {problem}")
            presets.append((patch, learned, mappings))
        for patch, learned, mappings in presets:   # nothing is changed before this point
            for slot, param, low, high in learned:
                self.measurements.add(patch, slot, param, [low, high])
            self.mappings[patch] = mappings
        self._store()
        say(f"Imported: preset {', '.join(display(patch) for patch, _, _ in presets)}")
        return [display(patch) for patch, _, _ in presets]

    def _store(self) -> None:
        """Rewrites the `mappings:` section at the end of config.yaml and applies it at once."""
        self._changeable()
        head, found, _ = CONFIG.read_text(encoding="utf-8").partition("\nmappings:")
        if not found:
            raise UserError("config.yaml has no “mappings:” section.")
        lines = []
        for patch, mappings in sorted(self.mappings.items()):
            lines.append(f'  "{patch[0]}/{patch[1]}":   # preset {display(patch)}')
            lines += ["    - " + yaml.safe_dump(asdict(mapping), default_flow_style=True, sort_keys=False,
                                                allow_unicode=True, width=1000).strip()
                      for mapping in mappings]
        block = "\nmappings:\n" + "\n".join(lines) + "\n" if lines else "\nmappings: {}\n"
        CONFIG.write_text(head + block, encoding="utf-8")
        if self.session is not None:
            self.session.refresh()


def interrupt(signum, frame) -> None:
    raise KeyboardInterrupt


def _wait_forever() -> None:
    while True:
        time.sleep(0.5)


def safe_mode(led: status_led.StatusLed, reason: str) -> None:
    """Nothing is sent to the pedal any more; the light says so until the service is stopped."""
    say(f"SAFE MODE – nothing is sent to the pedal: {reason}")
    led.show("safe")
    try:
        _wait_forever()
    except KeyboardInterrupt:
        pass


def run_headless(folder: Path) -> None:
    """The bridge on a computer without screen: settings from the file export.py wrote into the
    folder (or the previous one), no interface, nothing stored, everything logged to the console
    (the service's journal), and the status light instead of a display."""
    global HEADLESS
    HEADLESS = True
    zs.setup_logging(None, console=True)
    led = status_led.StatusLed.find()
    led.show("waiting")
    try:
        if led.problem:
            say(f"The status light cannot be used: {led.problem}")
        settings, source, problems = cs.load_with_fallback(folder)
        for problem in problems:
            say(f"Settings: {problem}")
        if settings is None:
            safe_mode(led, "there is no usable settings file")
            return
        try:
            bridge = Bridge(settings)
        except SystemExit as stopped:
            safe_mode(led, str(stopped))
            return
        bridge.led, bridge.fallback = led, source == "previous"
        say(f"Settings exported {settings.get('exported_at', 'at an unknown time')}"
            + (" – this is the PREVIOUS file, the current one is not usable" if bridge.fallback else ""))
        try:
            bridge.run()
        except SystemExit as stopped:                # the pedal is not the one the settings are for
            safe_mode(led, str(stopped))
    finally:
        led.close()


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-ui", action="store_true", help="start without the interface")
    parser.add_argument("--open", action="store_true", help="open the interface in the browser")
    parser.add_argument("--settings", metavar="FOLDER", type=Path,
                        help="run from the settings file in this folder (made by export.py), without interface")
    args = parser.parse_args(argv)

    zs.tolerant_console()
    for name in ("SIGTERM", "SIGHUP", "SIGBREAK"):   # also stop cleanly when the window is closed
        if hasattr(signal, name):                    # SIGHUP is missing on Windows, SIGBREAK elsewhere
            signal.signal(getattr(signal, name), interrupt)
    if args.settings is not None:
        run_headless(args.settings)
        return
    bridge = Bridge()
    zs.setup_logging(ROOT / "logs", console=False)
    if not args.no_ui:
        import ui
        address = ui.serve(bridge, bridge.cfg.get("ui") or {})
        say(f"Interface: {address}")
        if args.open:
            webbrowser.open(address)
    bridge.run()


if __name__ == "__main__":
    main()
