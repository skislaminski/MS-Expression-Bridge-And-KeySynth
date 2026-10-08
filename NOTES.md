# Notizen

## Phase 1, 2026-10-06 (MS-60B+ Firmware 1.20, macOS)

**Funktioniert**
- Identity: `F0 7E 00 06 02 52 6E 00 27 00 31 2E 32 30 F7`, Device-ID `6E`, Model `27 00`.
- Edit enable `50` und edit disable `51` werden mit `F0 52 00 6E 00 00 F7` bestätigt.
- Nach edit enable meldet das Pedal Reglerbewegungen als `64 20 00 <slot> <param> <LSB> 00 00 00 00`.
  Gemessen: Slot 0, Param 2, 0–100, lückenlos monoton, Wert direkt im LSB.
  Sonst kam in den 30 s nichts (kein Patch-Dump `64 12`, keine CC/PC).
- Chocolate Plus (Port `SINCO`): Expression = CC 25, Kanal 1, 0–127, nicht invertiert, im Stillstand ruhig.

**Parameter setzen (Gegenprobe)**
- Funktioniert: `F0 52 00 6E 64 20 00 00 02 <LSB> 00 00 00 00 F7` wird übernommen und nach 8–11 ms mit
  `64 20 01 …` bestätigt. Ab 02:22 Uhr fünf von fünf geänderten Werten bestätigt (50, 60, 40, 100),
  mit 0,5 s Pause nach edit enable und ganz ohne Pause.
- Das Ack kommt nur, wenn sich der Wert ändert. Denselben Wert noch einmal zu senden bleibt
  unbeantwortet (zweimal gezielt geprüft: 60 → 60, 40 → 40).
- Ungeklärt: Die allererste Sendung (02:09 Uhr, 100 → 50, ohne Pause) blieb ohne Ack, ebenso die
  zweite (02:12 Uhr, 50, mit Pause). Beim nächsten `learn` meldete der erste Reglerklick 49, das Pedal
  stand also auf 50. Passt zu „erste Sendung übernommen, aber nicht bestätigt; zweite war dann
  unverändert“. Der Nutzer hat den Regler dazwischen nicht von Hand bewegt, die erste Sendung wurde
  also übernommen, aber nicht bestätigt. Warum, ist offen. In Phase 3 beobachten, ob das erste Setzen
  nach dem Anstecken ohne Ack bleibt.

**Abgleich mit dem zoom-explorer-Quelltext** (`src/ZoomDevice.ts`, `src/index.ts`, Stand 2026-10-06)
- Format identisch: `64 20 00` + Slot, Param, dann 5 Wertbytes zu je 7 Bit (LSB zuerst).
- Param 0 = Effekt an/aus, Param 1 = Effekttyp, Parameter ab 2. Slot und Param werden beim Senden
  und Empfangen gleich gezählt.
- Reihenfolge dort: Probe-Sequenz (`05`, `29`, `64 13`, `07`, `44`, `09`, `46`, `33`, ggf. Program-Change-Test
  mit Zurückschreiben des aktuellen Patches, `64 02`), dann `51`, dann `50`; danach Parameter per `64 20 00`.
  PC mode (`52`) wird nirgends aufgerufen, keine Wartezeiten zwischen Nachrichten.

**Offen**
- Aktualisiert das Pedal sein Display, wenn der Wert per SysEx gesetzt wird?
- Ziel: Patch 095, Galactic Reverb (Custom Effect) an erster Position, erster Parameter „Hold“.
  Damit zählen Slots ab 0 und Parameter ab 2 (wie in der Referenz).
- 7-Bit-Aufteilung für Werte über 127 (an diesem Parameter nicht prüfbar).
- Meldet das Pedal Patchwechsel von sich aus? (`33` antwortet: Patch 095 = CC0 `0`, CC32 `9`, PC `4`, Kanal 1,
  auch ohne edit enable. Also 10 Patches pro Bank, Anzeige = Bank · 10 + Program + 1.)

## Phase 2/3, erster Lauf von bridge.py am echten Gerät, 2026-10-06 03:42 Uhr (ca. 17 s aktiv)

- Patch 095 = Bank 9, Program 4; Mapping `"9/4"` wurde beim Start über `33` erkannt.
- 190 Parameter gesendet, 190 bestätigt, Acks 1:1 in Sendereihenfolge, Werte 0–100 ausgefahren.
- Latenz TX→Ack Median 11,0 ms, Max 14,0 ms; CC→Ack Median 11,5 ms, Max 14,1 ms.
- Das Chocolate liefert höchstens ca. 30 CCs pro Sekunde (kleinster Abstand 33 ms). Die Drosselung auf
  10 ms greift also nie; bei schnellem Durchtreten springt der Wert um bis zu 10 Schritte (Median 2).
- Hotplug: Beim Start fehlte `SINCO`, 43 s später war es da und die Bridge hat von selbst verbunden.
  Die Port-Liste wird unter macOS also im laufenden Prozess aktualisiert.
- Auch das erste Setzen nach dem Start wurde diesmal bestätigt.
- Sauber beendet: edit disable gesendet und bestätigt.

## Phase 3, zweiter Lauf, 2026-10-06 03:45–03:48 Uhr

