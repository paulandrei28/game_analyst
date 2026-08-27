import asyncio
import logging

from sofascore_wrapper.api import SofascoreAPI
from sofascore_wrapper.search import Search
from sofascore_wrapper.match import Match

LOGGER = logging.getLogger(__name__)


async def fetch_team_streaks(games, *, request_interval: float = 5.0):
    """Return ``{game: team_streaks}`` for scraped upcoming games."""
    api = SofascoreAPI()
    results = {}
    last_request = 0.0
    processed_games = 0

    try:
        if isinstance(games, dict):
            games = games.get("games", [])
        if not isinstance(games, list):
            LOGGER.warning("Upcoming games input is not a list")
            return results

        LOGGER.info("Processing %d upcoming games", len(games))

        for game in games:
            if isinstance(game, str):
                teams = game.split(" - ", 1)
                if len(teams) != 2:
                    continue
                home_team, away_team = teams
            elif isinstance(game, dict):
                home_team = game.get("home_team") or game.get("homeTeam")
                away_team = game.get("away_team") or game.get("awayTeam")
            else:
                LOGGER.warning("Skipping game with unsupported input type")
                continue

            if not isinstance(home_team, str) or not isinstance(away_team, str):
                LOGGER.warning("Skipping game with invalid team values")
                continue

            if last_request:
                await asyncio.sleep(
                    max(
                        0,
                        request_interval
                        - (asyncio.get_running_loop().time() - last_request),
                    )
                )
            search = Search(api, search_string=f"{home_team} - {away_team}")
            search_match = await search.search_match(sport="football")
            last_request = asyncio.get_running_loop().time()
            processed_games += 1

            if not isinstance(search_match, dict):
                LOGGER.warning("Match search returned no usable response")
                continue
            search_results = search_match.get("results")
            if not isinstance(search_results, list) or not search_results:
                LOGGER.warning("Match search returned no results")
                continue
            selected_match = search_results[0]
            if not isinstance(selected_match, dict):
                LOGGER.warning("Match search returned an invalid result")
                continue
            entity = selected_match.get("entity")
            if not isinstance(entity, dict) or "id" not in entity:
                LOGGER.warning("Match search result has no usable entity")
                continue

            if last_request:
                await asyncio.sleep(
                    max(
                        0,
                        request_interval
                        - (asyncio.get_running_loop().time() - last_request),
                    )
                )
            match_init = Match(api, match_id=entity["id"])
            streaks = await match_init.team_streaks()
            last_request = asyncio.get_running_loop().time()
            results[f"{home_team} - {away_team}"] = streaks
            LOGGER.info("Retrieved streaks for game %s - %s", home_team, away_team)

        LOGGER.info("Retrieved streaks for %d of %d games", len(results), len(games))
        return results

    finally:
        await api.close()
