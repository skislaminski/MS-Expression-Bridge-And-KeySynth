"""SysEx for the Zoom MS-60B+: builders, allowlist, parsers and the send function.

The protocol is unofficial (reference: https://github.com/thammer/zoom-explorer, README,
documented on the MS-50G+). Every SysEx message in this project is created by a build_* function
and leaves the program only through ZoomSender.send(), where the allowlist and approvals apply.
"""
from __future__ import annotations

import binascii
import json
import logging
import logging.handlers
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, NamedTuple, Optional

import mido

log = logging.getLogger("sysex")

SOX, EOX = 0xF0, 0xF7
ZOOM = 0x52

IDENTITY_REQUEST = bytes([SOX, 0x7E, 0x7F, 0x06, 0x01, EOX])

# Message kinds on the allowlist and their form (for display and approval).
KINDS = {
    "identity_request": "F0 7E 7F 06 01 F7",
    "edit_enable": "F0 52 00 <ID> 50 F7",
    "edit_disable": "F0 52 00 <ID> 51 F7",
    "set_param": "F0 52 00 <ID> 64 20 00 <slot> <param> <LSB> <MSB> 00 00 00 F7",
    "query_program": "F0 52 00 <ID> 33 F7",
    "query_patch": "F0 52 00 <ID> 64 13 F7",
}

_FIXED_BODIES = {
    bytes([0x50]): "edit_enable",
    bytes([0x51]): "edit_disable",
    bytes([0x33]): "query_program",
    bytes([0x64, 0x13]): "query_patch",
}

# Safety rule 2: explicitly forbidden (firmware mode, factory reset, file access, overwriting
# patches, system/patch settings, inserting/deleting/moving effects).
FORBIDDEN_PREFIXES = (
    bytes([0x01]),
    bytes([0x04]),
    bytes([0x5B]),
    bytes([0x28]),
    bytes([0x60]),
    bytes([0x64, 0x47]),
    bytes([0x64, 0x20, 0x00, 0x64]),
)

# In the slot position these bytes mean something else according to the reference:
# 5F = edit patch name, 64 = patch/system settings.
RESERVED_SLOTS = (0x5F, 0x64)

# From the zoom-explorer source (src/ZoomDevice.ts, not in the README): param 0 switches the
# effect on/off, param 1 changes the effect type, real parameters start at 2.
MIN_PARAM = 2


# KeySynth, a DIY effect played from a MIDI keyboard: its first knob "Key" is gate and pitch in
# one number, so a note and its pitch bend can never arrive apart.
# Measured on the MS-60B+: the pedal has 6 effect slots.
KEY_PARAM = 2
KEY_MAX = 1000          # 0 = gate off, n = (n - 1) x 10 cents above KEY_BASE_NOTE
KEY_BASE_NOTE = 12      # the MIDI note of Key 1 (C0)
KEY_STEPS = 10          # Key clicks per semitone
KEY_LOW_NOTE = KEY_BASE_NOTE
KEY_HIGH_NOTE = KEY_BASE_NOTE + (KEY_MAX - 1) // KEY_STEPS    # 111 (D#8)
KEY_BEND_STEP = 9       # the effect takes a larger move of the Key knob for a new note, not a bend
# The knobs of KeySynth 0.21 in the order of the pedal (manifest.json in the ms-plus-synth
# project): name and highest value. Knob number k (from 0) is parameter k + 2.
SYNTH_KNOBS = (("Key", KEY_MAX), ("Level", 100), ("Wave1", 3), ("Wave2", 4), ("Pitch", 48),
               ("Dtune", 100), ("Mix", 100), ("Glide", 100), ("Atk", 100), ("Rel", 100),
               ("LFO", 100), ("Rate", 100))
MAX_SLOTS = 6


def is_effect_param(slot: int, param: int) -> bool:
    """A real effect parameter: not the patch name, a patch/system setting, on/off or effect type."""
    return slot not in RESERVED_SLOTS and param >= MIN_PARAM


class NotAllowed(Exception):
    """The message is not on the allowlist."""


class NotApproved(Exception):
    """The backup confirmation or the approval of the message kind is missing (rules 4 and 5)."""

    def __init__(self, kind: str):
        super().__init__(f"no approval for: {kind}")
        self.kind = kind


class PortError(Exception):
    """No MIDI port, or no unique one, matches a substring."""


