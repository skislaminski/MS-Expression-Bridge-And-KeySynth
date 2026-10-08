#!/usr/bin/env python3
"""Writes the settings for a bridge on a Raspberry Pi onto the Pi's SD card.

  python export.py                write to the Pi's boot partition (/Volumes/bootfs) and eject it
  python export.py --to FOLDER    write into another folder instead; nothing is ejected
  python export.py --check        only say whether the settings are complete and valid

The Pi has no screen, so everything is set up on this computer: ports, assignments, learned
ranges, approvals, the keyboard's controllers and the arpeggiator. This script gathers them from
config.yaml, measurements.json, approvals.json and controls.json into one file, checks it with the
same rules the Pi applies, and writes it as expression-bridge.yaml. A file that is already there is
kept as expression-bridge.prev.yaml; the Pi falls back to it if the new one turns out to be
unusable. The file holds values only, no code and no SysEx bytes.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import yaml

import bridge
import config_schema as cs
import zoom_sysex as zs

DEFAULT_TARGET = Path("/Volumes/bootfs")       # how macOS shows the boot partition of Raspberry Pi OS
BOOT_MARKER = "config.txt"                     # every Raspberry Pi boot partition has it


def gather() -> dict:
    """The settings file as a dictionary, from the files of this installation."""
    if not bridge.CONFIG.exists():
        sys.exit("config.yaml is missing – set the bridge up first (README.md, “First-time setup”).")
    config = yaml.safe_load(bridge.CONFIG.read_text(encoding="utf-8"))
    approvals = {"backup_confirmed": None, "messages": {}}
    if bridge.APPROVALS.exists():
        approvals.update(json.loads(bridge.APPROVALS.read_text(encoding="utf-8")))
    has_keyboard = bool((config.get("synth") or {}).get("keyboard"))
    controls = bridge.read_controls(bridge.CONTROLS) if has_keyboard else {}
    arp = bridge.read_arp(bridge.CONTROLS) if has_keyboard else None
    return cs.build(config, zs.Measurements(bridge.MEASUREMENTS).patches, approvals, controls,
                    time.strftime("%Y-%m-%d %H:%M:%S"), arp)


def write(settings: dict, folder: Path) -> Path:
    """Writes the file into the folder, keeping the one that was there, and reads it back."""
    target, previous = folder / cs.FILE_NAME, folder / cs.PREVIOUS_NAME
    if target.exists():
        target.replace(previous)
    target.write_text(cs.dump(settings), encoding="utf-8")
    written, problems = cs.load(target)
    if problems or written != settings:
        sys.exit(f"Reading {target} back did not give what was written – do not use this card as it is."
                 + "".join(f"\n  - {problem}" for problem in problems))
    return target


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--to", metavar="FOLDER", type=Path, help="write into this folder instead of the Pi's SD card")
    parser.add_argument("--check", action="store_true", help="only check the settings, write nothing")
    args = parser.parse_args(argv)

    zs.tolerant_console()
    settings = gather()
    problems = cs.validate(settings)
    if problems:
        sys.exit("Not exported, the settings are not complete:" + "".join(f"\n  - {problem}" for problem in problems))
    summary = (f"{len(settings['mappings'])} preset(s) with assignments, "
               f"{sum(len(entries) for entries in settings['learned'].values())} learned range(s), "
               f"{len(settings['controls'])} keyboard controller(s)")
    if args.check:
        print(f"The settings are complete and valid: {summary}.")
        return

    folder = args.to or DEFAULT_TARGET
    if not folder.is_dir():
        sys.exit(f"{folder} is not there. Put the Pi's SD card into this computer"
                 + ("" if args.to else ", or name another folder with --to") + ".")
    if args.to is None and not (folder / BOOT_MARKER).exists():
        sys.exit(f"{folder} does not look like the boot partition of a Raspberry Pi (no {BOOT_MARKER}) – nothing written.")
    target = write(settings, folder)
    print(f"Written: {target} ({summary}).")
    if args.to is None and sys.platform == "darwin":
        ejected = subprocess.run(["diskutil", "eject", str(folder)], capture_output=True, text=True)
        print("The card is ejected; you can take it out." if ejected.returncode == 0
              else f"The card could not be ejected ({ejected.stderr.strip() or ejected.stdout.strip()}) – eject it in Finder.")


if __name__ == "__main__":
    main()
