"""Testnetz fuer die drei Kernschichten.

Lauf: python -m unittest discover tests
Nur Stdlib, kein pytest - passt zum Minimal-Dependency-Ansatz.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.codec import TaskCodec, format_effort, parse_effort  # noqa: E402
from app.datastore import LocalFileStore  # noqa: E402
from app.scorer import UrgencyScorer  # noqa: E402
from app.server import Conflict, TaskService  # noqa: E402

TODAY = date(2026, 10, 8)


def service_on(content: str, features: dict | None = None) -> tuple[TaskService, Path]:
    path = Path(tempfile.mkdtemp()) / "todo.md"
    path.write_text(content, encoding="utf-8")
    svc = TaskService(LocalFileStore(path), TaskCodec(), UrgencyScorer(features=features))
    return svc, path


class TestDataStore(unittest.TestCase):
    def test_atomic_save_leaves_no_temp_files(self):
        path = Path(tempfile.mkdtemp()) / "todo.md"
        store = LocalFileStore(path)
        store.save("- [ ] A\n")
        self.assertEqual(store.load(), "- [ ] A\n")
        self.assertEqual([p.name for p in path.parent.iterdir()], ["todo.md"])

    def test_missing_file_is_created_empty(self):
        store = LocalFileStore(Path(tempfile.mkdtemp()) / "neu" / "todo.md")
        self.assertEqual(store.load(), "")

    def test_change_detection(self):
        path = Path(tempfile.mkdtemp()) / "todo.md"
        store = LocalFileStore(path)
        store.save("- [ ] A\n")
        store.load()
        self.assertFalse(store.changed_since_last_load())
        path.write_text("- [ ] A\n- [ ] von aussen\n", encoding="utf-8")
        self.assertTrue(store.changed_since_last_load())


class TestCodec(unittest.TestCase):
    def setUp(self):
        self.codec = TaskCodec()

    def test_fields(self):
        t = self.codec.parse_line(0, "- [ ] Titel \U0001f53a ⏱ 2h ➕ 2026-09-01 \U0001f4c5 2026-10-20 #P")
        self.assertEqual(t.description, "Titel #P")
        self.assertEqual(t.priority, "highest")
        self.assertEqual(t.effort, 120)
        self.assertEqual(t.created, date(2026, 9, 1))
        self.assertEqual(t.due, date(2026, 10, 20))
        self.assertEqual(t.tags, ["P"])
        self.assertFalse(t.done)

    def test_non_task_lines_untouched(self):
        src = "# Kopf\n\nFliesstext.\n1. Keine Checkbox\n- [ ] Echte Aufgabe\n"
        doc = self.codec.parse(src)
        self.assertEqual(len(doc.tasks), 1)
        self.assertEqual(self.codec.serialize(doc), src)

    def test_preserves_indent_marker_and_unknown_emoji(self):
        src = "  * [-] Abgebrochen ⏳ 2026-10-01 \U0001f501 every week\n"
        doc = self.codec.parse(src)
        task = doc.tasks[0]
        self.assertEqual(task.indent, "  ")
        self.assertEqual(task.marker, "*")
        self.assertTrue(task.done)
        out = self.codec.serialize(doc)
        self.assertIn("⏳ 2026-10-01", out)
        self.assertIn("\U0001f501 every week", out)

    def test_serialisation_is_idempotent(self):
        src = "- [ ] A \U0001f4c5 2026-10-20 ⏫ #X\n- [x] B ➕ 2026-01-01\n"
        once = self.codec.serialize(self.codec.parse(src))
        twice = self.codec.serialize(self.codec.parse(once))
        self.assertEqual(once, twice)

    def test_variation_selector_is_tolerated(self):
        t = self.codec.parse_line(0, "- [ ] A ⏱️ 90m")
        self.assertEqual(t.effort, 90)

    def test_text_after_a_date_field_survives(self):
        """Regression: der Datums-Zweig hat den Rest des Segments verworfen."""
        t = self.codec.parse_line(0, "- [ ] A \U0001f4c5 2026-10-20 #projekt")
        self.assertEqual(t.due, date(2026, 10, 20))
        self.assertEqual(t.tags, ["projekt"])
        self.assertIn("#projekt", t.to_line())

    def test_unreadable_date_is_kept_not_lost(self):
        t = self.codec.parse_line(0, "- [ ] A \U0001f4c5 irgendwann")
        self.assertIsNone(t.due)
        self.assertIn("irgendwann", t.to_line())

    def test_unreadable_effort_is_kept_not_lost(self):
        t = self.codec.parse_line(0, "- [ ] A ⏱ bald")
        self.assertIsNone(t.effort)
        self.assertIn("bald", t.to_line())

    def test_uid_detects_a_changed_line(self):
        doc = self.codec.parse("- [ ] A\n")
        before = doc.tasks[0].uid
        doc.tasks[0].description = "B"
        self.assertNotEqual(before, doc.tasks[0].uid)


class TestEffortParsing(unittest.TestCase):
    def test_roundtrip(self):
        for text, minutes, back in [
            ("20m", 20, "20m"),
            ("2h", 120, "2h"),
            ("1h30m", 90, "1h30m"),
            ("1,5h", 90, "1h30m"),
            ("1d", 480, "1d"),
            ("45", 45, "45m"),
        ]:
            self.assertEqual(parse_effort(text), minutes, text)
            self.assertEqual(format_effort(minutes), back, text)

    def test_unparsable(self):
        self.assertIsNone(parse_effort("bald"))
        self.assertIsNone(parse_effort(""))
        self.assertEqual(format_effort(None), "")


class TestScorer(unittest.TestCase):
    def setUp(self):
        self.codec = TaskCodec()
        self.scorer = UrgencyScorer()

    def task(self, line: str):
        return self.codec.parse_line(0, line)

    def test_no_due_date_no_due_contribution(self):
        s = self.scorer.score(self.task("- [ ] A"), TODAY)
        self.assertEqual(s.breakdown["due"], 0.0)

    def test_overdue_saturates(self):
        far = self.scorer.score(self.task("- [ ] A \U0001f4c5 2026-01-01"), TODAY)
        self.assertAlmostEqual(far.breakdown["due"], 12.0)

    def test_effort_stretches_the_horizon(self):
        """Gleiche Frist, mehr Aufwand -> hoeherer Score. Der Kernpunkt."""
        small = self.task("- [ ] A ⏱ 10m \U0001f4c5 2026-10-25")
        big = self.task("- [ ] B ⏱ 2d \U0001f4c5 2026-10-25")
        self.assertEqual(self.scorer.horizon_days(small), 14.25)
        self.assertEqual(self.scorer.horizon_days(big), 38.0)
        self.assertGreater(
            self.scorer.score(big, TODAY).urgency,
            self.scorer.score(small, TODAY).urgency,
        )

    def test_without_effort_field_behaviour_is_unchanged(self):
        plain = self.task("- [ ] A \U0001f4c5 2026-10-25")
        self.assertEqual(self.scorer.horizon_days(plain), 14.0)

    def test_disabled_feature_equals_missing_field(self):
        off = UrgencyScorer(features={"effort": False})
        with_effort = self.task("- [ ] A ⏱ 2d \U0001f4c5 2026-10-25")
        without = self.task("- [ ] A \U0001f4c5 2026-10-25")
        self.assertEqual(
            off.score(with_effort, TODAY).urgency, off.score(without, TODAY).urgency
        )

    def test_effort_extra_is_capped(self):
        huge = self.task("- [ ] A ⏱ 100d \U0001f4c5 2026-10-25")
        self.assertEqual(self.scorer.horizon_days(huge), 14.0 + 45.0)

    def test_priority_order(self):
        scores = [
            self.scorer.score(self.task(f"- [ ] A {emoji}"), TODAY).importance
            for emoji in ("\U0001f53a", "⏫", "\U0001f53c", "", "\U0001f53d", "⏬")
        ]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_sorted_descending(self):
        tasks = [
            self.task("- [ ] klein"),
            self.task("- [ ] dringend \U0001f4c5 2026-01-01 \U0001f53a"),
        ]
        order = [s.task.description for s in self.scorer.score_all(tasks, TODAY)]
        self.assertEqual(order, ["dringend", "klein"])

    def test_quadrants(self):
        self.assertEqual(
            self.scorer.score(self.task("- [ ] A \U0001f4c5 2026-01-01 \U0001f53a"), TODAY).quadrant,
            "do",
        )
        self.assertEqual(self.scorer.score(self.task("- [ ] A ⏬"), TODAY).quadrant, "drop")

    def test_inversion_round_trips_within_a_day(self):
        for target in (11.0, 9.0, 6.0, 3.0):
            task = self.task("- [ ] A")
            due = self.scorer.due_for_urgency(task, target, TODAY)
            task.due = due
            actual = self.scorer.score(task, TODAY).urgency
            self.assertLess(abs(actual - target), 0.6, f"Ziel {target} -> {actual}")

    def test_inversion_below_minimum_drops_the_date(self):
        self.assertIsNone(self.scorer.due_for_urgency(self.task("- [ ] A"), 1.0, TODAY))

    def test_importance_inversion_snaps_to_a_level(self):
        task = self.task("- [ ] A")
        self.assertEqual(self.scorer.priority_for_importance(task, 9.0), "highest")
        self.assertEqual(self.scorer.priority_for_importance(task, -1.8), "lowest")


class TestService(unittest.TestCase):
    def test_add_sets_created_date(self):
        svc, path = service_on("")
        svc.add("Neu #P", due="2026-10-20", priority="high", effort="2h")
        line = path.read_text(encoding="utf-8")
        self.assertIn("Neu #P", line)
        self.assertIn("⏱ 2h", line)
        self.assertIn(date.today().isoformat(), line)

    def test_add_rejects_empty(self):
        svc, _ = service_on("")
        with self.assertRaises(ValueError):
            svc.add("   ", None, "none")

    def test_add_rejects_bad_effort(self):
        svc, _ = service_on("")
        with self.assertRaises(ValueError):
            svc.add("A", None, "none", effort="bald")

    def test_toggle_done(self):
        svc, path = service_on("- [ ] A\n")
        uid = svc.list()["tasks"][0]["uid"]
        svc.update(uid, {"done": True})
        self.assertTrue(path.read_text(encoding="utf-8").startswith("- [x]"))

    def test_update_fields(self):
        svc, path = service_on("- [ ] A\n")
        uid = svc.list()["tasks"][0]["uid"]
        svc.update(uid, {"description": "B", "due": "2026-11-01", "priority": "medium", "effort": "30m"})
        out = path.read_text(encoding="utf-8")
        self.assertIn("B", out)
        self.assertIn("\U0001f4c5 2026-11-01", out)
        self.assertIn("⏱ 30m", out)

    def test_clearing_effort_and_due(self):
        svc, path = service_on("- [ ] A ⏱ 2h \U0001f4c5 2026-11-01\n")
        uid = svc.list()["tasks"][0]["uid"]
        svc.update(uid, {"effort": "", "due": None})
        out = path.read_text(encoding="utf-8")
        self.assertNotIn("⏱", out)
        self.assertNotIn("\U0001f4c5", out)

    def test_raw_edit_actually_writes(self):
        """Regression: serialize() hat die Ersetzung wieder ueberschrieben."""
        svc, path = service_on("# Kopf\n- [ ] Alt\n- [ ] Zweite\n")
        uid = [t for t in svc.list()["tasks"] if t["description"] == "Alt"][0]["uid"]
        svc.update(uid, {"raw": "- [ ] Komplett neu \U0001f53a #N"})
        out = path.read_text(encoding="utf-8")
        self.assertIn("Komplett neu", out)
        self.assertNotIn("Alt", out)
        self.assertIn("# Kopf", out)
        self.assertIn("Zweite", out)

    def test_raw_edit_rejects_non_task(self):
        svc, _ = service_on("- [ ] A\n")
        uid = svc.list()["tasks"][0]["uid"]
        with self.assertRaises(ValueError):
            svc.update(uid, {"raw": "kein task"})

    def test_delete_keeps_the_rest(self):
        svc, path = service_on("# Kopf\n- [ ] A\n- [ ] B\n")
        uid = [t for t in svc.list()["tasks"] if t["description"] == "A"][0]["uid"]
        svc.delete(uid)
        out = path.read_text(encoding="utf-8")
        self.assertNotIn("- [ ] A", out)
        self.assertIn("- [ ] B", out)
        self.assertIn("# Kopf", out)

    def test_stale_uid_conflicts(self):
        svc, _ = service_on("- [ ] A\n")
        uid = svc.list()["tasks"][0]["uid"]
        svc.update(uid, {"done": True})
        with self.assertRaises(Conflict):
            svc.update(uid, {"done": False})

    def test_drag_coordinates_become_date_and_priority(self):
        svc, _ = service_on("- [ ] A\n")
        uid = svc.list()["tasks"][0]["uid"]
        svc.update(uid, {"urgency": 10.5, "importance": 9.0})
        task = svc.list()["tasks"][0]
        self.assertEqual(task["priority"], "highest")
        self.assertIsNotNone(task["due"])

    def test_features_are_exposed(self):
        svc, _ = service_on("", features={"effort": False})
        self.assertFalse(svc.list()["features"]["effort"])


class TestSnooze(unittest.TestCase):
    def setUp(self):
        self.codec = TaskCodec()
        self.scorer = UrgencyScorer()

    def test_start_is_a_real_field_now(self):
        t = self.codec.parse_line(0, "- [ ] A 🛫 2026-11-01")
        self.assertEqual(t.start, date(2026, 11, 1))
        self.assertEqual(t.passthrough, [])
        self.assertIn("🛫 2026-11-01", t.to_line())

    def test_future_start_is_snoozed(self):
        t = self.codec.parse_line(0, "- [ ] A 🛫 2026-11-01")
        self.assertTrue(self.scorer.score(t, TODAY).snoozed)

    def test_past_start_is_not_snoozed(self):
        t = self.codec.parse_line(0, "- [ ] A 🛫 2026-09-01")
        self.assertFalse(self.scorer.score(t, TODAY).snoozed)

    def test_no_start_is_never_snoozed(self):
        self.assertFalse(self.scorer.score(self.codec.parse_line(0, "- [ ] A"), TODAY).snoozed)

    def test_snooze_does_not_change_the_score(self):
        """Zurueckgestellt wird ausgeblendet, nicht abgewertet."""
        plain = self.codec.parse_line(0, "- [ ] A 📅 2026-10-10")
        snoozed = self.codec.parse_line(0, "- [ ] A 📅 2026-10-10 🛫 2026-11-01")
        self.assertEqual(
            self.scorer.score(plain, TODAY).score, self.scorer.score(snoozed, TODAY).score
        )

    def test_disabled_feature_never_snoozes(self):
        off = UrgencyScorer(features={"snooze": False})
        t = self.codec.parse_line(0, "- [ ] A 🛫 2026-11-01")
        self.assertFalse(off.score(t, TODAY).snoozed)
        self.assertIn("🛫 2026-11-01", t.to_line())

    def test_service_snooze_days(self):
        svc, path = service_on("- [ ] A\n")
        uid = svc.list()["tasks"][0]["uid"]
        svc.update(uid, {"snooze_days": 7})
        task = svc.list()["tasks"][0]
        self.assertIsNotNone(task["start"])
        self.assertTrue(task["snoozed"])
        self.assertIn("🛫", path.read_text(encoding="utf-8"))

    def test_service_unsnooze(self):
        svc, path = service_on("- [ ] A \U0001f6eb 2026-11-01\n")
        uid = svc.list()["tasks"][0]["uid"]
        svc.update(uid, {"snooze_days": 0})
        self.assertIsNone(svc.list()["tasks"][0]["start"])
        self.assertNotIn("🛫", path.read_text(encoding="utf-8"))

    def test_add_with_start(self):
        svc, path = service_on("")
        svc.add("A", due=None, priority="none", start="2026-12-01")
        self.assertIn("🛫 2026-12-01", path.read_text(encoding="utf-8"))



if __name__ == "__main__":
    unittest.main(verbosity=2)
