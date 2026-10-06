#!/usr/bin/env python3
"""Phase 1: exploring the MS-60B+ and the Chocolate Plus.

  python probe.py ports      list MIDI ports (sends nothing)
  python probe.py identity   identity request to the MS-60B+
  python probe.py cc         measure the expression pedal on the Chocolate (sends nothing)
  python probe.py learn      edit enable, then log the parameters turned on the MS-60B+
  python probe.py verify     cross-check: send exactly one measured value back
  python probe.py program    query the current bank/program
  python probe.py approve    show or grant approvals
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

import mido
import yaml

import zoom_sysex as zs

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.yaml"
APPROVALS = ROOT / "approvals.json"
MEASUREMENTS = ROOT / "measurements.json"
IGNORED_TYPES = ("clock", "active_sensing")


def load_config() -> dict:
    if not CONFIG.exists():   # first run: start from the example
        shutil.copy(ROOT / "config.example.yaml", CONFIG)
        print(f"Created {CONFIG.name} from config.example.yaml – enter your values there.\n")
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def require_device_id(cfg: dict) -> int:
    device_id = cfg["zoom"]["device_id"]
    if device_id is None:
        sys.exit("zoom.device_id is missing in config.yaml – run `python probe.py identity` first.")
    return device_id


def ask(question: str) -> bool:
    return input(f"{question} (y + Enter = yes, Enter alone = no) ").strip().lower() in ("y", "yes")


def wait_until_ready(what: str) -> None:
    """In a terminal the user decides when to start."""
    if sys.stdin.isatty():
        input(f"{what} Press Enter when you are ready … ")


def ensure_approved(approvals: zs.Approvals, messages: list[tuple[str, bytes]]) -> None:
    """Rules 4 and 5: backup confirmation and approval of every new message kind, shown as hex."""
    todo = [(kind, msg) for kind, msg in messages if not approvals.is_approved(kind)]
    if approvals.backup_confirmed and not todo:
        return
    for kind, msg in todo:
        print(f"New message kind {kind}: {zs.to_hex(msg)}")
    if not sys.stdin.isatty():
        missing = [] if approvals.backup_confirmed else ["backup"]
        missing += [kind for kind, _ in todo]
        sys.exit("Approval missing – nothing sent. Grant it with: "
                 f"python probe.py approve {' '.join(missing)}")
    if not approvals.backup_confirmed:
        if not ask("Are the patches of the MS-60B+ backed up?"):
            sys.exit("Cancelled – nothing sent.")
        approvals.confirm_backup()
    for kind, _ in todo:
        if not ask(f"Approve sending {kind}?"):
            sys.exit("Cancelled – nothing sent.")
        approvals.approve(kind)


class Zoom:
    """Input and output to the MS-60B+; everything incoming is logged."""

    def __init__(self, cfg: dict, approvals: zs.Approvals, targets=()):
        needle = cfg["ports"]["zoom"]
        in_name = zs.find_port(mido.get_input_names(), needle)
        out_name = zs.find_port(mido.get_output_names(), needle)
        self.expected_id = cfg["zoom"]["device_id"]
        self.inp = mido.open_input(in_name)
        self.out = mido.open_output(out_name)
        self.sender = zs.ZoomSender(self.out, approvals, targets=targets)

    def __enter__(self) -> "Zoom":
        return self

    def __exit__(self, *exc) -> None:
        self.inp.close()
        self.out.close()

    def poll(self):
        """Yields (message, raw SysEx bytes or None) for everything currently pending."""
        for message in self.inp.iter_pending():
            if message.type not in IGNORED_TYPES:
                yield message, zs.log_rx(message)

    def wait_for(self, match, timeout: float = 1.0):
        """Waits for the first SysEx for which match(raw) returns something truthy."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for _, raw in self.poll():
                result = raw and match(raw)
                if result:
                    return result
            time.sleep(0.001)
        return None

    def identify(self, check: bool = True) -> zs.Identity:
        self.sender.send(zs.build_identity_request())
        identity = self.wait_for(zs.parse_identity_reply, 2.0)
        if identity is None:
            sys.exit("No identity reply from the MS-60B+ – nothing further sent.")
        if check and identity.device_id != self.expected_id:
            sys.exit(f"Device ID {identity.device_id:02X} does not match config.yaml – "
                     "nothing further sent.")
        self.sender.device_id = identity.device_id
        return identity

    def current_patch(self) -> tuple[int, int]:
        """Queries (bank, program); the pedal answers with CC 0, CC 32 and a program change."""
        self.sender.send(zs.build_query_program(self.sender.device_id))
        bank = {0: 0, 32: 0}
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            for message, _ in self.poll():
                if message.type == "control_change" and message.control in bank:
                    bank[message.control] = message.value
                elif message.type == "program_change":
                    return zs.decode_value(bank[32], bank[0]), message.program
            time.sleep(0.001)
        sys.exit("No reply to the program query – nothing further sent.")

    def command(self, msg: bytes) -> bool:
        """Sends a command and waits for the ack `00 00`."""
        self.sender.send(msg)
        device_id = self.sender.device_id
        return bool(self.wait_for(lambda raw: zs.is_ack(zs.zoom_body(raw, device_id))))


