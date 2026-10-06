# Expression Bridge for the Zoom MS-60B+

Control effect parameters of a **Zoom MS-60B+** with an expression pedal, in real time.

The MS-60B+ officially accepts only patch changes over USB MIDI. Parameter changes need
unofficial SysEx messages, which a plain MIDI controller cannot send. This program sits in
between: it receives the controller's CC messages and translates them into the pedal's SysEx.

```
expression pedal → MIDI controller (sends CC) → this bridge (computer, USB host) → MS-60B+
```

> **Unofficial and at your own risk.** The SysEx protocol was reverse engineered and is not
> documented or supported by Zoom. This project is not affiliated with Zoom or M-Vave.
> Read [Before you start](#before-you-start) and [Safety](#safety).

## Status

Developed and tested with exactly this setup:

- Zoom MS-60B+, firmware 1.20
- M-Vave Chocolate Plus (USB mode) with an expression pedal on its TRS input
- macOS on Apple Silicon, Python 3.9

Not tested: other MS Plus pedals (MS-50G+, MS-70CDR+, MS-200D+), other firmware versions,
Windows, Linux. Other MIDI controllers should work if they send the expression pedal as a CC.
See [Installation](#installation) for all three systems.

## Features

- Browser interface on `http://127.0.0.1:8765`: live status, assignments, export and import
- Up to four parameters per preset, each with its own range, direction (inverted or not) and curve
- Assign any of effect 1–6 / parameter 1–9 directly, or let the pedal report a knob's exact
  range ("Learn parameter")
- Follows preset changes on the pedal; presets without an assignment are left alone
- Waits for missing devices and reconnects after replugging (tested with the controller)
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

3. Measure the expression pedal. This sends nothing to the MS-60B+.

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
| macOS | `Start Bridge.command` | `.venv/bin/python bridge.py --open` |
| Windows | `Start Bridge.bat` | `.venv\Scripts\python bridge.py --open` |
| Linux | – | `.venv/bin/python bridge.py --open` |

The interface opens in the browser at `http://127.0.0.1:8765`. If a device is missing, the
bridge waits for it and connects as soon as it appears.

Stop the bridge with **Ctrl+C** in its window: it then switches the pedal's edit mode off and
closes the MIDI ports. Closing the window on Windows ends it without that step, which had no
lasting effect in testing on macOS.

## Using the interface

- **Add preset**: enter the preset number shown on the pedal and click "Add preset".
- **Parameter**: choose effect 1–6 (position in the chain) and parameter 1–9 (order on the pedal).
- **Heel / Toe**: the parameter's value at each end of the pedal's travel. Use them to limit
  the range (20 to 70) or to invert the direction (100 to 0). "Invert" swaps the two.
- **Curve**: linear, or changing early or late in the pedal's travel.
- **Learn parameter** (optional): select the preset on the pedal, click the button, turn the
  knob once from minimum to maximum, click "Apply". The pedal reports the effect, the
  parameter and its exact range.
- **Export / Import**: all presets or a single one as a JSON file, including learned ranges.


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

- Slots count from 0 (first effect in the chain), parameters from 2 (first knob). Per the
  zoom-explorer source, parameter 0 is effect on/off and 1 is the effect type; the bridge
  never sends those.
- With edit mode enabled the pedal reports knob movements as `64 20 00 <slot> <param> <LSB> <MSB> …`.
- On a preset change the pedal sends `64 26 00 00 <bank LSB MSB> <program LSB MSB>`, then
  `64 20 00 64 02 …` (tempo, per the reference), then CC 0, CC 32 and a program change.
- The number shown on the pedal is `bank × 10 + program + 1` (preset 095 = bank 9, program 4).
- Values up to 100 were verified, where the value sits in the LSB. The 7-bit split for values
  above 127 follows the reference and was only exercised in simulation.
- The very first "set parameter" after connecting was once applied without an ack.
- The Chocolate Plus sends about 30 CC messages per second. The bridge sends at most one
  SysEx per 10 ms, so with four parameters each one is updated about 25 times per second.

## Files

| File | Purpose |
|---|---|
| `bridge.py` | The bridge and its main loop |
| `ui.py`, `ui.html` | The browser interface (local HTTP server, reachable from this computer only) |
| `zoom_sysex.py` | SysEx builders, allowlist, parsers, approvals, learned ranges |
| `probe.py` | Setup and exploration: ports, identity, CC measurement, learn, verify, approvals |
| `config.example.yaml` | Template for `config.yaml` |
| `Start Bridge.command` | macOS launcher: sets up the environment if needed and starts the bridge |
| `Start Bridge.bat` | The same for Windows (untested) |
| `Prepare GitHub upload.command` | macOS helper for maintainers: collects the publishable files in `github-upload/` |
| `NOTES.md`, `CLAUDE.md` | Development notes and the original project brief (German) |

Created locally and not part of the repository: `config.yaml`, `approvals.json`,
`measurements.json`, `logs/sysex.log`.

## Credits

- [zoom-explorer](https://github.com/thammer/zoom-explorer) by thammer, for documenting the
  MS Plus SysEx protocol
- [mido](https://github.com/mido/mido) and [python-rtmidi](https://github.com/SpotlightKid/python-rtmidi)

## License

MIT, see [LICENSE](LICENSE).
