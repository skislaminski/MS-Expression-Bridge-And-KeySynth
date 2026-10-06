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
