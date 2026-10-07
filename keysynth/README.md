# KeySynth for the Zoom MS-60B+

A two-oscillator mono synth voice that runs **on the pedal itself** as a custom effect and is
played from a MIDI keyboard through the [Expression Bridge](../README.md#keyboard-synth). The
input signal passes through unchanged; the synth is added to it.

Its set of features follows **SYNTHESIS SYNx2** by Leemuzhko, a synth effect for the older Zoom
MS pedals (see [Credits](#credits-and-license)).

| File | What it is |
|---|---|
| `KEYSYNTH.ZD2` | The effect, version 0.21, ready to install on an **MS-60B+** |
| `KEYSYNTH.ZIC` | Its icon; the installer wants it next to the effect |
| `source/` | Everything needed to rebuild it: kernel, tables, desktop tests, tools |

SHA-256 of `KEYSYNTH.ZD2`: `c54edc5a5ac5d1f2f5d6ae5867c2ac3bb58820e876284f5e11a555687667243d`

> **Read this first.** A custom effect is code that runs inside your pedal. A faulty effect in a
> *saved* patch, or a file transfer that is interrupted, **can leave the pedal unable to start,
> and there is no dependable way back.** This effect was built with
> [stomphacks](https://github.com/thammer/stomphacks) and tested on **one** MS-60B+ with
> firmware **1.20**, nothing else. It is unofficial, not affiliated with or supported by Zoom,
> and you use it at your own risk. Read stomphacks' `SAFETY.md` completely before you connect
> anything.

## What it does

- Two oscillators (saw, square, triangle, sine; saw and square are band-limited), the second
  one −24 to +24 semitones away and detunable
- Attack and release, glide between tied notes, one LFO for vibrato or tremolo
- Monophonic. Pitch and gate come in through the first knob, **Key**, which the bridge writes
  for every note and for the pitch wheel

| Page | Knob | Range | Meaning |
|---|---|---|---|
| 1 | Key | 0–1000 | 0 = gate off; *n* = (*n* − 1) × 10 cents above C0. MIDI note *m* is 10 × (*m* − 12) + 1. Playable: MIDI 12–111 (C0 to D#8) |
| 1 | Level | 0–100 | Level of the synth; the input is not affected |
| 1 | Wave1 | Saw, Sqr, Tri, Sine | Waveform of oscillator 1 |
| 1 | Wave2 | Off, Saw, Sqr, Tri, Sine | Waveform of oscillator 2 |
| 2 | Pitch | −24 … +24 | Oscillator 2 against oscillator 1, in semitones |
| 2 | Dtune | 0–100 | Fine detune of oscillator 2, up to +50 cents |
| 2 | Mix | 0–100 | 0 = oscillator 1 only, 100 = oscillator 2 only |
| 2 | Glide | 0–100 | 0 = off; otherwise about 60 ms to 2 s. Only tied notes slide; a note after a gap starts on pitch |
| 3 | Atk | 0–100 | Attack, 1 ms to 3 s |
| 3 | Rel | 0–100 | Release, 1 ms to 3 s |
| 3 | LFO | Vib50 … Vib1, Off, Trm1 … Trm50 | Kind and depth in one knob: vibrato up to ±100 cents, or tremolo down to silence |
| 3 | Rate | 0–100 | LFO speed, 0.1 Hz to 20 Hz |

How the pitch moves: a note after a gap starts on pitch. A move of the Key knob below one
semitone counts as a bend and is smoothed over about 20 ms, so the 10-cent steps do not show.
A larger move while the gate is open is a tied note; with Glide on, the pitch slides there.

On the MS-60B+ the effect appears in the category **Pitch shift** as “KEY SYNTH”. It declares a
DSP load of 52 (the pedal adds up the declared loads of a patch and refuses effects beyond its
budget).

## Installing it

Installed and removed here with the tools of **stomphacks**, which check the file, switch Auto
Save off, read everything back and refuse to touch anything that is not a DIY effect. Set
stomphacks up as its README says (it needs Python, not the compiler), then, from its folder and
with every other MIDI program closed:

```bash
.venv/bin/python3 tools-pedal/safe_connect.py session/                  # Auto Save off, confirmed by the pedal
.venv/bin/python3 tools-pedal/backup_pedal.py backup/                   # full backup; keep a copy somewhere else
.venv/bin/python3 tools-pedal/pedal_diy.py classify /path/to/KEYSYNTH.ZD2
.venv/bin/python3 tools-pedal/pedal_diy.py install  /path/to/KEYSYNTH.ZD2 --dry-run
.venv/bin/python3 tools-pedal/pedal_diy.py install  /path/to/KEYSYNTH.ZD2
.venv/bin/python3 tools-pedal/readback.py KEYSYNTH.ZD2 session/KEYSYNTH-back.ZD2
cmp session/KEYSYNTH-back.ZD2 /path/to/KEYSYNTH.ZD2                     # no output = identical
```

- If a transfer hangs or is interrupted: **do not switch the pedal off.** stomphacks' `SAFETY.md`
  says what to do then.
- To remove it: take it out of every patch first (and save those patches), then
  `pedal_diy.py uninstall /path/to/KEYSYNTH.ZD2`. `readback.py` must then report it as absent.
- To replace it with another version: uninstall first, then install.
- Used here: stomphacks at commit `ebd5ced`, zoom-zt2 at `d9685f8`. Other ways of installing ZD2
  files were not tried.

Then add the effect to a patch on the pedal, set up the `synth:` section of the bridge's
`config.yaml` and play. Turning **Key** by hand is a quick test without a keyboard: a tone that
rises in fine steps.

### Saving it in a patch

stomphacks' rule is never to save a patch that contains an experimental effect: if such an
effect crashes while the pedal starts, the pedal crashes on every start. On the one pedal this
was developed on, versions of this effect were saved in a patch and the pedal started with that
patch without trouble. That is one pedal and one firmware. If you save it, make a fresh backup
first, and be aware of what you are risking.

### Other MS Plus pedals

`KEYSYNTH.ZD2` carries the header of an MS-60B+ effect (target field `0x00a0`, group name
“PITCH SHIFT”). stomphacks itself builds for the MS-70CDR+ with other values there. Whether this
file loads on any other pedal is unknown; do not try it without reading up on the differences.
For another model, build from `source/` and leave out the last step.

## What was tested (MS-60B+, firmware 1.20)

- Installed, read back identical, removed and replaced several times with the stomphacks tools.
- Version 0.10 (same oscillators and envelope, simpler Key knob): 71 minutes of sixteenth notes
  from a sequencer, 33,600 notes, every message confirmed by the pedal; note to sound about
  18 ms (median, through the bridge on a Mac); two square waves in a patch whose declared loads
  add up to 266.
- Version 0.21: played from a keyboard for well over an hour with pitch wheel, mod wheel
  on the LFO depth and knobs assigned to several parameters; the long value lists (Pitch with
  49 entries, LFO with 101) display correctly; knob settings survive chain edits.
- While you operate the pedal itself (turn a knob, browse effects), it answers parameter
  messages late for a moment, and a note can hang for a fraction of a second.

## Building it yourself

`source/` mirrors the layout it was developed in. You need a stomphacks clone at
`source/stomphacks/` (set up as its README says) and TI's C6000 code generation tools 8.5.0.LTS,
which stomphacks uses as its compiler. From `source/`:

```bash
stomphacks/.venv/bin/python3 tools-local/gen_tables.py                       # lookup tables
(cd stomphacks && ZOOM_TI_CGT=/path/to/ti-cgt-c6000_8.5.0.LTS \
    .venv/bin/python3 tools/zd2_make_effect.py ../effects/keysynth/manifest.json)
sh host/run_tests.sh                                                          # desktop tests, needs clang
stomphacks/.venv/bin/python3 tools-local/adapt_ms60b.py \
    effects/keysynth/build/KEYSYNTH.ZD2 /your/backup/files/B_OCTAVE.ZD2       # MS-60B+ header
```

- `effects/keysynth/keysynth.c` is the kernel: it runs once per block of 16 samples, without
  function calls, division, stack or static data, as stomphacks requires. Read stomphacks'
  `docs/building-effects.md` before changing it, and install only a build whose `REPORT.md` has
  no failed check.
- `host/` runs the same kernel source on the desktop: about 6.5 million checks (silence when
  uninitialised, random knob changes, every Key value, clicks, bypass, spectra, glide and bend)
  and a set of WAV files to listen to in `host/out/`.
- `tools-local/adapt_ms60b.py` copies three header fields (18 bytes) from a stock effect of the
  same category **out of your own pedal backup** and recomputes the checksum. The result lands
  in `effects/keysynth/ms60b/`. No file from Zoom is part of this repository.
- `tools-local/kernel_cycles.py` estimates the cycles per block from the disassembly, to choose
  the declared DSP load.

## Versions

- **0.21** LFO knob with 50 steps each way; vibrato up to ±100 cents.
- **0.20** Key knob 0–1000 (10-cent steps, so the pitch wheel fits in), Glide, the second
  oscillator's pitch shown as −24 … +24, LFO kind and depth in one knob.
- **0.10** First version: Key 0–128 (one step per note).

The bridge in this repository speaks the Key format of 0.20 and later, and the LFO scale of 0.21.

## Credits and license

**Modelled on [SYNTHESIS SYNx2](https://github.com/Leemuzhko/Zoom-ZDL-FX/blob/main/zdl/sfx/synthesis/README.md)**
by Leemuzhko (Zoom-ZDL-FX), a synth effect for the older Zoom MS pedals. KeySynth takes its set
of features from there: two oscillators, an envelope, an LFO for tremolo or vibrato, and pitch
and gate as parameters that a controller writes. Only the idea was taken. SYNx2 is published
as a binary in the older pedals' format, without source, and none of its code is in here.

Built with [stomphacks](https://github.com/thammer/stomphacks) by Thomas Hammer, which also
provided the kernel skeleton, and with [zoom-zt2](https://github.com/mungewell/zoom-zt2).

MIT, see [LICENSE](LICENSE).