- Patchwechsel am Pedal: Das MS-60B+ meldet ihn von sich aus, bei laufendem edit enable in dieser Reihenfolge:
  `64 26 00 00 <bank LSB MSB> <prog LSB MSB>`, dann `64 20 00 64 02 78 00 …` (Tempo 120), dann CC0, CC32,
  Program Change. Die Bridge hat fünf Wechsel (9/3, 9/4, 9/5) erkannt und das Mapping jeweils umgeschaltet.
  Eine regelmäßige Abfrage mit `33` ist nicht nötig.
- 92 Parameter gesendet, 92 bestätigt. Latenz CC→Ack Median 3,9–5,2 ms, Max 5,2 ms (im ersten Lauf 11,5 ms).
- Chocolate im Betrieb abgezogen: nach höchstens 1 s erkannt, edit disable gesendet und bestätigt, danach
  „Warte auf Geräte“. Bis zum Ende des Laufs (101 s) wurde es nicht wieder angesteckt; das Wiederverbinden
  nach einem Verlust ist also noch ungetestet (beim Start hatte es im ersten Lauf funktioniert).

- Dritter Lauf (03:51–03:58 Uhr): Chocolate abgezogen und wieder angesteckt, die Bridge war 4 s nach dem
  Wiedererscheinen des Ports wieder bereit. Sauber beendet (edit disable bestätigt).

**Noch nicht getestet (Phase 3):** Abziehen des MS-60B+ im Betrieb, Patchwechsel während das Pedal bewegt
wird, Zipper-Geräusche und Display-Verhalten nach Eindruck des Nutzers, gespeicherter Patch nach der
Session unverändert.

## Bedienoberfläche, 2026-10-06

- `bridge.py` startet jetzt zusätzlich eine Oberfläche im Browser (`ui.py`, `ui.html`): Status, „Regler lernen“
  für den am Pedal gewählten Preset, pro Preset Regler, Name, Wert an Ferse und Spitze, Verlauf.
- Messbereiche gelten pro Preset (`messungen.json`: Preset → Slot/Param → Bereich). `probe.py learn` und
  `verify` fragen dafür jetzt den aktuellen Preset per `33` ab.
- Am echten Gerät geprüft: Start, Anzeige von Preset 095, Speichern (Bereich und Umkehrung landen in
  `config.yaml` und gelten sofort), Lernen starten und abbrechen.
- Nur in der Simulation geprüft: ein vollständiger Lernvorgang (Regler drehen, übernehmen), Werte über 127.

## Erweiterung der Oberfläche, 2026-10-06 (ab 04:50 Uhr)

- Bis zu zwei Regler pro Preset; die Sendungen wechseln sich ab, je Mindestabstand geht eine raus.
- Ferse/Spitze je Regler (Bereich begrenzen, Richtung umkehren), Knopf „Umkehren“.
- Export (alle Presets oder einer) und Import als JSON, samt gelernten Bereichen.
- Der Nutzer hat um 04:35 Uhr selbst gelernt: Preset 006, Effekt 3, Parameter 1, 0–100. Der Lernvorgang
  funktioniert also am echten Gerät.
- Am echten Gerät geprüft: Anzeige beider Presets, Umkehren und Speichern, Export (alle, einzeln) und Import
  über die Schnittstelle. Nur in der Simulation: zwei Regler gleichzeitig senden, Import in eine leere
  Installation, der Download über die Knöpfe.
- Stoppen über den Browser-Bereich der App beendet den Prozess hart: Es wird kein edit disable gesendet.
  Bei Strg+C, SIGTERM oder Schließen des Terminalfensters wird sauber beendet.

## Englische Fassung und vier Parameter, 2026-10-06 (ab 05:00 Uhr)

- Bis zu vier Parameter pro Preset (`MAX_TARGETS = 4`). Das Chocolate liefert rund 30 CCs/s; vier Parameter
  wären 120 Sendungen/s, der Mindestabstand von 10 ms lässt 100 zu. In der Simulation kommen deshalb pro
  Parameter rund 25 von 33 Zwischenwerten an, die Endwerte stimmen immer. Am echten Gerät noch nicht geprüft.
- Der Mindestabstand zählt jetzt ab dem tatsächlichen Sendezeitpunkt (vorher ab Beginn des Schleifendurchlaufs;
  ein langsamer Durchlauf konnte den Abstand auf unter 6 ms verkürzen).
- Alles am Programm ist englisch: `bridge.py`, `ui.py`, `ui.html`, `zoom_sysex.py`, `probe.py`, Kommentare in
  `config.yaml`. Umbenannt: `freigaben.json` → `approvals.json` (Schlüssel `backup_confirmed`, `messages`),
  `messungen.json` → `measurements.json`, `probe.py freigabe` → `probe.py approve`,
  `Bridge starten.command` → `Start Bridge.command`, Exportdatei `…-alle-presets.json` → `…-all-presets.json`.
- Der Nutzer hat zwei Parameter in Preset 095 im Einsatz (Effekt 1/Parameter 1 und Effekt 2/Parameter 3,
  100→80) und meldet: klappt.

## Zuordnen ohne Lernen, 2026-10-06 (ab 05:20 Uhr)

- Das Auswahlfeld „Parameter“ bietet Effect 1–6 × Parameter 1–9 direkt an; gelernte Bereiche stehen in Klammern.
  „Add preset“ legt einen Preset per Nummer an, ohne dass er am Pedal gewählt sein muss.