# --- ports ---

def cmd_ports(args) -> None:
    cfg = load_config()
    listed = (("Inputs", mido.get_input_names()), ("Outputs", mido.get_output_names()))
    for title, names in listed:
        print(f"{title}:")
        for name in names or ["(none)"]:
            print(f"  {name}")
    print()
    for device, needle in cfg["ports"].items():
        for title, names in listed:
            try:
                print(f"{device} / {title}: {zs.find_port(names, needle)}")
            except zs.PortError as error:
                print(f"{device} / {title}: {error}")


# --- identity ---

def cmd_identity(args) -> None:
    cfg, approvals = load_config(), zs.Approvals(APPROVALS)
    ensure_approved(approvals, [("identity_request", zs.build_identity_request())])
    with Zoom(cfg, approvals) as zoom:
        identity = zoom.identify(check=False)
    print(f"\nDevice ID {identity.device_id:02X}, family {zs.to_hex(identity.family)}, "
          f"model {zs.to_hex(identity.model)}, firmware {identity.version}")
    print(f"→ config.yaml: zoom.device_id: 0x{identity.device_id:02X}, "
          f"zoom.firmware: \"{identity.version}\"")


# --- cc ---

def cmd_cc(args) -> None:
    cfg = load_config()
    name = zs.find_port(mido.get_input_names(), cfg["ports"]["chocolate"])
    steps = (
        ("heel", "Pedal all the way back (heel), then do NOT move it", args.rest),
        ("sweep", "press slowly and evenly from heel to toe", args.sweep),
        ("toe", "Keep the pedal fully pressed (toe) and do NOT move it", args.rest),
    )
    events = []  # (section, channel, CC, value)
    wait_until_ready("Put your foot on the expression pedal.")

    def listen(section: str, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            for message in port.iter_pending():
                if message.type == "control_change":
                    events.append((section, message.channel + 1, message.control, message.value))
                    print(f"\r  channel {message.channel + 1:2d}  CC {message.control:3d}  "
                          f"value {message.value:3d}", end="", flush=True)
                elif message.type not in IGNORED_TYPES:
                    print(f"\n  other message: {message}")
            time.sleep(0.001)

    with mido.open_input(name) as port:
        for section, instruction, seconds in steps:
            print(f"\n→ {instruction}. Recording starts in 3 s …")
            listen(f"before_{section}", 3)
            print(f"\n  recording ({seconds:g} s)")
            listen(section, seconds)
    print()
    report_cc(events)


def report_cc(events: list) -> None:
    if not events:
        print("No CC messages received. Check: mode switch on U, expression CC set in CubeSuite, "
              "and if needed Bluetooth off on the Chocolate (hold B + C).")
        return
    counts = Counter((channel, control) for _, channel, control, _ in events)
    (channel, control), count = counts.most_common(1)[0]
    series = [(section, value) for section, ch, cc, value in events if (ch, cc) == (channel, control)]
    values = [value for _, value in series]

    at_heel = [value for section, value in series if section in ("before_heel", "heel")]
    heel, toe = (at_heel[-1] if at_heel else values[0]), values[-1]
    sweep = [value for section, value in series if section == "sweep"]
    directions = [b - a for a, b in zip(sweep, sweep[1:]) if b != a]
    reversals = sum(1 for a, b in zip(directions, directions[1:]) if (a > 0) != (b > 0))

    print(f"Expression: CC {control} on channel {channel} ({count} messages)")
    print(f"Range: {min(values)}–{max(values)}")
    print(f"Heel {heel}, toe {toe} → inverted: {'yes' if heel > toe else 'no'}")
    for section in ("heel", "toe"):
        resting = [value for s, value in series if s == section]
        noise = f"{len(resting)} messages, values {min(resting)}–{max(resting)}" if resting else "quiet"
        print(f"At rest ({section}): {noise}")
    print(f"Direction changes during the sweep: {reversals} (should be 0 for an even press)")
    for (other_channel, other_control), n in counts.items():
        if (other_channel, other_control) != (channel, control):
            print(f"Also seen: CC {other_control} on channel {other_channel} ({n}×)")
    print(f"→ config.yaml: expression: {{cc: {control}, channel: {channel}, min: {min(values)}, "
          f"max: {max(values)}, invert: {'true' if heel > toe else 'false'}}}")


# --- learn ---

def cmd_learn(args) -> None:
    cfg, approvals = load_config(), zs.Approvals(APPROVALS)
    device_id = require_device_id(cfg)
    enable, disable = zs.build_edit_enable(device_id), zs.build_edit_disable(device_id)
    ensure_approved(approvals, [("identity_request", zs.build_identity_request()),
                                ("query_program", zs.build_query_program(device_id)),
                                ("edit_enable", enable), ("edit_disable", disable)])
    seen = {}  # (slot, param) -> [ParamMessage] in order of arrival
    wait_until_ready("Target patch and effect selected on the MS-60B+?")
    with Zoom(cfg, approvals) as zoom:
        zoom.identify()
        patch = zoom.current_patch()
        try:
            print("Edit enable:", "ack" if zoom.command(enable) else "NO ack")
            print(f"\nNow turn the target parameter on the MS-60B+ once from minimum to maximum "
                  f"({args.seconds:g} s, Ctrl+C ends earlier) …\n")
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline:
                for _, raw in zoom.poll():
                    change = zs.parse_param(zs.zoom_body(raw, device_id))
                    if change and not change.ack:
                        seen.setdefault((change.slot, change.param), []).append(change)
                time.sleep(0.001)
        except KeyboardInterrupt:
            print()
        finally:
            print("Edit disable:", "ack" if zoom.command(disable) else "NO ack")
    print()
    report_learn(seen, patch)


def report_learn(seen: dict, patch: tuple[int, int]) -> None:
    if not seen:
        print("No 64 20 00 messages received from the pedal. Fallback (step 6): determine the "
              "values with Zoom Explorer, https://www.waveformer.net/zoom-explorer/ (Chrome).")
        return
    measurements = zs.Measurements(MEASUREMENTS)
    print(f"Patch: bank {patch[0]}, program {patch[1]}")
    for (slot, param), changes in sorted(seen.items()):
        values = [change.value for change in changes]
        steps = [b - a for a, b in zip(values, values[1:])]
        monotonic = all(s >= 0 for s in steps) or all(s <= 0 for s in steps)
        msb_used = any(change.msb for change in changes)
        tail_zero = all(change.tail == b"\x00\x00\x00" for change in changes)
        first, last = changes[0], changes[-1]

        print(f"Slot {slot}, param {param}: {len(changes)} messages, value {min(values)}–{max(values)}")
        print(f"  raw LSB MSB: {first.lsb:02X} {first.msb:02X} → {last.lsb:02X} {last.msb:02X}")
        if not tail_zero:
            print("  Value encoding: trailing bytes are not 00 00 00 – the value may be wider than 2 bytes!")
        elif not monotonic:
            print("  Value encoding: not monotonic – turned back and forth, or the encoding "
                  "differs from LSB + 128·MSB.")
        elif msb_used:
            print("  Value encoding: LSB + 128·MSB fits the sequence.")
        else:
            print("  Value encoding: MSB stayed 00 – the split cannot be checked on this parameter "
                  "(all values ≤ 127).")
        print(f"  → config.yaml: \"{patch[0]}/{patch[1]}\": [{{slot: {slot}, param: {param}, "
              f"min: {min(values)}, max: {max(values)}, invert: false, curve: linear}}]")
        if zs.is_effect_param(slot, param):
            measurements.add(patch, slot, param, values)
    print(f"\nMeasured values saved in {MEASUREMENTS.name}.")


# --- verify ---

def cmd_verify(args) -> None:
    cfg, approvals = load_config(), zs.Approvals(APPROVALS)
    device_id = require_device_id(cfg)
    enable, disable = zs.build_edit_enable(device_id), zs.build_edit_disable(device_id)
    ensure_approved(approvals, [("identity_request", zs.build_identity_request()),
                                ("query_program", zs.build_query_program(device_id)),
                                ("edit_enable", enable), ("edit_disable", disable)])
    with Zoom(cfg, approvals) as zoom:
        zoom.identify()
        patch = zoom.current_patch()
        targets = zs.Measurements(MEASUREMENTS).for_patch(patch)
        chosen = [t for t in targets if (args.slot, args.param) in ((None, None), (t["slot"], t["param"]))]
        if len(chosen) != 1:
            found = ", ".join(f"slot {t['slot']} / param {t['param']}" for t in targets) or "nothing"
            sys.exit(f"No (unique) measurement for bank {patch[0]}, program {patch[1]} – "
                     f"pass --slot/--param. Measured: {found}")
        slot, param, values = chosen[0]["slot"], chosen[0]["param"], chosen[0]["values"]
        middle = (chosen[0]["min"] + chosen[0]["max"]) / 2
        value = args.value if args.value is not None else min(values, key=lambda v: abs(v - middle))
        if value not in values:
            sys.exit(f"Value {value} was not measured – it will not be sent.")
        msg = zs.build_set_param(device_id, slot, param, value)
        print(f"Cross-check: slot {slot}, param {param}, value {value}: {zs.to_hex(msg)}")
        ensure_approved(approvals, [("set_param", msg)])
        zoom.sender.targets = (zs.ParamTarget(slot, param, value, value),)
        kind, reported = send_and_observe(zoom, args, msg, slot, param, value)
    print()
    if kind == "ack":
        print("The pedal confirmed the value (64 20 01).")
    elif kind is None:
        print("No confirmation and no knob message.")
    elif abs(reported - value) <= 3:
        print(f"No 64 20 01, but the knob reports {reported}: the pedal applied {value}.")
    else:
        print(f"No 64 20 01, and the knob reports {reported}: the pedal was not at {value}.")
    print(f"Did the display show {value} when it was sent?")


def send_and_observe(zoom: Zoom, args, msg: bytes, slot: int, param: int, value: int):
    """Sends the value between edit enable and disable; returns (kind, value) of the reaction."""
    device_id = zoom.sender.device_id
    enable, disable = zs.build_edit_enable(device_id), zs.build_edit_disable(device_id)

    def answer(raw: bytes):
        """("ack", value) for the confirmation, ("knob", value) for a knob message from the pedal."""
        reply = zs.parse_param(zs.zoom_body(raw, device_id))
        if reply and (reply.slot, reply.param) == (slot, param):
            if not reply.ack:
                return "knob", reply.value
            if reply.value == value:
                return "ack", reply.value

    wait_until_ready("Watch the display of the MS-60B+.")
    if not zoom.command(enable):
        zoom.command(disable)
        sys.exit("NO ack for edit enable – parameter not sent.")
    try:
        zoom.wait_for(lambda raw: None, args.pause)  # pause after edit enable, still listening
        zoom.sender.send(msg)
        kind, reported = zoom.wait_for(answer, 2.0) or (None, None)
        if kind is None:
            # Without an ack the knob reveals the state: one click reports the neighbouring value.
            print(f"\nNo 64 20 01. Now turn the knob by ONE click ({args.listen:g} s) …")
            kind, reported = zoom.wait_for(answer, args.listen) or (None, None)
    finally:
        zoom.command(disable)
    return kind, reported


# --- program ---

def cmd_program(args) -> None:
    cfg, approvals = load_config(), zs.Approvals(APPROVALS)
    device_id = require_device_id(cfg)
    ensure_approved(approvals, [("identity_request", zs.build_identity_request()),
                                ("query_program", zs.build_query_program(device_id))])
    with Zoom(cfg, approvals) as zoom:
        zoom.identify()
        bank, program = zoom.current_patch()
    print(f"\nBank {bank}, program {program} → key \"{bank}/{program}\" in config.yaml "
          f"(shown on the pedal as {bank * 10 + program + 1:03d})")


# --- approve ---

def cmd_approve(args) -> None:
    approvals = zs.Approvals(APPROVALS)
    for item in args.items:
        if item == "backup":
            approvals.confirm_backup()
        elif item in zs.KINDS:
            approvals.approve(item)
        else:
            sys.exit(f"Unknown: {item} (available: backup, {', '.join(zs.KINDS)})")
    print(f"{'backup':17} {'confirmed' if approvals.backup_confirmed else 'open':10} "
          "the patches of the MS-60B+ are backed up")
    for kind, form in zs.KINDS.items():
        print(f"{kind:17} {'approved' if approvals.is_approved(kind) else 'open':10} {form}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ports").set_defaults(run=cmd_ports)
    sub.add_parser("identity").set_defaults(run=cmd_identity)
    cc = sub.add_parser("cc")
    cc.add_argument("--rest", type=float, default=5, help="seconds per at-rest measurement")
    cc.add_argument("--sweep", type=float, default=8, help="seconds for the sweep")
    cc.set_defaults(run=cmd_cc)
    learn = sub.add_parser("learn")
    learn.add_argument("--seconds", type=float, default=30, help="how long to listen")
    learn.set_defaults(run=cmd_learn)
    verify = sub.add_parser("verify")
    verify.add_argument("--slot", type=int)
    verify.add_argument("--param", type=int)
    verify.add_argument("--value", type=int, help="a measured value; default: near the middle")
    verify.add_argument("--pause", type=float, default=0.5,
                        help="seconds between edit enable and the parameter")
    verify.add_argument("--listen", type=float, default=20,
                        help="seconds to wait for a knob message when there is no ack")
    verify.set_defaults(run=cmd_verify)
    sub.add_parser("program").set_defaults(run=cmd_program)
    approve = sub.add_parser("approve")
    approve.add_argument("items", nargs="*", help="backup and/or message kinds")
    approve.set_defaults(run=cmd_approve)
    args = parser.parse_args()

    zs.tolerant_console()
    zs.setup_logging(ROOT / "logs")
    try:
        args.run(args)
    except zs.PortError as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
