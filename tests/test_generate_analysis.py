import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from game_analyst.generate_analysis import AnalysisGenerator


class AnalysisGeneratorTests(unittest.TestCase):
    def test_generate_returns_complete_flat_predictions(self):
        analyzer = Mock()
        analyzer.analyze.return_value = [{"home": "A", "away": "B", "prediction": 3}]
        generator = AnalysisGenerator(analyzer)

        predictions = generator.generate({"A - B": {}})

        analyzer.analyze.assert_called_once_with(
            {"A - B": {}}, prediction_threshold=None
        )
        self.assertEqual(predictions, analyzer.analyze.return_value)

    def test_generate_from_file_loads_json_and_output_date(self):
        analyzer = Mock()
        analyzer.analyze.return_value = []
        generator = AnalysisGenerator(analyzer)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "team_streaks_20260825.json"
            path.write_text('{"A - B": {}}', encoding="utf-8")
            generator.generate_from_file(path)

        analyzer.analyze.assert_called_once_with(
            {"A - B": {}}, prediction_threshold=None
        )
        self.assertEqual(generator.output_date(path), "20260825")

    def test_build_payload_marks_missing_league_unknown_and_save_round_trips(self):
        generator = AnalysisGenerator()
        payload = generator.build_payload(
            date="20260825",
            predictions=[
                {"home": "A", "away": "B", "score": 88, "prediction": 70},
                {"home": "C", "away": "D", "score": 80, "prediction": 65},
            ],
            fixture_metadata={"A - B": {"id": 39, "name": "Premier League"}},
        )
        self.assertEqual(payload["date"], "20260825")
        self.assertEqual(payload["predictions"][0]["league"], {"id": 39, "name": "Premier League"})
        self.assertEqual(payload["predictions"][1]["league"], {"id": None, "name": "Unknown"})
        with tempfile.TemporaryDirectory() as directory:
            output = generator.save_json(
                payload, Path(directory) / "nested/out.json"
            )
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                payload,
            )


if __name__ == "__main__":
    unittest.main()
