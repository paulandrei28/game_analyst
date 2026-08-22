import asyncio
import json

from sofascore_wrapper.api import SofascoreAPI
from sofascore_wrapper.search import Search
from sofascore_wrapper.match import Match
from sofascore_upcoming_scraper import SofascoreUpcomingScraper


async def match(games):
    """Return ``{game: team_streaks}`` for games from upcoming_scraper."""
    api = SofascoreAPI()
    results = {}
    request_interval = 5
    last_request = 0.0

    try:
        if isinstance(games, dict):
            games = games.get("games", [])
        if not isinstance(games, list):
            return results

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
                continue

            if not isinstance(home_team, str) or not isinstance(away_team, str):
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

            if not isinstance(search_match, dict):
                continue
            search_results = search_match.get("results")
            if not isinstance(search_results, list) or not search_results:
                continue
            selected_match = search_results[0]
            if not isinstance(selected_match, dict):
                continue
            entity = selected_match.get("entity")
            if not isinstance(entity, dict) or "id" not in entity:
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

        return results

    finally:
        await api.close()


async def main():
    scraper = SofascoreUpcomingScraper()
    games = await scraper.get_upcoming_games()
    streaks = await match(games)
    with open("streaks.json", "w", encoding="utf-8") as file:
        json.dump(streaks, file, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    asyncio.run(main())
