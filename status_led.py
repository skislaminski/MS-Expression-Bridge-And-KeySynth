"""The status light of a bridge without screen: the green activity LED of a Raspberry Pi.

    waiting    slow blinking      the bridge runs and waits for the pedal or a controller
    ready      steady             settings loaded, devices found, pedal identified
    fallback   two short flashes  ready, but with the previous settings file
    safe       fast blinking      nothing is sent: no usable settings, or the wrong pedal

The LED is driven through the kernel's files under /sys/class/leds. Where there is no such LED
(any other computer) or it may not be written, this does nothing. On exit the LED gets back the
job it had before (usually showing SD card activity).
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Optional

LEDS = Path("/sys/class/leds")
NAMES = ("ACT", "led0")            # the activity LED, as newer and older systems call it
PATTERNS = {                       # (on or off, seconds), repeated
    "waiting": ((1, 0.5), (0, 0.5)),
    "ready": ((1, 1.0),),
    "fallback": ((1, 0.12), (0, 0.12), (1, 0.12), (0, 1.0)),
    "safe": ((1, 0.1), (0, 0.1)),
}


class StatusLed:
    def __init__(self, folder: Optional[Path] = None):
        self.folder = folder
        self.state = "waiting"
        self.problem: Optional[str] = None      # why the LED is not driven, if it was found
        self._original: Optional[str] = None    # the trigger the LED had when we took it over
        self._stop = threading.Event()
        self._wake = threading.Event()          # the state has changed: start its pattern at once
        self._thread: Optional[threading.Thread] = None
        if folder is None:
            return
        try:
            chosen = re.search(r"\[(\S+)\]", (folder / "trigger").read_text())
            self._original = chosen.group(1) if chosen else None
            (folder / "trigger").write_text("none")
            self._write(0)
        except OSError as error:
            self.folder, self.problem = None, f"{folder}: {error.strerror or error}"
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @classmethod
    def find(cls, leds: Path = LEDS) -> "StatusLed":
        """The activity LED of this computer, or a stand-in that does nothing."""
        for name in NAMES:
            if (leds / name / "brightness").exists():
                return cls(leds / name)
        return cls(None)

    def show(self, state: str) -> None:
        if state not in PATTERNS:
            raise ValueError(state)
        if state != self.state:
            self.state = state
            self._wake.set()

    def _write(self, level: int) -> None:
        (self.folder / "brightness").write_text(str(level))

    def _run(self) -> None:
        while not self._stop.is_set():
            for level, seconds in PATTERNS[self.state]:
                try:
                    self._write(level)
                except OSError:
                    return                      # the LED is gone or no longer ours: leave it alone
                changed = self._wake.wait(seconds)
                if self._stop.is_set():
                    return
                if changed:
                    self._wake.clear()
                    break

    def close(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._wake.set()
        self._thread.join(2)
        self._thread = None
        try:
            (self.folder / "trigger").write_text(self._original or "none")
        except OSError:
            pass
