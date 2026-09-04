import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from game_analyst import team_streaks


class TeamStreaksTests(unittest.TestCase):
    def test_fetch_team_streaks_handles_invalid_games_and_closes_api(self):
        api = Mock()
        api.close = AsyncMock()
        search = Mock()
        search.search_match = AsyncMock(
            return_value={"results": [{"entity": {"id": 42}}]}
        )
        match = Mock()
        match.team_streaks = AsyncMock(return_value={"wins": 4})

        with (
            patch.object(team_streaks, "SofascoreAPI", return_value=api),
            patch.object(
                team_streaks, "Search", return_value=search
            ) as search_constructor,
            patch.object(
                team_streaks, "Match", return_value=match
            ) as match_constructor,
            patch.object(team_streaks.asyncio, "sleep", new_callable=AsyncMock),
        ):
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
        search_constructor.assert_called_once_with(api, search_string="Home - Away")
        search.search_match.assert_awaited_once_with(sport="football")
        match_constructor.assert_called_once_with(api, match_id=42)
        match.team_streaks.assert_awaited_once_with()
        api.close.assert_awaited_once_with()

    def test_fetch_team_streaks_skips_search_results_without_entity(self):
        api = Mock()
        api.close = AsyncMock()
        search = Mock()
        search.search_match = AsyncMock(return_value={"results": []})

        with (
            patch.object(team_streaks, "SofascoreAPI", return_value=api),
            patch.object(team_streaks, "Search", return_value=search),
            patch.object(team_streaks.asyncio, "sleep", new_callable=AsyncMock),
        ):
            result = asyncio.run(team_streaks.fetch_team_streaks(["Home - Away"]))

        self.assertEqual(result, {})
        api.close.assert_awaited_once_with()

    def test_fetch_team_streaks_uses_gaussian_delay_and_burst_pause(self):
        api = Mock()
        api.close = AsyncMock()
        search = Mock()
        search.search_match = AsyncMock(
            return_value={"results": [{"entity": {"id": 42}}]}
        )
        match = Mock()
        match.team_streaks = AsyncMock(return_value={"wins": 4})

        with (
            patch.object(team_streaks, "SofascoreAPI", return_value=api),
            patch.object(team_streaks, "Search", return_value=search),
            patch.object(team_streaks, "Match", return_value=match),
            patch.object(team_streaks.random, "gauss", return_value=2.5) as gauss,
            patch.object(
                team_streaks.asyncio, "sleep", new_callable=AsyncMock
            ) as sleep,
        ):
            result = asyncio.run(
                team_streaks.fetch_team_streaks(
                    [{"home_team": "Home", "awayTeam": "Away"}],
                    request_interval=1.5,
                    request_jitter=0.5,
                    request_burst_size=1,
                    request_burst_pause=0.0,
                )
            )

        self.assertEqual(result, {"Home - Away": {"wins": 4}})
        self.assertGreaterEqual(gauss.call_count, 1)
        self.assertGreaterEqual(sleep.await_count, 1)
        api.close.assert_awaited_once_with()

    def test_fetch_team_streaks_rejects_non_list_input(self):
        api = Mock()
        api.close = AsyncMock()
        with patch.object(team_streaks, "SofascoreAPI", return_value=api):
            result = asyncio.run(team_streaks.fetch_team_streaks({"games": "invalid"}))

        self.assertEqual(result, {})
        api.close.assert_awaited_once_with()

    def test_call_with_backoff_retries_wrapper_string_rate_limit_error(self):
        action = AsyncMock(
            side_effect=[Exception("Failed to fetch /search/events: 403"), {"ok": True}]
        )

        with patch.object(team_streaks.asyncio, "sleep", new_callable=AsyncMock):
            result = asyncio.run(
                team_streaks._call_with_backoff(
                    action,
                    request_interval=0,
                    request_jitter=0,
                    request_count=1,
                    request_burst_size=5,
                    request_burst_pause=0,
                    request_backoff_base=1,
                    request_max_retries=1,
                )
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(action.await_count, 2)


if __name__ == "__main__":
    unittest.main()