@dataclass(frozen=True)
class ParamTarget:
    """A measured parameter; only these may be set, and only within the measured range."""

    slot: int
    param: int
    min: int
    max: int


def to_hex(data: Iterable[int]) -> str:
    return " ".join(f"{b:02X}" for b in data)


# --- Value encoding (7-bit split; confirmed on the MS-60B+ for values up to 100 only) ---

def encode_value(value: int) -> tuple[int, int]:
    if not 0 <= value <= 0x3FFF:
        raise ValueError(f"value {value} does not fit into 14 bits")
    return value & 0x7F, value >> 7


def decode_value(lsb: int, msb: int) -> int:
    return lsb | (msb << 7)


# --- Builders ---

def _zoom(device_id: int, *body: int) -> bytes:
    return bytes([SOX, ZOOM, 0x00, device_id, *body, EOX])


def build_identity_request() -> bytes:
    return IDENTITY_REQUEST


def build_edit_enable(device_id: int) -> bytes:
    return _zoom(device_id, 0x50)


def build_edit_disable(device_id: int) -> bytes:
    return _zoom(device_id, 0x51)


def build_set_param(device_id: int, slot: int, param: int, value: int) -> bytes:
    lsb, msb = encode_value(value)
    return _zoom(device_id, 0x64, 0x20, 0x00, slot, param, lsb, msb, 0x00, 0x00, 0x00)


def build_set_key(device_id: int, slot: int, key: int) -> bytes:
    """The Key knob of the KeySynth effect in the given slot, and nothing else: the same message
    as build_set_param with the parameter fixed, so it can never reach on/off or the effect type."""
    if not 0 <= slot < MAX_SLOTS:
        raise ValueError(f"slot {slot} is not one of the {MAX_SLOTS} effect slots")
    if not 0 <= key <= KEY_MAX:
        raise ValueError(f"key {key} is outside 0–{KEY_MAX}")
    return build_set_param(device_id, slot, KEY_PARAM, key)


def key_for(note: int, bend: float = 0.0) -> int:
    """The Key value that sounds a MIDI note, bent by so many semitones. The result stays inside
    1–KEY_MAX, so a bend at the very top or bottom of the range is cut short."""
    if not KEY_LOW_NOTE <= note <= KEY_HIGH_NOTE:
        raise ValueError(f"note {note} is outside {KEY_LOW_NOTE}–{KEY_HIGH_NOTE}")
    return min(KEY_MAX, max(1, round((note - KEY_BASE_NOTE + bend) * KEY_STEPS) + 1))


