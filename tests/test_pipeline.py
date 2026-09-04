import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from game_analyst import pipeline  # type: ignore


class PipelineTests(unittest.TestCase):
    def test_cache_helpers_handle_missing_invalid_and_valid_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "streaks.json"
            self.assertIsNone(pipeline._load_cached_streaks(path))

            path.write_text("[]", encoding="utf-8")
            self.assertIsNone(pipeline._load_cached_streaks(path))

            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "No team streaks found"):
                pipeline._load_cached_streaks(path)
            self.assertFalse(path.exists())

            path.write_text('{"A - B": {}}', encoding="utf-8")
            self.assertEqual(pipeline._load_cached_streaks(path), {"A - B": {}})

            output = Path(directory) / "data.json"
            pipeline._save_json({"value": "ok"}, output)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")), {"value": "ok"}
            )

    def test_run_pipeline_uses_cache_and_writes_analysis_and_report(self):
        generator = Mock()
        predictions = [{"home": "A", "away": "B", "prediction": 1}]
        payload = {"date": "20260825", "predictions": predictions}
        generator.generate.return_value = predictions
        generator.build_payload.return_value = payload
        report_generator = Mock()

        with tempfile.TemporaryDirectory() as directory:
            streak_path = Path(directory) / "team_streaks/team_streaks_20260825.json"
            streak_path.parent.mkdir(parents=True)
            streak_path.write_text('{"A - B": {}}', encoding="utf-8")
            with (
                patch.object(
                    pipeline,
                    "resolve_date",
                    return_value=__import__("datetime").date(2026, 8, 25),
                ),
                patch.object(pipeline, "AnalysisGenerator", return_value=generator),
                patch.object(
                    pipeline, "HumanReadableReport", return_value=report_generator
                ),
                patch.object(
                    pipeline,
                    "load_or_fetch_fixtures_with_metadata",
                    return_value=(["A - B"], {"A - B": {"id": 39, "name": "Premier League"}}, Path(directory) / "fixtures/fixtures_20260825.json"),
                ),
                patch.object(
                    pipeline, "fetch_team_streaks", new_callable=AsyncMock
                ) as fetch,
            ):
                artifacts = asyncio.run(
                    pipeline.run_pipeline(output_dir=directory)
                )

            fetch.assert_not_awaited()
            generator.generate.assert_called_once_with(
                {"A - B": {}}, prediction_threshold=None
            )
            generator.save_json.assert_called_once_with(payload, artifacts["analysis"])
            self.assertEqual(generator.build_payload.call_args.kwargs["fixture_metadata"]["A - B"]["id"], 39)
            report_generator.save.assert_called_once()
            self.assertTrue(artifacts["analysis"].parent.is_dir())
            self.assertEqual(artifacts["report"].name, "report_20260825.md")

    def test_run_pipeline_stops_before_streaks_when_fixtures_are_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(
                    pipeline,
                    "resolve_date",
                    return_value=__import__("datetime").date(2026, 8, 25),
                ),
                patch.object(pipeline, "load_or_fetch_fixtures_with_metadata", return_value=([], {}, Path(directory) / "fixtures.json")),
                patch.object(
                    pipeline, "fetch_team_streaks", new_callable=AsyncMock
                ) as fetch,
            ):
                with self.assertRaisesRegex(RuntimeError, "No fixtures found"):
                    asyncio.run(pipeline.run_pipeline(output_dir=directory))

            fetch.assert_not_awaited()
            self.assertEqual(list(Path(directory).rglob("*.json")), [])
            self.assertEqual(list(Path(directory).rglob("*.md")), [])

    def test_run_pipeline_stops_before_analysis_when_streaks_are_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(
                    pipeline,
                    "resolve_date",
                    return_value=__import__("datetime").date(2026, 8, 25),
                ),
                patch.object(pipeline, "load_or_fetch_fixtures_with_metadata", return_value=(["A - B"], {"A - B": {"id": 39, "name": "Premier League"}}, Path(directory) / "fixtures/fixtures_20260825.json")),
                patch.object(
                    pipeline, "fetch_team_streaks", new_callable=AsyncMock
                ) as fetch,
            ):
                fetch.return_value = {}
                with self.assertRaisesRegex(RuntimeError, "No team streaks found"):
                    asyncio.run(pipeline.run_pipeline(output_dir=directory))

            fetch.assert_awaited_once()
            self.assertEqual(list(Path(directory).rglob("*.json")), [])
            self.assertEqual(list(Path(directory).rglob("*.md")), [])

    def test_run_pipeline_loads_default_config_when_not_supplied(self):
        config = Mock()
        config.date = "today"
        config.output_dir = Path("output")
        config.prediction_threshold = None
        config.allowed_league_ids = {39}
        config.league_ids = {}
        config.enabled_markets = ()
        config.api_timeout_seconds = 10.0
        config.sofascore_request_interval_seconds = 0.0
        config.sofascore_request_jitter_seconds = 0.5
        config.sofascore_request_burst_size = 4
        config.sofascore_request_burst_pause_seconds = 10.0
        config.sofascore_request_backoff_base_seconds = 3.0
        config.sofascore_request_max_retries = 2

        with (
            patch.object(pipeline, "load_config", return_value=config),
            patch.object(
                pipeline,
                "resolve_date",
                return_value=__import__("datetime").date(2026, 8, 25),
            ),
            patch.object(pipeline, "_load_cached_streaks", return_value={}),
            patch.object(pipeline, "load_or_fetch_fixtures_with_metadata", side_effect=RuntimeError("offline")),
            patch.object(pipeline, "AnalysisGenerator") as generator_class,
            patch.object(pipeline, "HumanReadableReport"),
        ):
            generator_class.return_value.generate.return_value = []
            generator_class.return_value.build_payload.return_value = {"date": "20260825", "predictions": []}
            asyncio.run(pipeline.run_pipeline())

        generator_class.assert_called_once_with(enabled_markets=config.enabled_markets)


if __name__ == "__main__":
    unittest.main()
