import tempfile
import unittest
from pathlib import Path

from report_generator import HumanReadableReport


class ReportGeneratorTests(unittest.TestCase):
    def test_render_empty_predictions(self):
        report = HumanReadableReport().render([], title="Daily")
        self.assertEqual(report, "# Daily\n\nNo predictions were generated.\n")

    def test_render_groups_evidence_and_optional_sections(self):
        prediction = {
            "rank": 1,
            "home": "Home",
            "away": "Away",
            "market": "Home wins",
            "confidence": 80.0,
            "score": 24.5,
            "prediction": 19.6,
            "evidence": [
                {"section": "general", "team": "home", "name": "Wins", "value": "5"},
                {
                    "section": "head2head",
                    "team": "both",
                    "name": "Wins",
                    "value": "3/4",
                },
                {"section": "future", "team": None, "name": "Signal", "value": "yes"},
            ],
            "supporting_evidence": [],
            "bonuses": ["form -> result"],
            "penalties": ["conflict"],
        }
        report = HumanReadableReport().render([prediction], title="Daily")

        self.assertIn("# Daily", report)
        self.assertIn(
            "| 1 | Home - Away | Home wins | [View details](#prediction-1) |", report
        )
        self.assertIn("Home — Wins: **5**", report)
        self.assertIn("Both — Wins: **3/4**", report)
        self.assertIn("Future:", report)
        self.assertIn("Why the prediction is strengthened", report)
        self.assertIn("Conflicting evidence", report)

    def test_save_writes_rendered_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.md"
            result = HumanReadableReport().save([], str(path), title="Saved")
            self.assertEqual(result, str(path))
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "# Saved\n\nNo predictions were generated.\n",
            )

    def test_render_includes_threshold_notice(self):
        report = HumanReadableReport().render(
            [],
            title="Daily",
            threshold_notice="Prediction threshold 100.00 excluded 2 prediction(s).",
        )
        self.assertIn("Prediction threshold 100.00 excluded 2 prediction(s).", report)


if __name__ == "__main__":
    unittest.main()
