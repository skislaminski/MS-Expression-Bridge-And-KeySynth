"""SysEx for the Zoom MS-60B+: builders, allowlist, parsers and the send function.

The protocol is unofficial (reference: https://github.com/thammer/zoom-explorer, README,
documented on the MS-50G+). Every SysEx message in this project is created by a build_* function
and leaves the program only through ZoomSender.send(), where the allowlist and approvals apply.
"""
from __future__ import annotations

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


def setup_logging(log_dir: Path, console: bool = True) -> Path:
    """Everything sent and received goes to logs/sysex.log (rotating), optionally to the console."""
    log_dir.mkdir(exist_ok=True)
    path = log_dir / "sysex.log"
    fmt = "%(asctime)s.%(msecs)03d  %(message)s"
    to_file = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3,
                                                   encoding="utf-8")
    to_file.setFormatter(logging.Formatter(fmt, "%Y-%m-%d %H:%M:%S"))
    log.handlers[:] = [to_file]
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
