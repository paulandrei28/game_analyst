import json
import tempfile
import unittest
from pathlib import Path

from web_export import export_reports


class WebExportTests(unittest.TestCase):
    def test_export_reports_copies_files_and_orders_newest_first(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            analysis_dir = root / "output" / "analysis"
            analysis_dir.mkdir(parents=True)
            (analysis_dir / "analysis_20260902.json").write_text(
                '{"old": []}', encoding="utf-8"
            )
            (analysis_dir / "analysis_20260904.json").write_text(
                '{"new": []}', encoding="utf-8"
            )

            destination = root / "site"
            entries = export_reports(root / "output", destination)

            self.assertEqual(
                [entry["date"] for entry in entries], ["20260904", "20260902"]
            )
            self.assertEqual(
                json.loads((destination / "data" / "reports.json").read_text()),
                {"reports": entries},
            )
            self.assertEqual(
                json.loads(
                    (destination / "data" / "reports" / "20260904.json").read_text()
                ),
                {"date": "20260904", "predictions": {"new": []}},
            )

    def test_export_reports_ignores_invalid_names_and_missing_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            analysis_dir = root / "output" / "analysis"
            analysis_dir.mkdir(parents=True)
            (analysis_dir / "analysis_latest.json").write_text(
                "ignore", encoding="utf-8"
            )
            (analysis_dir / "analysis_20260903.json").write_text("{}", encoding="utf-8")

            entries = export_reports(root / "output", root / "site")

            self.assertEqual(
                entries, [{"date": "20260903", "report": "data/reports/20260903.json"}]
            )


if __name__ == "__main__":
    unittest.main()
