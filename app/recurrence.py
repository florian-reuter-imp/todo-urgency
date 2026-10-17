"""Wiederholungsregeln: Rohtext -> naechstes Datum.

Bewusst eine pragmatische Teilmenge der Obsidian-Tasks-Syntax, nicht die
ganze rrule-Welt. Was hier nicht erkannt wird, bleibt als Text in der
Datei erhalten und wird einfach nicht ausgewertet - eine Aufgabe geht so
nie verloren, sie wiederholt sich nur nicht automatisch.

Erkannt wird:

    every day              every 3 days
    every week             every 2 weeks
    every month            every 6 months
    every year
    every monday           (englische und deutsche Wochentage)
    ... jeweils optional mit dem Zusatz "when done"

Diese Komponente kennt weder Dateiformat noch Speicherort.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

UNITS = {
    "day": "day", "days": "day",
    "tag": "day", "tage": "day", "tagen": "day",
    "week": "week", "weeks": "week",
    "woche": "week", "wochen": "week",
    "month": "month", "months": "month",
    "monat": "month", "monate": "month", "monaten": "month",
    "year": "year", "years": "year",
    "jahr": "year", "jahre": "year", "jahren": "year",
}

WEEKDAYS = {
    "monday": 0, "montag": 0, "mo": 0,
    "tuesday": 1, "dienstag": 1, "di": 1,
    "wednesday": 2, "mittwoch": 2, "mi": 2,
    "thursday": 3, "donnerstag": 3, "do": 3,
    "friday": 4, "freitag": 4, "fr": 4,
    "saturday": 5, "samstag": 5, "sa": 5,
    "sunday": 6, "sonntag": 6, "so": 6,
}

_EVERY_RE = re.compile(
    r"^\s*(?:every|jeden|jede|alle)\s+(?:(\d+)\s+)?([a-zA-Zaeiouäöü]+)\s*$",
    re.IGNORECASE,
)
_WHEN_DONE_RE = re.compile(r"\bwhen\s+done\b|\bnach\s+erledigung\b", re.IGNORECASE)


@dataclass(frozen=True)
class Rule:
    interval: int = 1
    unit: str = "day"          # day | week | month | year
    weekday: int | None = None  # 0 = Montag, nur bei "every monday"
    when_done: bool = False
    raw: str = ""

    def describe(self) -> str:
        if self.weekday is not None:
            name = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
                    "Freitag", "Samstag", "Sonntag"][self.weekday]
            base = f"jeden {name}"
        else:
            names = {"day": "Tag", "week": "Woche", "month": "Monat", "year": "Jahr"}
            plural = {"day": "Tage", "week": "Wochen", "month": "Monate", "year": "Jahre"}
            base = (f"jeden {names[self.unit]}" if self.interval == 1
                    else f"alle {self.interval} {plural[self.unit]}")
        return base + (", ab Erledigung" if self.when_done else "")


def parse(text: str | None) -> Rule | None:
    """None, wenn die Regel nicht erkannt wird - dann passiert nichts."""
    if not text:
        return None
    raw = text.strip()
    when_done = bool(_WHEN_DONE_RE.search(raw))
    body = _WHEN_DONE_RE.sub("", raw).strip()

    m = _EVERY_RE.match(body)
    if not m:
        return None
    count, word = m.group(1), m.group(2).lower()

    if word in WEEKDAYS:
        # "every 2 mondays" gibt es nicht - Wochentag heisst immer woechentlich.
        return Rule(1, "week", WEEKDAYS[word], when_done, raw)
    if word in UNITS:
        return Rule(max(int(count or 1), 1), UNITS[word], None, when_done, raw)
    return None


def add_months(base: date, months: int) -> date:
    """Monate addieren und auf den letzten gueltigen Tag begrenzen.

    31. Januar + 1 Monat ergibt den 28./29. Februar, nicht einen Fehler.
    """
    total = base.month - 1 + months
    year = base.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(base.day, calendar.monthrange(year, month)[1]))


def next_date(rule: Rule, base: date) -> date:
    """Das naechste Vorkommen nach `base` (streng danach)."""
    if rule.weekday is not None:
        ahead = (rule.weekday - base.weekday()) % 7
        return base + timedelta(days=ahead or 7)
    if rule.unit == "day":
        return base + timedelta(days=rule.interval)
    if rule.unit == "week":
        return base + timedelta(weeks=rule.interval)
    if rule.unit == "month":
        return add_months(base, rule.interval)
    return add_months(base, 12 * rule.interval)
