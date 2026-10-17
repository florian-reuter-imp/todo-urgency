"""Tests fuer Wiederholungen (Phase 2)."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import recurrence  # noqa: E402
from app.codec import TaskCodec  # noqa: E402
from test_core import service_on  # noqa: E402

RECUR = "\U0001f501"  # arrows in circle
DONE = "✅"       # check mark
DUE = "\U0001f4c5"    # calendar
START = "\U0001f6eb"  # airplane


class TestRuleParsing(unittest.TestCase):
    def test_simple_units(self):
        for text, interval, unit in [
            ("every day", 1, "day"),
            ("every 3 days", 3, "day"),
            ("every week", 1, "week"),
            ("every 2 weeks", 2, "week"),
            ("every month", 1, "month"),
            ("every 6 months", 6, "month"),
            ("every year", 1, "year"),
        ]:
            rule = recurrence.parse(text)
            self.assertIsNotNone(rule, text)
            self.assertEqual((rule.interval, rule.unit), (interval, unit), text)
            self.assertFalse(rule.when_done, text)

    def test_weekdays_both_languages(self):
        for text, weekday in [("every monday", 0), ("jeden Freitag", 4), ("every sunday", 6)]:
            rule = recurrence.parse(text)
            self.assertIsNotNone(rule, text)
            self.assertEqual(rule.weekday, weekday, text)

    def test_german_units(self):
        rule = recurrence.parse("alle 2 Wochen")
        self.assertEqual((rule.interval, rule.unit), (2, "week"))

    def test_when_done(self):
        rule = recurrence.parse("every 2 weeks when done")
        self.assertTrue(rule.when_done)
        self.assertEqual((rule.interval, rule.unit), (2, "week"))

    def test_unknown_rules_return_none(self):
        for text in ["", None, "every blue moon", "wenn ich lust habe", "every 2"]:
            self.assertIsNone(recurrence.parse(text), repr(text))

    def test_describe_is_human_readable(self):
        self.assertEqual(recurrence.parse("every day").describe(), "jeden Tag")
        self.assertEqual(recurrence.parse("every 2 weeks").describe(), "alle 2 Wochen")
        self.assertEqual(recurrence.parse("every monday").describe(), "jeden Montag")
        self.assertEqual(
            recurrence.parse("every month when done").describe(), "jeden Monat, ab Erledigung"
        )


class TestNextDate(unittest.TestCase):
    def test_days_and_weeks(self):
        base = date(2026, 10, 8)
        self.assertEqual(recurrence.next_date(recurrence.parse("every day"), base),
                         date(2026, 10, 9))
        self.assertEqual(recurrence.next_date(recurrence.parse("every 3 days"), base),
                         date(2026, 10, 11))
        self.assertEqual(recurrence.next_date(recurrence.parse("every 2 weeks"), base),
                         date(2026, 10, 22))

    def test_month_end_is_clamped(self):
        """31. Januar + 1 Monat darf nicht krachen."""
        rule = recurrence.parse("every month")
        self.assertEqual(recurrence.next_date(rule, date(2026, 1, 31)), date(2026, 2, 28))
        self.assertEqual(recurrence.next_date(rule, date(2028, 1, 31)), date(2028, 2, 29))

    def test_year(self):
        self.assertEqual(
            recurrence.next_date(recurrence.parse("every year"), date(2026, 10, 8)),
            date(2027, 10, 8),
        )

    def test_weekday_always_moves_forward(self):
        rule = recurrence.parse("every monday")
        monday = date(2026, 10, 12)
        self.assertEqual(rule.weekday, monday.weekday())
        # Von einem Montag aus geht es eine ganze Woche weiter, nicht null Tage.
        self.assertEqual(recurrence.next_date(rule, monday), date(2026, 10, 19))
        self.assertEqual(recurrence.next_date(rule, date(2026, 10, 8)), monday)


class TestCodecRecurrence(unittest.TestCase):
    def setUp(self):
        self.codec = TaskCodec()

    def test_recurrence_is_a_real_field(self):
        t = self.codec.parse_line(0, f"- [ ] Muell {RECUR} every week {DUE} 2026-10-10")
        self.assertEqual(t.recurrence, "every week")
        self.assertEqual(t.due, date(2026, 10, 10))
        self.assertEqual(t.description, "Muell")

    def test_done_date_is_a_real_field(self):
        t = self.codec.parse_line(0, f"- [x] A {DONE} 2026-10-01")
        self.assertEqual(t.done_date, date(2026, 10, 1))
        self.assertTrue(t.done)

    def test_roundtrip_keeps_both(self):
        src = f"- [x] A {RECUR} every 2 weeks {DUE} 2026-10-10 {DONE} 2026-10-01\n"
        once = self.codec.serialize(self.codec.parse(src))
        self.assertEqual(once, src)

    def test_unknown_rule_text_is_preserved(self):
        t = self.codec.parse_line(0, f"- [ ] A {RECUR} every blue moon")
        self.assertEqual(t.recurrence, "every blue moon")
        self.assertIn("every blue moon", t.to_line())


class TestServiceRecurrence(unittest.TestCase):
    def complete_first(self, svc):
        uid = [t for t in svc.list()["tasks"] if not t["done"]][0]["uid"]
        svc.update(uid, {"done": True})

    def test_completing_spawns_the_next_instance(self):
        svc, path = service_on(f"- [ ] Muell rausbringen {RECUR} every week {DUE} 2026-10-10\n")
        self.complete_first(svc)
        lines = [l for l in path.read_text(encoding="utf-8").split("\n") if l.strip()]
        self.assertEqual(len(lines), 2, lines)
        self.assertTrue(any(l.startswith("- [ ]") and "2026-10-17" in l for l in lines), lines)
        self.assertTrue(any(l.startswith("- [x]") and "2026-10-10" in l for l in lines), lines)

    def test_next_instance_keeps_fields(self):
        svc, path = service_on(
            f"- [ ] Rechnung #Haushalt ⏫ {RECUR} every month ⏱ 30m {DUE} 2026-10-10\n"
        )
        self.complete_first(svc)
        nxt = [t for t in svc.list()["tasks"] if not t["done"]][0]
        self.assertEqual(nxt["priority"], "high")
        self.assertEqual(nxt["effort"], 30)
        self.assertEqual(nxt["tags"], ["Haushalt"])
        self.assertEqual(nxt["recurrence"], "every month")
        self.assertEqual(nxt["due"], "2026-11-10")

    def test_start_date_keeps_its_distance_to_the_due_date(self):
        svc, _ = service_on(
            f"- [ ] A {RECUR} every month {START} 2026-10-05 {DUE} 2026-10-10\n"
        )
        self.complete_first(svc)
        nxt = [t for t in svc.list()["tasks"] if not t["done"]][0]
        self.assertEqual(nxt["due"], "2026-11-10")
        self.assertEqual(nxt["start"], "2026-11-05")

    def test_unknown_rule_just_completes(self):
        svc, path = service_on(f"- [ ] A {RECUR} every blue moon\n")
        self.complete_first(svc)
        lines = [l for l in path.read_text(encoding="utf-8").split("\n") if l.strip()]
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("- [x]"))

    def test_non_recurring_task_does_not_multiply(self):
        svc, path = service_on("- [ ] Einmalig\n")
        self.complete_first(svc)
        lines = [l for l in path.read_text(encoding="utf-8").split("\n") if l.strip()]
        self.assertEqual(len(lines), 1, lines)

    def test_disabled_feature_does_not_spawn(self):
        svc, path = service_on(
            f"- [ ] A {RECUR} every week {DUE} 2026-10-10\n", features={"recurrence": False}
        )
        self.complete_first(svc)
        lines = [l for l in path.read_text(encoding="utf-8").split("\n") if l.strip()]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("every week", lines[0])

    def test_surrounding_lines_survive_the_insertion(self):
        svc, path = service_on(
            "# Kopf\n"
            "- [ ] Davor\n"
            f"- [ ] Wiederkehrend {RECUR} every week {DUE} 2026-10-10\n"
            "- [ ] Danach\n"
        )
        uid = [t for t in svc.list()["tasks"] if "Wiederkehrend" in t["description"]][0]["uid"]
        svc.update(uid, {"done": True})
        out = path.read_text(encoding="utf-8")
        self.assertIn("# Kopf", out)
        self.assertIn("- [ ] Davor", out)
        self.assertIn("- [ ] Danach", out)
        self.assertEqual(out.count("Wiederkehrend"), 2)

    def test_done_date_is_written_and_removed(self):
        svc, path = service_on("- [ ] A\n")
        uid = svc.list()["tasks"][0]["uid"]
        svc.update(uid, {"done": True})
        self.assertIn(DONE, path.read_text(encoding="utf-8"))
        uid = svc.list()["tasks"][0]["uid"]
        svc.update(uid, {"done": False})
        self.assertNotIn(DONE, path.read_text(encoding="utf-8"))

    def test_done_dates_can_be_switched_off(self):
        svc, path = service_on("- [ ] A\n", features={"done_dates": False})
        uid = svc.list()["tasks"][0]["uid"]
        svc.update(uid, {"done": True})
        self.assertNotIn(DONE, path.read_text(encoding="utf-8"))

    def test_bad_rule_is_rejected_on_write(self):
        svc, _ = service_on("- [ ] A\n")
        uid = svc.list()["tasks"][0]["uid"]
        with self.assertRaises(ValueError):
            svc.update(uid, {"recurrence": "wenn ich lust habe"})

    def test_when_done_anchors_on_today(self):
        svc, _ = service_on(f"- [ ] A {RECUR} every 3 days when done {DUE} 2026-01-01\n")
        self.complete_first(svc)
        nxt = [t for t in svc.list()["tasks"] if not t["done"]][0]
        expected = recurrence.next_date(recurrence.parse("every 3 days"), date.today())
        self.assertEqual(nxt["due"], expected.isoformat())


if __name__ == "__main__":
    unittest.main(verbosity=2)
