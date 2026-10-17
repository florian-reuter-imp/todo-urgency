"""Schicht 2: Rohtext <-> Task-Objekte.

Format angelehnt an die Obsidian-Tasks-Syntax, damit die Datei auch
ausserhalb dieser App (Editor, Obsidian) bearbeitbar bleibt:

    - [ ] Praesentation vorbereiten ➕ 2026-09-10 📅 2026-09-20 🔺 #IMVERSE

Der Codec kennt weder Speicherort noch Urgency-Formel.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date

# --- Feld-Emojis ------------------------------------------------------
# Bewusst als Escape-Sequenzen notiert: dieser Block bleibt damit reines
# ASCII und kann von keinem Werkzeug mit falscher Kodierung zerschossen
# werden. Die Klarnamen stehen im Kommentar.
DUE        = "\U0001f4c5"  # calendar          faellig
CREATED    = "\u2795"      # heavy plus        erstellt am
START      = "\U0001f6eb"  # airplane          nicht vor (Snooze)
DONE_DATE  = "\u2705"      # check mark        erledigt am
EFFORT     = "\u23f1"      # stopwatch         Aufwand (kein offizielles Tasks-Feld)
RECURRENCE = "\U0001f501"  # arrows in circle  Wiederholung

#: Emojis, die wir aktiv als Datumsfeld auswerten.
DATE_FIELDS: dict[str, str] = {
    DUE: "due",
    CREATED: "created",
    START: "start",
    DONE_DATE: "done_date",
}

#: Weitere Tasks-Emojis. Werden nicht ausgewertet, aber beim Speichern
#: unveraendert wieder angehaengt, damit nichts verloren geht.
PASSTHROUGH_DATE_FIELDS = (
    "\u23f3",        # hourglass    scheduled
    "\u274c",        # cross mark   abgebrochen am
    "\U0001f194",    # id button    id        (fuer spaetere Abhaengigkeiten)
    "\u26d4",        # no entry     blockedBy (dito)
)

PRIORITIES: dict[str, str] = {
    "\U0001f53a": "highest",   # red triangle up
    "\u23eb": "high",      # double arrow up
    "\U0001f53c": "medium",    # triangle up
    "\U0001f53d": "low",       # triangle down
    "\u23ec": "lowest",    # double arrow down
}
PRIORITY_EMOJI: dict[str, str] = {name: emoji for emoji, name in PRIORITIES.items()}
PRIORITY_ORDER = ("highest", "high", "medium", "none", "low", "lowest")

#: Ein "d" im Aufwandsfeld meint einen Arbeitstag, keine 24 Stunden.
MINUTES_PER_WORKDAY = 8 * 60

_TASK_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>[-*+])\s\[(?P<status>.)\]\s?(?P<body>.*)$")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_TAG_RE = re.compile(r"(?:^|\s)#([^\s#]+)")
_EFFORT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*([dhm])", re.IGNORECASE)
_BARE_NUMBER_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*$")
_VS16 = "\ufe0f"            # Variation Selector-16

_ALL_FIELD_EMOJIS = (
    tuple(DATE_FIELDS)
    + PASSTHROUGH_DATE_FIELDS
    + tuple(PRIORITIES)
    + (EFFORT, RECURRENCE)
)
# Jedes Emoji darf ein optionales Variation Selector-16 tragen, sonst landet
# das unsichtbare Zeichen im Wert und verfaelscht das Parsen.
_FIELD_SPLIT_RE = re.compile(
    "(" + "|".join(re.escape(e) + _VS16 + "?" for e in _ALL_FIELD_EMOJIS) + ")"
)

DONE_MARKERS = {"x", "X", "-"}


def _parse_date(text: str) -> date | None:
    m = _DATE_RE.search(text)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def parse_effort(text: str) -> int | None:
    """'2h' / '30m' / '1h30m' / '1,5h' / '1d' / '45' -> Minuten.

    Eine nackte Zahl wird als Minuten gelesen. Nicht interpretierbarer
    Text ergibt None, damit der Aufruf entscheiden kann, was passiert.
    """
    text = (text or "").strip()
    if not text:
        return None
    bare = _BARE_NUMBER_RE.match(text)
    if bare:
        return max(int(round(float(bare.group(1).replace(",", ".")))), 0)

    minutes = 0.0
    matched = False
    for amount, unit in _EFFORT_RE.findall(text):
        value = float(amount.replace(",", "."))
        unit = unit.lower()
        minutes += value * {"d": MINUTES_PER_WORKDAY, "h": 60.0, "m": 1.0}[unit]
        matched = True
    if not matched:
        return None
    return max(int(round(minutes)), 0)


def format_effort(minutes: int | None) -> str:
    """Minuten -> kompakte Schreibweise ('1d', '2h30m', '45m')."""
    if not minutes or minutes <= 0:
        return ""
    if minutes >= MINUTES_PER_WORKDAY and minutes % MINUTES_PER_WORKDAY == 0:
        return f"{minutes // MINUTES_PER_WORKDAY}d"
    hours, rest = divmod(minutes, 60)
    return (f"{hours}h" if hours else "") + (f"{rest}m" if rest else "")


@dataclass
class Task:
    """Eine geparste Aufgabe. `line_no` verankert sie in der Quelldatei."""

    line_no: int
    description: str
    done: bool = False
    status: str = " "
    priority: str = "none"
    due: date | None = None
    created: date | None = None
    #: nicht vor diesem Datum anfassen (Snooze); None = sofort relevant
    start: date | None = None
    #: Datum des Abhakens; wird nur mit Feature done_dates gesetzt
    done_date: date | None = None
    #: geschaetzter Aufwand in Minuten; None = nicht angegeben
    effort: int | None = None
    #: Wiederholungsregel als Rohtext, z.B. "every 2 weeks when done".
    #: Der Codec interpretiert sie nicht - das macht app/recurrence.py.
    recurrence: str | None = None
    indent: str = ""
    marker: str = "-"
    #: unveraendert durchgereichte Segmente, z.B. [("🛫", "2026-09-15")]
    passthrough: list[tuple[str, str]] = field(default_factory=list)

    @property
    def tags(self) -> list[str]:
        return _TAG_RE.findall(self.description)

    @property
    def uid(self) -> str:
        """Stabil solange die Zeile unveraendert bleibt.

        Dient dem Client als Handle und gleichzeitig als Stale-Check:
        passt die uid nicht mehr, wurde die Zeile inzwischen veraendert.
        """
        digest = hashlib.sha1(self.to_line().encode("utf-8")).hexdigest()[:10]
        return f"{self.line_no}-{digest}"

    def to_line(self) -> str:
        # Feldreihenfolge wie im Tasks-Plugin ueblich.
        parts = [self.description.strip()]
        if self.priority != "none":
            parts.append(PRIORITY_EMOJI[self.priority])
        if self.recurrence:
            parts.append(f"{RECURRENCE} {self.recurrence}")
        if self.effort:
            parts.append(f"{EFFORT} {format_effort(self.effort)}")
        if self.created:
            parts.append(f"{CREATED} {self.created.isoformat()}")
        if self.start:
            parts.append(f"{START} {self.start.isoformat()}")
        for emoji, value in self.passthrough:
            parts.append(f"{emoji} {value}".strip())
        if self.due:
            parts.append(f"{DUE} {self.due.isoformat()}")
        if self.done_date:
            parts.append(f"{DONE_DATE} {self.done_date.isoformat()}")
        body = " ".join(p for p in parts if p)
        return f"{self.indent}{self.marker} [{self.status}] {body}".rstrip()

    def copy_for_next_occurrence(self, due: date | None, start: date | None) -> "Task":
        """Naechste Instanz einer wiederkehrenden Aufgabe: gleiche Inhalte,
        neue Termine, offen, mit heutigem Erstellungsdatum."""
        return Task(
            line_no=self.line_no,
            description=self.description,
            done=False,
            status=" ",
            priority=self.priority,
            due=due,
            created=date.today(),
            start=start,
            effort=self.effort,
            recurrence=self.recurrence,
            indent=self.indent,
            marker=self.marker,
            passthrough=list(self.passthrough),
        )


@dataclass
class Document:
    """Die komplette Datei: alle Zeilen plus die darin gefundenen Tasks.

    Nicht-Task-Zeilen (Ueberschriften, Leerzeilen, Fliesstext) bleiben
    unangetastet erhalten - die Datei bleibt normales Markdown.
    """

    lines: list[str]
    tasks: list[Task]
    trailing_newline: bool = True

    def by_uid(self, uid: str) -> Task | None:
        return next((t for t in self.tasks if t.uid == uid), None)


class TaskCodec:
    def parse(self, content: str) -> Document:
        trailing_newline = content.endswith("\n") or content == ""
        lines = content.split("\n")
        if trailing_newline and lines and lines[-1] == "":
            lines.pop()

        tasks: list[Task] = []
        for i, line in enumerate(lines):
            task = self.parse_line(i, line)
            if task is not None:
                tasks.append(task)
        return Document(lines=lines, tasks=tasks, trailing_newline=trailing_newline)

    def serialize(self, doc: Document) -> str:
        lines = list(doc.lines)
        for task in doc.tasks:
            if 0 <= task.line_no < len(lines):
                lines[task.line_no] = task.to_line()
        out = "\n".join(lines)
        if doc.trailing_newline and out and not out.endswith("\n"):
            out += "\n"
        return out

    def parse_line(self, line_no: int, line: str) -> Task | None:
        """Eine einzelne Zeile parsen. None, wenn es keine Checkbox-Zeile ist."""
        m = _TASK_RE.match(line)
        if not m:
            return None

        status = m.group("status")
        task = Task(
            line_no=line_no,
            description="",
            done=status in DONE_MARKERS,
            status=status,
            indent=m.group("indent"),
            marker=m.group("marker"),
        )

        # Body an den bekannten Feld-Emojis zerlegen: das erste Segment ist
        # die Beschreibung, danach folgt paarweise (Emoji, Wert).
        segments = _FIELD_SPLIT_RE.split(m.group("body"))
        task.description = segments[0].strip()
        for emoji, value in zip(segments[1::2], segments[2::2]):
            emoji = emoji.replace(_VS16, "")
            value = value.strip()
            if emoji in PRIORITIES:
                task.priority = PRIORITIES[emoji]
                # Ein Prioritaets-Emoji hat keinen Wert; was folgt, gehoert
                # zur Beschreibung (z.B. ein nachgestellter Tag).
                if value:
                    task.description = f"{task.description} {value}".strip()
            elif emoji in DATE_FIELDS:
                parsed = _parse_date(value)
                if parsed is None:
                    # Unlesbares Datum: als Text erhalten statt verlieren.
                    task.passthrough.append((emoji, value))
                else:
                    setattr(task, DATE_FIELDS[emoji], parsed)
                    # '📅 2026-10-20 #projekt' -> der Rest hinter dem Datum
                    # gehoert zur Beschreibung und darf nicht wegfallen.
                    rest = _DATE_RE.sub("", value, count=1).strip()
                    if rest:
                        task.description = f"{task.description} {rest}".strip()
            elif emoji == RECURRENCE:
                # Rohtext behalten; die Semantik liegt in app/recurrence.py.
                task.recurrence = value or None
            elif emoji == EFFORT:
                minutes = parse_effort(value)
                if minutes is None:
                    # Unlesbarer Wert: lieber als Text erhalten als verlieren.
                    task.passthrough.append((EFFORT, value))
                else:
                    task.effort = minutes
                    # '⏱ 2h #tag' -> der Rest hinter der Dauer bleibt Beschreibung.
                    rest = _EFFORT_RE.sub("", value).strip() if not _BARE_NUMBER_RE.match(value) else ""
                    if rest:
                        task.description = f"{task.description} {rest}".strip()
            else:
                task.passthrough.append((emoji, value))
        return task
