"""Schicht 3: Urgency-Score.

Adaptiert die offen dokumentierten Formeln von Taskwarrior und dem
Obsidian-Tasks-Plugin. Der Score wird bewusst in ZWEI Achsen zerlegt:

    Dringlichkeit (X) = Faelligkeit + Alter        -> zeitgetrieben
    Wichtigkeit   (Y) = Prioritaet  + Tags         -> inhaltsgetrieben
    Score             = Dringlichkeit + Wichtigkeit

Damit sind Listenansicht (nach Score) und Eisenhower-Matrix (X gegen Y)
garantiert widerspruchsfrei: die Matrix zeigt dieselbe Zahl, nur
aufgetrennt.

Eigene Erweiterung gegenueber beiden Vorlagen: der Aufwand verlaengert
die VORLAUFZEIT. Eine Zwei-Tage-Aufgabe beginnt viel frueher zu steigen
als ein Zehn-Minuten-Anruf mit derselben Frist - bei beiden Vorlagen
waeren sie ununterscheidbar. Fehlt das Aufwandsfeld, gilt exakt das
Verhalten ohne diese Erweiterung.

Diese Komponente kennt weder Dateiformat noch Speicherort.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .codec import Task

DEFAULTS: dict = {
    "due": {
        # Wirkung der Faelligkeit insgesamt.
        "coefficient": 12.0,
        # Ab so vielen Tagen Ueberfaelligkeit ist der Faktor maximal (1.0).
        "overdue_days_for_max": 7,
        # Ab so vielen Tagen in der Zukunft ist der Faktor minimal.
        # Der Aufwand verschiebt diesen Horizont nach vorn (siehe effort).
        "future_days_for_min": 14,
        "min_factor": 0.2,
    },
    # Werte aus dem Tasks-Plugin; "none" liegt bewusst ueber "low", damit
    # eine bewusst niedrig priorisierte Aufgabe unter einer unmarkierten liegt.
    "priority": {
        "highest": 9.0,
        "high": 6.0,
        "medium": 3.9,
        "none": 1.95,
        "low": 0.0,
        "lowest": -1.8,
    },
    # Taskwarrior-Idee: je aelter, desto dringender - gedeckelt.
    "age": {"coefficient": 2.0, "max_days": 365},
    # Getaggte Aufgaben gehoeren zu einem Projekt -> leicht wichtiger.
    "tags": {"coefficient": 1.0},
    # Aufwand -> Vorlaufzeit. Je Stunde Aufwand beginnt die Rampe
    # `days_per_hour` Tage frueher, maximal um `max_extra_days`.
    "effort": {"days_per_hour": 1.5, "max_extra_days": 45},
    # Trennlinien der Eisenhower-Matrix.
    "thresholds": {"urgency": 6.0, "importance": 3.0},
}

FEATURE_DEFAULTS: dict = {
    "effort": True,
    "grouping": True,
    "snooze": True,
    "recurrence": True,
    "done_dates": True,
    "dependencies": False,
}


@dataclass
class Scored:
    task: Task
    urgency: float
    importance: float
    breakdown: dict[str, float]
    quadrant: str = "drop"
    #: Ab so vielen Tagen vor der Frist beginnt der Score zu steigen.
    horizon_days: float = 0.0
    #: Startdatum liegt in der Zukunft -> noch nicht handlungsreif.
    snoozed: bool = False

    @property
    def score(self) -> float:
        return self.urgency + self.importance


def _quadrant(urgent: bool, important: bool) -> str:
    """Eisenhower: do (jetzt) / plan (terminieren) / delegate / drop."""
    if urgent and important:
        return "do"
    if important:
        return "plan"
    if urgent:
        return "delegate"
    return "drop"


class UrgencyScorer:
    def __init__(self, config: dict | None = None, features: dict | None = None) -> None:
        self.cfg = _merge(DEFAULTS, config or {})
        self.features = _merge(FEATURE_DEFAULTS, features or {})

    def score(self, task: Task, today: date | None = None) -> Scored:
        today = today or date.today()
        horizon = self.horizon_days(task)
        parts = {
            "due": self._due(task, today, horizon),
            "age": self._age(task, today),
            "priority": self.cfg["priority"].get(task.priority, 0.0),
            "tags": self.cfg["tags"]["coefficient"] if task.tags else 0.0,
        }
        urgency = parts["due"] + parts["age"]
        importance = parts["priority"] + parts["tags"]
        th = self.cfg["thresholds"]
        quadrant = _quadrant(urgency >= th["urgency"], importance >= th["importance"])
        return Scored(
            task, urgency, importance, parts, quadrant, horizon, self.is_snoozed(task, today)
        )

    def is_snoozed(self, task: Task, today: date | None = None) -> bool:
        """True, wenn das Startdatum noch in der Zukunft liegt.

        Zurueckgestellte Aufgaben werden ausgeblendet statt abgewertet: eine
        Aufgabe, die man noch nicht anfangen kann, ist Rauschen. Wer sie
        trotzdem sehen will, blendet sie in der Oberflaeche ein - dann steht
        dort ihr ungeschoenter Score.
        """
        if not self.features.get("snooze") or task.start is None:
            return False
        return task.start > (today or date.today())

    def score_all(self, tasks: list[Task], today: date | None = None) -> list[Scored]:
        today = today or date.today()
        scored = [self.score(t, today) for t in tasks]
        scored.sort(key=lambda s: (-s.score, s.task.due or date.max, s.task.line_no))
        return scored

    def horizon_days(self, task: Task) -> float:
        """Vorlaufzeit in Tagen: Basis, durch den Aufwand nach vorn verschoben.

        Ohne Aufwandsfeld (oder mit abgeschaltetem Feature) ist das exakt
        der Basiswert - also das Verhalten vor dieser Erweiterung.
        """
        base = float(self.cfg["due"]["future_days_for_min"])
        if not self.features.get("effort") or not task.effort:
            return base
        c = self.cfg["effort"]
        extra = (task.effort / 60.0) * c["days_per_hour"]
        return base + min(extra, float(c["max_extra_days"]))

    # -- Teilfaktoren ---------------------------------------------------
    def _due(self, task: Task, today: date, horizon: float) -> float:
        if task.due is None:
            return 0.0
        c = self.cfg["due"]
        days = (task.due - today).days
        lo, hi = -float(c["overdue_days_for_max"]), horizon
        if days <= lo:
            factor = 1.0
        elif days >= hi:
            factor = c["min_factor"]
        else:
            # linear von 1.0 (lo) auf min_factor (hi)
            factor = 1.0 - (days - lo) / (hi - lo) * (1.0 - c["min_factor"])
        return c["coefficient"] * factor

    def _age(self, task: Task, today: date) -> float:
        if task.created is None:
            return 0.0
        c = self.cfg["age"]
        days = max((today - task.created).days, 0)
        return c["coefficient"] * min(days / c["max_days"], 1.0)

    # -- Umkehrung (fuer das Ziehen in der Matrix) -----------------------
    def due_for_urgency(self, task: Task, target: float, today: date | None = None) -> date | None:
        """Welches Faelligkeitsdatum ergaebe diese Dringlichkeit?

        Umkehrung von _due() plus Alter. None, wenn das Ziel unterhalb
        dessen liegt, was ein Datum ueberhaupt beitragen kann - dann ist
        "kein Datum" die richtige Antwort.
        """
        today = today or date.today()
        c = self.cfg["due"]
        wanted = target - self._age(task, today)
        factor = wanted / c["coefficient"]
        if factor <= c["min_factor"]:
            return None
        if factor >= 1.0:
            return today - timedelta(days=int(c["overdue_days_for_max"]))
        lo, hi = -float(c["overdue_days_for_max"]), self.horizon_days(task)
        days = lo + (1.0 - factor) / (1.0 - c["min_factor"]) * (hi - lo)
        return today + timedelta(days=int(round(days)))

    def priority_for_importance(self, task: Task, target: float) -> str:
        """Nachstgelegene Prioritaetsstufe fuer eine Wichtigkeit auf der Y-Achse."""
        offset = self.cfg["tags"]["coefficient"] if task.tags else 0.0
        wanted = target - offset
        return min(
            self.cfg["priority"], key=lambda name: abs(self.cfg["priority"][name] - wanted)
        )


def _merge(base: dict, override: dict) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out
