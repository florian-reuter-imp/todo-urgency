# Projektauftrag: Persönliches Todo-Tool mit automatischer Priorisierung

Du hilfst mir (Florian), ein kleines, selbstgebautes Todo-Tool zu konzipieren und zu bauen. Wir haben die Anforderungen bereits ausführlich durchdacht – hier ist die vollständige Zusammenfassung. Bitte lies sie komplett, bevor du anfängst, und kläre die "Offenen Punkte" am Ende mit mir, bevor du größere Architekturentscheidungen triffst.

## Kontext / Warum dieses Projekt

Ich habe zwei bestehende Ansätze durchprobiert und beide sind gescheitert:
- **Microsoft To Do**: Reine Liste ohne echte Priorisierung. Die "Vorschläge"-Funktion sortiert im Zweifel nur nach "zuletzt hinzugefügt", weil ohne gepflegte Fälligkeits-/Wichtig-Flags kein besseres Signal existiert – und selbst mit Pflege zeigt es nur mehrere getrennte gefilterte Listen nebeneinander, keinen einzigen kombinierten Score.
- **Kalender-Blocking**: Scheitert am manuellen Verschieben nicht geschaffter Termine – mache ich in der Praxis nicht.
- Ein Trevor-AI-Abo hat mich nicht überzeugt (KI/UX mittelmäßig) – der einzige praktische Nutzen war die Outlook-Anbindung, aber Öffnen bedeutete jedes Mal erst Pflege/Triage, bevor es nützlich wurde. Das will ich unbedingt vermeiden.

## Muss-Kriterien (nicht verhandelbar)

