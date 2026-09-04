import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import fixtures_scraper


class FixturesScraperTests(unittest.TestCase):
    def test_resolve_date_accepts_supported_relative_dates(self):
        reference = date(2026, 8, 25)
        self.assertEqual(
            fixtures_scraper.resolve_date("yesterday", today=reference),
            date(2026, 8, 24),
        )
        self.assertEqual(
            fixtures_scraper.resolve_date("today", today=reference), reference
        )
        self.assertEqual(
            fixtures_scraper.resolve_date("tomorrow", today=reference),
            date(2026, 8, 26),
        )
        with self.assertRaises(ValueError):
            fixtures_scraper.resolve_date("next-week", today=reference)

    def test_get_filtered_matches_uses_key_and_top_league_filter(self):
        response = Mock()
        response.json.return_value = {
            "errors": [],
            "paging": {"current": 1, "total": 1},
            "results": 3,
            "response": [
                {"league": {"id": 39}},
                {"league": {"id": 999}},
                {"league": {}},
            ],
        }
        with patch.object(
            fixtures_scraper.requests, "get", return_value=response
        ) as request:
            matches = fixtures_scraper.get_filtered_matches(
                "2026-08-25", api_key="secret"
            )

        response.raise_for_status.assert_called_once_with()
        request.assert_called_once_with(
            fixtures_scraper.BASE_URL,
            headers={"x-apisports-key": "secret"},
            params={"date": "2026-08-25"},
            timeout=10,
        )
        self.assertEqual(matches, [{"league": {"id": 39}}])

    def test_format_and_cache_path(self):
        matches = [
            {"teams": {"home": {"name": "Home"}, "away": {"name": "Away"}}},
            {"teams": {"home": {"name": "Incomplete"}, "away": {}}},
        ]
        self.assertEqual(fixtures_scraper.format_fixtures(matches), ["Home - Away"])
        self.assertEqual(
            fixtures_scraper.cache_path("out", date(2026, 8, 25)),
            Path("out/fixtures/fixtures_20260825.txt"),
        )

    def test_load_or_fetch_fixtures_reads_cache_without_fetching(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixtures/fixtures_20260825.txt"
            path.parent.mkdir()
            path.write_text("A - B\n\nC - D\n", encoding="utf-8")
            with patch.object(
                fixtures_scraper, "resolve_date", return_value=date(2026, 8, 25)
            ):
                with patch.object(fixtures_scraper, "get_filtered_matches") as fetch:
                    fixtures, actual_path = fixtures_scraper.load_or_fetch_fixtures(
                        output_dir=directory
                    )

            self.assertEqual(fixtures, ["A - B", "C - D"])
            self.assertEqual(actual_path, path)
            fetch.assert_not_called()

    def test_load_or_fetch_fixtures_fetches_and_writes_missing_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                fixtures_scraper, "resolve_date", return_value=date(2026, 8, 25)
            ):
                with patch.object(
                    fixtures_scraper,
                    "get_filtered_matches",
                    return_value=[
                        {"teams": {"home": {"name": "A"}, "away": {"name": "B"}}}
                    ],
                ):
                    fixtures, path = fixtures_scraper.load_or_fetch_fixtures(
                        output_dir=directory,
                        api_key="key",
                    )

            self.assertEqual(fixtures, ["A - B"])
            self.assertEqual(path.read_text(encoding="utf-8"), "A - B\n")

    def test_load_or_fetch_fixtures_does_not_write_empty_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                fixtures_scraper,
                "resolve_date",
                return_value=date(2026, 8, 25),
            ), patch.object(
                fixtures_scraper, "get_filtered_matches", return_value=[]
            ):
                with self.assertRaisesRegex(RuntimeError, "No fixtures found"):
                    fixtures_scraper.load_or_fetch_fixtures(
                        output_dir=directory,
                        api_key="key",
                    )

            self.assertFalse(
                (Path(directory) / "fixtures/fixtures_20260825.txt").exists()
            )

    def test_load_or_fetch_fixtures_removes_empty_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixtures/fixtures_20260825.txt"
            path.parent.mkdir()
            path.write_text("\n", encoding="utf-8")
            with patch.object(
                fixtures_scraper, "resolve_date", return_value=date(2026, 8, 25)
            ):
                with self.assertRaisesRegex(RuntimeError, "No fixtures found"):
                    fixtures_scraper.load_or_fetch_fixtures(output_dir=directory)

            self.assertFalse(path.exists())

    def test_metadata_cache_preserves_league_id_and_name_and_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            matches = [{"teams": {"home": {"name": "A"}, "away": {"name": "B"}}, "league": {"id": 39, "name": "Premier League"}}]
            with patch.object(fixtures_scraper, "resolve_date", return_value=date(2026, 8, 25)), patch.object(fixtures_scraper, "get_filtered_matches", return_value=matches) as fetch:
                fixtures, metadata, path = fixtures_scraper.load_or_fetch_fixtures_with_metadata(output_dir=directory, api_key="key")
                cached_fixtures, cached_metadata, cached_path = fixtures_scraper.load_or_fetch_fixtures_with_metadata(output_dir=directory, api_key="key")
            self.assertEqual(fixtures, ["A - B"])
            self.assertEqual(metadata["A - B"], {"id": 39, "name": "Premier League"})
            self.assertEqual((cached_fixtures, cached_metadata, cached_path), (fixtures, metadata, path))
            fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
