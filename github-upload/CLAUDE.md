# Zoom MS-60B+ Expression-Bridge

## Ziel

Ein Expression-Pedal soll in Echtzeit einen Effektparameter am **Zoom MS-60B+** steuern.

Signalweg: Expression-Pedal → **M-Vave Chocolate Plus** (TRS-Buchse, sendet MIDI-CC) → **Bridge** (dieses Projekt: empfängt CC, übersetzt in Zoom-SysEx) → MS-60B+ (USB-MIDI).

Hintergrund: Das MS-60B+ nimmt offiziell per USB-MIDI nur Patchwechsel an. Parameteränderungen gehen nur über inoffizielle, per Reverse Engineering ermittelte SysEx-Befehle. Das Chocolate kann kein SysEx senden, deshalb braucht es die Bridge. Beide Geräte sind USB-Devices, die Bridge muss also USB-Host sein.

## Setup

- **Entwicklungsrechner:** macOS (Mac Mini M1), Python 3, `mido` + `python-rtmidi` in einem venv
- **Chocolate Plus:** Modusschalter auf **U** (USB-Device), per USB-C am Mac. Expression-CC wird in CubeSuite eingestellt (Werte siehe „Offene Werte“).
  - Falls über USB keine CCs ankommen: laut Nutzerberichten Bluetooth am Chocolate deaktivieren (B + C lange drücken).
- **MS-60B+:** per USB-C am Mac
- **Auto-Save am MS-60B+ ist in den Pedal-Einstellungen AUSGESCHALTET.** Sonst landet womöglich die letzte Pedalstellung im gespeicherten Patch. Nicht per SysEx umschalten.
- **Zielplattform für die Bühne:** offen (Raspberry Pi oder Teensy 4.1). Wird erst nach Phase 3 entschieden.

## Protokoll

Inoffiziell. Referenz: https://github.com/thammer/zoom-explorer (README).
Dokumentiert ist es am **MS-50G+** (Device-ID `6E`). Für das MS-60B+ muss alles in Phase 1 verifiziert werden. Alle Werte hexadezimal, `<ID>` = Device-ID.

| Zweck | Nachricht | Antwort |
|---|---|---|
| Identity Request | `F0 7E 7F 06 01 F7` | `F0 7E 00 06 02 52 <family LSB> <family MSB> <model LSB> <model MSB> <Version ASCII> F7`. Die Device-ID ist das erste Family-Byte. |
| Parameter edit enable | `F0 52 00 <ID> 50 F7` | `F0 52 00 <ID> 00 00 F7` (Ack) |
| Parameter edit disable | `F0 52 00 <ID> 51 F7` | `F0 52 00 <ID> 00 00 F7` |
| Parameter setzen | `F0 52 00 <ID> 64 20 00 <slot> <param> <LSB> <MSB> 00 00 00 F7` | `F0 52 00 <ID> 64 20 01 <slot> <param> <LSB> <MSB> 00 00 00 F7`, kommt nur, wenn der Wert gültig ist |
| Aktuelle Bank/Programm abfragen | `F0 52 00 <ID> 33 F7` | Bank Select (CC0/CC32) + Program Change |
| Aktuellen Patch abfragen | `F0 52 00 <ID> 64 13 F7` | `F0 52 00 <ID> 64 12 …` (Patch-Dump, ca. 985 Bytes) |

Laut Referenz sendet das Pedal `64 20 00 …` auch selbst, wenn am Gerät ein Parameter verändert wird. Darüber werden Slot, Parameternummer und Wertebereich ermittelt.

**Nicht gesichert und in Phase 1 zu klären:**
- Wertkodierung. Vermutet: 7-Bit-Aufteilung, Wert = LSB + 128 · MSB. Stand 2026-10-06: für 0–100 bestätigt (Wert im LSB), über 127 ungeprüft.
- Zählweise der Slots (ab 0 oder ab 1). Stand 2026-10-06: ab 0 (erste Position der Kette = Slot `0`); erster Parameter eines Effekts = Param `2`.
- Ob das MS-60B+ überhaupt dieselben Befehle versteht. Stand 2026-10-06: ja für Identity, `50`, `51` und Parameter setzen; `33` (antwortet auch ohne edit enable); `64 13` noch nicht gesendet.

**In Phase 1 gemessen (MS-60B+, Firmware 1.20), Details in `NOTES.md`:**
- Das Ack `64 20 01` kommt nach ca. 10 ms, aber nur, wenn sich der Wert ändert. Derselbe Wert noch einmal bleibt unbeantwortet.
- Laut zoom-explorer-Quelltext: Param 0 = Effekt an/aus, Param 1 = Effekttyp, Parameter ab 2. Die Allowlist sperrt Param 0 und 1.

## Sicherheitsregeln (verbindlich)

