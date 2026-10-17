# Todo-Tool

Kleines persönliches Todo-Tool mit automatischer Priorisierung. Keine externen
Dependencies — nur Python 3.11+ (Stdlib). Läuft unter Windows und Linux.

## Starten

| Plattform | Befehl |
|---|---|
| Windows | Doppelklick auf `todo.cmd` (ohne Konsole) bzw. `todo-debug.cmd` (mit Ausgabe) |
| Linux / macOS | `./todo.sh` |
| überall | `python -m app` |

Der Starter hebt den lokalen Server an, öffnet die Oberfläche und **beendet sich
nach 20 Minuten ohne offenes Fenster** von selbst — kein Hintergrunddienst, aber
auch kein manuelles Starten. Ein zweiter Doppelklick öffnet nur ein weiteres
Fenster, statt am belegten Port zu scheitern.

### App-Fenster statt Browser-Tab

Standardmäßig wird ein Chromium im App-Modus gestartet (`--app=<url>`): keine
Adressleiste, keine Tabs, eigenes Fenster, eigener Taskleisten-Eintrag. Gesucht
wird in dieser Reihenfolge Edge, Chrome, Chromium, Brave, Vivaldi; ist keiner zu
finden, geht es im Standardbrowser auf.

```
python -m app                      # App-Fenster (Default)
python -m app --window tab         # normaler Browser-Tab
python -m app --window none        # nur Server
python -m app --size 1600,1000     # Startgröße
python -m app --restart            # laufende Instanz ersetzen
```

### Neustart

Läuft bereits eine Instanz, öffnet ein zweiter Start normalerweise nur ein
weiteres Fenster. **Hat sich aber der Code geändert, wird die laufende Instanz
beendet und ersetzt.** Dafür vergleicht der Starter den Fingerabdruck der
Quelldateien mit dem, den der laufende Server beim *eigenen Start* eingelesen hat
(`GET /api/version`) — nicht mit dem aktuellen Stand der Platte, sonst meldete ein
veralteter Prozess immer Gleichstand.

Das ist nötig, weil das offene Fenster den Server am Leben hält: der Auto-Stop
greift erst 20 Minuten *ohne* Fenster. Ohne diese Prüfung kann man stundenlang auf
altem Backend-Code sitzen, während die Oberfläche längst neu ist — das Frontend
lädt bei jedem Öffnen frisch (`Cache-Control: no-store`), der Python-Prozess nicht.

Ein Neustart leert den Verlauf für Rückgängig; der Starter sagt das dazu.
`--restart` erzwingt ihn auch ohne Codeänderung.

Die OS-Titelleiste bleibt dabei bestehen. Ganz rahmenlos wird es, wenn du es im
Browser über „Diese Seite als App installieren" installierst — dann greift
`display_override: window-controls-overlay` aus dem Manifest, und die Seite
zeichnet den Titelleisten-Bereich selbst (die Kopfzeile wird zur Ziehfläche).

Die Sollgröße steht in `config.toml` unter `[window] size` (Default `1240,860`),
`--size` überschreibt sie einmalig.

### Layoutregel: nur Unbegrenztes scrollt

Die Seite selbst scrollt **nie**. Es gibt immer genau einen Scrollbereich, und der
liegt innerhalb des Layouts. Die Entscheidung hängt daran, ob die Länge eines
Inhalts von den Daten bestimmt wird:

| Bereich | Länge | Verhalten bei wenig Platz |
|---|---|---|
| Aufgabenliste | von den Daten, unbegrenzt | **scrollt** — bei 200 Aufgaben hilft kein größeres Fenster |
| Gewichte-Tafel | fest, aber hoch; nur zeitweise offen | **scrollt**, als Overlay über der Ansicht, damit sie die Matrix nicht verdrängt |
| Matrix | fester Inhalt, beliebig skalierbar | **skaliert** — ein Diagramm scrollt man nicht |
| Kopfzeile, Erfassung, Filter | fest und kurz | **nie** — dafür sorgen die Kompaktstufen |