- Nicht gelernte Parameter: Bereich vom Nutzer (Vorschlag 0–100, Grenze 16383). Solange das Pedal einen solchen
  Parameter noch nie bestätigt hat, geht immer nur eine Nachricht raus und es wird auf Ack oder Timeout (250 ms)
  gewartet; nach 5 unbestätigten Sendungen stoppt die Bridge ihn bis zum nächsten Presetwechsel, Speichern oder
  Drehen am Regler. Die Oberfläche zeigt „not confirmed“ bzw. „stopped“.
- Ein teilweise zu großer Bereich (z. B. 0–100 bei echtem Maximum 50) wird nicht gestoppt, nur als „not confirmed“
  markiert; die globale Drosselung kann dabei anspringen.
- Nur in der Simulation geprüft (gültig, teilweise gültig, ungültig). Am echten Pedal ist offen, was bei einer
  nicht vorhandenen Effekt- oder Parameternummer passiert. Hinweis aus der Referenz: Beim Systembefehl
  `64 20 00 64 09` frieren Werte über 0A das Pedal ein; die Firmware prüft also nicht alles.
- Lernt man einen schon zugeordneten Parameter, wird sein Bereich auf das Gelernte begrenzt.

## Umzug und Windows, 2026-10-06 (ab 05:45 Uhr)

- Das Projekt liegt jetzt in `~/Desktop/zoom-expression-bridge`. Der Nutzer hat dort `Start Bridge.command`
  per Doppelklick gestartet: Die Python-Umgebung wurde automatisch neu angelegt, die Bridge lief sofort.
- Veröffentlichung: Quellcode auf GitHub per Web-Upload aus `github-upload/`, Lizenz MIT.
- Windows (alles ungetestet, kein Windows-Rechner vorhanden): `Start Bridge.bat` ergänzt. Behoben, was dort
  sicher gescheitert wäre: `signal.SIGHUP` gibt es unter Windows nicht (Absturz beim Start). Vorsorglich:
  Konsolenausgabe bricht bei nicht darstellbaren Zeichen nicht mehr ab. Bekannt: Vor Python 3.11 schläft
  `time.sleep` unter Windows in Schritten von typisch 16 ms, die Bridge reagiert dann träger.

## Parameter 1–12, 2026-10-07

- Das Auswahlfeld bietet jetzt Parameter 1–12 je Effekt (`MAX_PARAMS = 12`, param 2–13), vorher 1–9.
  Unverändert: Effekt 1–6, höchstens vier Parameter pro Preset, Schutz für nicht gelernte Parameter.
- Ob das MS-60B+ Parameter 10–12 annimmt, ist am Gerät nicht geprüft.
- Die Simulationsskripte aus der ersten Sitzung lagen in einem temporären Ordner und sind nicht mehr da.
  Geprüft wurde diesmal nur die Validierung (Parameter 12 zulässig, 13 abgelehnt) und der gemeldete Grenzwert.

## Tests fest im Projekt, 2026-10-07

- `tests/` enthält 31 Tests gegen simulierte Geräte: Allowlist und Freigaben (`test_sysex.py`), Bridge und
  Oberfläche (`test_bridge.py`), Einrichtungsskript (`test_probe.py`). Aufruf: `.venv/bin/python -m unittest`.
- Das simulierte Pedal verhält sich wie am echten Gerät gemessen (Ack nur bei geändertem Wert, Presetmeldung
  als `64 26 …` plus CC 0, CC 32, Program Change).
- Klarstellung zur Zählweise: In der Oberfläche und am Pedal Parameter 1–12, intern `param` 2–13.

## Keyboard-Synth, 2026-10-07

- Neu: Ein MIDI-Keyboard spielt den DIY-Effekt KeySynth (Projekt `~/Desktop/ms-plus-synth`) über dessen
  Key-Regler. Abschnitt `synth:` in `config.yaml`; beim Nutzer eingetragen: Keyboard `KOMPLETE KONTROL A61`,
  alle Kanäle, Effekt-ID `07000f61`.
- `zoom_sysex.py`: `build_set_key` (Parameter fest 2, Werte 0–128, Slot 0–5), `key_target`, `parse_patch_dump`.
  Der Parser wurde gegen fünf echte Dumps des MS-60B+ vom 2026-10-07 geprüft (IDs, an/aus und Wert des ersten
  Reglers stimmten mit dem Pedal überein); einer davon liegt als Testdaten in `tests/test_synth.py`.
- `bridge.py`: Keyboard-Port, Spiellogik (`Keys`), Suche des Effekts per Patch-Abfrage, Noten ohne
  Mindestabstand, Key = 0 beim Beenden. Chocolate und Keyboard sind mit `synth`-Abschnitt beide optional.
- Tests: 29 neue in `tests/test_synth.py`, zusammen 60, alle bestanden. `tests/sim.py` kennt jetzt ein
  Keyboard und beantwortet die Patch-Abfrage.
- Am echten Gerät gemessen (im Projekt ms-plus-synth, nicht mit der Bridge): Key-Schreibvorgänge im
  Sechzehntelraster bei 140 BPM (53,6 ms) 128 von 128 bestätigt, Ack im Median nach 9,4 ms, höchstens
  11,5 ms; bei 26,8 ms Abstand ebenfalls 128 von 128. Slot-Zählung ab 0, 6 Slots.
