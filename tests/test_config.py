import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from game_analyst.config import AppConfig, load_config


class ConfigTests(unittest.TestCase):
    def test_load_config_supports_commented_leagues_and_relative_output(self):
        contents = """
[pipeline]
date = "tomorrow"
output_dir = "artifacts"

[analysis]
top_n = 7
prediction_threshold = 120.5
enabled_markets = ["goals_ou", "btts", "wins"]

[fixtures]
api_timeout_seconds = 12

[fixtures.leagues]
premier-league = 39
laliga = 140

[sofascore]
request_interval_seconds = 1.5
request_jitter_seconds = 0.75
request_burst_size = 5
request_burst_pause_seconds = 12.0
request_backoff_base_seconds = 3.0
request_max_retries = 4
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(contents, encoding="utf-8")
            config = load_config(path)

        self.assertEqual(config.date, "tomorrow")
        self.assertEqual(config.top_n, 7)
        self.assertEqual(config.prediction_threshold, 120.5)
        self.assertEqual(config.enabled_markets, ("goals_ou", "btts", "wins"))
        self.assertEqual(config.enabled_leagues, ("premier-league", "laliga"))
        self.assertEqual(config.league_ids, {"premier-league": 39, "laliga": 140})
        self.assertEqual(config.allowed_league_ids, {39, 140})
        self.assertEqual(config.output_dir, Path(directory) / "artifacts")
        self.assertEqual(config.api_timeout_seconds, 12)
        self.assertEqual(config.sofascore_request_interval_seconds, 1.5)
        self.assertEqual(config.sofascore_request_jitter_seconds, 0.75)
        self.assertEqual(config.sofascore_request_burst_size, 5)
        self.assertEqual(config.sofascore_request_burst_pause_seconds, 12.0)
        self.assertEqual(config.sofascore_request_backoff_base_seconds, 3.0)
        self.assertEqual(config.sofascore_request_max_retries, 4)

    def test_load_config_rejects_unknown_league_and_invalid_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                "[fixtures]\n"
                'enabled_leagues = ["made-up"]\n'
                "[fixtures.leagues]\n"
                "premier-league = 39\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Unknown league"):
                load_config(path)

            path.write_text("[analysis]\nprediction_threshold = -1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "non-negative"):
                load_config(path)

            path.write_text(
                '[analysis]\nenabled_markets = ["goals_ou", "made-up"]\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Unknown market"):
                load_config(path)

    def test_missing_config_returns_defaults(self):
        config = load_config(Path("does-not-exist.toml"))
        self.assertEqual(config, AppConfig())


if __name__ == "__main__":
    unittest.main()
