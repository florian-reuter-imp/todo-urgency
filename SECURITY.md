# Sicherheit

Dieses Dokument beschreibt, wogegen das Tool schützt, wogegen **nicht**, und
eine Schwachstelle, die es hatte. Es steht hier, damit jeder das Restrisiko
selbst einschätzen kann, statt es mir glauben zu müssen.

Stand: 10.11.2026

## Was das hier für ein Programm ist

Ein Werkzeug für **einen** Benutzer auf dem **eigenen** Rechner. Es gibt
bewusst keine Benutzerkonten, keine Anmeldung und keine Verschlüsselung:

- Die Aufgaben liegen als Klartext-Markdown in einer Datei, die der Benutzer
  selbst im Editor bearbeiten soll. Das ist eine Kernanforderung, kein
  Versehen.
- Der Server lauscht nur auf `127.0.0.1`. Aus dem LAN oder dem Internet ist er
  nicht erreichbar.
- Es gibt keine Authentifizierung. Wer auf dem Rechner Code ausführen darf,
  kann die Datei ohnehin direkt lesen und schreiben — eine Anmeldung am Server
  würde daran nichts ändern und nur Sicherheit vortäuschen.

Daraus folgt das Schutzziel: **Webseiten im Browser des Benutzers dürfen das
Tool nicht fernsteuern.** Alles andere ist außerhalb des Bedrohungsmodells und
unten unter „Restrisiko" aufgeführt.

## Gefundene Schwachstelle: fehlende Herkunftsprüfung

**Betroffen:** alle Versionen bis einschließlich Commit `d3a8fd6`
**Behoben in:** `a896d73` (10.11.2026)
**Schweregrad:** mittel — unbemerkte Datenänderung, unter Zusatzbedingung auch
Datenabfluss
**Fremde Nutzer betroffen:** nein. Das Repository war bis zu diesem Zeitpunkt
privat, es gab außer dem Autor keine Nutzer.

### Das Missverständnis dahinter

Ein Server auf `127.0.0.1` ist **nicht** automatisch privat. Jede Webseite, die
im Browser offen ist, darf ihn anfunken — sie läuft auf demselben Rechner, ihre
Anfrage kommt also von `127.0.0.1` und ist auf Netzwerkebene von der eigenen
Oberfläche nicht zu unterscheiden. Keine Firewall hilft; Windows filtert
Loopback-Verkehr überhaupt nicht.

Der Browser schützt dabei nur die **Antwort**, nicht die **Wirkung**: Bei einer
sogenannten *simple request* — POST mit `Content-Type: text/plain`, ohne
eigene Kopfzeilen — fragt er nicht vorher um Erlaubnis (kein Preflight). Die
Anfrage geht raus, der Server führt sie aus, und nur das Ergebnis bleibt der
fremden Seite verborgen. Das braucht sie auch nicht: der Schaden ist dann schon
eingetreten.

### Was konkret ausnutzbar war

| Wirkung | ausnutzbar | wie |
|---|---|---|
| Aufgaben anlegen | **ja** | POST `/api/tasks` mit `text/plain` |
| Server beenden | **ja** | POST `/api/shutdown` |
| Prozess neu starten | **ja** | POST `/api/restart` — startete `pythonw -m app` |
| Aufgabenliste lesen | **ja, mit DNS-Rebinding** | eigener Domainname, der auf `127.0.0.1` zeigt; dann hält der Browser seine Seite für gleicher Herkunft und gibt ihr auch die Antworten |
| Ändern, Abhaken, Löschen | nein, aber nur zufällig | `PATCH`/`DELETE` lösen einen Preflight aus, den der Server nicht beantwortete |
| Gewichte verdrehen | nein, aber nur zufällig | `PUT`, gleiche Begründung |

Der Lesefall über DNS-Rebinding ist der ernste: dort fließen Aufgabentexte,
Projektnamen und Termine ab.

### Nachgewiesen, nicht vermutet

Mit einem echten headless Edge, Angreiferseite auf Port 8898, Tool auf 8899 —
also eine andere Herkunft, obwohl beides `127.0.0.1` ist:

```
ERGEBNIS: Der Browser hat die Anfrage rausgelassen,
          der Server hat sie ausgefuehrt.
```

Die Zeile stand anschließend in der Datei, obwohl die Angreiferseite die
Antwort nie zu sehen bekam.

## Der Fix

Eine Prüfung in `Handler._dispatch` (`app/server.py`), vor jeder Route und vor
jedem Dateizugriff. Sie wertet zwei Kopfzeilen aus, die **der Browser** setzt —
nicht die Seite, deshalb sind sie aus einer Webseite heraus nicht fälschbar:

| Kopfzeile | Regel | wogegen |
|---|---|---|
| `Host` | muss einer der eigenen Namen sein (`127.0.0.1`, `localhost`, `::1` oder der eingestellte `server.host`). Gilt für **alle** Methoden. | DNS-Rebinding — schützt damit auch das Lesen |
| `Origin` | bei `POST`/`PUT`/`PATCH`/`DELETE` müssen Adresse **und Port** die eigene Herkunft sein | fremde Webseiten (CSRF) |
| `Sec-Fetch-Site` | alles außer `same-origin` und `none` wird abgewiesen | greift auch, wenn kein `Origin` mitkommt |