- Freigabe: `query_patch` vom Nutzer am 2026-10-07 um 02:22 Uhr erteilt.
- Sicherung des Stands vor der Änderung: `~/Desktop/ms-plus-synth/backup/bridge-vor-phase4-2026-10-07/`.
- `github-upload/` ist nicht aktualisiert; dafür `Prepare GitHub upload.command` ausführen.

## Keyboard-Synth am echten Gerät, 2026-10-07 02:23–02:27 Uhr

- Start nur mit Keyboard (`KOMPLETE KONTROL A61`), Chocolate nicht angesteckt: bereit nach unter 1 s,
  Patch-Abfrage in 6 ms beantwortet.
- Der Nutzer hat den KeySynth erst danach am Pedal eingefügt: sofort gefunden, ohne zweite Abfrage.
  **Das Pedal meldet Kettenänderungen von sich aus:** Bei laufendem edit enable schickt es den Patch als
  `64 12 00 …` (19-mal in diesem Lauf, auch beim Blättern im Effektmenü). Die Antwort auf `64 13`
  beginnt mit `64 12 01 …`. Der Parser nimmt beide.
- Effekt in der Kette verschoben (Effekt 1 → 2): über denselben Dump erkannt, danach an Slot 1 gesendet.
- 188 Key-Sendungen, 186 bestätigt. Taste → Ack: Median 9,9 ms, 90 % unter 10,5 ms, 99 % unter 20,5 ms.
- Zwei blieben unbestätigt:
  1. Ein Gate-off ging verloren, während der Nutzer im Effektmenü des Pedals war. Die Wiederholung
     250 ms später wurde bestätigt (Ack nach 47,9 ms). Daraufhin geändert: `KEY_ACK_TIMEOUT` 60 ms und bis
     zu drei Wiederholungen des letzten Werts (`KEY_RETRIES`). Diese Änderung ist nur in der Simulation
     geprüft.
  2. Gate-off und neue Note im Abstand von 1 ms: Das Pedal bestätigte nur die Note. Kein Fehler im
     Klang gemeldet.
- Meldung des Nutzers: „Alles funktioniert.“ Kommt eine neue Note, während er im Menü des Pedals ist,
  wirft ihn das Pedal aus dem Menü; bei gehaltenem Ton kann er sich im Menü bewegen. Das ist die Reaktion
  des Pedals auf eine Parameter-Nachricht, die Bridge kann daran nichts ändern.
- Unbekannte Meldung vom Pedal, einmal: `F0 52 00 6E 64 20 00 64 01 00 00 00 00 00 F7`.
- Sauber beendet (SIGTERM): edit disable gesendet und bestätigt; Key stand schon auf 0.
- 62 Tests bestanden (zwei neue für den verlorenen Gate-off).
- **Noch nicht am echten Gerät geprüft:** Key 128 (7-Bit-Aufteilung), Abziehen des Keyboards im Betrieb,
  Chocolate und Keyboard gleichzeitig, die schnellere Wiederholung.

## Dauertest mit Keyboard-Synth, 2026-10-07 02:57–03:21 Uhr

Aufbau: REAPER spielt Sechzehntel (8 Noten pro Sekunde) über den IAC-Treiber, die Bridge liest von dort.
Zwei Durchgänge, zusammen rund 13 Minuten; der Nutzer hat nach 13 Minuten abgebrochen, die geplante Stunde
ist nicht gelaufen.

- 12233 Key-Sendungen, 11021 bestätigt. Kein Absturz, kein Verbindungsabbruch, sauber beendet.
- **Unbestätigte Noten gibt es nur, während der Nutzer das Pedal bedient.** In den Minuten ohne Bedienung
  (03:08, 03:09): 1421 von 1421 bestätigt, und in der Aufnahme kein gehaltener oder fehlender Ton. Mit
  Bedienung (Regler eines anderen Effekts drehen, in der Kette oder der Effektauswahl blättern) antwortet
  das Pedal 0,1 bis 0,7 s lang verspätet oder gar nicht; hörbar als kurz gehaltener Ton.
- Presetwechsel weg und zurück (zweimal): Synth als fehlend gemeldet, Noten ignoriert, nach dem
  Wiedereinfügen 10 bis 14 s später von selbst gefunden.
- **USB-Kabel des Pedals rund 2 s gezogen (03:20:41):** Die Bridge hat das nicht bemerkt (keine Meldung
  „Connection lost“; die Portprüfung läuft einmal pro Sekunde). Danach beantwortete das Pedal die
  Patch-Abfrage, bestätigte aber 27 s lang keinen einzigen Parameter. Deutung: Nach dem Wiedereinstecken ist
  der Edit-Modus aus. Laut Nutzer lief der Ton weiter; eine Aufnahme davon gibt es nicht.
- Daraufhin geändert:
  - Bleiben Parameter unbestätigt, sendet die Bridge erneut edit enable, höchstens alle 2 s
    (`_reenable_edit`). Das gilt für Noten und für Expression.
  - Die Warnung und die erneute Patch-Abfrage bei unbestätigten Noten kommen höchstens alle 5 s
    (vorher bis zu einmal pro Sekunde, 59 Warnungen in neun Minuten).
  - Das simulierte Pedal kennt jetzt den Edit-Modus und ein kurzes Umstecken (`World.replug`).
- 65 Tests bestanden (drei neue). Am echten Gerät sind diese Änderungen noch nicht geprüft.
- Am Rande: Die Parameter 9 und 10 (intern) hat das Pedal per SysEx angenommen und bestätigt.

