import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from game_analyst.generate_analysis import AnalysisGenerator


class AnalysisGeneratorTests(unittest.TestCase):
    def test_generate_delegates_and_groups_predictions(self):
        analyzer = Mock()
        analyzer.analyze.return_value = [{"home": "A", "away": "B", "prediction": 3}]
        analyzer.group_predictions_by_game.return_value = {"A - B": analyzer.analyze.return_value}
        generator = AnalysisGenerator(analyzer)

        predictions, grouped = generator.generate({"A - B": {}}, top_n=4)

        analyzer.analyze.assert_called_once_with(
            {"A - B": {}}, top_n=4, prediction_threshold=None
        )
        analyzer.group_predictions_by_game.assert_called_once_with(predictions)
        self.assertEqual(grouped, {"A - B": predictions})

    def test_generate_from_file_loads_json_and_output_date(self):
        analyzer = Mock()
        analyzer.analyze.return_value = []
        analyzer.group_predictions_by_game.return_value = {}
        generator = AnalysisGenerator(analyzer)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "team_streaks_20260825.json"
            path.write_text('{"A - B": {}}', encoding="utf-8")
            generator.generate_from_file(path, top_n=2)

        analyzer.analyze.assert_called_once_with(
            {"A - B": {}}, top_n=2, prediction_threshold=None
        )
        self.assertEqual(generator.output_date(path), "20260825")

    def test_save_json_creates_parent_and_round_trips_data(self):
        generator = AnalysisGenerator()
        with tempfile.TemporaryDirectory() as directory:
            output = generator.save_json({"A - B": [{"prediction": 1}]}, Path(directory) / "nested/out.json")
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"A - B": [{"prediction": 1}]})


if __name__ == "__main__":
    unittest.main()