Abgewiesen wird mit `403`. Ein **fehlender** `Origin` ist erlaubt: ein Browser
darf ihn bei fremder Herkunft nicht weglassen, während `curl`, Python und das
eigene `--stop` ihn nie mitschicken. „Kein `Origin`" heißt also „keine
Webseite" — siehe dazu aber den ersten Punkt unter Restrisiko.

Nebenbei behoben: Der Rumpf einer abgelehnten Anfrage wird vor der Antwort
verworfen (`_drain`). Ohne das blieben die Bytes ungelesen im Socket stehen,
das Betriebssystem schickte beim Schließen ein RST, und der Absender sah einen
Verbindungsabbruch statt der `403`.

## Restrisiko

Das Folgende ist **nicht** abgedeckt. Teils bewusst, teils weil es sich mit
diesem Ansatz nicht abdecken lässt.

1. **Andere Programme auf demselben Rechner.** Die Herkunftsprüfung bindet nur
   Browser, weil nur Browser gezwungen sind, die Wahrheit zu sagen. Ein
   beliebiges lokales Programm setzt `Origin` und `Host` frei und hat damit
   vollen Zugriff auf die API. Das ist hingenommen: so ein Programm kann die
   Markdown-Datei genauso gut direkt öffnen.
2. **Andere Benutzerkonten auf demselben Rechner.** `127.0.0.1` ist pro
   Rechner, nicht pro Benutzer. Auf einem gemeinsam genutzten Gerät kann ein
   anderes angemeldetes Konto den Port erreichen und — ohne `Origin` — alles
   tun. Wer das Tool auf einem Mehrbenutzergerät laufen lässt, sollte das
   wissen.
3. **Browser-Erweiterungen.** Eine Erweiterung mit Host-Berechtigung für
   `127.0.0.1` kann Kopfzeilen umschreiben und die Prüfung damit umgehen.
   Gegen eine installierte Erweiterung hilft kein serverseitiger Check.
4. **Keine Authentifizierung.** Der `Host`-Vergleich ist kein Passwortersatz.
   Wer `server.host` auf eine nicht-lokale Adresse setzt, stellt ein
   schutzloses Schreib-/Leseinterface ins Netz. Dann bitte die konkrete
   IP-Adresse eintragen, nie `0.0.0.0`, und besser gar nicht.
5. **Klartext ohne Verschlüsselung.** Die Aufgabendatei ist unverschlüsseltes
   Markdown. Liegt sie in einem synchronisierten Ordner, sieht der
   Sync-Anbieter den Inhalt. Wer das nicht will, legt sie woanders ab
   (`data.file` in `config.toml`) — am Code ändert sich nichts.
6. **Kein HTTPS.** Auf Loopback ohne Belang, weil der Verkehr den Rechner nicht
   verlässt. Relevant würde es erst mit Punkt 4, und davon ist abzuraten.
7. **`/api/restart` ist das wertvollste Ziel.** Es startet einen Prozess
   (`pythonw -m app`). Sollte die Herkunftsprüfung je umgangen werden, ist das
   der Endpunkt mit der größten Wirkung. Er ist abgedeckt, aber wer am Server
   etwas ändert, sollte ihn im Auge behalten.
8. **Kein Schutz gegen den Benutzer selbst.** Die Datei ist zum Bearbeiten von
   außen gedacht. Eine kaputte Zeile fängt der Parser ab und lässt sie
   unverändert stehen, aber ein Löschen der Datei bleibt ein Löschen.

## Selbst nachprüfen

Die automatisierten Tests stellen den Angriff ohne Browser nach:

```
python -m unittest tests.test_origin -v
```

18 Prüfungen, darunter „gleiche Adresse, anderer Port", `Origin: null`,
`Sec-Fetch-Site: cross-site` und DNS-Rebinding auf alle lesenden Endpunkte.
Jede Prüfung kontrolliert zusätzlich, dass nichts in der Datei landet.

Mit echtem Browser gegen eine **laufende** Instanz: eine Datei
`angriff.html` anlegen, über einen *anderen* Port ausliefern
(`python -m http.server 8898`) und im Browser öffnen.

```html
<script>
fetch("http://127.0.0.1:8777/api/tasks", {
  method: "POST", mode: "no-cors",
  headers: { "Content-Type": "text/plain;charset=UTF-8" },
  body: JSON.stringify({ description: "EINGESCHMUGGELT" })
});
</script>
```

Erwartung: In der Aufgabenliste erscheint nichts, und in der Konsole des
Browsers steht ein Fehler. Die Antwort selbst ist für die Seite unsichtbar —
die Datei ist der Beweis, nicht die Konsole.

Direkt auf der Kommandozeile prüfen lässt sich der `Host`-Teil:

```
python -c "import http.client as h; c=h.HTTPConnection('127.0.0.1',8777); \
c.putrequest('GET','/api/tasks',skip_host=True); \
c.putheader('Host','boese.example:8777'); c.endheaders(); \
print(c.getresponse().status)"
```

Erwartung: `403`. Vor dem Fix war es `200` samt vollständiger Aufgabenliste.

## Etwas gefunden?

Ein Hobbyprojekt eines einzelnen Entwicklers, ohne zugesagte Reaktionszeiten.
Bitte ein Issue aufmachen — bei etwas, das man besser nicht öffentlich
diskutiert, gern zuerst ohne Details mit der Bitte um Kontakt.
