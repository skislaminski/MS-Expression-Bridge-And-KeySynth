#!/usr/bin/env python3
"""Expression Bridge: CC from the Chocolate Plus → parameter SysEx to the MS-60B+, with a web UI.

  python bridge.py           bridge + interface in the browser (the address is printed on start)
  python bridge.py --open    also opens the interface in the browser
  python bridge.py --no-ui   bridge only

Runs until Ctrl+C; if a device is missing it keeps looking. Only approved message kinds are sent
(`python probe.py approve`), and only values within the ranges the pedal itself reported while
learning.
"""
from __future__ import annotations

import argparse
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

import zoom_sysex as zs

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.yaml"
APPROVALS = ROOT / "approvals.json"
MEASUREMENTS = ROOT / "measurements.json"

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
MAX_EFFECTS = 6            # effects 1–6 and parameters 1–9 can be assigned without learning them
MAX_PARAMS = 9
UNLEARNED_MAX = 0x3FFF     # value limit for a parameter whose range has not been learned
UNCONFIRMED_HINT = 3       # misses in a row before the interface flags a parameter
UNCONFIRMED_LIMIT = 5      # misses before sending stops for a parameter the pedal never confirmed
EXPORT_FORMAT = "expression-bridge/1"

CURVES = {
    "linear": lambda x: x,
    "log": lambda x: math.log1p(9 * x) / math.log(10),   # changes quickly at the start
    "exp": lambda x: (10 ** x - 1) / 9,                   # changes slowly at the start
}

EVENTS: deque = deque(maxlen=8)   # latest status lines, for the interface


class Disconnected(Exception):
    """A device is missing or not answering; the connection is rebuilt."""


class UserError(Exception):
    """A request from the interface cannot be carried out; the text is shown there."""


def say(text: str) -> None:
    line = f"{time.strftime('%H:%M:%S')}  {text}"
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


class Session:
    """One connection to both devices, from opening the ports to closing them."""

    def __init__(self, bridge: "Bridge"):
        cfg = bridge.cfg
        inputs, outputs = mido.get_input_names(), mido.get_output_names()
        self.zoom_in_name = zs.find_port(inputs, cfg["ports"]["zoom"])
        self.zoom_out_name = zs.find_port(outputs, cfg["ports"]["zoom"])
        self.chocolate_name = zs.find_port(inputs, cfg["ports"]["chocolate"])
        self.zoom_in = mido.open_input(self.zoom_in_name)
        self.zoom_out = mido.open_output(self.zoom_out_name)
        self.chocolate = mido.open_input(self.chocolate_name)

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
        self.ready = True
        say(f"Ready: {self.chocolate_name} → {self.zoom_out_name}, firmware {identity.version}")

    def close(self) -> None:
        try:
            if self.edit_enabled:
                self.sender.send(zs.build_edit_disable(self.device_id))
                self._wait(lambda raw: zs.is_ack(zs.zoom_body(raw, self.device_id)), 0.5)
        except OSError:
            pass   # the device is already gone
        finally:
            for port in (self.zoom_in, self.zoom_out, self.chocolate):
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
        for message in self.chocolate.iter_pending():
            self._on_chocolate(message, now)
        for message in self.zoom_in.iter_pending():
            self._on_zoom(message, now)
        self._flush(now)
        self._check_timeouts(now)
        if self.query_at is not None and now >= self.query_at:
            self.query_at = None
            self.sender.send(zs.build_query_program(self.device_id))
        self._report(now)
        if now >= self.next_port_check:
            self._check_ports()
            self.next_port_check = now + PORT_CHECK_SECONDS

    def _check_ports(self) -> None:
        inputs, outputs = mido.get_input_names(), mido.get_output_names()
        for name, names in ((self.zoom_in_name, inputs), (self.zoom_out_name, outputs),
                            (self.chocolate_name, inputs)):
            if name not in names:
                raise Disconnected(f"{name} is gone")

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
                    if not target.stopped:
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
            if program_info:
                self._set_patch(*program_info)
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
        self.refresh()
        if not self.targets:
            say(f"Preset {display(self.patch)}: no assignment, expression is ignored")
        else:
            say(f"Preset {display(self.patch)}: expression → "
                + " and ".join(target.mapping.describe() for target in self.targets))

    def refresh(self) -> None:
        """Re-derive the assignments and allowlist targets for the current patch."""
        self.targets, allowed = [], []
        for mapping in (self.bridge.mappings.get(self.patch, ()) if self.patch else ()):
            learned = self.bridge.measurements.get(self.patch, mapping.slot, mapping.param) is not None
            self.targets.append(Target(mapping, learned))
            allowed.append(zs.ParamTarget(mapping.slot, mapping.param, mapping.min, mapping.max))
        self.sender.targets = tuple(allowed)


class Bridge:
    """Configuration, assignments and the current connection. run() runs in the main thread; the
    interface reads state() and queues changes into the loop through call()."""

    def __init__(self) -> None:
        if not CONFIG.exists():
            sys.exit("config.yaml is missing – see “First-time setup” in README.md.")
        self.cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        self.approvals = zs.Approvals(APPROVALS)
        self.measurements = zs.Measurements(MEASUREMENTS)
        missing = [] if self.approvals.backup_confirmed else ["backup"]
        missing += [kind for kind in REQUIRED_KINDS if not self.approvals.is_approved(kind)]
        if missing:
            sys.exit(f"Approval missing: {', '.join(missing)} – see `python probe.py approve`.")
        if self.cfg["zoom"]["device_id"] is None or self.cfg["expression"]["cc"] is None:
            sys.exit("config.yaml is incomplete – finish phase 1 with probe.py first.")

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
            "targets": [{"value": target.last_value, "stopped": target.stopped,
                         "unconfirmed": target.misses_in_row >= UNCONFIRMED_HINT}
                        for target in session.targets] if session else [],
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

    @staticmethod
    def _fit(mapping: Mapping, limits: dict) -> Mapping:
        """The mapping narrowed to the learned range; the whole range if nothing is left."""
        low, high = max(mapping.min, limits["min"]), min(mapping.max, limits["max"])
        if low > high:
            low, high = limits["min"], limits["max"]
        return replace(mapping, min=low, max=high)

    def save_mapping(self, key: str, data: dict) -> None:
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


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-ui", action="store_true", help="start without the interface")
    parser.add_argument("--open", action="store_true", help="open the interface in the browser")
    args = parser.parse_args(argv)

    zs.tolerant_console()
    bridge = Bridge()
    zs.setup_logging(ROOT / "logs", console=False)
    for name in ("SIGTERM", "SIGHUP", "SIGBREAK"):   # also stop cleanly when the window is closed
        if hasattr(signal, name):                    # SIGHUP is missing on Windows, SIGBREAK elsewhere
            signal.signal(getattr(signal, name), interrupt)
    if not args.no_ui:
        import ui
        address = ui.serve(bridge, bridge.cfg.get("ui") or {})
        say(f"Interface: {address}")
        if args.open:
            webbrowser.open(address)
    bridge.run()


if __name__ == "__main__":
    main()