## Umbau für KeySynth 0.20, 2026-10-07 (entwickelt in `ms-plus-synth/bridge-staging/`, übernommen um 17:57 Uhr zusammen mit der Installation von 0.20 auf dem Pedal)

Anlass: Der Nutzer wünscht Glide, Pitch Bend, Mod-Wheel und Keyboard-Regler für den Synth, und die Zuweisung
soll mit jedem Keyboard gehen. Der Effekt bekommt dafür eine neue Reglerbelegung (Projekt ms-plus-synth,
`NOTES.md`, „KeySynth 0.20“). Bridge und Effekt müssen zusammen gewechselt werden: Die alte Bridge spielt nur
0.10, die neue nur 0.20.

- **Key 0–1000:** Note und Bend in einer Zahl (`zs.key_for`), 10 Cent pro Schritt ab C0. Noten außerhalb
  12–111 werden nicht gespielt.
- **Pitch-Wheel:** `bend_range` in der Config (Standard 2). Bei klingender Note höchstens 9 Schritte pro
  Nachricht, im Mindestabstand, vor allen anderen Reglern. Der letzte Schritt wird wiederholt, wenn das
  Pedal ihn nicht bestätigt.
- **Controller auf Regler:** anlernbar in der Oberfläche (Karte „Keyboard synth“), abgelegt in
  `controls.json`. Ohne Datei: Mod-Wheel auf Rate.
- **Anzeige:** nur noch Noten im Terminal, Bend-Schritte nicht. Die Zeile nennt die klingende Note.
- **Überholte Werte** zählen nicht mehr als unbestätigt (neuer Zähler `overtaken`).
- **Expression auf einen Synth-Regler** wird nicht gesendet, wenn der gelernte Bereich größer ist als der
  Regler. Betrifft die Zuordnung des Nutzers für Preset 050 (Parameter 11, mit 0.10 als „Rate“ gelernt):
  Mit 0.20 liegt dort „LFO“ (0–20), sie muss neu gelernt werden.
- 84 Tests bestanden (19 neu). Die Karte wurde im Browser gegen einen nachgebauten Zustand geprüft
  (Anlernen starten und abbrechen, keine Skriptfehler), nicht gegen echte Geräte.
- **Am Gerät noch nicht geprüft:** alles hiervon. Key-Werte über 128, die Regler 3–13 von 0.20 per SysEx,
  das Verhalten bei schnellen Bends (bis zu 100 Nachrichten pro Sekunde).

## Erster Lauf mit KeySynth 0.20 am Gerät, 2026-10-07 18:09–18:13 Uhr

- **Key bis 1000 funktioniert am Pedal:** 623 Key-Nachrichten, 588 bestätigt, die 35 übrigen von der nächsten
  Nachricht überholt. Werte über 128 (bis 271) wurden 540-mal gesendet und bestätigt; ACK im Median nach 11 ms.
- **Bend:** 520 Schritte bei klingender Note, Abstand mindestens 10 ms, bis zu 71 Key-Nachrichten in einer
  Sekunde, ohne Ausfall und ohne Warnung.
- **Controller auf Regler:** Mod-Wheel auf Rate 861 gesendet, 844 bestätigt. Die Parameter 3–12 des Effekts
  (intern 3–11 und 13) wurden per SysEx gesetzt und bestätigt. Unbestätigt blieben nur Werte ohne Änderung
  und überholte Werte.
- **Anlernen in der Oberfläche:** elfmal benutzt, funktioniert. Das KOMPLETE KONTROL A61 sendet auf seinen
  acht Drehreglern CC 14–21.
- **Nachgebessert nach diesem Lauf:** Die Meldung über eine gesperrte Expression-Zuordnung kommt nur noch
  einmal pro Preset (vorher nach jedem Anlernen erneut), und ein Regler, dessen Zuweisung sich nicht ändert,
  behält seinen zuletzt gesendeten Wert (vorher ging er nach jedem Anlernen noch einmal hinaus). 85 Tests.
- **Ziele „Vib“ und „Trm“ (Wunsch des Nutzers, 18:3x Uhr):** LFO-Tiefe per Wheel oder Regler ab Off, ohne
  dass der Effekt einen weiteren Regler braucht. Zehn Stufen je Richtung (so fein wie der LFO-Regler des
  Effekts). 86 Tests. Am Gerät noch nicht geprüft.
- **Zweiter Lauf, 18:13–18:39 Uhr:** Der Nutzer hat einen Effekt vor den Synth gesetzt (Synth jetzt Effekt 2).
  Die alte Expression-Zuordnung für Preset 050 (Effekt 1, Parameter 11, 0–100, gelernt mit KeySynth 0.10 an
  dieser Stelle) zeigte damit auf den neuen Effekt 1 und war nicht mehr gesperrt. Um 18:37:50 gingen sieben
  Werte an Slot 0, Parameter 12; das Pedal hat keinen bestätigt. Folge: „acks are missing“, Mindestabstand
  20 ms, dann 40 ms, und damit auch ein langsamerer Bend. Das ist der bekannte Fall „Effekt getauscht, neu
  lernen“; die Zuordnung sollte gelöscht oder neu gelernt werden.
- **Daraufhin geändert:** Bend-Schritte halten immer den eingestellten Mindestabstand ein, unabhängig von der
  Drosselung der übrigen Parameter. 87 Tests.
