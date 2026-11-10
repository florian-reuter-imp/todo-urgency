"""Tests fuer das Aufschieben: die Frist selbst wandert.

Abgrenzung zum Zurueckstellen (🛫): dort bleibt die Frist stehen und nur
die Sichtbarkeit aendert sich.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_core import service_on  # noqa: E402

DUE = "\U0001f4c5"


def only(svc):
    return svc.list()["tasks"][0]


class TestPostpone(unittest.TestCase):
    def test_adds_whole_weeks_to_the_old_deadline(self):
        svc, _ = service_on(f"- [ ] A {DUE} 2026-11-05\n")
        svc.update(only(svc)["uid"], {"postpone_weeks": 1})
        self.assertEqual(only(svc)["due"], "2026-11-12")

    def test_two_and_four_weeks(self):
        for weeks, expected in [(2, "2026-11-19"), (4, "2026-12-03")]:
            svc, _ = service_on(f"- [ ] A {DUE} 2026-11-05\n")
            svc.update(only(svc)["uid"], {"postpone_weeks": weeks})
            self.assertEqual(only(svc)["due"], expected, f"{weeks} Wochen")

    def test_weekday_is_preserved(self):
        """Der Donnerstag des Jour fixe bleibt ein Donnerstag."""
        start = date(2026, 11, 5)
        self.assertEqual(start.weekday(), 3)          # Donnerstag
        svc, _ = service_on(f"- [ ] A {DUE} {start}\n")
        for _ in range(3):
            svc.update(only(svc)["uid"], {"postpone_weeks": 1})
        self.assertEqual(date.fromisoformat(only(svc)["due"]).weekday(), 3)

    def test_counts_from_the_old_deadline_not_from_today(self):
        """Bei langem Verzug landet +1 Woche weiter in der Vergangenheit.

        Das ist gewollt: die Oberflaeche zeigt das Ergebnis vorher an,
        statt heimlich auf heute umzustellen.
        """
        alt = date.today() - timedelta(days=18)
        svc, _ = service_on(f"- [ ] A {DUE} {alt}\n")
        svc.update(only(svc)["uid"], {"postpone_weeks": 1})
        neu = date.fromisoformat(only(svc)["due"])
        self.assertEqual(neu, alt + timedelta(days=7))
        self.assertLess(neu, date.today())

    def test_repeated_clicks_accumulate(self):
        svc, _ = service_on(f"- [ ] A {DUE} 2026-11-05\n")
        for _ in range(3):
            svc.update(only(svc)["uid"], {"postpone_weeks": 1})
        self.assertEqual(only(svc)["due"], "2026-11-26")

    def test_lowers_urgency_for_a_fresh_miss(self):
        gestern = date.today() - timedelta(days=1)
        svc, _ = service_on(f"- [ ] A ⏫ {DUE} {gestern}\n")
        vorher = only(svc)["urgency"]
        svc.update(only(svc)["uid"], {"postpone_weeks": 1})
        self.assertLess(only(svc)["urgency"], vorher)

    def test_without_a_deadline_it_is_refused(self):
        svc, _ = service_on("- [ ] A\n")
        with self.assertRaises(ValueError):
            svc.update(only(svc)["uid"], {"postpone_weeks": 1})

    def test_zero_or_negative_is_refused(self):
        svc, _ = service_on(f"- [ ] A {DUE} 2026-11-05\n")
        for weeks in (0, -1):
            with self.assertRaises(ValueError):
                svc.update(only(svc)["uid"], {"postpone_weeks": weeks})

    def test_start_date_is_untouched(self):
        """Aufschieben fasst die Sichtbarkeit nicht an."""
        svc, _ = service_on(f"- [ ] A \U0001f6eb 2026-11-20 {DUE} 2026-11-05\n")
        svc.update(only(svc)["uid"], {"postpone_weeks": 1})
        task = only(svc)
        self.assertEqual(task["start"], "2026-11-20")
        self.assertEqual(task["due"], "2026-11-12")

    def test_snooze_leaves_the_deadline_alone(self):
        """Gegenprobe: Zurueckstellen aendert die Frist nicht."""
        svc, _ = service_on(f"- [ ] A {DUE} 2026-11-05\n")
        svc.update(only(svc)["uid"], {"snooze_days": 7})
        task = only(svc)
        self.assertEqual(task["due"], "2026-11-05")
        self.assertIsNotNone(task["start"])

    def test_is_undoable(self):
        svc, path = service_on(f"- [ ] A {DUE} 2026-11-05\n")
        before = path.read_text(encoding="utf-8")
        svc.update(only(svc)["uid"], {"postpone_weeks": 2})
        self.assertIn("aufgeschoben", svc.history()["undo"]["label"])
        svc.undo()
        self.assertEqual(path.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
