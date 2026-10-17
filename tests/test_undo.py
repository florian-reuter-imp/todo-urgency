"""Tests fuer Rueckgaengig und Wiederholen."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.server import Conflict  # noqa: E402
from test_core import service_on  # noqa: E402

RECUR = "\U0001f501"
DUE = "\U0001f4c5"
DONE = "✅"


def first_uid(svc, text=None):
    for t in svc.list()["tasks"]:
        if text is None or text in t["description"]:
            return t["uid"]
    raise AssertionError("Aufgabe nicht gefunden")


class TestUndoBasics(unittest.TestCase):
    def test_delete_then_undo_restores_byte_for_byte(self):
        src = "# Kopf\n\n- [ ] A \U0001f53a #P\n- [ ] B\n"
        svc, path = service_on(src)
        svc.delete(first_uid(svc, "A"))
        self.assertNotIn("- [ ] A", path.read_text(encoding="utf-8"))
        svc.undo()
        self.assertEqual(path.read_text(encoding="utf-8"), src)

    def test_add_then_undo(self):
        svc, path = service_on("- [ ] A\n")
        before = path.read_text(encoding="utf-8")
        svc.add("Neu", None, "none")
        svc.undo()
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_field_change_then_undo(self):
        svc, path = service_on("- [ ] A\n")
        before = path.read_text(encoding="utf-8")
        svc.update(first_uid(svc), {"description": "B", "due": "2026-12-01", "priority": "high"})
        svc.undo()
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_raw_edit_then_undo(self):
        svc, path = service_on("- [ ] Alt\n")
        before = path.read_text(encoding="utf-8")
        svc.update(first_uid(svc), {"raw": "- [ ] Voellig anders \U0001f53a"})
        svc.undo()
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_drag_then_undo(self):
        svc, path = service_on("- [ ] A\n")
        before = path.read_text(encoding="utf-8")
        svc.update(first_uid(svc), {"urgency": 10.5, "importance": 9.0})
        self.assertNotEqual(path.read_text(encoding="utf-8"), before)
        svc.undo()
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_snooze_then_undo(self):
        svc, path = service_on("- [ ] A\n")
        before = path.read_text(encoding="utf-8")
        svc.update(first_uid(svc), {"snooze_days": 7})
        svc.undo()
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_recurring_completion_is_undone_completely(self):
        """Der Fall, der die Schnappschuss-Bauform gerechtfertigt hat:
        Abhaken legt eine zweite Zeile an, der Haken allein reicht nicht."""
        src = f"- [ ] Muell {RECUR} every week {DUE} 2026-10-20\n"
        svc, path = service_on(src)
        svc.update(first_uid(svc), {"done": True})
        self.assertEqual(path.read_text(encoding="utf-8").count("Muell"), 2)
        svc.undo()
        self.assertEqual(path.read_text(encoding="utf-8"), src)
        self.assertEqual(path.read_text(encoding="utf-8").count("Muell"), 1)

    def test_nothing_to_undo(self):
        svc, _ = service_on("- [ ] A\n")
        with self.assertRaises(ValueError):
            svc.undo()

    def test_unchanged_write_creates_no_entry(self):
        svc, _ = service_on("- [ ] A\n")
        svc.update(first_uid(svc), {"description": "A"})   # nichts geaendert
        self.assertFalse(svc.history()["undo"]["can"])


class TestUndoRedoStacks(unittest.TestCase):
    def states(self, svc, path):
        return path.read_text(encoding="utf-8")

    def test_walks_back_and_forth_more_than_two_steps(self):
        """Kein Pendeln zwischen zwei Zustaenden - eine begehbare Linie."""
        svc, path = service_on("")
        svc.add("A", None, "none")
        a = self.states(svc, path)
        svc.add("B", None, "none")
        b = self.states(svc, path)
        svc.add("C", None, "none")
        c = self.states(svc, path)

        svc.undo()
        self.assertEqual(self.states(svc, path), b)
        svc.undo()
        self.assertEqual(self.states(svc, path), a)
        svc.redo()
        self.assertEqual(self.states(svc, path), b)
        svc.redo()
        self.assertEqual(self.states(svc, path), c)

    def test_new_change_after_undo_clears_the_redo_stack(self):
        svc, path = service_on("")
        svc.add("A", None, "none")
        svc.add("B", None, "none")
        svc.undo()
        self.assertTrue(svc.history()["redo"]["can"])
        svc.add("C", None, "none")          # Abzweig
        self.assertFalse(svc.history()["redo"]["can"])
        with self.assertRaises(ValueError):
            svc.redo()

    def test_undo_all_the_way_back_to_empty(self):
        svc, path = service_on("")
        for name in "ABCDE":
            svc.add(name, None, "none")
        for _ in range(5):
            svc.undo()
        self.assertEqual(path.read_text(encoding="utf-8"), "")
        self.assertFalse(svc.history()["undo"]["can"])

    def test_depth_is_capped(self):
        svc, _ = service_on("")
        for i in range(svc.UNDO_DEPTH + 7):
            svc.add(f"A{i}", None, "none")
        self.assertEqual(len(svc._undo), svc.UNDO_DEPTH)

    def test_labels_describe_the_step(self):
        svc, _ = service_on("- [ ] dfg antrag\n")
        svc.delete(first_uid(svc))
        self.assertIn("dfg antrag", svc.history()["undo"]["label"])
        self.assertIn("geloescht", svc.history()["undo"]["label"])
        svc.undo()
        self.assertIn("dfg antrag", svc.history()["redo"]["label"])


class TestForeignEdits(unittest.TestCase):
    def test_undo_refuses_after_an_outside_edit(self):
        svc, path = service_on("- [ ] A\n")
        svc.delete(first_uid(svc))
        # Jemand bearbeitet die Datei im Editor oder OneDrive synchronisiert.
        path.write_text("- [ ] Von aussen\n", encoding="utf-8")
        with self.assertRaises(Conflict):
            svc.undo()
        # Die fremde Aenderung steht unangetastet da.
        self.assertEqual(path.read_text(encoding="utf-8"), "- [ ] Von aussen\n")

    def test_redo_refuses_after_an_outside_edit(self):
        svc, path = service_on("- [ ] A\n")
        svc.add("B", None, "none")
        svc.undo()
        path.write_text("- [ ] Von aussen\n", encoding="utf-8")
        with self.assertRaises(Conflict):
            svc.redo()
        self.assertEqual(path.read_text(encoding="utf-8"), "- [ ] Von aussen\n")

    def test_stack_survives_a_refused_undo(self):
        svc, path = service_on("- [ ] A\n")
        svc.delete(first_uid(svc))
        restored = path.read_text(encoding="utf-8")
        path.write_text("- [ ] Von aussen\n", encoding="utf-8")
        with self.assertRaises(Conflict):
            svc.undo()
        # Eintrag ist nicht verbraucht: nach Wiederherstellen geht es doch.
        path.write_text(restored, encoding="utf-8")
        svc.undo()
        self.assertIn("- [ ] A", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