- **Die Bridge endete um 18:39:53 ohne sauberes Beenden** (kein „Stopped.“, kein edit disable), als die
  Claude-Sitzung neu startete. Letzte Nachricht war ein bestätigtes Key 0. Neu gestartet um 18:52 Uhr.

## Umstellung auf KeySynth 0.21, 2026-10-07 19:49 Uhr

- Der LFO-Regler des Effekts hat jetzt 101 Stellungen (Vib50 … Vib1, Off, Trm1 … Trm50), das Vibrato reicht
  bis ±100 Cent. In der Bridge: `SYNTH_KNOBS` LFO 0–100, `LFO_OFF` 50. „Vib“ und „Trm“ haben damit 50 Stufen.
- Zusammen mit der Installation von 0.21 auf dem Pedal übernommen; die Bridge spielt 0.20 nicht mehr richtig
  (dort war Off = 10). 87 Tests.

## Oberfläche und Name, 2026-10-07 21:15 Uhr (Wünsche des Nutzers)

- **Name:** „Controller Bridge“ statt „Expression Bridge“ (Titel, README, Meldungen, Dateiname des Exports).
  Exportdateien tragen jetzt `controller-bridge/1`; das alte `expression-bridge/1` wird beim Import weiter
  angenommen. Der Ordner und die geplanten Pi-Dateinamen bleiben, wie sie sind.
- **Keine Vorbelegung:** Ohne `controls.json` ist kein Keyboard-Regler zugewiesen (vorher Mod-Wheel auf Rate).
  Die Zuweisungen des Nutzers wurden geleert: `controls.json` leer, `mappings: {}` in `config.yaml` (Preset 050
  und 095). Die gelernten Bereiche in `measurements.json` sind geblieben. Der Stand davor liegt in
  `ms-plus-synth/backup/bridge-vor-oberflaeche-2026-10-07/`.
- **Reihenfolge:** „Keyboard synth“ steht oben, darunter „Expression pedal“ und die Expression-Zuordnungen.
- **Aussehen:** schwarzes Bedienfeld im Holzrahmen, siehe `CLAUDE.md`. Nur CSS, kein Bild, keine fremde
  Schrift. Im Browser gegen einen nachgebauten Zustand geprüft, auch mit 375 px Breite.
- 88 Tests.

## Durchsicht und Stand für GitHub, 2026-10-07 21:40–22:00 Uhr

- **Learn-Schalter** einheitlich blau (auch „Learn parameter“); Orange bleibt für „Apply“ und „Save“.
- **Zwei Fehler bei der Durchsicht gefunden und behoben:**
  - `POST /api/synth` mit einem Regler, der kein Text ist (Liste, Objekt), hätte die Hauptschleife mit einem
    `TypeError` beendet. Jetzt „Unknown knob.“; Test dazu.
  - Wer nur das Keyboard nutzt, musste trotzdem einen Expression-CC in die Config eintragen, sonst startete
    die Bridge nicht. Jetzt nur noch nötig, wenn unter `ports:` ein Expression-Controller steht; Test dazu.
- pyflakes ohne Befund (ein ungenutzter Import in den Tests entfernt). Keine persönlichen Pfade im Code.
- **App mit Icon:** `Start Bridge.command` legt `Controller Bridge.app` an (siehe `CLAUDE.md`). Am Mac des
  Nutzers angelegt und darüber gestartet: Terminal-Fenster öffnet sich, Bridge „Ready“.
- **`keysynth/`:** Effekt 0.21 (bytegleich mit der auf dem Pedal installierten Datei), Icon, Quellen, README,
  Lizenz.
- README überarbeitet (Keyboard gleichrangig, App-Icon, neue Messwerte, Dateiliste, Danksagung).
- 89 Tests. Oberfläche mit den echten Geräten im Browser angesehen.

## Name wieder „Expression Bridge“, 2026-10-07 22:10 Uhr (Wunsch des Nutzers)

- Die Umbenennung in „Controller Bridge“ ist vollständig zurückgenommen, damit Name, Ordner und Repository
  übereinstimmen: Titel, README, Meldungen, Export (`expression-bridge/1`, Dateiname `expression-bridge-…`),
  Config-Kopfzeilen. Die App heißt jetzt `Expression Bridge.app`; die alte `Controller Bridge.app` ist entfernt.
- Die kurzlebige Kennung `controller-bridge/1` wird beim Import nicht angenommen; in der Zwischenzeit gab es
  keine Zuordnungen, also auch keine Exportdateien damit.
- **SYNTHESIS SYNx2** wird jetzt deutlicher genannt: im README des Synths oben und unter „Credits“, außerdem im
  README der Bridge. Übernommen wurde nur der Funktionsumfang als Idee, kein Code.

## Phase 4 begonnen: Vorbereitung für den Raspberry Pi, 2026-10-07 22:30–23:00 Uhr (ohne Pi)

- Neu: `config_schema.py`, `export.py`, `status_led.py`, `pi/expression-bridge.service`, `bridge.py --settings ORDNER`;
  Einzelheiten und Abweichungen vom Plan stehen in `CLAUDE.md` unter „Phase 4“.
- Der echte Stand dieser Installation besteht `export.py --check` (0 Presets mit Zuordnung, 5 gelernte Bereiche,
  1 Keyboard-Controller). Geschrieben wurde nichts.