1. **Nur die Nachrichten aus der Tabelle oben senden.** Kein Ausprobieren anderer Befehle oder Bytes, kein Fuzzing, keine Werte außerhalb des gemessenen Bereichs.
   - **Ausnahme seit 2026-10-06 (Wunsch des Nutzers):** In der Oberfläche lassen sich Effekt 1–6 und Parameter 1–9 auch ohne Lernen zuordnen; den Wertebereich gibt dann der Nutzer an (0–16383). Die Nachrichtenart bleibt `64 20 00 <slot> <param> …` mit slot 0–5 und param 2–10. Schutz dafür: Ein nicht gelernter Parameter bekommt immer nur eine Nachricht auf einmal, bis das Pedal ihn bestätigt, und nach 5 unbestätigten Sendungen wird er gestoppt. Gelernte Bereiche gelten weiterhin strikt. Ob ungültige Effekt-/Parameternummern am Pedal Nebenwirkungen haben, ist nicht getestet.
2. **Ausdrücklich verboten** (u. a. Firmware-Modus, Factory Reset, Dateizugriff, Bank löschen, Patch überschreiben, Systemeinstellungen, Effekte einfügen/löschen/verschieben):
   - `01`, `04`, `5B`, `28`
   - `60 xx`, `64 47`
   - `64 20 00 64 xx`
3. **Allowlist im Code.** Alle SysEx-Nachrichten werden ausschließlich in `zoom_sysex.py` über Builder-Funktionen erzeugt. Die Sendefunktion prüft gegen eine Allowlist und verweigert alles andere.
4. **Vor dem allerersten Senden** bestätigt der Nutzer, dass seine Patches gesichert sind.
5. **Jede neue Nachrichtenart** wird dem Nutzer vor dem ersten Senden als Hex angezeigt, erst nach Freigabe wird gesendet.
6. Alle gesendeten und empfangenen SysEx-Nachrichten werden als Hex geloggt.

## Phasen

### Phase 1: Erkundung (`probe.py`)

1. Alle MIDI-Ein- und -Ausgänge auflisten. Port-Namen beider Geräte in `config.yaml` eintragen.
2. Identity Request ans MS-60B+ senden, Antwort parsen. Device-ID und Firmware-Version in `config.yaml` und unten in „Offene Werte“ eintragen.
3. Chocolate-Monitor: Der Nutzer bewegt das Expression-Pedal. CC-Nummer, Kanal und tatsächliches Min/Max loggen. Prüfen, ob der Wert im Stillstand springt (Rauschen) und ob die Richtung invertiert ist.
4. Parameter edit enable senden und Ack prüfen. Dann dreht der Nutzer am MS-60B+ den Zielparameter einmal von Minimum bis Maximum. Eingehende `64 20 …`-Nachrichten loggen und daraus ermitteln:
   - Slot
   - Parameternummer
   - Min/Max
   - Wertkodierung
