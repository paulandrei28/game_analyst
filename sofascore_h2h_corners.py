from sofascore_wrapper.api import SofascoreAPI
from sofascore_wrapper.search import Search
from sofascore_wrapper.match import Match
import time
import asyncio

from sofascore_upcoming_scraper import SofascoreUpcomingScraper


async def match():
    upcoming_scraper = SofascoreUpcomingScraper()
    api = SofascoreAPI()

    try:
        upcoming_games = await upcoming_scraper.get_upcoming_games()

        for game in upcoming_games:
            init_search = Search(api, search_string=game)
            search_match = await init_search.search_match(sport="football")
            results = search_match.get("results", [])

            if not results:
                print(f"No match found for the search query: {game}")
                continue

            match_entity = results[0]["entity"]
            match_obj = Match(api, match_id=match_entity["id"])
            h2h_matches = await match_obj.h2h_results(match_entity["customId"])
            today = int(time.time())
            ten_years = 315_360_000
            total_past_corners = []

            for past_event in h2h_matches["events"]:
                if today - ten_years < past_event["startTimestamp"] < today:
                    history = Match(api, match_id=past_event["id"])
                    try:
                        history_stats = await history.stats()
                    except:
                        print(f"No statistics found for: {game}")
                        continue
                    # total corners
                    total = (
                        history_stats["statistics"][0]["groups"][0]["statisticsItems"][3]["homeValue"]
                        + history_stats["statistics"][0]["groups"][0]["statisticsItems"][3]["awayValue"]
                    )
                    total_past_corners.append(str(total))

            print(game + (" " + " ".join(total_past_corners) if total_past_corners else ""))
    finally:
        await api.close()

if __name__ == "__main__":
        asyncio.run(match())