- Beim Bauen gefunden und behoben: Zeitstempel wurden zuerst als „rohe Bytes“ erkannt; abgelehnte Änderungen im
  Betrieb aus der Datei veränderten den Zustand, bevor sie abgelehnt wurden; die LED wechselte ihr Muster erst nach
  bis zu einer Sekunde.
- 110 Tests (21 neu), pyflakes ohne Befund.
- **Nichts davon lief auf einem Pi.** Die Recherche zur Hardware steht im Projekt ms-plus-synth, `NOTES.md`.
- `github-upload/` enthält noch den Stand von 22:15 Uhr, ohne diese Dateien.


## KeySynth für MS-50G+ und MS-70CDR+, 2026-10-07 (Wunsch des Nutzers, ungetestet)

- `keysynth/` hat jetzt zwei Ordner: `ms-60b-plus/` (die bisherige, am Pedal geprüfte Datei, nur verschoben) und
  `ms-50g-plus_ms-70cdr-plus/` (der unveränderte stomphacks-Build 0.21, Target `0x0090`, „SFX“). Beide Dateien
  heißen `KEYSYNTH.ZD2`; Code und Tabellen sind bytegleich, sie unterscheiden sich in 18 Kopf-Bytes.
- **Die Datei für MS-50G+ und MS-70CDR+ war auf keinem Pedal.** Der Nutzer hat keines der beiden Geräte.
  Begründung und offene Punkte stehen im README des Synths und in `ms-plus-synth/NOTES.md`.
- **Am Code der Bridge ist nichts geändert.** Sie prüft das Modell-Byte nicht, die Effekt-ID ist dieselbe,
  Geräte-ID `6E` und Portname „ZOOM MS Plus Series“ gelten laut stomphacks und nam-to-zoom für alle drei Modelle,
  alle drei haben 6 Slots. Nur am MS-60B+ belegt ist der Leser für den Patch-Dump (`parse_patch_dump`); findet er
  den Synth nicht, meldet die Bridge das und ignoriert das Keyboard. Die Meldungen nennen weiter das MS-60B+.
- README und Dateiliste nachgezogen.

## KeySynth 1.00 im Veröffentlichungsordner, 2026-10-08 03:1x Uhr (Nutzer: „Passt mache ready“)

- `keysynth/ms-60b-plus/` und `keysynth/ms-50g-plus_ms-70cdr-plus/` enthalten jetzt **Version 1.00**: der Code von
  0.21, bytegleich, mit neuem Bild (heller Bodentreter, LED und vier Regler an den Stellen der Stock-Effekte,
  „KEY SYNTH“, Tastatur mit Fußschalter, unten „SLAMINSKI“, oben rechts „V1.0“ aus dem Manifest).
  `ms-60b-plus/KEYSYNTH.ZD2` ist bytegleich mit der am 08.10. um 03:07 installierten und zurückgelesenen Datei
  (sha256 `77642922…`). 0.22 und 0.23 waren Bildversuche am Pedal und sind nicht veröffentlicht.
- Quellen ergänzt: `source/effects/keysynth/icon/` (beide Bilder), `source/tools-local/draw_icon.py` und
  `set_icon.py`, Versionsnummer im Manifest.
- **Gegenprobe:** aus `keysynth/source/` in einer Kopie neu gebaut (zeichnen, bauen, Bild einsetzen, Kopf
  angleichen): beide Effektdateien und die Icon-Datei bytegleich mit den veröffentlichten. Desktop-Tests:
  6 486 010 Prüfungen bestanden.
- README des Synths nachgezogen (Version, Prüfsummen, Bild, Bauanleitung, Versionsliste).
- **Am Code der Bridge ist nichts geändert**, die Tests liefen deshalb nicht neu. Mit 1.00 auf dem Pedal ist die
  Bridge noch nicht gelaufen (der Code des Effekts ist derselbe wie bei 0.21).
- `github-upload/` neu gefüllt.
- **Nutzer am Pedal (08.10., ca. 03:10):** „Passt.“ Das Bild von 1.00 stimmt, „V1.0“ ist lesbar. Sobald etwas
  editiert wurde, schreibt das Pedal „Edited“ über die Ecke oben rechts und verdeckt die Versionsnummer; laut
  Nutzer „halb so wild“, bleibt so. README entsprechend gekürzt und ergänzt, `github-upload/` erneut gefüllt.

## KeySynth 1.10 statt 1.00, 2026-10-08 03:3x Uhr (Entscheidung des Nutzers)

- Der Synth heißt jetzt **1.10** (im Bild „V1.1“), passend zum Commit „Update V1.1 Beta“, mit dem die Dateien für
  MS-50G+ und MS-70CDR+ auf GitHub kamen. 1.00 war nur auf dem Pedal des Nutzers und wurde nicht committet.
- Beide Pedal-Ordner, Manifest und Bilder unter `keysynth/` sind 1.10; `ms-60b-plus/KEYSYNTH.ZD2` ist bytegleich
  mit der am 08.10. um 03:35 installierten und zurückgelesenen Datei (sha256 `ab92affa…`). Code weiter bytegleich
  mit 0.21. Gegenprobe aus `keysynth/source/`: alle drei Dateien bytegleich.
- README des Synths nachgezogen, `github-upload/` neu gefüllt. Am Code der Bridge nichts geändert.

## Stimmenverteilung für KeyPoly, 2026-10-08 (ohne Pedal, ohne den Effekt)