5. Gegenprobe: genau einen gemessenen gültigen Wert zurücksenden (nach Freigabe gemäß Regel 5) und Ack plus Änderung am Display prüfen.
6. Falls das Pedal nichts mitsendet: Nutzer bitten, die Werte mit Zoom Explorer zu ermitteln (https://www.waveformer.net/zoom-explorer/, Chrome, Web MIDI). Dabei muss der Port in `probe.py` geschlossen sein.

Ergebnis: ausgefüllte Mapping-Einträge in `config.yaml`.

### Phase 2: Bridge (`bridge.py`)

- **Konfiguration** (`config.yaml`):
  - Port-Namen (Teilstring-Match)
  - Expression-CC und -Kanal
  - Mapping pro Patch: `program → {slot, param, min, max, invert, curve}`
- **Start:**
  - Ports öffnen; wenn ein Gerät fehlt, periodisch neu versuchen (Hotplug)
  - Identity prüfen
  - Edit enable senden
  - Aktuelle Bank/Programm abfragen und passendes Mapping aktivieren
- **Patchwechsel verfolgen:** Bank Select und Program Change vom MS-60B+ mitlesen und das aktive Mapping wechseln. Patches ohne Mapping: Expression ignorieren.
- **Optional:** PC/CC der Chocolate-Fußschalter ans MS-60B+ durchreichen.
- **Skalierung:**
  - CC 0–127 → min–max des Parameters
  - Optional Kurve (linear/log) und Invertierung
  - Deadband bzw. leichtes Smoothing gegen Pedalrauschen
- **Drosselung:**
  - Nur senden, wenn sich der *Zielwert* ändert
  - Mindestabstand zwischen Sendungen konfigurierbar (Start: 10 ms)
  - Bei Stau nur den jeweils letzten Wert senden (Coalescing)
  - Acks zählen; bleiben sie aus, warnen und Rate senken
- **Beenden** (auch bei Strg+C): Edit disable senden, Ports sauber schließen.

### Phase 3: Tests mit echtem Gerät

- Latenz: Zeit von eingehendem CC bis Ack messen und loggen
- Schnelle Sweeps: Hängt das Pedal? Gibt es Zipper-Geräusche, laggt das Display?
- Patchwechsel, während das Pedal bewegt wird
- Beide Geräte im Betrieb abziehen und wieder anstecken
- Nach einer Session: Ist der gespeicherte Patch unverändert? (Auto-Save-Kontrolle)
- Ergebnisse kurz in `NOTES.md` festhalten

### Phase 4: Zielplattform (erst nach Entscheidung des Nutzers)

**Raspberry Pi:**
- Python-Code übernehmen
- systemd-Service mit Autostart und Restart bei Absturz
- Read-only-Root (Overlay-Dateisystem), damit hartes Ausschalten die SD-Karte nicht beschädigt
- Ports per Name, nicht per Nummer öffnen

**Teensy 4.1:**
- Nach C++ portieren (PlatformIO/Teensyduino, `USBHost_t36`)
- Hub am Host-Port, falls Chocolate und MS-60B+ beide per USB angeschlossen werden
- Puffergröße für große SysEx-Antworten (Patch-Dump) beachten
- Alternative: Expression-Pedal direkt an einen Analogeingang. Dann entfällt das Chocolate für die Expression. Die Belegung des TRS-Steckers (Tip/Ring) ist bei Expression-Pedalen nicht einheitlich, also vorher mit dem Nutzer klären.

## Konventionen

- Python 3 im venv, Abhängigkeiten in `requirements.txt`, nichts global installieren
- Dateien:
  - `zoom_sysex.py` (Builder + Allowlist + Parser)
  - `probe.py`
  - `bridge.py`
  - `config.yaml`
  - `NOTES.md`
  - `ui.py`, `ui.html` (Bedienoberfläche der Bridge im Browser, nur lokal erreichbar, Port in `config.yaml`)
  - `README.md` (englisch), `LICENSE` (MIT, Lukas Kaminski), `.gitignore`, `config.example.yaml`
  - `Prepare GitHub upload.command` füllt `github-upload/` mit genau den Dateien für GitHub; der Nutzer lädt sie auf github.com per Drag & Drop hoch (kein `gh`, kein lokales Git-Repository)
  - `Start Bridge.bat` (Doppelklick-Starter für Windows, ungetestet; CRLF-Zeilenenden beibehalten)
  - `Start Bridge.command` (Doppelklick-Starter für macOS), `.claude/launch.json` (Start im Browser-Bereich der App)
  - Erzeugt: `approvals.json` (Regeln 4 und 5), `measurements.json` (gelernte Wertebereiche je Preset, Slot und Param), `logs/sysex.log`
- Zuordnungen: Schlüssel `"Bank/Program"` (Anzeige 095 = `"9/4"`), darunter eine Liste mit bis zu vier Parametern pro Preset; `slot` = Effektnummer − 1, `param` = Parameternummer + 1, `invert: true` heißt Ferse = max, Spitze = min. Den Abschnitt `mappings:` schreibt die Oberfläche; er steht am Ende von `config.yaml`.
- Export/Import (Oberfläche): Eine Exportdatei enthält Zuordnungen und gelernte Bereiche. Beim Import gelten diese Bereiche als gelernt. Das ist die einzige Stelle, an der ein Bereich nicht aus einer Meldung des angeschlossenen Pedals stammt; nur eigene Exportdateien importieren.
- Gelernte Parameter (Regler am Pedal gedreht) dürfen nur im dabei gemeldeten Bereich gesetzt werden; wird in einem Preset ein Effekt getauscht, muss neu gelernt werden. Nicht gelernte Parameter: siehe Ausnahme bei Sicherheitsregel 1.
- Code, Oberfläche, Meldungen, Kommentare und Dateinamen des Programms sind englisch (Wunsch des Nutzers vom 2026-10-06). `probe.py freigabe` heißt jetzt `probe.py approve`. Diese Datei und `NOTES.md` bleiben deutsch.
- Erst lesen und loggen, dann senden. Bei Unklarheit über das Protokoll nachfragen statt raten.

## Offene Werte (in Phase 1 ermitteln bzw. vom Nutzer eintragen)

| Wert | Inhalt |
|---|---|
| Device-ID MS-60B+ | `6E` (Identity-Antwort `F0 7E 00 06 02 52 6E 00 27 00 31 2E 32 30 F7`, Model `27 00`) |
| Firmware MS-60B+ | 1.20 |
| Port-Name MS-60B+ | `ZOOM MS Plus Series` (nicht nur „ZOOM“ matchen: das UAC-232 hat eigene MIDI-Ports) |
| Port-Name Chocolate Plus | `SINCO` |
| Expression-CC / Kanal (CubeSuite) | CC 25 / Kanal 1 |
| Expression Min / Max / invertiert? | 0 / 127 / nein; im Stillstand ruhig (kein Rauschen an Ferse und Spitze) |
| Ziel-Patch(es) und Effekt | Patch 095 (Anzeige am Pedal) = Bank 9, Program 4 (Antwort auf `33`: CC0 `0`, CC32 `9`, PC `4`, Kanal 1): Galactic Reverb (Custom Effect), erste Position der Kette, erster Parameter „Hold“ |
| Slot / Param / Min / Max je Ziel | Slot `0`, Param `2`, 0–100 (vom Pedal selbst gemeldet, `learn` am 2026-10-06) |
| Wertkodierung bestätigt? | Für 0–100 ja: Wert im LSB, MSB `00`; Gegenprobe mit 50, 60, 40, 100 vom Pedal bestätigt. Die 7-Bit-Aufteilung (> 127) ist an diesem Parameter nicht prüfbar. |