Die Matrix setzt ihre `viewBox` zur Laufzeit auf die **gemessene Pixelgröße**
(`ResizeObserver`). Dadurch füllt sie die Fläche immer ganz aus, und die Schrift
skaliert *nicht* mit dem Fenster — geschrumpfte Beschriftung wäre wertlos. Wird es
eng, fallen stattdessen gestaffelt Elemente weg: zuerst die Achsentexte, dann die
Quadrantennamen, zuletzt die Punktbeschriftungen.

Der Achsenbereich folgt den Daten (statt fester Untergrenzen), hält die
Trennlinien aber immer deutlich im Bild. Punktbeschriftungen weichen sowohl
anderen Beschriftungen als auch fremden Punkten aus; findet sich keine freie Lage,
bleibt ein Punkt lieber unbeschriftet als überdruckt.

Darunter greifen Kompaktstufen (ab 780 px Höhe weniger Abstand und kein
„mehr"-Block, ab 660 px kleinere Schrift und kein Titel) statt Scrollbalken.
Unterhalb von etwa 900×620 wird die Matrix knapp — dann hilft nur, das Fenster
wieder größer zu ziehen; eine Mindestgröße kann eine Webseite nicht erzwingen.

`#list` und `#matrix` in der Adresse wählen die Ansicht, sie übersteht damit einen
Reload.

## Daten

Alles steht in `todo.md`, normalem Markdown in Obsidian-Tasks-Syntax:

```
- [ ] Präsentation vorbereiten #IMVERSE 🔺 ➕ 2026-09-02 📅 2026-09-18
```

| Feld | Syntax |
|---|---|
| erledigt | `- [x]` |
| Priorität | `🔺` höchste · `⏫` hoch · `🔼` mittel · `🔽` niedrig · `⏬` niedrigste |
| fällig | `📅 YYYY-MM-DD` |
| erstellt | `➕ YYYY-MM-DD` (wird beim Anlegen automatisch gesetzt) |
| Aufwand | `⏱ 20m` · `⏱ 2h` · `⏱ 1h30m` · `⏱ 1d` (= 8 h) — optional |
| nicht vor | `🛫 YYYY-MM-DD` — bis dahin ausgeblendet |
| Wiederholung | `🔁 every week` · `every 2 weeks` · `every monday` · `… when done` |
| erledigt am | `✅ YYYY-MM-DD` (wird beim Abhaken gesetzt) |
| Projekt | `#tag` irgendwo in der Beschreibung |

`⏱` ist kein offizielles Tasks-Feld; Obsidian zeigt es schlicht als Text in der
Beschreibung an, es geht also nichts verloren.

### Zurückstellen (`🛫`)

Eine Aufgabe mit Startdatum in der Zukunft **verschwindet** aus Liste und Matrix,
statt abgewertet zu werden — was man nicht anfangen kann, ist Rauschen. Der Knopf
`🛫` in der Zeile stellt eine Woche zurück; „zurückgestellte zeigen" blendet sie
wieder ein, dann mit ihrem ungeschönten Score.

### Wiederholungen (`🔁`)

Beim Abhaken wird die nächste Instanz direkt über der abgehakten Zeile angelegt,
mit allen Feldern und verschobenen Terminen. Ein vorhandenes `🛫` behält seinen
Abstand zur Frist. Erkannt wird eine pragmatische Teilmenge der Tasks-Syntax
(`every day`, `every 3 days`, `every 2 weeks`, `every month`, `every year`,
`every monday`, deutsche Wochentage und `alle 2 Wochen`, jeweils optional mit
`when done`). Wird eine Regel nicht erkannt, bleibt sie als Text erhalten und die
Aufgabe ist beim Abhaken einfach erledigt — verloren geht nie etwas.

Die Datei darf jederzeit von Hand oder in Obsidian bearbeitet werden. Überschriften,
Fließtext, Einrückungen und nicht ausgewertete Tasks-Emojis (`🛫 ⏳ ✅ 🔁`) bleiben
beim Speichern unverändert erhalten; nur die Feldreihenfolge innerhalb einer
Task-Zeile wird auf die kanonische Reihenfolge normalisiert.

## Synchronisation

Bewusst **nicht** in der App. `todo.md` liegt im OneDrive-Ordner und wird vom
OneDrive-Client synchronisiert. Geschrieben wird atomar (Temp-Datei + `os.replace`),
damit der Sync-Client nie eine halb geschriebene Datei hochlädt. Vor jedem Schreiben
lädt die App frisch; ist die Zeile inzwischen von außen verändert worden, antwortet
die API mit `409` und die Oberfläche lädt neu, statt fremde Änderungen zu überschreiben.

Anderer Speicherort (Syncthing, anderer Cloud-Ordner)? Nur `data.file` in
`config.toml` ändern — am Code ändert sich nichts.

## Priorisierung

Der Score ist in zwei Achsen zerlegt, deren Summe der Listen-Score ist:

```
Dringlichkeit (X) = Fälligkeit + Alter
Wichtigkeit   (Y) = Priorität  + Tags
Score             = Dringlichkeit + Wichtigkeit
```

Dadurch zeigen **Liste** (nach Score sortiert) und **Matrix** (X gegen Y, Eisenhower)
garantiert dasselbe — die Matrix trennt die Zahl nur auf. Alle Gewichte stehen
kommentiert in `config.toml`; Defaults sind an Taskwarrior und das
Obsidian-Tasks-Plugin angelehnt. Der Score-Wert in der Liste zeigt im Tooltip
seine Einzelbeiträge.

### Aufwand steuert die Vorlaufzeit

Eigene Erweiterung gegenüber beiden Vorlagen: dort sind ein Zehn-Minuten-Anruf und
eine Zwei-Tage-Ausarbeitung mit derselben Frist ununterscheidbar. Hier verlängert
der Aufwand die Vorlaufzeit — der Score beginnt früher zu steigen:

```
Horizont = future_days_for_min + Aufwand_in_Stunden × days_per_hour   (14 + h × 1.5)
```

| Aufwand | fällig in 17 Tagen | Horizont |
|---|---|---|
| kein Feld | 7.30 | 14.0 d |
| `⏱ 10m` | 7.30 | 14.2 d |
| `⏱ 2d` | **11.78** | 38.0 d |

Ohne Aufwandsfeld gilt exakt das Verhalten ohne diese Erweiterung.

## Optionale Features

Jedes der folgenden Felder ist in `[features]` der `config.toml` abschaltbar.
`false` heißt: das Feld wird weiter geparst und beim Speichern **unverändert
erhalten**, aber vom Scorer ignoriert und in der Oberfläche ausgeblendet. Alles
aus ergibt den einfachen Weg: eine sortierte Liste aus Titel, Datum, Priorität.

| Feature | Wirkung |
|---|---|
| `effort` | Aufwandsfeld `⏱`, der Horizont daraus, und der Filter „höchstens … Aufwand" |
| `grouping` | Umschalter „nach Projekt gruppieren" (nach erstem `#tag`, Gruppen nach ihrer dringendsten Aufgabe) |
| `snooze` | `🛫 nicht vor`: zurückgestellte Aufgaben ausblenden |
| `recurrence` | `🔁`: beim Abhaken die nächste Instanz anlegen |
| `done_dates` | `✅`: Datum des Abhakens mitschreiben |
| `dependencies` | noch nicht implementiert |

## Eingabe

Aufwand und Wiederholung sind **Zahl plus Einheit**, kein Freitext — es gibt
nichts zu erraten:

- Aufwand: Zahl + `Minuten` / `Stunden` / `Arbeitstage`. Leer = keine Angabe.
- Wiederholung: `nicht wiederkehrend` / `alle … Tage|Wochen|Monate|Jahre` /
  `an einem Wochentag`, dazu optional `ab Erledigung`.

Beim Laden wird zurückgerechnet: 90 Minuten erscheinen als „90 Minuten", 120 als
„2 Stunden", 480 als „1 Arbeitstag". Eine Wiederholungsregel, welche die Dropdowns
nicht darstellen können, bleibt beim Speichern unangetastet erhalten.

Freitext geht weiter über `Rohzeile` im Bearbeitungsmodus — dort steht eine Legende
aller Felder darunter.

## Gewichte tunen

`⚙ Gewichte` öffnet eine Tafel in zwei Modi. Die Liste sortiert sich beim Schieben
**probeweise** um (`POST /api/tasks/preview`, schreibt nichts); erst „Übernehmen"
schreibt in die `config.toml`, per gezieltem Zeilenersatz und atomar — Kommentare,
Einrückung und der Datenpfad bleiben erhalten.

**Einfach** (Default): acht ganzzahlige Regler mit Fragen statt Variablennamen.
Die fünf Wirkungsregler laufen 0–10, wobei **5 exakt dem Auslieferungswert
entspricht** — „5" liest sich als normal, 0 schaltet den Faktor aus. Darunter
liegen die Feinwerte; die Umrechnung ist so gewählt, dass die Defaults genau auf
ganze Zahlen fallen. Hat jemand im Erweitert-Modus etwas feiner eingestellt, als
die Regler darstellen können, sagt die Tafel das und warnt, dass Schieben die
Feinwerte überschreibt.

**Erweitert**: alle 17 Rohwerte als Fließkomma-Regler. `★` und ein farbiger
Hintergrund markieren die Stellschrauben, an denen man üblicherweise dreht —
Fälligkeits-Wirkung, Vorlauf in Tagen, Aufwands-Vorlauf, höchste Priorität,
„ohne Marker" und die beiden Matrix-Schwellen.

„Auslieferungswerte" setzt in beiden Modi alles auf die Defaults zurück.

Eine Teilübergabe an die API legt die Werte über den **aktuell geladenen** Stand,
nicht über die Defaults — eine unvollständige Anfrage kann die übrigen Gewichte
also nicht stillschweigend zurücksetzen.

## Zeitreise

Der Regler über der Matrix verschiebt das angenommene Heute um bis zu 28 Tage
(`GET /api/tasks?today=YYYY-MM-DD`) und zeigt, was gerade dabei ist, nach rechts in
„Sofort erledigen" zu rutschen. Solange er nicht auf „heute" steht, ist **jede
Bearbeitung gesperrt** — sonst schreibt man irgendwann Werte, die auf einem
simulierten Datum beruhen.

## Rückgängig und Wiederholen

`Strg+Z` / `Strg+Y` (auch `Strg+Umschalt+Z`), dazu ↶ ↷ in der Kopfzeile und nach
jeder Änderung ein Hinweis unten links mit „Rückgängig“.

Umgesetzt als **Schnappschuss-Stapel**, nicht als Inverse pro Operation: der
`DataStore` ersetzt ohnehin immer den ganzen Dateiinhalt, also genügt es, den Text
davor zu merken. Dadurch ist jede Operation gleich gut abgedeckt — auch die, bei
denen eine Inverse mühsam wäre: Rohzeilen-Ersetzung und das Abhaken einer
wiederkehrenden Aufgabe, bei dem zwei Zeilen auf einmal entstehen. Alle
Schreibpfade laufen über `TaskService._commit()`.

Zwei Stapel, 20 Schritte tief:

```
Zustände   A → B → C → D        undo: A B C   redo: –
Strg+Z     A → B → C → D        undo: A B     redo: D C
                 ↑
Strg+Y     A → B → C → D        undo: A B C   redo: D
                       ↑
```

Es ist also kein Pendeln zwischen zwei Zuständen, sondern eine begehbare Linie.
Wer nach einem Rückschritt etwas Neues ändert, verwirft damit den Redo-Pfad — die
alte Zukunft existiert nicht mehr.

**Grenze zur Außenwelt:** Rückgängig prüft vor dem Schreiben, ob die Datei noch
exakt so aussieht, wie die App sie zuletzt hinterlassen hat. Hat ein Editor,
Obsidian oder OneDrive zwischendurch etwas geändert, antwortet die API mit `409`
und tut **nichts** — eine fremde Bearbeitung wird nie verworfen. Der Stapeleintrag
bleibt erhalten.

Die Historie lebt nur im laufenden Server und ist gerätelokal. Nach dem Auto-Stop
(20 Minuten ohne offenes Fenster) ist sie weg — deshalb fragt Löschen weiterhin
nach, solange es kein Archiv gibt. Gewichtsänderungen haben keinen eigenen Stapel;
dafür gibt es die Vorschau und „Auslieferungswerte“.

## Tests

```
python -m unittest discover tests
```

107 Tests, nur Stdlib. Sie deckten beim Schreiben zwei stille Datenverluste auf:
die Rohzeilen-Bearbeitung schrieb nicht, und Text **nach** einem Datumsfeld
(`📅 2026-10-20 #projekt`) ging beim Parsen verloren. Beide sind als
Regressionstests festgehalten.

## Bearbeiten

- **Liste**: Titel anklicken oder `✎` — Beschreibung, Datum, Priorität, Aufwand.
  `Esc` bricht ab.
- **Rohzeile**: im Bearbeitungsmodus auf `Rohzeile` — die Markdown-Zeile direkt
  editieren. Notausgang für alles, was die Oberfläche nicht kennt.
- **Matrix**: Punkte ziehen. Waagerecht ändert die Frist, senkrecht die Priorität
  (sie rastet auf die nächste Stufe ein). Der Client schickt nur die
  Zielkoordinaten; die Umrechnung macht der Scorer, die GUI kennt die Formel nicht.
  Doppelklick hakt ab.

## Aufbau

Drei unabhängig austauschbare Schichten:

| Datei | Aufgabe |
|---|---|
| [app/datastore.py](app/datastore.py) | `DataStore`-Protokoll + `LocalFileStore`: nur Rohtext lesen/schreiben, atomar, mit Change-Detection |
| [app/codec.py](app/codec.py) | `TaskCodec`: Rohtext ↔ `Task`-Objekte |
| [app/scorer.py](app/scorer.py) | `UrgencyScorer`: Score, Achsen, Horizont |
| [app/recurrence.py](app/recurrence.py) | Wiederholungsregeln ↔ nächstes Datum |
| [app/config.py](app/config.py) | `config.toml` lesen und kommentarerhaltend schreiben |
| [app/server.py](app/server.py) | HTTP-API + Auslieferung der Oberfläche |
| [app/web/](app/web/) | Vanilla-JS-Oberfläche, kein Build-Schritt |
| [tests/](tests/) | 107 Tests, `python -m unittest discover tests` |

Die Feld-Emojis in `codec.py` stehen bewusst als Escape-Sequenzen (`"\U0001f4c5"`)
und der Block ist reines ASCII — sonst zerschießt irgendwann ein Werkzeug mit
falscher Kodierung die Konstanten, und ein unsichtbarer Variation Selector ist im
Editor nicht zu sehen.

Die Oberfläche kennt nur geparste Tasks und Scores — nie das Rohformat, nie den
Speicherort.

## Noch offen

- **Abhängigkeiten** (`🆔` / `⛔`): blockierte Aufgaben ausblenden, Blocker erbt die
  Dringlichkeit dessen, was er blockiert. Die beiden Emojis werden bereits
  unverändert durchgereicht, aber noch nicht ausgewertet.
- Natürlichsprachiges Quick-Add („morgen", „in 3 Tagen", `!!`)
- Tastaturbedienung (`j`/`k`/`x`/`e`)
- Kontext-Einfärbung der Matrixpunkte nach Projekt
- CLI (`todo add`, `todo next`)
- Auto-Übertrag verstrichener Fälligkeitsdaten
- Dateisystem-Watcher (`watchdog`) statt mtime-Vergleich
