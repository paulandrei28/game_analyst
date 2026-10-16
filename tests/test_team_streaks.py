import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from game_analyst import team_streaks
from game_analyst.sofascore_client import SofascoreClient


class TeamStreaksTests(unittest.TestCase):
    def make_client(self):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        return client

    def test_fetch_team_streaks_handles_invalid_games_and_closes_client(self):
        client = self.make_client()
        client.resolve_event_by_match_name.return_value = {"event_id": 42}
        client.fetch_team_streaks.return_value = {"wins": 4}

        with patch.object(team_streaks, "SofascoreClient", return_value=client):
            result = asyncio.run(
                team_streaks.fetch_team_streaks(
                    [
                        "bad fixture",
                        {"home_team": "Home", "awayTeam": "Away"},
                        {"home_team": "Missing"},
                        123,
                    ]
                )
            )

        self.assertEqual(result, {"Home - Away": {"wins": 4}})
        client.resolve_event_by_match_name.assert_awaited_once_with(
            "Home", "Away", target_date=None
        )
        client.fetch_team_streaks.assert_awaited_once_with(
            42, home_team="Home", away_team="Away"
        )
        client.__aexit__.assert_awaited_once()

    def test_fetch_team_streaks_skips_matches_without_a_resolved_event(self):
        client = self.make_client()
        client.resolve_event_by_match_name.return_value = None

        with patch.object(team_streaks, "SofascoreClient", return_value=client):
            result = asyncio.run(team_streaks.fetch_team_streaks(["Home - Away"]))

        self.assertEqual(result, {})
        client.fetch_team_streaks.assert_not_awaited()
        client.__aexit__.assert_awaited_once()

    def test_fetch_team_streaks_forwards_request_pacing_settings(self):
        client = self.make_client()
        client.resolve_event_by_match_name.return_value = None
        settings = {
            "request_interval": 1.5,
            "request_jitter": 0.5,
            "request_burst_size": 1,
            "request_burst_pause": 0.0,
            "request_backoff_base": 1.0,
            "request_max_retries": 2,
        }

        with patch.object(
            team_streaks, "SofascoreClient", return_value=client
        ) as client_factory:
            asyncio.run(
                team_streaks.fetch_team_streaks(["Home - Away"], **settings)
            )

        client_factory.assert_called_once_with(
            **settings,
            search_timeout_seconds=120.0,
            statistics_cache_dir=None,
        )

    def test_fetch_team_match_data_fetches_context_and_statistics(self):
        client = self.make_client()
        event_data = {
            "tournament": {"uniqueTournament": {"id": 7}},
            "season": {"id": 2026},
            "homeTeam": {"id": 1},
            "awayTeam": {"id": 2},
        }
        client.resolve_event_by_match_name.return_value = {
            "event_id": 42,
            "event_data": event_data,
        }
        client.fetch.return_value = {"markets": []}
        client.fetch_match_referee.return_value = {"referee": {"id": 88}}
        client.fetch_team_statistics.return_value = None
        client.fetch_team_streaks.return_value = {"wins": 4}

        with (
            patch.object(team_streaks, "SofascoreClient", return_value=client),
            patch.object(
                team_streaks,
                "enrich_qualifying_team_statistics",
                new_callable=AsyncMock,
            ) as enrich,
        ):
            streaks, contexts = asyncio.run(
                team_streaks.fetch_team_match_data(["Home - Away"])
            )

        self.assertEqual(streaks, {"Home - Away": {"wins": 4}})
        self.assertEqual(contexts["Home - Away"]["event_id"], 42)
        self.assertEqual(
            contexts["Home - Away"]["referee"], {"referee": {"id": 88}}
        )
        self.assertEqual(client.fetch_team_statistics.await_count, 2)
        enrich.assert_awaited_once_with(client, contexts, {"Home - Away"})
        client.__aexit__.assert_awaited_once()

    def test_non_list_game_input_returns_without_starting_client(self):
        with patch.object(team_streaks, "SofascoreClient") as client_factory:
            result = asyncio.run(
                team_streaks.fetch_team_streaks({"games": "invalid"})
            )

        self.assertEqual(result, {})
        client_factory.assert_not_called()

    def test_client_backoff_uses_exponential_delay(self):
        client = SofascoreClient.__new__(SofascoreClient)
        client.request_backoff_base = 1.0
        client.request_jitter = 0.0
        client.request_max_retries = 2

        with (
            patch("game_analyst.sofascore_client.random.uniform", return_value=0.0),
            patch(
                "game_analyst.sofascore_client.asyncio.sleep",
                new_callable=AsyncMock,
            ) as sleep,
        ):
            asyncio.run(client._backoff(403, "/api/v1/search", 1))

        sleep.assert_awaited_once_with(2.0)


if __name__ == "__main__":
    unittest.main()
