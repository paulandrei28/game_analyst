import asyncio
import logging
import random

from sofascore_wrapper.api import SofascoreAPI
from sofascore_wrapper.search import Search
from sofascore_wrapper.match import Match

LOGGER = logging.getLogger(__name__)

_BROWSER_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Edge/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
)


def _apply_browser_headers(api):
    headers = {
        "User-Agent": random.choice(_BROWSER_USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": random.choice(["en-US,en;q=0.9", "en-GB,en;q=0.9"]),
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Referer": "https://www.sofascore.com/",
        "Origin": "https://www.sofascore.com",
    }
    for container_name in ("session", "client"):
        container = getattr(api, container_name, None)
        if container is not None and hasattr(container, "headers"):
            container.headers.update(headers)
            return
    if hasattr(api, "headers"):
        api.headers.update(headers)


def _compute_human_delay(
    *,
    request_interval: float,
    request_jitter: float,
    request_count: int,
    request_burst_size: int,
    request_burst_pause: float,
) -> float:
    if request_interval <= 0 and request_jitter <= 0:
        return 0.0

    base_delay = max(0.0, request_interval)
    delay = base_delay
    if request_jitter > 0:
        mean_delay = max(base_delay, base_delay + (request_jitter / 2.0))
        std_dev = max(request_jitter / 2.0, 0.5)
        delay = max(base_delay * 0.7, random.gauss(mean_delay, std_dev))
        delay = min(delay, base_delay + request_jitter * 2.5)

    if request_burst_size > 0 and request_count > 0 and request_count % request_burst_size == 0:
        burst_pause = max(0.0, request_burst_pause)
        if burst_pause > 0:
            # Using a randomized burst pause makes the traffic look human rather than periodic.
            burst_pause = random.uniform(burst_pause * 0.7, burst_pause * 1.5)
            delay += burst_pause

    return max(0.0, delay)


async def _sleep_with_human_pacing(
    *,
    last_request: float | None,
    request_interval: float,
    request_jitter: float,
    request_count: int,
    request_burst_size: int,
    request_burst_pause: float,
    request_backoff_base: float,
    request_max_retries: int,
) -> None:
    if last_request is None:
        return

    loop = asyncio.get_running_loop()
    elapsed = loop.time() - last_request
    intended_delay = _compute_human_delay(
        request_interval=request_interval,
        request_jitter=request_jitter,
        request_count=request_count,
        request_burst_size=request_burst_size,
        request_burst_pause=request_burst_pause,
    )
    sleep_for = max(0.0, intended_delay - elapsed)
    if sleep_for > 0:
        LOGGER.debug(
            "Sleeping %.2fs before next Sofascore request (elapsed=%.2fs, target=%.2fs)",
            sleep_for,
            elapsed,
            intended_delay,
        )
        await asyncio.sleep(sleep_for)


async def _call_with_backoff(
    action,
    *,
    request_interval: float,
    request_jitter: float,
    request_count: int,
    request_burst_size: int,
    request_burst_pause: float,
    request_backoff_base: float,
    request_max_retries: int,
):
    for attempt in range(request_max_retries + 1):
        try:
            return await action()
        except Exception as exc:  # pragma: no cover - library may surface rate-limit errors differently
            status_code = getattr(exc, "status", None) or getattr(exc, "status_code", None)
            if status_code not in {429, 403}:
                raise
            if attempt >= request_max_retries:
                raise
            backoff = request_backoff_base * (2**attempt)
            backoff += max(0.0, random.gauss(0.0, max(request_jitter, 0.5)))
            LOGGER.warning(
                "Sofascore rate-limited (status=%s); retrying in %.2fs (attempt %d/%d)",
                status_code,
                backoff,
                attempt + 1,
                request_max_retries,
            )
            await asyncio.sleep(backoff)


async def fetch_team_streaks(
    games,
    *,
    request_interval: float = 2.0,
    request_jitter: float = 1.5,
    request_burst_size: int = 5,
    request_burst_pause: float = 12.0,
    request_backoff_base: float = 3.0,
    request_max_retries: int = 4,
):
    """Return ``{game: team_streaks}`` for scraped upcoming games."""
    api = SofascoreAPI()
    _apply_browser_headers(api)
    results = {}
    last_request = None
    request_count = 0

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

            request_count += 1
            await _sleep_with_human_pacing(
                last_request=last_request,
                request_interval=request_interval,
                request_jitter=request_jitter,
                request_count=request_count,
                request_burst_size=request_burst_size,
                request_burst_pause=request_burst_pause,
                request_backoff_base=request_backoff_base,
                request_max_retries=request_max_retries,
            )

            search = Search(api, search_string=f"{home_team} - {away_team}")
            search_match = await _call_with_backoff(
                lambda: search.search_match(sport="football"),
                request_interval=request_interval,
                request_jitter=request_jitter,
                request_count=request_count,
                request_burst_size=request_burst_size,
                request_burst_pause=request_burst_pause,
                request_backoff_base=request_backoff_base,
                request_max_retries=request_max_retries,
            )
            last_request = asyncio.get_running_loop().time()

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

            request_count += 1
            await _sleep_with_human_pacing(
                last_request=last_request,
                request_interval=request_interval,
                request_jitter=request_jitter,
                request_count=request_count,
                request_burst_size=request_burst_size,
                request_burst_pause=request_burst_pause,
                request_backoff_base=request_backoff_base,
                request_max_retries=request_max_retries,
            )

            match_init = Match(api, match_id=entity["id"])
            streaks = await _call_with_backoff(
                match_init.team_streaks,
                request_interval=request_interval,
                request_jitter=request_jitter,
                request_count=request_count,
                request_burst_size=request_burst_size,
                request_burst_pause=request_burst_pause,
                request_backoff_base=request_backoff_base,
                request_max_retries=request_max_retries,
            )
            last_request = asyncio.get_running_loop().time()
            results[f"{home_team} - {away_team}"] = streaks
            LOGGER.info("Retrieved streaks for game %s - %s", home_team, away_team)

        LOGGER.info("Retrieved streaks for %d of %d games", len(results), len(games))
        return results

    finally:
        await api.close()
