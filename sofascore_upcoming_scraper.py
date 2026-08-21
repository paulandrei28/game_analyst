import asyncio
from datetime import datetime
from playwright.async_api import async_playwright

class SofascoreUpcomingScraper:
    def __init__(self, headless=True):
        self.headless = headless
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        self.viewport = {"width": 1280, "height": 720}

    async def get_upcoming_games(self):
        async with async_playwright() as p:
            # 1. Launch browser context
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                user_agent=self.user_agent,
                viewport=self.viewport
            )
            page = await context.new_page()

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
                            
                            # Isolate fixtures that haven't kicked off yet
                            if status_type == "notstarted":
                                event_id = event.get("id")
                                if event_id:
                                    upcoming_matches[event_id] = event
                    except Exception:
                        pass

            # Attach network sniffer to the page instance
            page.on("response", handle_response)

            # 3. Trigger the initial UI stream loading flow
            print("Opening Sofascore...")
            await page.goto("https://www.sofascore.com", wait_until="networkidle")
            
            # 4. Interact with the layout to pull upcoming matches specifically
            try:
                print("Clicking the 'Upcoming' filter tab inside the UI...")
                upcoming_button = page.get_by_role("button", name="Upcoming", exact=True).first
                await upcoming_button.click()
                
                print("Filter applied successfully. Streaming network packets...")
                await page.wait_for_timeout(5000)  # Wait for the background AJAX pipe to conclude
            except Exception:
                print("Could not locate the 'Upcoming' tab. Falling back to default layout telemetry data...")
                await page.wait_for_timeout(3000)

            await browser.close()
            
            # 5. Format and process the captured event models into "Home - Away" lists
            formatted_games_list = []
            for event in upcoming_matches.values():
                home_team = event.get("homeTeam", {}).get("name", "Home Team")
                away_team = event.get("awayTeam", {}).get("name", "Away Team")
                
                # Combine teams using your required separator format
                match_string = f"{home_team} - {away_team}"
                formatted_games_list.append(match_string)

            return formatted_games_list