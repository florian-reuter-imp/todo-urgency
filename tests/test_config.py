"""Tests fuer Zeitreise und Gewichts-Tuning (Phasen 3 und 4)."""

from __future__ import annotations

import sys
import tempfile
import tomllib
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from test_core import service_on  # noqa: E402

SAMPLE = """\
# Konfiguration mit wichtigen Kommentaren
[data]
file = "todo.md"

[urgency.due]
coefficient = 12.0          # Gesamtwirkung der Faelligkeit
overdue_days_for_max = 7    # ab 7 Tagen ueberfaellig
min_factor = 0.2

[urgency.priority]
highest = 9.0   # Dreieck rot
low     = 0.0
"""


class TestConfigWriter(unittest.TestCase):
    def write(self, updates, text=SAMPLE):
        path = Path(tempfile.mkdtemp()) / "config.toml"
        path.write_text(text, encoding="utf-8")
        config.update_file(updates, path)
        return path, path.read_text(encoding="utf-8")

    def test_replaces_value_in_place(self):
        _, out = self.write({"urgency.due": {"coefficient": 9.5}})
        self.assertIn("coefficient = 9.5", out)
        self.assertNotIn("coefficient = 12.0", out)

    def test_keeps_comments_and_other_sections(self):
        _, out = self.write({"urgency.due": {"coefficient": 9.5}})
        self.assertIn("# Konfiguration mit wichtigen Kommentaren", out)
        self.assertIn("# Gesamtwirkung der Faelligkeit", out)
        self.assertIn('file = "todo.md"', out)
        self.assertIn("# Dreieck rot", out)

    def test_keeps_alignment_style(self):
        _, out = self.write({"urgency.priority": {"low": -1.0}})
        self.assertIn("low     = -1.0", out)

    def test_result_is_still_valid_toml(self):
        path, _ = self.write(
            {"urgency.due": {"coefficient": 9.5}, "urgency.priority": {"highest": 8.0}}
        )
        with path.open("rb") as fh:
            parsed = tomllib.load(fh)
        self.assertEqual(parsed["urgency"]["due"]["coefficient"], 9.5)
        self.assertEqual(parsed["urgency"]["priority"]["highest"], 8.0)
        self.assertEqual(parsed["data"]["file"], "todo.md")

    def test_missing_key_is_added_to_its_section(self):
        path, out = self.write({"urgency.due": {"future_days_for_min": 21.0}})
        with path.open("rb") as fh:
            parsed = tomllib.load(fh)
        self.assertEqual(parsed["urgency"]["due"]["future_days_for_min"], 21.0)
        self.assertEqual(parsed["urgency"]["due"]["coefficient"], 12.0)

    def test_missing_section_is_appended(self):
        path, _ = self.write({"urgency.effort": {"days_per_hour": 2.0}})
        with path.open("rb") as fh:
            parsed = tomllib.load(fh)
        self.assertEqual(parsed["urgency"]["effort"]["days_per_hour"], 2.0)

    def test_missing_file_is_created(self):
        path = Path(tempfile.mkdtemp()) / "config.toml"
        config.update_file({"urgency.due": {"coefficient": 5.0}}, path)
        with path.open("rb") as fh:
            self.assertEqual(tomllib.load(fh)["urgency"]["due"]["coefficient"], 5.0)

    def test_no_temp_file_is_left_behind(self):
        path, _ = self.write({"urgency.due": {"coefficient": 9.5}})
        self.assertEqual([p.name for p in path.parent.iterdir()], ["config.toml"])


class TestTimeTravel(unittest.TestCase):
    def test_later_date_raises_urgency(self):
        svc, _ = service_on("- [ ] A \U0001f4c5 2026-10-20 \U0001f53c\n")
        near = svc.list(date(2026, 10, 8))["tasks"][0]
        later = svc.list(date(2026, 10, 19))["tasks"][0]
        self.assertGreater(later["score"], near["score"])
        self.assertEqual(near["quadrant"], "plan")
        self.assertEqual(later["quadrant"], "do")

    def test_reported_today_matches_the_request(self):
        svc, _ = service_on("- [ ] A\n")
        self.assertEqual(svc.list(date(2026, 12, 24))["today"], "2026-12-24")

    def test_snooze_resolves_in_the_future(self):
        svc, _ = service_on("- [ ] A \U0001f6eb 2026-11-01\n")
        self.assertTrue(svc.list(date(2026, 10, 8))["tasks"][0]["snoozed"])
        self.assertFalse(svc.list(date(2026, 11, 2))["tasks"][0]["snoozed"])


