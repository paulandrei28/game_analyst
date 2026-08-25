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
        search.search_match = AsyncMock(return_value={"results": [{"entity": {"id": 42}}]})
        match = Mock()
        match.team_streaks = AsyncMock(return_value={"wins": 4})

        with patch.object(team_streaks, "SofascoreAPI", return_value=api), patch.object(
            team_streaks, "Search", return_value=search
        ) as search_constructor, patch.object(
            team_streaks, "Match", return_value=match
        ) as match_constructor, patch.object(
            team_streaks.asyncio, "sleep", new_callable=AsyncMock
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

        with patch.object(team_streaks, "SofascoreAPI", return_value=api), patch.object(
            team_streaks, "Search", return_value=search
        ), patch.object(team_streaks.asyncio, "sleep", new_callable=AsyncMock):
            result = asyncio.run(team_streaks.fetch_team_streaks(["Home - Away"]))

        self.assertEqual(result, {})
        api.close.assert_awaited_once_with()

    def test_fetch_team_streaks_rejects_non_list_input(self):
        api = Mock()
        api.close = AsyncMock()
        with patch.object(team_streaks, "SofascoreAPI", return_value=api):
            result = asyncio.run(team_streaks.fetch_team_streaks({"games": "invalid"}))

        self.assertEqual(result, {})
        api.close.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