1. **Echte automatische Priorisierung**: Ein berechneter Score aus mehreren Faktoren (Fälligkeit, Priorität, Alter, ggf. Abhängigkeiten/Tags) – kein manuelles Einsortieren, keine bloße Sortierung nach einem einzelnen Feld. Vorbild/Inspiration ist der offen dokumentierte Urgency-Algorithmus des Obsidian-Tasks-Plugins (https://publish.obsidian.md/tasks/Advanced/Urgency) und von Taskwarrior (https://taskwarrior.org/docs/urgency/) – **die Formel/Idee soll übernommen bzw. adaptiert werden, nicht die Software selbst** (siehe unten, warum wir weder Obsidian noch Taskwarrior direkt nutzen).
2. **Auf Abruf nutzbar, ohne Pflege-Overhead beim Öffnen**: Ich öffne das Tool nicht ständig, sondern bei Bedarf. Es muss dann sofort einen sinnvoll sortierten Zustand zeigen, ohne dass ich erst aufräumen/triagieren muss.
3. **Kostenlos**: Keine neuen Abos. Nur freie/Open-Source-Bausteine.
4. **Windows 11 als Hauptgerät**, Linux-Kompatibilität ist ein Plus (kein Muss, aber anstreben).
5. **Zugriff von mehreren Geräten** (Multi-Device), aber **Kalender-Synchronisation ist explizit nicht gefordert** – ich nutze diese Liste, um frei meine nächste Tätigkeit zu wählen, nicht um Termine zu blocken.

## Warum weder Obsidian noch Taskwarrior direkt

- **Obsidian Tasks-Plugin**: Hat genau die gewünschte Urgency-Logik, aber sie läuft nur als Plugin innerhalb der Obsidian-Electron-Runtime. Das würde bedeuten, Obsidian müsste offen sein, nur um die Liste zu sehen – das ist der Overhead, den ich vermeiden will.
- **Taskwarrior**: Hat eine sehr gute, transparente Urgency-Formel, aber es gibt bis heute kein offiziell unterstütztes natives Windows-Binary (nur WSL wird empfohlen, Community-Workarounds sind quasi tot). WSL dauerhaft laufen lassen oder bei Bedarf starten ist echte, dauerhafte Reibung auf meinem Hauptgerät – widerspricht Kriterium 2.

→ Deshalb: **Eigenbau**, der die *Idee* (Urgency-Score) und das *Datenformat* (klartextbasiert, an die Tasks-Plugin-Syntax angelehnt) übernimmt, aber komplett unabhängig von Obsidian oder Taskwarrior läuft.

## Architektur-Entscheidungen (bereits getroffen)

### GUI: Lokale Weboberfläche
Eine kleine HTML/JS-Oberfläche, lokal im Browser geöffnet (nicht als gehostete Website, sondern lokal). Läuft identisch unter Windows und Linux, kein natives Toolkit-Setup nötig. Impliziert einen kleinen lokalen Server (Backend), der die Weboberfläche ausliefert und eine einfache API zum Lesen/Schreiben der Daten bereitstellt.

**Vorschlag Tech-Stack** (bitte mit mir absprechen, ich bin offen): Python-Backend (ich arbeite viel mit Python), z. B. FastAPI oder Flask, + einfaches Vanilla-JS-Frontend ohne schweren Build-Prozess. Minimal-Dependency-Ansatz bevorzugt.

### Datenspeicherung: Klartext-Datei, Format an Obsidian-Tasks-Syntax angelehnt
Beispiel: `- [ ] Präsentation vorbereiten 📅 2026-09-20 🔺 #IMVERSE`
- Menschenlesbar, editierbar auch außerhalb der App (z. B. falls ich die Datei doch mal in Obsidian öffnen will – bewusst kompatibel gehalten, keine Einbahnstraße).
- Kein eigenes Binärformat, keine Datenbank nötig für den Anfang.

### Modulare Trennung (bitte strikt einhalten)
Drei klar getrennte Schichten, jede unabhängig austauschbar:

1. **`DataStore`** – kümmert sich NUR um Lesen/Schreiben von rohen Bytes/Text an einem Ort. Kennt weder das Dateiformat noch die Sync-Mechanik.
   ```
   class DataStore(Protocol):
       def load(self) -> str: ...
       def save(self, content: str) -> None: ...
       def changed_since_last_load(self) -> bool: ...
   ```
   Standardimplementierung: `LocalFileStore`, die einfach einen lokalen Dateipfad liest/schreibt.
   - **Wichtig**: Atomares Schreiben (erst in Temp-Datei schreiben, dann umbenennen), damit eine Sync-Software (siehe unten) nie eine halb geschriebene Datei hochlädt.
   - **Wichtig**: Change-Detection (mtime-Vergleich reicht für den Anfang; später ggf. Dateisystem-Watcher via `watchdog`-Lib, plattformübergreifend Windows/Linux), damit von einem anderen Gerät synchronisierte Änderungen nicht überschrieben werden.

2. **`TaskCodec`** – übersetzt Rohtext ↔ Task-Objekte (Parsen der Markdown-Checkbox-Syntax und Serialisieren zurück). Komplett unabhängig vom `DataStore`.

3. **Urgency-Scorer** – berechnet aus den geparsten Task-Objekten einen numerischen Score zur Sortierung. Eigene, von den anderen beiden unabhängige Komponente, damit die Formel später leicht tunbar/austauschbar ist.

Die GUI-Schicht kennt nur die geparsten Task-Objekte + Scores, nie das Rohformat oder den Speicherort.

### Synchronisation: Bewusst NICHT in der App, sondern auf Betriebssystem-Ebene
Die App weiß nichts von OneDrive, Syncthing o.ä. – sie sieht nur einen lokalen Ordner/Datei. Die Synchronisation übernimmt vollständig eine externe, separat laufende Software:

- **Geplant**: Die Datei liegt in einem OneDrive-synchronisierten Ordner (das Konto, das bereits auf meinem Windows-11-Gerät eingeloggt ist).
  - Windows: nativer OneDrive-Client (bereits vorhanden, nichts einzurichten).
  - Linux (falls/wenn relevant): [abraunegg/onedrive](https://github.com/abraunegg/onedrive) – aktiv gepflegter Open-Source-Client, unterstützt Daemon-Modus mit inotify-Überwachung (nahezu Echtzeit-Sync), läuft als systemd-Service im Hintergrund.
- Warum kein Syncthing: Meine Geräte sind zeitlich eher disjunkt online (kein zuverlässiges gleichzeitiges Treffen im Netz), reines P2P würde also nicht robust syncen. Ein Cloud-Zwischenspeicher (OneDrive) löst das, weil der Anbieter selbst als immer-online-Hub fungiert.
- Diese Entscheidung ist bewusst außerhalb der App gehalten – falls ich später doch Syncthing (mit eigenem Hub) oder einen anderen Cloud-Ordner nutzen will, ändert sich nur der Pfad, den `LocalFileStore` bekommt, nichts am Code.

### Auto-Übertrag nicht erledigter Aufgaben
Ist nur für datumsgebundene Aufgaben relevant (die sollen nicht "verloren" wirken, wenn ihr Datum verstreicht). Aufgaben ohne Datum brauchen keine explizite Übertragslogik – sie bleiben ohnehin in der Liste und werden vom Urgency-Score mit steigendem Alter automatisch wieder nach oben gespült.

## Nicht im Scope

- Keine Kalender-/Outlook-Synchronisation (bewusst nicht gefordert).
- Keine Cloud-API-Direktanbindung an OneDrive (Microsoft Graph API o.ä.) – die Sync-Schicht ist bewusst extern/OS-seitig gehalten, nicht in der App.
- Keine Multi-User-/Team-Funktionen, das ist ein reines Einzelnutzer-Tool.
- Keine mobile App (Startpunkt ist Desktop Windows/Linux; ob/wie Handyzugriff später kommt, ist offen und nicht Teil des ersten Wurfs).

## Offene Punkte – bitte zuerst mit mir klären, nicht annehmen

1. **Genaues Datenmodell einer Aufgabe**: Welche Felder brauche ich wirklich (Titel, Fälligkeitsdatum, Priorität – wie viele Stufen? geplantes/Start-Datum, Projekt/Tag, Notizfeld, "erledigt"-Status)?
2. **Genaue Urgency-Formel/Gewichtung**: Start mit einer sinnvollen Default-Gewichtung (angelehnt an Taskwarrior/Tasks-Plugin), aber offen für Anpassung – soll ich die Gewichte konfigurierbar machen?
3. **Darstellung**: Eine einzige durchsortierte Liste, oder zusätzlich Gruppierungen (z. B. nach Projekt/Tag)?
4. **Wie startet die App im Alltag?** Doppelklick-Starter, Tray-Icon, Autostart bei Login? Soll der lokale Server dauerhaft im Hintergrund laufen oder nur bei Bedarf gestartet werden (Kriterium: kein Overhead, aber auch kein manuelles "Server starten" jedes Mal)?
5. **Genauer OneDrive-Ordnerpfad**, in dem die Datei liegen soll.
6. **Linux-Distribution**, falls/wenn das konkret getestet werden soll.

## Meine Arbeitsweise (bitte berücksichtigen)

Ich bevorzuge iteratives Vorgehen – schnell zu einem realistischen, besprechbaren Ergebnis kommen statt vorschnell auf Vollständigkeit/Polish zu optimieren. Bitte erst ein kleines lauffähiges Grundgerüst (Datei lesen, parsen, Score berechnen, sortiert in einer simplen Weboberfläche anzeigen) bauen, bevor Details wie Auto-Übertrag, Change-Detection oder Feinschliff der GUI dazukommen.
