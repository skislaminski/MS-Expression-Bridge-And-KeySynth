# Expression Bridge for the Zoom MS-60B+

Control a **Zoom MS-60B+** from MIDI controllers, in real time:

- an **expression pedal** moves effect parameters, and
- a **MIDI keyboard** plays [KeySynth](keysynth/README.md), a synth voice that runs on the pedal
  as a custom effect (optional, the effect is included), or **KeyPoly**, its four-voice sibling
  (see [Four voices: KeyPoly](#four-voices-keypoly); the effect is not part of this repository yet).

The MS-60B+ officially accepts only patch changes over USB MIDI. Parameter changes need
unofficial SysEx messages, which a plain MIDI controller cannot send. This program sits in
between: it receives the controllers' messages and translates them into the pedal's SysEx.

```
expression pedal → MIDI controller (sends CC) ─┐
                                               ├→ this bridge (computer, USB host) → MS-60B+
MIDI keyboard (notes, wheels, knobs) ──────────┘
```

> **Unofficial and at your own risk.** The SysEx protocol was reverse engineered and is not
> documented or supported by Zoom. This project is not affiliated with Zoom or M-Vave.
> Read [Before you start](#before-you-start) and [Safety](#safety).

## Status

Developed and tested with exactly this setup:

- Zoom MS-60B+, firmware 1.20
- M-Vave Chocolate Plus (USB mode) with an expression pedal on its TRS input
- Native Instruments Komplete Kontrol A61 as the keyboard, with KeySynth 0.21 on the pedal
- macOS on Apple Silicon, Python 3.9

Not tested: other MS Plus pedals (MS-50G+, MS-70CDR+, MS-200D+), other firmware versions,
Windows, Linux. Other MIDI controllers should work if they send the expression pedal as a CC.
See [Installation](#installation) for all three systems.

## Features

- Browser interface on `http://127.0.0.1:8765`: live status, assignments, export and import
- Up to four parameters per preset, each with its own range, direction (inverted or not) and curve
- Assign any of effect 1–6 / parameter 1–12 directly, or let the pedal report a knob's exact
  range ("Learn parameter")
- Follows preset changes on the pedal; presets without an assignment are left alone
- Waits for missing devices and reconnects after replugging (tested with the controller)
- Optional: a MIDI keyboard plays the custom effect KeySynth (one voice) or KeyPoly (four voices)
  on the pedal, with pitch wheel, and with any wheel, knob or slider of the keyboard assigned to
  the synth's knobs by "Learn", see [Keyboard synth](#keyboard-synth)
- Optional: an arpeggiator for the keyboard synth, at its own tempo or to MIDI clock, see
  [Arpeggiator](#arpeggiator)
- Sends only message kinds you have approved, and logs every SysEx message sent and received

## Before you start

- **Back up your patches.** The bridge asks you to confirm this before it sends anything to
  the pedal.
- **Turn Auto Save off** in the settings of the MS-60B+. With Auto Save on, every pedal
  movement is written into the stored patch.
- **Mind the firmware.** Everything here was tested on an MS-60B+ with firmware 1.20 only.
  Other MS Plus pedals and other firmware versions may behave differently, and a firmware
  update can break the bridge.
- **The computer stays in the chain.** Controller and pedal are both USB devices, so both plug
  into the computer, and the bridge has to run while you play.
- **Close other software that talks to the pedal** (Zoom Explorer, editors, a DAW that has the
  MIDI ports open). On Windows a MIDI port can usually be opened by one program at a time.
- **Your controller must send the expression pedal as a MIDI CC over USB.** On the M-Vave
  Chocolate Plus: mode switch on U, and the expression CC assigned in the CubeSuite app. If no
  CC arrives over USB, users report that switching the Chocolate's Bluetooth off helps
  (hold B + C).
- **Effect and parameter numbers are not checked against the preset.** If you assign a number
  that does not exist there, the bridge stops sending after five unconfirmed messages, but how
  the pedal itself reacts has not been tested. Use "Learn parameter" when in doubt. If the
  pedal ever stops responding, disconnect its power and USB and start it again.
- **After changing the effects in a preset**, check its assignments: the numbers now point to
  other parameters, and ranges read with "Learn parameter" have to be learned again.
- The interface is reachable from the same computer only (`127.0.0.1`). Closing the browser
  tab does not stop the bridge.

## Installation

You need **Python 3.9 or newer** and this repository: click the green **Code** button →
**Download ZIP** and unpack it, or clone it.

### macOS

1. Open **Terminal** and go to the unpacked folder: type `cd ` (with the space), drag the
   folder into the Terminal window and press Enter.
2. Check that Python is there:

   ```bash
   python3 --version
   ```

   If macOS offers to install the command line developer tools, accept and repeat the command
   when it has finished. Alternatively install Python from [python.org](https://www.python.org/downloads/).
3. Create the environment and install the two MIDI libraries and the YAML reader into it:

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   ```

4. Optional, for starting by double-click later. A ZIP download loses the "executable" flag
   and macOS marks downloaded files as untrusted, so run this once:

   ```bash
   chmod +x "Start Bridge.command"
   xattr -d com.apple.quarantine "Start Bridge.command"
   ```

   The second command may answer that there is nothing to remove; that is fine.
5. Continue with [First-time setup](#first-time-setup).

### Windows

Nobody has run this on Windows yet, so expect rough edges and please report what you find.

1. Install Python from [python.org](https://www.python.org/downloads/). Version 3.11 or newer is
   recommended: older versions sleep in coarse steps on Windows (typically about 16 ms), which
   makes the bridge less responsive.
2. Open the unpacked folder in Explorer, type `cmd` into the address bar and press Enter. A
   terminal opens in that folder.
3. Create the environment:

   ```bat
   py -3 -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```

4. Continue with [First-time setup](#first-time-setup), writing `.venv\Scripts\python` wherever
   the steps say `.venv/bin/python`.

### Linux

Not tested either. The commands are the same as on macOS, without step 4.

- On Debian and Ubuntu, install the venv module first: `sudo apt install python3-venv`.
- If `pip` has to build python-rtmidi from source, it needs a C++ compiler and the ALSA
  development package (`libasound2-dev`).

## First-time setup

This is needed once per computer. Connect the controller and the MS-60B+ by USB first. The
values go into `config.yaml`, a plain text file you can edit with any text editor.

1. List the MIDI ports. On the first run this creates `config.yaml` from the example.

   ```bash
   .venv/bin/python probe.py ports
   ```

   Enter a unique part of each port name under `ports:` in `config.yaml`.

2. Identify the pedal. This is the first message sent to it: the script asks you to confirm
   that your patches are backed up and shows the message as hex before sending.

   ```bash
   .venv/bin/python probe.py identity
   ```

   Enter the reported `device_id` and `firmware` in `config.yaml`.

3. Measure the expression pedal. This sends nothing to the MS-60B+. (Only a keyboard, no
   expression controller? Leave `chocolate:` under `ports:` empty and skip this step.)

   ```bash
   .venv/bin/python probe.py cc
   ```

   Enter the reported `cc`, `channel`, `min`, `max` and `invert` under `expression:`.

4. Approve the remaining message kinds the bridge needs. `probe.py approve` without arguments
   lists their exact bytes.

   ```bash
   .venv/bin/python probe.py approve edit_enable edit_disable set_param query_program
   ```

## Starting the bridge

| System | Double-click | Or in the terminal |
|---|---|---|
| macOS | `Start Bridge.command`, afterwards the app it makes | `.venv/bin/python bridge.py --open` |
| Windows | `Start Bridge.bat` | `.venv\Scripts\python bridge.py --open` |
| Linux | – | `.venv/bin/python bridge.py --open` |

The interface opens in the browser at `http://127.0.0.1:8765`. If a device is missing, the
bridge waits for it and connects as soon as it appears.

**A clickable icon.** On macOS the first start with `Start Bridge.command` makes
`Expression Bridge.app` in the same folder: an app with its own icon that opens that script in
a Terminal window and nothing more. Double-click it from then on, or drag it into the Dock. It
is made on your computer because macOS blocks unsigned apps that come out of a download; if you
move the folder, start once with `Start Bridge.command` again and the app is made afresh. On
Windows, make a shortcut to `Start Bridge.bat` and give it `icon.ico` (Properties → Change
Icon).

Stop the bridge with **Ctrl+C** in its window: it then switches the pedal's edit mode off and
closes the MIDI ports. Closing the window on Windows ends it without that step, which had no
lasting effect in testing on macOS.

## Using the interface

At the top is the keyboard synth (only when it is set up, see [Keyboard synth](#keyboard-synth)),
below it everything about the expression pedal:

- **Add preset**: enter the preset number shown on the pedal and click "Add preset".
- **Parameter**: choose effect 1–6 (position in the chain) and parameter 1–12 (order on the pedal).
- **Heel / Toe**: the parameter's value at each end of the pedal's travel. Use them to limit
  the range (20 to 70) or to invert the direction (100 to 0). "Invert" swaps the two.
- **Curve**: linear, or changing early or late in the pedal's travel.
- **Learn parameter** (optional): select the preset on the pedal, click the button, turn the
  knob once from minimum to maximum, click "Apply". The pedal reports the effect, the
  parameter and its exact range.
- **Export / Import**: all presets or a single one as a JSON file, including learned ranges.


## Keyboard synth

Optional, and only useful with the custom effect **KeySynth** (version 0.21 or later) or its
four-voice sibling **KeyPoly** ([below](#four-voices-keypoly)) installed on the pedal. KeySynth is
a two-oscillator synth voice whose first knob, "Key", is gate and pitch in one number (0 = off,
n = 10-cent steps above C0). The effect, its source and how to install it are in
[`keysynth/`](keysynth/README.md); **read the warnings there first**, a custom effect can
make a pedal unusable. (There is also a build of the effect for the MS-50G+ and MS-70CDR+ in
there. It is untested, and so is the bridge with those pedals.) With the `synth:` section filled
in in `config.yaml`, a MIDI keyboard plays that effect:

- KeySynth is monophonic, last note wins; releasing it returns to a note that is still held.
  Note on with velocity 0 counts as note off, the sustain pedal (CC 64) holds notes, "all notes
  off" and stopping the bridge close the gate. The effect plays MIDI notes 12–111 (C0 to D#8).
- **Pitch wheel:** bends the sounding note, ±2 semitones unless `bend_range` says otherwise.
  Note and bend travel as one value, so a note always arrives with its pitch. While a note
  sounds the wheel is followed in steps of at most 90 cents per message; the effect smooths
  them.
- **Any other control of the keyboard** (mod wheel, knobs, sliders) can set one of the effect's
  other knobs. In the interface, under "Keyboard synth", click "Learn" next to a knob and move
  the control you want; "Clear" removes it. It works with any keyboard and any controller
  number, as long as the control sends absolute values 0–127 (endless encoders in relative
  mode do not). One control sets one knob. Nothing is assigned until you do it, not even the
  mod wheel. The assignments are kept in `controls.json`.
- **LFO depth from a wheel:** the effect's LFO knob is kind and depth in one (Vib50 … Vib1, Off,
  Trm1 … Trm50), so a control assigned to "LFO" has Off in the middle of its travel. "Vib" and
  "Trm" in the same list are the two halves of that knob: with the control at rest the LFO is
  off, at full travel the vibrato or tremolo is deepest, as with the mod wheel of a hardware
  synth.
- The bridge finds the effect itself: it asks the pedal for the current patch when it starts,
  after every preset change, and while a key is held and the effect is not there yet (once
  per second). No effect in the preset: the keyboard is ignored.
- Notes are sent at once, never spaced or merged; expression values wait their turn.
- The keyboard and the expression controller are both optional then: the bridge starts with
  either one, and the other may be plugged in or pulled while it runs.
- It needs one more approval, for the patch query:
  `.venv/bin/python probe.py approve query_patch`

Each confirmed note is printed with the time from key press to the pedal's acknowledgement
(bend steps are not, they would flood the terminal).

### Four voices: KeyPoly

KeyPoly is a four-voice synth effect for the pedal, built separately (it is not in this
repository yet, and **neither it nor this part of the bridge has run on a pedal**). It has four
Key knobs, Key1–Key4, each in KeySynth's format, and eight knobs the voices share: Level, Wave,
Cutoff, Reso, Atk, Rel, LFO, Rate. Without the bridge it can only be played by turning the four
Key knobs by hand; the bridge gives every note one of them:

- Enter KeyPoly's effect id (from its `manifest.json`) as `poly_effect_id` under `synth:` in
  `config.yaml`. `effect_id` (KeySynth) may stay; the bridge plays whichever of the two is in the
  preset, the first in the chain if both are.
- **A note keeps its voice.** Releasing a key closes its voice only; the others are not written
  at all. A chord goes out at once, one message per note.
- **A new note** takes the free voice that last played the same note (so one note never sounds
  on two voices), otherwise the voice that has been free longest, whose release has faded most.
- **A fifth note** takes the voice of the oldest note. Keys that are down beat notes the sustain
  pedal holds. If a newer key is released while the older one is still down, the older one gets a
  voice back, as with KeySynth.
- **Pitch wheel:** every sounding voice follows, in the same small steps as on KeySynth; the
  voices take turns, so a full bend of a four-note chord takes about four times as long.
- Key2–Key4 are left alone until a note needs them: the patch dump shows the first knob of an
  effect only, so their values are unknown to the bridge until it writes them. When the bridge
  stops it closes every voice it has played.
- Controllers learned for knobs both effects have (Level, Atk, Rel, LFO, Rate, Vib, Trm) set them
  on whichever effect is in the preset. The interface lists the knobs of that effect.
- **The knob ranges are assumed**, not read from KeyPoly: Key1–Key4 0–1000, Wave 0–3 (as KeySynth's
  Wave1), LFO 0–100 with Off in the middle (as KeySynth's), all others 0–100. They are in
  `zoom_sysex.py` (`POLY_KNOBS`); compare them with KeyPoly's `manifest.json` before you play it.

### Arpeggiator

The bridge can play the keys that are down one after another, on KeySynth and KeyPoly alike
("Arpeggiator" under "Keyboard synth" in the interface). **It has not run on a pedal yet.**

- **Mode:** up, down, up and down, as played (in the order the keys were pressed) or random.
  **Octaves:** 1–4. **Rate:** 1/4 to 1/32, triplets included. **Gate:** how much of a step a note
  sounds; at 100 % the notes are tied (with KeySynth's Glide they slide).
- **Latch** keeps a chord playing after you let go, until you play a new one. Notes the sustain
  pedal holds stay in the chord, too.
- **Clock:** its own tempo (40–300 BPM; the first key starts it at once), or **MIDI clock**: it
  follows the clock arriving at the keyboard's port, a step every so many clocks counted from Start,
  so it stays on the beat. Stop silences it and starts the pattern over, Continue picks up again; a
  clock that just stops silences it after half a second. The bridge listens to one keyboard port
  only: for a DAW's clock, route the keyboard through the DAW to a virtual port (IAC on macOS) and
  enter that port as `keyboard:`.
- **On and off** in the interface, or from the keyboard: learn "On/Off" for a button or pedal (on
  at values 64–127, like the sustain pedal). It is off whenever the bridge starts; its other
  settings are kept in `controls.json` and travel to the Pi with `export.py`.
- On KeyPoly every note takes the next voice, so the notes ring into each other. The pitch wheel
  bends the arpeggio. Its notes are not printed in the terminal.
- **Load:** a note and its gate-off per step, kept at least the minimum interval (10 ms) apart. At
  1/16 and 120 BPM that is 16 messages per second, as in the 71-minute test; 1/32 at 300 BPM would
  be 80 per second, more than has been tried on the pedal.

## On a Raspberry Pi, without screen (in progress)

The aim is a small box on the pedalboard instead of a computer: a Raspberry Pi with the
controllers and the pedal plugged in, starting the bridge when it is switched on. **This has
not run on a Pi yet.** What exists was written and tested against the simulated devices only:

- `export.py` gathers everything that was set up on this computer (ports, assignments, learned
  ranges, approvals, the keyboard's controllers) into one file, `expression-bridge.yaml`, checks
  it and writes it to the Pi's SD card. The file holds values only, no code and no SysEx bytes.

  ```bash
  .venv/bin/python export.py --check     # are the settings complete and valid?
  .venv/bin/python export.py             # write to the card's boot partition and eject it
  ```

- `bridge.py --settings FOLDER` runs from that file: no interface, nothing stored, every message
  logged to the console. A file that breaks a rule is not used; the bridge then takes the
  previous one (`expression-bridge.prev.yaml`), and if that is unusable too it sends nothing.
- The Pi's green activity LED is the display: slow blinking = waiting for a device, steady =
  ready, two short flashes = ready with the previous settings, fast blinking = nothing is sent
  (no usable settings, or a pedal other than the one the settings are for).
- `pi/expression-bridge.service` is the unit for starting it at boot.

Still to do, on the real device: installing, access to the LED, the read-only file system,
power for a bus-powered keyboard, and the tests after pulling the plug.

## Safety

- **Allowlist.** Every SysEx message is built in `zoom_sysex.py` and checked before sending.
  Only six message kinds exist: identity request, parameter edit enable/disable, set parameter,
  query current program, query current patch. Commands for firmware mode, factory reset, file
  access, overwriting patches, system settings and inserting, deleting or moving effects are
  explicitly refused.
- **Approvals.** Nothing is sent until you have confirmed a backup and approved each message
  kind once (`approvals.json`).
- **Learned ranges are strict.** A learned parameter is only ever set within the range the
  pedal reported for it.
- **Parameters that were not learned** are sent with the range you enter. Until the pedal has
  confirmed such a parameter once, it gets one message at a time; after five unconfirmed
  messages the bridge stops sending to it and the interface says so.
- **The keyboard synth writes the synth effect's knobs only.** Notes and bends are built with the
  parameter fixed to one of the four Key knobs (parameters 2–5, 0–1000); a controller writes the
  knob it was assigned to, within that knob's own range. All of it passes the allowlist only
  while the pedal's own patch dump shows KeySynth or KeyPoly in that slot, and only with the
  knobs of the one it shows. An expression assignment that points at a Key knob, or whose range
  is wider than the knob it points at, is not sent.
- **Not tested:** what the pedal does with an effect or parameter number that does not exist
  in the preset. The reference documentation mentions a related system command whose invalid
  values freeze the pedal, so the firmware does not validate everything.
- Parameter changes are not stored on the pedal as long as Auto Save is off.

## What was measured on the MS-60B+ (firmware 1.20)

The protocol reference is the README of [zoom-explorer](https://github.com/thammer/zoom-explorer),
written for the MS-50G+. These points were confirmed on the MS-60B+; all bytes are hex.

| What | Message | Observed |
|---|---|---|
| Identity request | `F0 7E 7F 06 01 F7` | Reply `F0 7E 00 06 02 52 6E 00 27 00 31 2E 32 30 F7`: device ID `6E`, model `27 00`, version "1.20" |
| Parameter edit enable / disable | `F0 52 00 6E 50 F7` / `… 51 F7` | Ack `F0 52 00 6E 00 00 F7` |
| Set parameter | `F0 52 00 6E 64 20 00 <slot> <param> <LSB> <MSB> 00 00 00 F7` | Ack `… 64 20 01 <slot> <param> <LSB> <MSB> …` after about 4–11 ms, **only if the value changed** |
| Query current program | `F0 52 00 6E 33 F7` | CC 0, CC 32, program change; also answered without edit enable |
| Query current patch | `F0 52 00 6E 64 13 F7` | Reply `… 64 12 01 <length LSB MSB> <patch, 7-bit packed> <CRC32, 5 bytes> F7`, 985 bytes in all for an 848-byte patch |

- Slots count from 0 (first effect in the chain), parameters from 2 (first knob). Per the
  zoom-explorer source, parameter 0 is effect on/off and 1 is the effect type; the bridge
  never sends those.
- With edit mode enabled the pedal reports knob movements as `64 20 00 <slot> <param> <LSB> <MSB> …`.
- On a preset change the pedal sends `64 26 00 00 <bank LSB MSB> <program LSB MSB>`, then
  `64 20 00 64 02 …` (tempo, per the reference), then CC 0, CC 32 and a program change.
- The number shown on the pedal is `bank × 10 + program + 1` (preset 095 = bank 9, program 4).
- The value is split into 7-bit groups, lowest first. Verified up to 451 (with the KeySynth's
  Key knob); values up to 127 sit in the LSB alone.
- The very first "set parameter" after connecting was once applied without an ack.
- The patch ("PTCF") lists the effect ids in chain order from offset 36, the effect count is at
  offset 12; the pedal has 6 effect slots. Read with zoom-zt2's `decode_preset.py` layout.
- 128 "set parameter" messages 26.8 ms apart were all acknowledged (measured with the
  KeySynth's Key knob); the ack came after 1–10 ms. In a 71-minute run, 67,200 messages
  (about 16 per second) were all acknowledged.
- Of two messages 1 ms apart the pedal acknowledges only the second. While its own knobs are
  turned or its menus are open, it answers late or not at all for 0.1 to 0.7 s.
- The Chocolate Plus sends about 30 CC messages per second. The bridge sends at most one
  SysEx per 10 ms, so with four parameters each one is updated about 25 times per second.

## Tests

The tests run the bridge, its interface and the setup script against a simulated pedal and
controller. They open no real MIDI port and need no hardware.

```bash
.venv/bin/python -m unittest
```

Run them after every change. They cover the allowlist, throttling, preset changes, replugging,
learning, saving, export and import, how parameters that were not learned are handled, the
keyboard synth (with one real patch dump of the pedal as a fixture), KeyPoly's voices and the
arpeggiator. They take about two
minutes.
What they cannot tell you is how a real pedal reacts, so check changes to the messages on
hardware as well.

## Files

| File | Purpose |
|---|---|
| `bridge.py` | The bridge and its main loop |
| `ui.py`, `ui.html` | The browser interface (local HTTP server, reachable from this computer only) |
| `zoom_sysex.py` | SysEx builders, allowlist, parsers, approvals, learned ranges |
| `arpeggiator.py` | The arpeggiator's notes and settings (bridge.py runs its clock) |
| `config_schema.py`, `export.py` | The settings file for a bridge without screen: its rules, and writing it to the Pi's SD card |
| `status_led.py`, `pi/` | Status light and systemd unit for the Raspberry Pi (not yet tried on one) |
| `probe.py` | Setup and exploration: ports, identity, CC measurement, learn, verify, approvals |
| `config.example.yaml` | Template for `config.yaml` |
| `tests/` | Tests against simulated devices (`sim.py` is the simulated pedal and controller) |
| `Start Bridge.command` | macOS launcher: sets up the environment if needed, makes the clickable app and starts the bridge |
| `Start Bridge.bat` | The same for Windows, without the app (untested) |
| `icon.png`, `icon.ico` | The icon of the app, and the same for a Windows shortcut |
| `keysynth/` | The KeySynth effect for the pedal: ready-made files (MS-60B+; untested for MS-50G+ and MS-70CDR+), source, tests, install notes |
| `Prepare GitHub upload.command` | macOS helper for maintainers: collects the publishable files in `github-upload/` |
| `NOTES.md`, `CLAUDE.md` | Development notes and the original project brief (German) |

Created locally and not part of the repository: `config.yaml`, `approvals.json`,
`measurements.json`, `controls.json`, `logs/sysex.log`, `Expression Bridge.app`.

## Credits

- [zoom-explorer](https://github.com/thammer/zoom-explorer) by thammer, for documenting the
  MS Plus SysEx protocol
- [stomphacks](https://github.com/thammer/stomphacks) by thammer, the toolchain KeySynth is built
  and installed with, and [zoom-zt2](https://github.com/mungewell/zoom-zt2) by mungewell
- [SYNTHESIS SYNx2](https://github.com/Leemuzhko/Zoom-ZDL-FX/blob/main/zdl/sfx/synthesis/README.md)
  by Leemuzhko, a synth effect for the older Zoom MS pedals and the model for KeySynth's set of
  features (the idea only; none of its code is used)
- [mido](https://github.com/mido/mido) and [python-rtmidi](https://github.com/SpotlightKid/python-rtmidi)

## License

MIT, see [LICENSE](LICENSE).
