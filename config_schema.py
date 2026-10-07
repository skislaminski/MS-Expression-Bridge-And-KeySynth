"""The settings file for a bridge that runs without screen and keyboard (Raspberry Pi), and its rules.

A settings file is config.yaml plus what otherwise lives in files of its own, so that one file
carries everything the bridge needs:

    schema_version   1
    exported_at      when export.py wrote it
    learned          learned ranges per preset: {"9/4": [{slot, param, min, max}, …]}
    approvals        {"backup_confirmed": timestamp, "messages": {message kind: timestamp}}
    controls         which controller of the keyboard sets what on the KeySynth: {name: CC number}

It holds values only: never code, and never raw SysEx bytes. Messages are still built in
zoom_sysex.py alone. export.py (on the computer where everything was set up) and bridge.py
(where the file is read) both check a file with validate(), so they cannot disagree.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml

import zoom_sysex as zs

SCHEMA_VERSION = 1
FILE_NAME = "expression-bridge.yaml"
PREVIOUS_NAME = "expression-bridge.prev.yaml"

REQUIRED_KINDS = ("identity_request", "edit_enable", "edit_disable", "set_param", "query_program")
SYNTH_KINDS = ("query_patch",)                 # needed in addition when a keyboard plays the KeySynth
CURVES = ("linear", "log", "exp")
MAX_TARGETS = 4                                # parameters the expression pedal controls per preset
MAX_EFFECTS = 6                                # without a learned range: effect 1–6 …
MAX_PARAMS = 12                                # … and parameter 1–12 only
UNLEARNED_MAX = 0x3FFF
MAX_BEND_RANGE = 12
RESERVED_CONTROLS = (0, 32, 64) + tuple(range(120, 128))   # bank select, sustain, channel mode

SECTIONS = {
    "ports": {"zoom", "chocolate"},
    "zoom": {"device_id", "firmware"},
    "expression": {"cc", "channel", "min", "max", "invert"},
    "bridge": {"min_interval_ms", "deadband", "passthrough"},
    "ui": {"port"},
    "synth": {"keyboard", "channel", "effect_id", "low_note", "high_note", "sustain", "bend_range"},
}
EXTRA = {"schema_version", "exported_at", "learned", "approvals", "controls", "mappings"}
# A message, not a name or a value: four or more two-digit hex numbers in a row ("F0 52 00 6E"),
# or one run of hex from F0 to F7. Dates and times do not match (their numbers are joined by - and :).
LOOKS_LIKE_BYTES = re.compile(r"(?:\b[0-9A-Fa-f]{2}\b[\s,]+){3,}\b[0-9A-Fa-f]{2}\b|\b[Ff]0(?:[0-9A-Fa-f]{2})+[Ff]7\b")


def _is_int(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _preset(key) -> Optional[tuple]:
    """"bank/program" → (bank, program), or None if it is not a preset key."""
    bank, _, program = str(key).rpartition("/")
    try:
        patch = (int(bank or 0), int(program))
    except ValueError:
        return None
    return patch if 0 <= patch[0] <= 0x3FFF and 0 <= patch[1] <= 0x7F else None


def _strings(value, where: str):
    """Every string or bytes value in the data, with the place it sits in."""
    if isinstance(value, (str, bytes, bytearray)):
        yield where, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key, f"{where}.{key}" if where else str(key))
            yield from _strings(item, f"{where}.{key}" if where else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _strings(item, f"{where}[{index}]")


def validate(data) -> list:
    """Everything that is wrong with a settings file, as sentences; an empty list means it is valid."""
    if not isinstance(data, dict):
        return ["The file does not contain settings."]
    problems = []
    add = problems.append

    for key in data:                                       # a typo must not be skipped silently
        if key not in SECTIONS and key not in EXTRA:
            add(f"Unknown entry “{key}”.")
    for name, allowed in SECTIONS.items():
        section = data.get(name)
        if section is None:
            if name not in ("ui", "synth"):
                add(f"The section “{name}” is missing.")
        elif not isinstance(section, dict):
            add(f"“{name}” must be a section with settings.")
        else:
            for key in section:
                if key not in allowed:
                    add(f"Unknown setting “{key}” in “{name}”.")
    for where, text in _strings(data, ""):                 # values only, never messages
        if isinstance(text, (bytes, bytearray)) or LOOKS_LIKE_BYTES.search(text):
            add(f"“{where}” looks like raw bytes; a settings file holds values only.")
    if problems:
        return problems                                    # the shape is wrong: the rest would only confuse

    if data.get("schema_version") != SCHEMA_VERSION:
        add(f"schema_version must be {SCHEMA_VERSION}.")
    if "exported_at" in data and not isinstance(data["exported_at"], str):
        add("exported_at must be a text.")

    ports, zoom, expression, bridge = data["ports"], data["zoom"], data["expression"], data["bridge"]
    synth = data.get("synth") or {}
    keyboard = bool(synth.get("keyboard"))
    if not isinstance(ports.get("zoom"), str) or not ports.get("zoom"):
        add("ports: the name of the pedal's port is missing.")
    if not isinstance(ports.get("chocolate"), str):
        add("ports: chocolate must be a text (empty if there is no expression controller).")
    if not ports.get("chocolate") and not keyboard:
        add("Neither an expression controller nor a keyboard is set up.")
    if not _is_int(zoom.get("device_id"), 0, 0x7F):
        add("zoom: device_id must be a number from 0 to 127.")
    if not isinstance(zoom.get("firmware"), str):
        add("zoom: firmware must be a text.")

    if ports.get("chocolate"):                             # only then is the pedal's CC needed
        if not _is_int(expression.get("cc"), 0, 127):
            add("expression: cc must be a number from 0 to 127.")
        if not _is_int(expression.get("channel"), 1, 16):
            add("expression: channel must be a number from 1 to 16.")
    low, high = expression.get("min"), expression.get("max")
    if not (_is_int(low, 0, 127) and _is_int(high, 0, 127) and low < high):
        add("expression: min and max must be numbers from 0 to 127, with min below max.")
    if not isinstance(expression.get("invert"), bool):
        add("expression: invert must be true or false.")

    interval = bridge.get("min_interval_ms")
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not 1 <= interval <= 1000:
        add("bridge: min_interval_ms must be a number from 1 to 1000.")
    if not _is_int(bridge.get("deadband", 0), 0, 127):
        add("bridge: deadband must be a number from 0 to 127.")
    if not isinstance(bridge.get("passthrough", False), bool):
        add("bridge: passthrough must be true or false.")

    if keyboard:
        if not isinstance(synth.get("keyboard"), str):
            add("synth: keyboard must be a text.")
        if not _is_int(synth.get("channel", 0), 0, 16):
            add("synth: channel must be a number from 0 to 16 (0 = any).")
        if not _is_int(synth.get("effect_id"), 1, 0xFFFFFFFF):
            add("synth: effect_id must be the id of the KeySynth effect.")
        note_low, note_high = synth.get("low_note", 0), synth.get("high_note", 127)
        if not (_is_int(note_low, 0, 127) and _is_int(note_high, 0, 127) and note_low <= note_high):
            add("synth: low_note and high_note must be notes from 0 to 127, with low_note ≤ high_note.")
        if not isinstance(synth.get("sustain", True), bool):
            add("synth: sustain must be true or false.")
        bend = synth.get("bend_range", 2)
        if isinstance(bend, bool) or not isinstance(bend, (int, float)) or not 0 <= bend <= MAX_BEND_RANGE:
            add(f"synth: bend_range must be a number from 0 to {MAX_BEND_RANGE}.")

    learned = data.get("learned", {})
    ranges = {}                                            # (preset, slot, param) → (min, max)
    if not isinstance(learned, dict):
        add("learned must list ranges per preset.")
        learned = {}
    for key, entries in learned.items():
        patch = _preset(key)
        if patch is None or not isinstance(entries, list):
            add(f"learned: “{key}” is not a preset with a list of ranges.")
            continue
        for entry in entries:
            if not (isinstance(entry, dict) and set(entry) == {"slot", "param", "min", "max"}
                    and _is_int(entry["slot"], 0, 0x7F) and _is_int(entry["param"], zs.MIN_PARAM, 0x7F)
                    and zs.is_effect_param(entry["slot"], entry["param"])
                    and _is_int(entry["min"], 0, UNLEARNED_MAX) and _is_int(entry["max"], entry["min"], UNLEARNED_MAX)):
                add(f"learned: preset {key} has an entry that is not a valid range.")
                continue
            ranges[(patch, entry["slot"], entry["param"])] = (entry["min"], entry["max"])

    mappings = data.get("mappings")
    if not isinstance(mappings, dict):
        add("mappings must list the assignments per preset (an empty one is written as {}).")
        mappings = {}
    for key, entries in mappings.items():
        patch = _preset(key)
        if patch is None or not isinstance(entries, list) or not 1 <= len(entries) <= MAX_TARGETS:
            add(f"mappings: “{key}” must be a preset with 1 to {MAX_TARGETS} parameters.")
            continue
        seen = set()
        for entry in entries:
            if not (isinstance(entry, dict)
                    and {"slot", "param", "min", "max"} <= set(entry) <= {"slot", "param", "min", "max", "invert", "curve", "name"}
                    and all(_is_int(entry[field], 0, UNLEARNED_MAX) for field in ("slot", "param", "min", "max"))):
                add(f"mappings: preset {key} has an entry that is not a valid assignment.")
                continue
            place = (entry["slot"], entry["param"])
            where = f"preset {key}, effect {entry['slot'] + 1}, parameter {entry['param'] - 1}"
            if place in seen:
                add(f"mappings: {where} is assigned twice.")
            seen.add(place)
            limits = ranges.get((patch,) + place)
            if limits is None:                             # not learned: the fixed grid only
                if not (entry["slot"] < MAX_EFFECTS and zs.MIN_PARAM <= entry["param"] < zs.MIN_PARAM + MAX_PARAMS):
                    add(f"mappings: {where} is outside effects 1–{MAX_EFFECTS} / parameters 1–{MAX_PARAMS} "
                        "and has no learned range.")
                elif not entry["min"] <= entry["max"]:
                    add(f"mappings: {where} has min above max.")
            elif not limits[0] <= entry["min"] <= entry["max"] <= limits[1]:
                add(f"mappings: {where} leaves its learned range {limits[0]}–{limits[1]}.")
            if not isinstance(entry.get("invert", False), bool):
                add(f"mappings: {where}: invert must be true or false.")
            if entry.get("curve", "linear") not in CURVES:
                add(f"mappings: {where}: unknown curve.")
            if not isinstance(entry.get("name", ""), str) or len(entry.get("name", "")) > 40:
                add(f"mappings: {where}: the name must be a text of at most 40 characters.")

    approvals = data.get("approvals")
    if not (isinstance(approvals, dict) and set(approvals) <= {"backup_confirmed", "messages"}
            and isinstance(approvals.get("messages"), dict)):
        add("approvals must hold backup_confirmed and the approved message kinds.")
    else:
        if not approvals.get("backup_confirmed"):
            add("approvals: the backup of the patches has not been confirmed (python probe.py approve).")
        for kind in approvals["messages"]:
            if kind not in zs.KINDS:
                add(f"approvals: “{kind}” is not a message kind of the bridge.")
        needed = REQUIRED_KINDS + (SYNTH_KINDS if keyboard else ())
        missing = [kind for kind in needed if not approvals["messages"].get(kind)]
        if missing:
            add(f"approvals: not approved: {', '.join(missing)} (python probe.py approve).")

    controls = data.get("controls", {})
    if not isinstance(controls, dict):
        add("controls must list a controller number per name.")
    else:
        for name, number in controls.items():
            if name not in zs.SYNTH_CONTROLS:
                add(f"controls: “{name}” is nothing a controller can set on the KeySynth.")
            elif not _is_int(number, 0, 127) or number in RESERVED_CONTROLS:
                add(f"controls: {name} needs a controller number from 0 to 127 that is not reserved.")
        numbers = [number for number in controls.values() if isinstance(number, int)]
        if len(set(numbers)) < len(numbers):
            add("controls: one controller is assigned twice.")
    return problems


class Approvals:
    """The approvals of a settings file: what zs.Approvals is on the computer, but read-only."""

    def __init__(self, data: dict):
        self._data = data

    @property
    def backup_confirmed(self) -> bool:
        return bool(self._data.get("backup_confirmed"))

    def is_approved(self, kind: str) -> bool:
        return bool(self._data.get("messages", {}).get(kind))


class Measurements:
    """The learned ranges of a settings file: what zs.Measurements is on the computer, but read-only."""

    def __init__(self, learned: dict):
        self.patches = {preset: {f"{entry['slot']}/{entry['param']}": dict(entry) for entry in entries}
                        for preset, entries in learned.items()}

    def get(self, patch: tuple, slot: int, param: int) -> Optional[dict]:
        return self.patches.get(f"{patch[0]}/{patch[1]}", {}).get(f"{slot}/{param}")

    def for_patch(self, patch: tuple) -> list:
        return list(self.patches.get(f"{patch[0]}/{patch[1]}", {}).values())


def build(config: dict, learned_patches: dict, approvals: dict, controls: dict, exported_at: str) -> dict:
    """The settings file made from what the computer keeps in config.yaml, measurements.json,
    approvals.json and controls.json. Of the learned ranges only the limits go in."""
    settings = {"schema_version": SCHEMA_VERSION, "exported_at": exported_at}
    settings.update({key: value for key, value in config.items() if key != "mappings"})
    settings["mappings"] = config.get("mappings") or {}
    settings["learned"] = {
        preset: [{field: entry[field] for field in ("slot", "param", "min", "max")}
                 for entry in sorted(entries.values(), key=lambda entry: (entry["slot"], entry["param"]))]
        for preset, entries in sorted(learned_patches.items())}
    settings["approvals"] = {"backup_confirmed": approvals.get("backup_confirmed"),
                             "messages": dict(approvals.get("messages") or {})}
    settings["controls"] = dict(controls)
    return settings


def dump(settings: dict) -> str:
    return ("# Settings of the Expression Bridge for a computer without screen (Raspberry Pi).\n"
            "# Written by export.py - change the settings on the computer you set the bridge up on\n"
            "# and export again, rather than editing this file.\n"
            + yaml.safe_dump(settings, sort_keys=False, allow_unicode=True))


def load(path: Path) -> tuple:
    """(settings, problems) of one file. A file that is missing or cannot be read is a problem too."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        return None, [f"{Path(path).name} cannot be read: {error.strerror or error}."]
    except (yaml.YAMLError, UnicodeDecodeError):
        return None, [f"{Path(path).name} is not a readable settings file."]
    problems = validate(data)
    return (None if problems else data), problems


def load_with_fallback(folder: Path) -> tuple:
    """The settings to run with: the current file, or the previous one if the current one is
    missing or invalid. Returns (settings or None, "current" / "previous" / None, what was wrong)."""
    current, problems = load(Path(folder) / FILE_NAME)
    if current is not None:
        return current, "current", []
    previous, older = load(Path(folder) / PREVIOUS_NAME)
    if previous is not None:
        return previous, "previous", problems
    return None, None, problems + older
