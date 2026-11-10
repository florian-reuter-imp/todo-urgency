"""Lokaler Server: liefert die Weboberflaeche aus und stellt die API bereit.

Bewusst nur Stdlib (http.server) - keine externen Dependencies.
Die GUI-Schicht sieht ausschliesslich geparste Tasks + Scores, nie das
Rohformat und nie den Speicherort.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config, recurrence
from .codec import PRIORITY_ORDER, Task, TaskCodec, format_effort, parse_effort
from .datastore import DataStore, LocalFileStore
from .scorer import DEFAULTS as SCORER_DEFAULTS
from .scorer import Scored, UrgencyScorer

WEB_DIR = Path(__file__).resolve().parent / "web"
CONTENT_TYPES = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".webmanifest": "application/manifest+json",
}


class Conflict(Exception):
    """Die Datei wurde von aussen veraendert - Client muss neu laden."""


@dataclass(frozen=True)
class Snapshot:
    """Ein Schritt der Historie: Dateiinhalt davor und danach."""

    before: str
    after: str
    label: str


def _quote(text: str, limit: int = 32) -> str:
    text = " ".join(text.split())
    return '"' + (text[: limit - 1] + "…" if len(text) > limit else text) + '"'


_KEEP = object()


class TaskService:
    """Bindet die drei Schichten zusammen.

    Jede Operation arbeitet auf frisch geladenem Stand, damit Aenderungen
    von anderen Geraeten (per OneDrive hereinsynchronisiert) nie
    ueberschrieben werden.
    """

    #: So viele Schritte lassen sich zurueckgehen.
    UNDO_DEPTH = 20

    def __init__(self, store: DataStore, codec: TaskCodec, scorer: UrgencyScorer) -> None:
        self.store = store
        self.codec = codec
        self.scorer = scorer
        self._lock = threading.Lock()
        self._undo: list[Snapshot] = []
        self._redo: list[Snapshot] = []

    def _fresh(self):
        return self.codec.parse(self.store.load())

    # -- Schreiben mit Schnappschuss ------------------------------------
    def _commit(self, before: str, doc, label: str) -> None:
        """Einziger Schreibpfad. Legt den Zustand davor auf den Undo-Stapel.

        Weil der DataStore ohnehin immer den ganzen Inhalt ersetzt, deckt
        ein Schnappschuss jede Operation ab - auch solche, die mehrere
        Zeilen auf einmal anfassen (Rohzeile, Wiederholung). Inverse pro
        Operation braucht es dafuer nicht.
        """
        after = self.codec.serialize(doc)
        if after == before:
            return                      # nichts geaendert, kein Eintrag
        self.store.save(after)
        self._undo.append(Snapshot(before, after, label))
        del self._undo[:-self.UNDO_DEPTH]
        # Ein neuer Schritt verwirft die Zukunft: der Redo-Pfad gilt nicht mehr.
        self._redo.clear()

    def _restore(self, stack, other, target: str, source: str, nothing: str, noun: str) -> str:
        """Gemeinsamer Kern von Rückgängig und Wiederholen."""
        if not stack:
            raise ValueError(nothing)
        entry = stack[-1]
        if self.store.load() != getattr(entry, source):
            # Lieber nichts tun als eine fremde Bearbeitung wegwerfen.
            raise Conflict(
                "Die Datei wurde seit dieser Änderung von außen bearbeitet "
                "(Editor, Obsidian oder ein anderes Gerät). "
                f"{noun} würde diese Bearbeitung verwerfen."
            )
        self.store.save(getattr(entry, target))
        other.append(stack.pop())
        return entry.label

    def undo(self) -> str:
        with self._lock:
            return self._restore(
                self._undo, self._redo, "before", "after",
                "Nichts rückgängig zu machen.", "Das Zurücknehmen",
            )

    def redo(self) -> str:
        with self._lock:
            return self._restore(
                self._redo, self._undo, "after", "before",
                "Nichts zu wiederholen.", "Das Wiederholen",
            )

    def history(self) -> dict:
        return {
            "undo": {"can": bool(self._undo), "label": self._undo[-1].label if self._undo else None},
            "redo": {"can": bool(self._redo), "label": self._redo[-1].label if self._redo else None},
        }

    def list(self, today: date | None = None) -> dict:
        today = today or date.today()
        with self._lock:
            doc = self._fresh()
            scored = self.scorer.score_all(doc.tasks, today)
        th = self.scorer.cfg["thresholds"]
        return {
            "tasks": [_as_json(s) for s in scored],
            "thresholds": {"urgency": th["urgency"], "importance": th["importance"]},
            "features": self.scorer.features,
            "today": today.isoformat(),
            "history": self.history(),
        }

    def add(
        self, description: str, due: str | None, priority: str,
        effort=None, start=None, recur=None,
    ) -> None:
        description = description.strip()
        if not description:
            raise ValueError("Beschreibung darf nicht leer sein.")
        with self._lock:
            before = self.store.load()
            doc = self.codec.parse(before)
            task = Task(line_no=len(doc.lines), description=description, created=date.today())
            _apply(task, due=due, priority=priority, effort=effort,
                   start=start, recur=recur)
            doc.lines.append(task.to_line())
            self._commit(before, doc, f"{_quote(description)} angelegt")

    def update(self, uid: str, changes: dict) -> None:
        with self._lock:
            before = self.store.load()
            doc = self.codec.parse(before)
            task = doc.by_uid(uid)
            if task is None:
                raise Conflict("Aufgabe nicht mehr auffindbar – die Datei wurde verändert.")

            # Notausgang: die Rohzeile direkt setzen. Ersetzt alle Felder,
            # darum exklusiv behandelt.
            if "raw" in changes:
                replacement = self.codec.parse_line(task.line_no, str(changes["raw"]))
                if replacement is None:
                    raise ValueError(
                        "Das ist keine gültige Aufgabenzeile (erwartet: '- [ ] Text …')."
                    )
                doc.lines[task.line_no] = replacement.to_line()
                # Auch in doc.tasks ersetzen: serialize() schreibt jeden Task
                # an seine Zeile zurueck und wuerde die alte Fassung sonst
                # wieder darueberlegen.
                doc.tasks[doc.tasks.index(task)] = replacement
                self._commit(before, doc, f"Rohzeile von {_quote(task.description)} ersetzt")
                return

            labels = []
            if "done" in changes:
                was_done = task.done
                task.done = bool(changes["done"])
                task.status = "x" if task.done else " "
                if self.scorer.features.get("done_dates"):
                    task.done_date = date.today() if task.done else None
                if task.done and not was_done:
                    self._spawn_next_occurrence(doc, task)
                labels.append("abgehakt" if task.done else "wieder geoeffnet")
            if "description" in changes:
                description = str(changes["description"]).strip()
                if not description:
                    raise ValueError("Beschreibung darf nicht leer sein.")
                task.description = description

            # Ziehen in der Matrix: der Client schickt die Zielkoordinaten,
            # die Umrechnung in Datum/Prioritaet bleibt beim Scorer - die
            # GUI kennt die Formel nicht.
            if "urgency" in changes or "importance" in changes:
                if "urgency" in changes:
                    task.due = self.scorer.due_for_urgency(task, float(changes["urgency"]))
                if "importance" in changes:
                    task.priority = self.scorer.priority_for_importance(
                        task, float(changes["importance"])
                    )
                labels.append("in der Matrix verschoben")

            # Aufschieben: die Frist selbst wandert, immer von der ALTEN
            # Frist aus. Das erhaelt den Wochentag - bei terminbezogenen
            # Aufgaben ist der Donnerstag des Jour fixe wieder ein
            # Donnerstag. Von heute aus gerechnet waere es ein beliebiger
            # Tag, nur weil man zufaellig an einem Freitag geklickt hat.
            if "postpone_weeks" in changes:
                weeks = int(changes["postpone_weeks"])
                if task.due is None:
                    raise ValueError(
                        "Ohne Fälligkeitsdatum lässt sich nichts aufschieben."
                    )
                if weeks < 1:
                    raise ValueError("Aufschieben geht nur um ganze Wochen.")
                task.due += timedelta(weeks=weeks)
                labels.append(
                    f"um {weeks} Woche{'n' if weeks != 1 else ''} aufgeschoben"
                )

            # Zurueckstellen: nur die Sichtbarkeit, die Frist bleibt stehen.
            start = changes.get("start", _KEEP)
            if "snooze_days" in changes:
                labels.append(
                    "zurueckgestellt" if int(changes["snooze_days"]) > 0 else "wieder aktiviert"
                )
                days = int(changes["snooze_days"])
                start = (date.today() + timedelta(days=days)).isoformat() if days > 0 else None

            _apply(
                task,
                due=changes.get("due", _KEEP),
                priority=changes.get("priority"),
                effort=changes.get("effort", _KEEP),
                start=start,
                recur=changes.get("recurrence", _KEEP),
            )
            if not labels:
                labels.append("geaendert")
            self._commit(before, doc, f"{_quote(task.description)} {', '.join(labels)}")

    # -- Gewichte ------------------------------------------------------
    def get_config(self) -> dict:
        return {
            "urgency": self.scorer.cfg,
            "features": self.scorer.features,
            "defaults": scorer_defaults(),
        }

    def preview(self, urgency: dict, today: date | None = None) -> dict:
        """Mit testweisen Gewichten bewerten, ohne irgendetwas zu speichern."""
        trial = UrgencyScorer(self._overlay(urgency), self.scorer.features)
        today = today or date.today()
        with self._lock:
            doc = self._fresh()
            scored = trial.score_all(doc.tasks, today)
        th = trial.cfg["thresholds"]
        return {
            "tasks": [_as_json(s) for s in scored],
            "thresholds": {"urgency": th["urgency"], "importance": th["importance"]},
            "features": trial.features,
            "today": today.isoformat(),
        }

    def save_config(self, urgency: dict) -> None:
        """Gewichte in die config.toml schreiben und sofort aktiv setzen."""
        clean = _validated(urgency)
        merged = self._overlay(urgency)
        config.update_file({f"urgency.{sec}": vals for sec, vals in clean.items()})
        self.scorer = UrgencyScorer(merged, self.scorer.features)

    def _overlay(self, urgency: dict) -> dict:
        """Uebergebene Gewichte ueber die AKTUELL geladenen legen.

        Nicht ueber die Auslieferungswerte: eine Teiluebergabe darf die
        restlichen Werte aus der config.toml nicht stillschweigend auf
        Default zuruecksetzen.
        """
        merged = {sec: dict(vals) for sec, vals in self.scorer.cfg.items()}
        for section, values in _validated(urgency).items():
            merged.setdefault(section, {}).update(values)
        return merged

    def _spawn_next_occurrence(self, doc, task: Task) -> None:
        """Beim Abhaken einer wiederkehrenden Aufgabe die naechste anlegen.

        Die neue Zeile kommt direkt ueber die abgehakte, damit sie im
        Dokument an ihrem Platz bleibt. Wird die Regel nicht erkannt,
        passiert nichts - die Aufgabe ist dann einfach erledigt.
        """
        if not self.scorer.features.get("recurrence"):
            return
        rule = recurrence.parse(task.recurrence)
        if rule is None:
            return

        today = date.today()
        anchor = today if rule.when_done else (task.due or task.start or today)
        next_due = recurrence.next_date(rule, anchor)

        # Ein vorhandenes Startdatum um denselben Abstand mitschieben,
        # damit der Vorlauf zur Frist gleich bleibt.
        next_start = None
        if task.start is not None:
            if task.due is not None:
                next_start = next_due - (task.due - task.start)
            else:
                next_start = recurrence.next_date(rule, task.start)

        nxt = task.copy_for_next_occurrence(
            due=next_due if task.due is not None or task.start is None else None,
            start=next_start,
        )
        at = task.line_no
        doc.lines.insert(at, nxt.to_line())
        for other in doc.tasks:
            if other.line_no >= at:
                other.line_no += 1
        nxt.line_no = at
        doc.tasks.insert(doc.tasks.index(task), nxt)

    def delete(self, uid: str) -> None:
        with self._lock:
            before = self.store.load()
            doc = self.codec.parse(before)
            task = doc.by_uid(uid)
            if task is None:
                raise Conflict("Aufgabe nicht mehr auffindbar – die Datei wurde verändert.")
            del doc.lines[task.line_no]
            doc.tasks = [t for t in doc.tasks if t is not task]
            for t in doc.tasks:
                if t.line_no > task.line_no:
                    t.line_no -= 1
            self._commit(before, doc, f"{_quote(task.description)} geloescht")


def _apply(
    task: Task, due, priority: str | None, effort=_KEEP, start=_KEEP, recur=_KEEP
) -> None:
    if due is not _KEEP:
        task.due = date.fromisoformat(due) if due else None
    if start is not _KEEP:
        task.start = date.fromisoformat(start) if start else None
    if priority:
        if priority not in PRIORITY_ORDER:
            raise ValueError("Unbekannte Priorität: " + str(priority))
        task.priority = priority
    if recur is not _KEEP:
        text = (recur or "").strip()
        if text and recurrence.parse(text) is None:
            raise ValueError(
                "Wiederholung nicht lesbar: '" + text
                + "' (erwartet z.B. every day, every 2 weeks, every monday)."
            )
        task.recurrence = text or None
    if effort is not _KEEP:
        if effort in (None, ""):
            task.effort = None
        else:
            minutes = parse_effort(str(effort))
            if minutes is None:
                raise ValueError(
                    "Aufwand nicht lesbar: '" + str(effort) + "' (erwartet z.B. 30m, 2h, 1d)."
                )
            task.effort = minutes or None


def scorer_defaults() -> dict:
    """Die Auslieferungsgewichte - fuer den "zuruecksetzen"-Knopf."""
    return {sec: dict(vals) for sec, vals in SCORER_DEFAULTS.items()}


def _validated(urgency: dict) -> dict:
    """Nur bekannte Abschnitte und Schluessel mit Zahlen durchlassen.

    Verhindert, dass ein Tippfehler im Client Unsinn in die config.toml
    schreibt oder eine unbekannte Formel-Variable erfindet.
    """
    clean: dict[str, dict[str, float]] = {}
    for section, values in (urgency or {}).items():
        if section not in SCORER_DEFAULTS:
            raise ValueError("Unbekannter Abschnitt: urgency." + str(section))
        if not isinstance(values, dict):
            raise ValueError("urgency." + section + " muss ein Objekt sein.")
        for key, value in values.items():
            if key not in SCORER_DEFAULTS[section]:
                raise ValueError(f"Unbekannter Schlüssel: urgency.{section}.{key}")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"urgency.{section}.{key} muss eine Zahl sein.")
            clean.setdefault(section, {})[key] = float(value)
    return clean


def _describe_recurrence(raw: str | None) -> str:
    rule = recurrence.parse(raw)
    return rule.describe() if rule else (raw or "")


def _recurrence_rule(raw: str | None) -> dict | None:
    """Strukturierte Regel fuer die Dropdowns der Oberflaeche.

    None, wenn die Regel nicht erkannt wurde - dann zeigt die GUI den
    Rohtext und laesst ihn unangetastet.
    """
    rule = recurrence.parse(raw)
    if rule is None:
        return None
    return {
        "interval": rule.interval,
        "unit": rule.unit,
        "weekday": rule.weekday,
        "when_done": rule.when_done,
    }


def _as_json(s: Scored) -> dict:
    t = s.task
    return {
        "uid": t.uid,
        "description": t.description,
        "done": t.done,
        "due": t.due.isoformat() if t.due else None,
        "created": t.created.isoformat() if t.created else None,
        "start": t.start.isoformat() if t.start else None,
        "snoozed": s.snoozed,
        "done_date": t.done_date.isoformat() if t.done_date else None,
        "recurrence": t.recurrence,
        "recurrence_text": _describe_recurrence(t.recurrence),
        "recurrence_rule": _recurrence_rule(t.recurrence),
        "priority": t.priority,
        "tags": t.tags,
        "effort": t.effort,
        "effort_text": format_effort(t.effort),
        "score": round(s.score, 2),
        "urgency": round(s.urgency, 2),
        "importance": round(s.importance, 2),
        "quadrant": s.quadrant,
        "horizon_days": round(s.horizon_days, 1),
        "breakdown": {k: round(v, 2) for k, v in s.breakdown.items()},
        "raw": t.to_line(),
    }


class Handler(BaseHTTPRequestHandler):
    service: TaskService
    activity: "Activity"
    #: Codestand beim Start dieses Prozesses (siehe /api/version).
    fingerprint: str = ""
    #: Kennung dieses Prozesses, wechselt bei jedem Neustart.
    instance: str = ""
    server_version = "TodoTool/0.1"

    def log_message(self, fmt: str, *args) -> None:
        pass  # leiser Server

    # -- Helfer ---------------------------------------------------------
    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _static(self, name: str) -> None:
        path = (WEB_DIR / name).resolve()
        if not path.is_file() or WEB_DIR.resolve() not in path.parents:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        ctype = CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _today(query: dict) -> date | None:
        """?today=YYYY-MM-DD fuer den Zeitreise-Regler. Nur lesend."""
        values = query.get("today")
        if not values or not values[0]:
            return None
        try:
            return date.fromisoformat(values[0])
        except ValueError:
            raise ValueError("today muss YYYY-MM-DD sein, war: " + values[0])

    def _dispatch(self, method: str) -> None:
        self.activity.touch()
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if method == "GET":
                if path == "/":
                    return self._static("index.html")
                if path.startswith("/static/"):
                    return self._static(path[len("/static/"):])
                if path == "/api/tasks":
                    return self._json(self.service.list(self._today(query)))
                if path == "/api/config":
                    return self._json(self.service.get_config())
                if path == "/api/version":
                    # fingerprint: der beim START eingelesene Codestand, nicht
                    #   der aktuelle - sonst meldet ein veralteter Prozess den
                    #   Fingerabdruck der neuen Dateien und gilt als aktuell.
                    # instance: eindeutig je Prozess. Nur daran erkennt die
                    #   Oberflaeche nach einem Neustart, dass wirklich der
                    #   NACHFOLGER antwortet und nicht noch der alte Server.
                    return self._json({
                        "fingerprint": self.fingerprint,
                        "instance": self.instance,
                    })
            elif method == "POST":
                if path == "/api/heartbeat":
                    return self._json({"ok": True})
                if path == "/api/tasks":
                    body = self._read_body()
                    self.service.add(
                        body.get("description", ""),
                        body.get("due"),
                        body.get("priority", "none"),
                        body.get("effort"),
                        body.get("start"),
                        body.get("recurrence"),
                    )
                    return self._json(self.service.list(), 201)
                if path == "/api/tasks/preview":
                    body = self._read_body()
                    return self._json(
                        self.service.preview(body.get("urgency", {}), self._today(query))
                    )
                if path == "/api/undo":
                    label = self.service.undo()
                    return self._json({**self.service.list(), "undone": label})
                if path == "/api/redo":
                    label = self.service.redo()
                    return self._json({**self.service.list(), "redone": label})
                if path == "/api/shutdown":
                    self.activity.stop()
                    return self._json({"ok": True})
                if path == "/api/restart":
                    # Ein losgeloester neuer Prozess mit --restart beendet uns
                    # selbst und uebernimmt den Port. Dieselbe Mechanik wie
                    # beim Doppelklick nach einer Codeaenderung.
                    spawn_replacement()
                    return self._json({"ok": True})
            elif method == "PUT":
                if path == "/api/config":
                    self.service.save_config(self._read_body().get("urgency", {}))
                    return self._json(self.service.get_config())
            elif method in ("PATCH", "DELETE") and path.startswith("/api/tasks/"):
                uid = path[len("/api/tasks/"):]
                if method == "PATCH":
                    self.service.update(uid, self._read_body())
                else:
                    self.service.delete(uid)
                return self._json(self.service.list())
            self.send_error(HTTPStatus.NOT_FOUND)
        except Conflict as exc:
            self._json({"error": str(exc), "reload": True}, 409)
        except (ValueError, KeyError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:  # letzte Auffanglinie
            self._json({"error": type(exc).__name__ + ": " + str(exc)}, 500)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")


class Activity:
    """Beendet den Server nach laengerer Inaktivitaet.

    Der Browser-Tab sendet einen Heartbeat; ist kein Tab mehr offen,
    laeuft die Uhr ab und der Prozess verschwindet von selbst - kein
    dauerhafter Hintergrunddienst, aber auch kein manuelles Starten.
    """

    def __init__(self, timeout_minutes: float) -> None:
        self.timeout = timeout_minutes * 60
        self.last = time.monotonic()
        self._httpd: ThreadingHTTPServer | None = None

    def touch(self) -> None:
        self.last = time.monotonic()

    def attach(self, httpd: ThreadingHTTPServer) -> None:
        self._httpd = httpd
        if self.timeout > 0:
            threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self) -> None:
        while True:
            time.sleep(5)
            if time.monotonic() - self.last > self.timeout:
                self.stop()
                return

    def stop(self) -> None:
        if self._httpd is not None:
            threading.Thread(target=self._httpd.shutdown, daemon=True).start()


def spawn_replacement() -> None:
    """Startet eine neue, losgeloeste Instanz, die diese hier abloest.

    Der Kindprozess darf uns ueberleben, deshalb ohne Prozessgruppe und
    ohne offene Handles - sonst stirbt er mit uns, bevor er den Port
    uebernehmen kann.
    """
    exe = Path(sys.executable)
    quiet = exe.with_name("pythonw.exe")
    flags = {}
    if os.name == "nt":
        flags["creationflags"] = 0x00000008 | 0x00000200   # DETACHED | NEW_GROUP
    else:
        flags["start_new_session"] = True
    subprocess.Popen(
        [str(quiet if quiet.is_file() else exe), "-m", "app", "--window", "none", "--restart"],
        cwd=str(config.ROOT),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        **flags,
    )


def build(cfg: dict) -> tuple[ThreadingHTTPServer, Activity]:
    service = TaskService(
        LocalFileStore(cfg["data"]["path"]),
        TaskCodec(),
        UrgencyScorer(cfg["urgency"], cfg.get("features")),
    )
    activity = Activity(cfg["server"]["idle_shutdown_minutes"])
    handler = type("BoundHandler", (Handler,), {
        "service": service,
        "activity": activity,
        # Einmalig beim Start festhalten - siehe /api/version.
        "fingerprint": config.fingerprint(),
        "instance": f"{os.getpid()}-{time.time_ns()}",
    })
    httpd = ThreadingHTTPServer((cfg["server"]["host"], cfg["server"]["port"]), handler)
    activity.attach(httpd)
    return httpd, activity