Anlass: KeyPoly, ein vierstimmiger Synth-Effekt, entsteht an anderer Stelle. Laut seiner Beschreibung
liegen Key1–Key4 auf Param 2–5 im Format von KeySynth, dahinter acht geteilte Regler (Level, Wave, Cutoff,
Reso, Atk, Rel, LFO, Rate); pro Stimme hält der Effekt Phase, Hüllkurve, Tonhöhe und die zuletzt verlangte
Tonhöhe für die Bend-Glättung. Offen war die Bridge: Sie konnte die vier Stimmen nicht verteilen.

- **Ein Codepfad für beide Effekte.** KeySynth ist jetzt der Sonderfall „eine Stimme“: `Session.voices`
  hält pro Key-Regler eine `Voice` (zugewiesene Note, gesendeter Key-Wert, offene Acks). Alle 66 bisherigen
  Synth- und SysEx-Tests laufen unverändert durch.
- **Zuteilung (`Session._assign`):** Gespielt werden die letzten vier Noten, gehaltene Tasten vor solchen,
  die nur das Sustain-Pedal hält (`Keys.notes`). Eine klingende Note behält ihre Stimme; beim Loslassen
  wird nur ihre Stimme geschlossen, die anderen werden gar nicht beschrieben. Eine neue Note nimmt die
  freie Stimme, die zuletzt dieselbe Note gespielt hat (so liegt ein Ton nie auf zwei Stimmen, siehe
  „phasengleich, 1,97 ×“ in der Beschreibung), sonst die am längsten freie. Die fünfte Note nimmt die
  Stimme der ältesten und wechselt ohne Gate-off dorthin; wird die neuere losgelassen, kommt die ältere
  zurück, wie bei KeySynth.
- **Akkorde gehen sofort raus**, eine Nachricht pro Note, kein Abstand. Jede Stimme hat ihre eigene
  Wiederholung bei fehlendem Ack (wie bisher 60 ms, bis zu drei Mal).
- **Pitch-Wheel:** jede klingende Stimme in Schritten von höchstens 9 Klicks, die Stimmen abwechselnd im
  Mindestabstand. Ein voller Bend eines Vierklangs dauert damit etwa viermal so lang wie auf KeySynth.
- **Key2–Key4 unbekannt:** Der Patch-Dump zeigt nur den ersten Regler eines Effekts. Ein Versuch, die
  übrigen Regler als je 12 Bit zu lesen, passte beim echten Dump vom 07.10. nicht (KeySynth: 0, 38, 1, 2,
  36, 1024, …). Die Bridge fasst Key2–Key4 deshalb erst an, wenn eine Note sie braucht; da freie Stimmen
  der Reihe nach drankommen, ist nach vier Noten jede einmal beschrieben. Beim Beenden schließt sie jede
  Stimme, die sie gespielt hat.
- **Config:** `synth.poly_effect_id` (Standard `null`). `effect_id` darf jetzt leer sein, wenn
  `poly_effect_id` gesetzt ist; beide verschieden. Gespielt wird der erste der beiden Effekte in der Kette.
  Gleiche Regeln in `config_schema.py` für den Pi.
- **Controller:** `controls.json` speichert weiter Name → CC. Namen, die beide Effekte haben (Level, Atk,
  Rel, LFO, Rate, Vib, Trm), wirken auf den Effekt im Preset; Cutoff, Reso, Wave nur auf KeyPoly, Wave1,
  Wave2, Pitch, Dtune, Mix, Glide nur auf KeySynth. Die Oberfläche zeigt die Regler des Effekts im Preset,
  ohne Effekt alle; die Statuszeile nennt den Effekt und bei KeyPoly die Note jeder Stimme.
- **Annahmen, am Manifest von KeyPoly zu prüfen** (`zoom_sysex.POLY_KNOBS`): Key1–Key4 0–1000, Wave 0–3
  (Saw, Sqr, Tri, Sine wie Wave1), LFO 0–100 mit Off = 50 (wie KeySynth 0.21, sonst stimmen „Vib“/„Trm“
  nicht), Level, Cutoff, Reso, Atk, Rel, Rate 0–100. Ein zu hoher Höchstwert würde Werte außerhalb des
  Reglers senden; KeySynth schaltet dann stumm (Guard im Kernel), was KeyPoly tut, ist offen.
- 22 neue Tests in `tests/test_poly.py`, 4 neue Fälle in `tests/test_export.py`. Der LED-Test
  `test_an_led_that_may_not_be_written_is_left_alone` schlägt in der Cloud-Umgebung schon vor der Änderung
  fehl (läuft als root, `chmod 0o444` sperrt dort nicht).
- **Am Pedal noch zu prüfen:**
  - Vier Key-Writes pro Akkord, fast gleichzeitig: Bestätigt das Pedal alle? Bisher bekannt ist nur, dass
    es von zwei Nachrichten an *denselben* Parameter im Abstand von 1 ms nur die zweite bestätigt. Gilt das
    auch für verschiedene Parameter, meldet die Bridge bei Akkorden „does not confirm notes“ und
    wiederholt die Noten; dann brauchen die Writes eines Akkords einen kleinen Abstand.
  - Zeit Taste → Ack pro Stimme (die Konsole nennt jetzt `Key1` … `Key4`).
  - Last mit vier Dreiecken, Bend eines Vierklangs, Stimmenklau bei gehaltenem Sustain-Pedal.
