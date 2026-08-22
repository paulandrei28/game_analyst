import logging
from datetime import datetime
from playwright.async_api import async_playwright

LOGGER = logging.getLogger(__name__)

TOP_TOURNAMENTS = [
    "premier-league",
    "fa-cup",
    "laliga",
    "copa-del-rey",
    "serie-a",
    "coppa-italia",
    "bundesliga",
    "dfb-pokal",
    "ligue-1",
    "coupe-de-france",
    "eredivisie",
    "primeira-liga",
    "taca-de-portugal",
    "pro-league",
    "super-lig",
    "premiership",
    "uefa-champions-league",
    "uefa-europa-league",
    "uefa-conference-league",
    "uefa-super-cup",
]
TOP_TOURNAMENTS_SET = set(TOP_TOURNAMENTS)


class SofascoreUpcomingScraper:
    def __init__(self, headless=True):
        self.headless = headless
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        self.viewport = {"width": 1280, "height": 720}

    async def get_upcoming_games(self):
        LOGGER.info("Starting Sofascore upcoming-games scrape")
        async with async_playwright() as p:
            # 1. Launch browser context
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                user_agent=self.user_agent, viewport=self.viewport  # type: ignore
            )
            page = await context.new_page()

            # Get today's local date string (YYYY-MM-DD) to use as a strict validation filter
            today_date_str = datetime.now().strftime("%Y-%m-%d")

            # Dictionary to ensure we capture unique matches by their event ID
            upcoming_matches = {}

            # 2. Define the background network response interceptor
            async def handle_response(response):
                if "scheduled-events" in response.url:
                    try:
                        data = await response.json()
                        events = data.get("events", [])
                        for event in events:
                            status_type = event.get("status", {}).get("type")
                            tournament_slug = (
                                event.get("tournament", {})
                                .get("uniqueTournament", {})
                                .get("slug")
                            )

                            # Extract the exact match date from its Unix start timestamp
                            start_timestamp = event.get("startTimestamp")
                            match_date_str = ""
                            if start_timestamp:
                                match_date_str = datetime.fromtimestamp(
                                    start_timestamp
                                ).strftime("%Y-%m-%d")

                            # Isolate fixtures that haven't kicked off yet, match top tournaments, AND belong to today
                            if (
                                status_type == "notstarted"
                                and tournament_slug in TOP_TOURNAMENTS_SET
                                and match_date_str == today_date_str
                            ):
                                event_id = event.get("id")
                                if event_id:
                                    upcoming_matches[event_id] = event
                    except Exception:
                        LOGGER.debug(
                            "Could not process a scheduled-events response",
                            exc_info=True,
                        )

            # Attach network sniffer to the page instance
            page.on("response", handle_response)

            # 3. Trigger the initial UI stream loading flow
            LOGGER.info("Opening Sofascore")
            await page.goto("https://www.sofascore.com", wait_until="networkidle")

            # 4. Use the default layout telemetry data without checking the UI filter.
            LOGGER.info("Waiting for today's scheduled events (%s)", today_date_str)
            await page.wait_for_timeout(3000)

            await browser.close()
            LOGGER.info("Captured %d upcoming matches", len(upcoming_matches))

            # 5. Format and process the captured event models into "Home - Away" lists
            formatted_games_list = []
            for event in upcoming_matches.values():
                home_team = event.get("homeTeam", {}).get("name", "Home Team")
                away_team = event.get("awayTeam", {}).get("name", "Away Team")

                # Combine teams using your required separator format
                match_string = f"{home_team} - {away_team}"
                formatted_games_list.append(match_string)

            LOGGER.info(
                "Returning %d formatted upcoming games", len(formatted_games_list)
            )
            return formatted_games_list
