import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from game_analyst.sofascore_client import SofascoreClient
from game_analyst.sofascore_statistics import (
    enrich_qualifying_team_statistics,
    parse_team_statistics,
)


def _context(home_id, away_id):
    return {
        "event": {
            "event": {
                "tournament": {"uniqueTournament": {"id": 17}},
                "season": {"id": 52186},
                "homeTeam": {"id": home_id},
                "awayTeam": {"id": away_id},
            }
        }
    }


class SofaScoreStatisticsTests(unittest.TestCase):
    def test_overall_endpoint_uses_team_tournament_season_and_type(self):
        path = SofascoreClient.build_resource_path(
            "team_statistics", team_id=42, tournament_id=17,
            season_id=52186, statistics_type="overall",
        )
        self.assertEqual(
            path,
            "/api/v1/team/42/unique-tournament/17/season/52186/statistics/overall",
        )

    def test_parser_normalizes_totals_and_missing_keys_safely(self):
        parsed = parse_team_statistics({"statistics": {
            "matches": 10,
            "fouls": 120,
            "yellowCards": 40,
            "redCards": 2,
            "yellowRedCards": 1,
            "penaltiesTaken": 3,
            "penaltiesCommited": 1,
        }})
        self.assertEqual(parsed["fouls_per_match"], 12.0)
        self.assertEqual(parsed["yellow_cards_per_match"], 4.0)
        self.assertEqual(parsed["red_cards_per_match"], 0.2)
        self.assertEqual(parsed["second_yellow_cards_per_match"], 0.1)
        self.assertEqual(parsed["penalties_per_match"], 0.4)
        missing = parse_team_statistics({"statistics": {"matches": 0, "fouls": 20}})
        self.assertIsNone(missing["fouls_per_match"])
        self.assertIsNone(missing["yellow_cards_per_match"])
        self.assertIsNone(parse_team_statistics({})["matches"])

    def test_fetches_each_unique_team_season_once_and_builds_competition_baseline(self):
        raw_stats = {
            "statistics": {
                "matches": 10, "fouls": 100, "yellowCards": 40,
                "redCards": 2, "yellowRedCards": 1,
                "penaltiesTaken": 2, "penaltiesCommited": 1,
            }
        }
        games = {
            "A - B": _context(1, 2),
            "A - C": _context(1, 3),
            "D - E": _context(4, 5),
        }
        client = SofascoreClient()
        client.fetch_team_statistics = AsyncMock(return_value=raw_stats)

        asyncio.run(enrich_qualifying_team_statistics(client, games, set(games)))

        self.assertEqual(client.fetch_team_statistics.await_count, 5)
        client.fetch_team_statistics.assert_any_await(1, 17, 52186, statistics_type="overall")
        self.assertIs(games["A - B"]["team_statistics"]["home"], games["A - C"]["team_statistics"]["home"])
        self.assertEqual(games["A - B"]["competition_statistics_baseline"]["fouls_per_match"], 10.0)
        self.assertEqual(games["A - B"]["competition_statistics_baseline_team_counts"]["yellow_cards_per_match"], 5)

    def test_client_reuses_disk_cache_and_instance_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            client = SofascoreClient(statistics_cache_dir=directory)
            client.fetch = AsyncMock(return_value={"statistics": {"matches": 2}})
            first = asyncio.run(client.fetch_team_statistics(42, 17, 52186))
            second = asyncio.run(client.fetch_team_statistics(42, 17, 52186))
            self.assertEqual(first, second)
            client.fetch.assert_awaited_once_with(
                "team_statistics", team_id=42, tournament_id=17,
                season_id=52186, statistics_type="overall",
            )
            client.fetch = AsyncMock()
            other_client = SofascoreClient(statistics_cache_dir=directory)
            other_client.fetch = AsyncMock()
            self.assertEqual(
                asyncio.run(other_client.fetch_team_statistics(42, 17, 52186)), first
            )
            other_client.fetch.assert_not_awaited()

    def test_enrichment_only_uses_aggregate_endpoint(self):
        context = {"A - B": _context(1, 2)}
        client = SofascoreClient()
        client.fetch_team_statistics = AsyncMock(return_value={"statistics": {"matches": 10}})
        client.fetch = AsyncMock(side_effect=AssertionError("non-aggregate request"))
        asyncio.run(enrich_qualifying_team_statistics(client, context, {"A - B"}))
        self.assertEqual(client.fetch_team_statistics.await_count, 2)
        client.fetch.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