def key_pitch(key: int) -> tuple[int, int]:
    """The nearest MIDI note of a Key value above 0, and how many cents the value is away from it."""
    clicks = key - 1
    note = (clicks + KEY_STEPS // 2) // KEY_STEPS
    return KEY_BASE_NOTE + note, (clicks - note * KEY_STEPS) * (100 // KEY_STEPS)


def synth_knob(name: str) -> tuple[int, int]:
    """Parameter number and highest value of a KeySynth knob."""
    for index, (knob, top) in enumerate(SYNTH_KNOBS):
        if knob == name:
            return MIN_PARAM + index, top
    raise ValueError(f"KeySynth has no knob called {name}")


def synth_targets(slot: int) -> tuple:
    """The allowlist entries for the KeySynth in that slot: every knob with its own range."""
    return tuple(ParamTarget(slot, MIN_PARAM + index, 0, top) for index, (_, top) in enumerate(SYNTH_KNOBS))


def cc_to_knob(value: int, top: int) -> int:
    """A controller value 0–127 spread evenly over a knob's 0–top (at most 127)."""
    return min(top, value * (top + 1) // 128)


# What a controller of the keyboard can be assigned to, as name → (knob, value at rest, value at
# full travel): every knob but Key over its whole range, and the two halves of the LFO knob on
# their own. The LFO knob is depth and kind in one (Vib50 … Vib1, Off, Trm1 … Trm50), so a
# wheel on "LFO" would have Off in the middle of its travel. "Vib" and "Trm" run from Off to the
# deepest vibrato or tremolo instead: at rest the LFO is off, as with the mod wheel of a
# hardware synth.
LFO_OFF = 50
SYNTH_CONTROLS = {name: (name, 0, top) for name, top in SYNTH_KNOBS[1:]}
SYNTH_CONTROLS.update({"Vib": ("LFO", LFO_OFF, 0), "Trm": ("LFO", LFO_OFF, 2 * LFO_OFF)})


def control_value(name: str, value: int) -> int:
    """The knob value for a controller value 0–127 on one of SYNTH_CONTROLS."""
    _, rest, full = SYNTH_CONTROLS[name]
    steps = cc_to_knob(value, abs(full - rest))
    return rest + steps if full >= rest else rest - steps


def build_query_program(device_id: int) -> bytes:
    return _zoom(device_id, 0x33)


def build_query_patch(device_id: int) -> bytes:
    return _zoom(device_id, 0x64, 0x13)


# --- Allowlist ---

def classify(msg: bytes, device_id: Optional[int] = None,
             targets: Iterable[ParamTarget] = ()) -> str:
    """Returns the message kind or raises NotAllowed."""
    msg = bytes(msg)
    if len(msg) < 2 or msg[0] != SOX or msg[-1] != EOX or any(b > 0x7F for b in msg[1:-1]):
        raise NotAllowed(f"not a valid SysEx message: {to_hex(msg)}")
    if msg == IDENTITY_REQUEST:
        return "identity_request"
    if device_id is None:
        raise NotAllowed("device ID unknown: only the identity request is allowed")
    if msg[1:4] != bytes([ZOOM, 0x00, device_id]):
        raise NotAllowed(f"no Zoom header for device ID {device_id:02X}: {to_hex(msg)}")

    body = msg[4:-1]
    for prefix in FORBIDDEN_PREFIXES:
        if body.startswith(prefix):
            raise NotAllowed(f"explicitly forbidden command {to_hex(prefix)}: {to_hex(msg)}")
    if body in _FIXED_BODIES:
        return _FIXED_BODIES[body]
    if len(body) == 10 and body[:3] == b"\x64\x20\x00" and body[7:] == b"\x00\x00\x00":
        slot, param, lsb, msb = body[3:7]
        _check_param(slot, param, decode_value(lsb, msb), targets)
        return "set_param"
    raise NotAllowed(f"not on the allowlist: {to_hex(msg)}")


def _check_param(slot: int, param: int, value: int, targets: Iterable[ParamTarget]) -> None:
    if slot in RESERVED_SLOTS:
        raise NotAllowed(f"slot byte {slot:02X} is not an effect slot")
    if param < MIN_PARAM:
        raise NotAllowed(f"param {param} is blocked (effect on/off or effect type)")
    for target in targets:
        if (target.slot, target.param) == (slot, param):
            if target.min <= value <= target.max:
                return
            raise NotAllowed(f"value {value} is outside the measured range "
                             f"{target.min}–{target.max} (slot {slot}, param {param})")
    raise NotAllowed(f"slot {slot}, param {param} has not been measured")


# --- Measurements ---

class Measurements:
    """Value ranges the pedal itself reported while a knob was turned, per patch (bank, program)
    and parameter. Only what is recorded here may be set (rule 1)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.patches: dict = {}
        if self.path.exists():
            self.patches = json.loads(self.path.read_text(encoding="utf-8")).get("patches", {})

    def get(self, patch: tuple[int, int], slot: int, param: int) -> Optional[dict]:
        return self.patches.get(f"{patch[0]}/{patch[1]}", {}).get(f"{slot}/{param}")

    def for_patch(self, patch: tuple[int, int]) -> list:
        return list(self.patches.get(f"{patch[0]}/{patch[1]}", {}).values())

    def add(self, patch: tuple[int, int], slot: int, param: int, values: Iterable[int]) -> dict:
        """Records reported values (merged with earlier ones) and saves."""
        known = self.get(patch, slot, param) or {}
        merged = sorted(set(known.get("values", [])) | set(values))
        entry = {"slot": slot, "param": param, "min": merged[0], "max": merged[-1], "values": merged}
        self.patches.setdefault(f"{patch[0]}/{patch[1]}", {})[f"{slot}/{param}"] = entry
        self.path.write_text(json.dumps({"patches": self.patches}) + "\n", encoding="utf-8")
        return entry


# --- Approvals ---

class Approvals:
    """Persistent approvals: backup confirmation (rule 4) and message kinds (rule 5)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._data = {"backup_confirmed": None, "messages": {}}
        if self.path.exists():
            self._data.update(json.loads(self.path.read_text(encoding="utf-8")))

    @property
    def backup_confirmed(self) -> bool:
        return bool(self._data["backup_confirmed"])

    def confirm_backup(self) -> None:
        self._data["backup_confirmed"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._save()

    def is_approved(self, kind: str) -> bool:
        return kind in self._data["messages"]

    def approve(self, kind: str) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown message kind: {kind}")
        self._data["messages"][kind] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._save()

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2) + "\n", encoding="utf-8")


# --- Sending ---

class ZoomSender:
    """The only way SysEx leaves the program."""

    def __init__(self, port, approvals: Approvals, device_id: Optional[int] = None,
                 targets: Iterable[ParamTarget] = ()):
        self.port = port
        self.approvals = approvals
        self.device_id = device_id
        self.targets = tuple(targets)

    def send(self, msg: bytes) -> str:
        kind = classify(msg, self.device_id, self.targets)
        if not self.approvals.backup_confirmed:
            raise NotApproved("backup")
        if not self.approvals.is_approved(kind):
            raise NotApproved(kind)
        log.info("TX  %s  (%s)", to_hex(msg), kind)
        self.port.send(mido.Message("sysex", data=msg[1:-1]))
        return kind


# --- Parsers ---

class Identity(NamedTuple):
    device_id: int
    family: bytes
    model: bytes
    version: str


def parse_identity_reply(msg: Optional[bytes]) -> Optional[Identity]:
    """F0 7E <ch> 06 02 52 <family LSB MSB> <model LSB MSB> <version ASCII> F7"""
    if msg is None or len(msg) < 11 or msg[1] != 0x7E or msg[3:6] != bytes([0x06, 0x02, ZOOM]):
        return None
    return Identity(device_id=msg[6], family=bytes(msg[6:8]), model=bytes(msg[8:10]),
                    version=bytes(msg[10:-1]).decode("ascii", "replace"))


def zoom_body(msg: Optional[bytes], device_id: int) -> Optional[bytes]:
    """The content of a Zoom SysEx between `F0 52 00 <ID>` and `F7`, otherwise None."""
    if msg is None or len(msg) < 6 or msg[:4] != bytes([SOX, ZOOM, 0x00, device_id]):
        return None
    return bytes(msg[4:-1])


def is_ack(body: Optional[bytes]) -> bool:
    return body == b"\x00\x00"


class ParamMessage(NamedTuple):
    ack: bool  # False: 64 20 00 (change), True: 64 20 01 (the pedal's confirmation)
    slot: int
    param: int
    lsb: int
    msb: int
    tail: bytes

    @property
    def value(self) -> int:
        return decode_value(self.lsb, self.msb)


def parse_param(body: Optional[bytes]) -> Optional[ParamMessage]:
    if body is None or len(body) != 10 or body[:2] != b"\x64\x20" or body[2] not in (0, 1):
        return None
    return ParamMessage(bool(body[2]), body[3], body[4], body[5], body[6], bytes(body[7:]))


def parse_program_info(body: Optional[bytes]) -> Optional[tuple[int, int]]:
    """`64 26 00 00 <bank LSB MSB> <prog LSB MSB>` → (bank, program). The MS-60B+ sends this on
    every patch change, before bank select and program change."""
    if body is None or len(body) != 8 or body[:4] != b"\x64\x26\x00\x00":
        return None
    return decode_value(body[4], body[5]), decode_value(body[6], body[7])


class PatchEffect(NamedTuple):
    id: int
    enabled: Optional[bool]       # None if the dump carries no settings for the effect
    first_param: Optional[int]    # the value of its first knob


class PatchDump(NamedTuple):
    name: str
    effects: tuple                # one PatchEffect per slot, in chain order


def unpack_7bit(packed: bytes) -> bytes:
    """Eight bytes on the wire carry seven data bytes: the first holds their top bits, bit 6 for
    the first data byte down to bit 0 for the seventh (as in zoom-zt2's zoomzt2.py)."""
    data = bytearray()
    for start in range(0, len(packed), 8):
        high = packed[start]
        for index, byte in enumerate(packed[start + 1:start + 8]):
            data.append(byte | ((high >> (6 - index)) & 1) << 7)
    return bytes(data)


def parse_patch_dump(body: Optional[bytes]) -> Optional[PatchDump]:
    """The reply to query_patch, `64 12 01 <length LSB MSB> <patch, 7-bit packed> <CRC32, 5 bytes>`
    → the effects of the current patch in slot order. None if it is not a patch dump or does
    not check out. Layout of the patch ("PTCF") as in zoom-zt2's decode_preset.py, verified
    against dumps of the MS-60B+ (firmware 1.20): effect count at 12, name at 26, ids from 36,
    then tagged chunks; "EDTB" holds 24 bytes of settings per effect."""
    if body is None or len(body) < 10 or body[:2] != b"\x64\x12":
        return None
    length = decode_value(body[3], body[4])
    data = unpack_7bit(body[5:-5])
    crc = sum(byte << (7 * index) for index, byte in enumerate(body[-5:]))
    if len(data) != length or (binascii.crc32(data) ^ 0xFFFFFFFF) != crc:
        return None
    if data[:4] != b"PTCF" or length < 36:
        return None
    count = int.from_bytes(data[12:16], "little")
    if count > MAX_SLOTS or length < 36 + 4 * count:
        return None
    ids = [int.from_bytes(data[36 + 4 * slot:40 + 4 * slot], "little") for slot in range(count)]

    settings: list = [None] * count
    position = 36 + 4 * count
    while position + 8 <= length:                # chunks: 4-byte tag, 4-byte length, content
        tag, size = data[position:position + 4], int.from_bytes(data[position + 4:position + 8], "little")
        if tag == b"EDTB" and size >= 24 * count and position + 8 + 24 * count <= length:
            for slot in range(count):
                # 24 bytes read as one little-endian number: bit 0 on/off, 29 bits id, then
                # 12 bits for the first knob
                block = int.from_bytes(data[position + 8 + 24 * slot:position + 32 + 24 * slot], "little")
                if (block >> 1) & 0x1FFFFFFF == ids[slot] & 0x1FFFFFFF:
                    settings[slot] = (bool(block & 1), (block >> 30) & 0xFFF)
            break
        position += 8 + size
    return PatchDump(
        name=data[26:36].decode("ascii", "replace").strip(),
        effects=tuple(PatchEffect(effect_id, *(settings[slot] or (None, None)))
                      for slot, effect_id in enumerate(ids)))


# --- Logging and ports ---

class _ConsoleFormatter(logging.Formatter):
    """Shortens long lines (patch dumps) on the console; the log file stays complete."""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        if len(line) <= 240:
            return line
        return f"{line[:200]} … (truncated, complete in the log file)"


def tolerant_console() -> None:
    """Never fail on printing: a Windows console may not know characters such as → or –."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def setup_logging(log_dir: Optional[Path], console: bool = True) -> Optional[Path]:
    """Everything sent and received goes to logs/sysex.log (rotating), optionally to the console.
    Without a folder it goes to the console only (a service's journal, on a read-only system)."""
    path = None
    fmt = "%(asctime)s.%(msecs)03d  %(message)s"
    log.handlers[:] = []
    if log_dir is not None:
        log_dir.mkdir(exist_ok=True)
        path = log_dir / "sysex.log"
        to_file = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3,
                                                       encoding="utf-8")
        to_file.setFormatter(logging.Formatter(fmt, "%Y-%m-%d %H:%M:%S"))
        log.addHandler(to_file)
    if console:
        to_console = logging.StreamHandler()
        to_console.setFormatter(_ConsoleFormatter(fmt, "%H:%M:%S"))
        log.addHandler(to_console)
    log.setLevel(logging.INFO)
    log.propagate = False
    return path


def log_rx(message: mido.Message) -> Optional[bytes]:
    """Logs an incoming message; for SysEx returns the raw bytes (F0 … F7)."""
    if message.type == "sysex":
        raw = bytes(message.bytes())
        log.info("RX  %s", to_hex(raw))
        return raw
    log.info("RX  %s", message)
    return None


def find_port(names: Iterable[str], needle: str) -> str:
    """Exactly one port name containing the substring (case-insensitive)."""
    if not needle:
        raise PortError("port name missing in config.yaml (run `python probe.py ports` first)")
    matches = sorted({name for name in names if needle.lower() in name.lower()})
    if len(matches) != 1:
        found = ", ".join(matches) if matches else "none"
        raise PortError(f"no unique port matching “{needle}” (found: {found})")
    return matches[0]
