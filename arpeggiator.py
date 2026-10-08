"""The arpeggiator of the keyboard synth: which note sounds at each step, and its settings.

Note logic only, no timing: bridge.py runs the clock (its own tempo, or the MIDI clock arriving at
the keyboard's port) and asks next_note() at every step. The settings, as kept in controls.json and
in the settings file of a bridge without screen (an entry left out has its default):

    mode     up, down, updown, played (in the order the keys were pressed) or random
    rate     the length of a step: 1/4, 1/8, 1/8T, 1/16, 1/16T or 1/32
    octaves  1–4: the chord is played this many octaves upwards
    gate     5–100: how much of a step a note sounds, in percent; 100 = tied to the next note
    latch    true: the chord keeps playing after the keys are released, until a new one is played
    clock    internal (at `tempo`) or midi (the MIDI clock arriving at the keyboard's port)
    tempo    40–300 beats per minute, for the internal clock

Whether it plays is not a setting: it is switched on in the interface or with a controller of the
keyboard (SWITCH), and it is off whenever the bridge starts.
"""
from __future__ import annotations

import random
from typing import Optional

MODES = ("up", "down", "updown", "played", "random")
RATES = {"1/4": 24, "1/8": 12, "1/8T": 8, "1/16": 6, "1/16T": 4, "1/32": 3}   # MIDI clocks per step (24 per beat)
CLOCKS = ("internal", "midi")
DEFAULTS = {"mode": "up", "rate": "1/16", "octaves": 1, "gate": 50, "latch": False, "clock": "internal",
            "tempo": 120}
MAX_OCTAVES = 4
MIN_GATE, MAX_GATE = 5, 100
MIN_TEMPO, MAX_TEMPO = 40, 300
SWITCH = "Arp"          # the name under which a controller of the keyboard switches it on (64–127) and off


def _is_int(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def problems(settings) -> list:
    """What is wrong with arpeggiator settings (all of them or some), as sentences; empty if nothing."""
    if not isinstance(settings, dict):
        return ["arp must be a section with settings."]
    found = [f"arp: unknown setting “{key}”." for key in settings if key not in DEFAULTS]
    checks = {
        "mode": (lambda value: value in MODES, f"mode must be one of {', '.join(MODES)}"),
        "rate": (lambda value: value in RATES, f"rate must be one of {', '.join(RATES)}"),
        "octaves": (lambda value: _is_int(value, 1, MAX_OCTAVES), f"octaves must be a number from 1 to {MAX_OCTAVES}"),
        "gate": (lambda value: _is_int(value, MIN_GATE, MAX_GATE),
                 f"gate must be a number from {MIN_GATE} to {MAX_GATE} (percent)"),
        "latch": (lambda value: isinstance(value, bool), "latch must be true or false"),
        "clock": (lambda value: value in CLOCKS, f"clock must be {' or '.join(CLOCKS)}"),
        "tempo": (lambda value: isinstance(value, (int, float)) and not isinstance(value, bool)
                  and MIN_TEMPO <= value <= MAX_TEMPO, f"tempo must be a number from {MIN_TEMPO} to {MAX_TEMPO}"),
    }
    for key, (valid, sentence) in checks.items():
        if key in settings and not valid(settings[key]):
            found.append(f"arp: {sentence}.")
    return found


def read(stored) -> dict:
    """The settings to play with: the defaults, overlaid by every stored entry that is valid."""
    settings = dict(DEFAULTS)
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key in DEFAULTS and not problems({key: value}):
                settings[key] = value
    return settings


def changed(settings: dict) -> dict:
    """The entries that differ from the defaults: what is worth storing."""
    return {key: value for key, value in settings.items() if value != DEFAULTS.get(key)}


class Arpeggiator:
    """Which note sounds at the next step. Up and down continue from the note played last, so a
    chord whose keys arrive a few milliseconds apart, or that changes while it plays, does not
    start over or repeat a note."""

    def __init__(self, settings: dict, high_note: int = 127, seed: Optional[int] = None):
        self.settings = settings            # shared with the bridge: a change applies at the next step
        self.high_note = high_note          # notes moved up by `octaves` stop here
        self.latched: list = []             # the keys pressed since all keys were last up, in that order
        self.random = random.Random(seed)
        self.reset()

    def reset(self) -> None:
        """The next chord starts from its beginning."""
        self.last: Optional[int] = None
        self.index = 0
        self.rising = True

    def pressed(self, note: int, alone: bool) -> None:
        """A key was pressed; alone: no other key was down. For Latch, which keeps that chord."""
        if alone:
            self.latched = []
        if note not in self.latched:
            self.latched.append(note)

    def clear(self) -> None:
        """All notes off: nothing is kept for Latch either."""
        self.latched = []
        self.reset()

    def chord(self, played: list) -> list:
        """The notes to play: the keys that are down (or held by the sustain pedal) in the order
        they were pressed, or with Latch the chord played last."""
        return list(self.latched) if self.settings["latch"] and self.latched else list(played)

    def next_note(self, played: list) -> Optional[int]:
        """The note for the next step; None when there is nothing to play (then it starts over)."""
        chord = self.chord(played)
        if not chord:
            self.reset()
            return None
        notes = [note + 12 * octave for octave in range(self.settings["octaves"]) for note in chord
                 if note + 12 * octave <= self.high_note]
        mode = self.settings["mode"]
        if mode == "played":
            note = notes[self.index % len(notes)]
            self.index += 1
        elif mode == "random":
            note = self.random.choice([other for other in notes if other != self.last] or notes)
        else:
            ladder = sorted(set(notes))
            above = [other for other in ladder if self.last is None or other > self.last]
            below = [other for other in ladder if self.last is not None and other < self.last]
            if mode == "up":
                note = above[0] if above else ladder[0]
            elif mode == "down":
                note = below[-1] if below else ladder[-1]
            elif self.last is None:             # updown: from the bottom to the top and back,
                note, self.rising = ladder[0], True     # without playing the turning notes twice
            elif self.rising:
                if above:
                    note = above[0]
                else:
                    note, self.rising = (below[-1] if below else ladder[0]), False
            elif below:
                note = below[-1]
            else:
                note, self.rising = (above[0] if above else ladder[-1]), True
        self.last = note
        return note