class TestWeightTuning(unittest.TestCase):
    def test_get_config_reports_weights_and_defaults(self):
        svc, _ = service_on("")
        cfg = svc.get_config()
        self.assertEqual(cfg["urgency"]["due"]["coefficient"], 12.0)
        self.assertEqual(cfg["defaults"]["due"]["coefficient"], 12.0)
        self.assertIn("effort", cfg["features"])

    def test_preview_changes_the_order_without_saving(self):
        svc, path = service_on(
            "- [ ] dringend \U0001f4c5 2026-01-01\n- [ ] wichtig \U0001f53a\n"
        )
        before = path.read_text(encoding="utf-8")
        normal = [t["description"] for t in svc.list(date(2026, 10, 8))["tasks"]]
        self.assertEqual(normal[0], "dringend")

        # Faelligkeit entwerten -> die wichtige Aufgabe zieht vorbei.
        flat = svc.preview({"due": {"coefficient": 0.0}}, date(2026, 10, 8))
        self.assertEqual([t["description"] for t in flat["tasks"]][0], "wichtig")
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        # Der echte Scorer bleibt unberuehrt.
        self.assertEqual(svc.scorer.cfg["due"]["coefficient"], 12.0)

    def test_save_config_takes_effect_immediately(self):
        svc, _ = service_on("- [ ] A \U0001f4c5 2026-01-01\n")
        path = Path(tempfile.mkdtemp()) / "config.toml"
        original = config.CONFIG_PATH
        config.CONFIG_PATH = path  # Schreibziel umlenken, nie die echte Datei
        try:
            svc.save_config({"due": {"coefficient": 6.0}})
            with path.open("rb") as fh:
                self.assertEqual(tomllib.load(fh)["urgency"]["due"]["coefficient"], 6.0)
        finally:
            config.CONFIG_PATH = original
        self.assertEqual(svc.scorer.cfg["due"]["coefficient"], 6.0)
        self.assertAlmostEqual(svc.list(date(2026, 10, 8))["tasks"][0]["breakdown"]["due"], 6.0)

    def test_partial_update_keeps_the_other_weights(self):
        """Regression: Teiluebergabe darf nicht auf Auslieferungswerte zurueckfallen."""
        svc, _ = service_on("- [ ] A\n")
        svc.scorer.cfg["tags"]["coefficient"] = 3.0   # abweichender Stand aus der Datei
        preview = svc.preview({"due": {"coefficient": 1.0}})
        self.assertEqual(preview["tasks"][0]["breakdown"]["tags"], 0.0)  # Task hat kein Tag

        svc, _ = service_on("- [ ] A #P\n")
        svc.scorer.cfg["tags"]["coefficient"] = 3.0
        preview = svc.preview({"due": {"coefficient": 1.0}})
        self.assertEqual(preview["tasks"][0]["breakdown"]["tags"], 3.0)

    def test_unknown_section_is_rejected(self):
        svc, _ = service_on("")
        with self.assertRaises(ValueError):
            svc.preview({"mondphase": {"coefficient": 1.0}})

    def test_unknown_key_is_rejected(self):
        svc, _ = service_on("")
        with self.assertRaises(ValueError):
            svc.preview({"due": {"erfundener_wert": 1.0}})

    def test_non_numeric_value_is_rejected(self):
        svc, _ = service_on("")
        with self.assertRaises(ValueError):
            svc.preview({"due": {"coefficient": "viel"}})
        with self.assertRaises(ValueError):
            svc.preview({"due": {"coefficient": True}})


if __name__ == "__main__":
    unittest.main(verbosity=2)
