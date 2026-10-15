from __future__ import annotations

import asyncio
import logging
from datetime import date
from pathlib import Path
from typing import Any, Callable

try:
    from .sofascore_client import SofascoreClient
    from .sofascore_statistics import (
        enrich_qualifying_team_statistics,
        team_statistics_request_keys,
    )
except ImportError:  # pragma: no cover
    from sofascore_client import SofascoreClient
    from sofascore_statistics import (
        enrich_qualifying_team_statistics,
        team_statistics_request_keys,
    )

LOGGER = logging.getLogger(__name__)


def _as_games(games: Any) -> list[Any]:
    if isinstance(games, dict):
        games = games.get("games", [])
    return games if isinstance(games, list) else []


def _game_teams(game: Any) -> tuple[str, str] | None:
    if isinstance(game, str):
        parts = game.split(" - ", 1)
        if len(parts) != 2:
            return None
        home_team, away_team = parts
    elif isinstance(game, dict):
        home_team = game.get("home_team") or game.get("homeTeam")
        away_team = game.get("away_team") or game.get("awayTeam")
    else:
        return None

    if not isinstance(home_team, str) or not isinstance(away_team, str):
        return None
    return home_team.strip(), away_team.strip()


async def fetch_team_streaks(
    games: Any,
    *,
    # Kept for compatibility with older callers; event resolution no longer
    # uses the date-based SofaScore schedule endpoint.
    target_date: str | date | None = None,
    sport: str = "football",
    inverse: bool = False,
    request_interval: float = 2.0,
    request_jitter: float = 1.5,
    request_burst_size: int = 5,
    request_burst_pause: float = 12.0,
    request_backoff_base: float = 3.0,
    request_max_retries: int = 4,
) -> dict[str, Any]:
    """Return ``{game: team_streaks}`` using the cached fixture names.

    SofaScore event IDs are resolved from the football page's match links.
    The page is indexed once per client session, then each fixture is matched
    by home/away names and, when supplied, its actual event date.
    """
    streaks, _ = await fetch_team_match_data(
        games,
        target_date=target_date,
        sport=sport,
        inverse=inverse,
        include_streaks=True,
        include_context=False,
        request_interval=request_interval,
        request_jitter=request_jitter,
        request_burst_size=request_burst_size,
        request_burst_pause=request_burst_pause,
        request_backoff_base=request_backoff_base,
        request_max_retries=request_max_retries,
    )
    return streaks


async def fetch_team_match_data(
    games: Any,
    *,
    target_date: str | date | None = None,
    sport: str = "football",
    inverse: bool = False,
    include_streaks: bool = True,
    include_context: bool = True,
    worth_watching_qualifier: Callable[[str, Any], bool] | None = None,
    statistics_cache_dir: str | Path | None = None,
    request_interval: float = 2.0,
    request_jitter: float = 1.5,
    request_burst_size: int = 5,
    request_burst_pause: float = 12.0,
    request_backoff_base: float = 3.0,
    request_max_retries: int = 4,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch streaks and optional event context in one SofaScore session."""
    del sport, inverse

    fixture_list = _as_games(games)
    results: dict[str, Any] = {}
    contexts: dict[str, Any] = {}
    if not fixture_list:
        LOGGER.warning("No games supplied to fetch_team_streaks")
        return results, contexts

    client = SofascoreClient(
        request_interval=request_interval,
        request_jitter=request_jitter,
        request_burst_size=request_burst_size,
        request_burst_pause=request_burst_pause,
        request_backoff_base=request_backoff_base,
        request_max_retries=request_max_retries,
        statistics_cache_dir=statistics_cache_dir,
    )

    async with client:
        statistics_tasks: dict[tuple[str, str, str, str], Any] = {}
        for game in fixture_list:
            teams = _game_teams(game)
            if teams is None:
                LOGGER.warning("Skipping unsupported game input: %r", game)
                continue

            home_team, away_team = teams
            display_key = f"{home_team} - {away_team}"

            try:
                event = await client.resolve_event_by_match_name(
                    home_team,
                    away_team,
                    target_date=target_date,
                )
                if event is None:
                    LOGGER.warning(
                        "Could not resolve SofaScore match by name: %s",
                        display_key,
                    )
                    continue

                event_id = event["event_id"]
                LOGGER.info(
                    "Resolved %s -> SofaScore event %s",
                    display_key,
                    event_id,
                )

                if include_context:
                    context: dict[str, Any] = {
                        "event_id": event_id,
                        "event": event.get("event_data"),
                        "referee": None,
                        "odds": None,
                    }
                    # Start team-stat requests as soon as this match's IDs are
                    # known; they can run while the next fixtures are searched.
                    _, team_statistic_keys = team_statistics_request_keys(context)
                    for request_key in set(team_statistic_keys.values()):
                        cache_key = (
                            str(request_key[0]),
                            str(request_key[1]),
                            str(request_key[2]),
                            request_key[3],
                        )
                        if cache_key not in statistics_tasks:
                            statistics_tasks[cache_key] = asyncio.create_task(
                                client.fetch_team_statistics(
                                    request_key[0],
                                    request_key[1],
                                    request_key[2],
                                    statistics_type=request_key[3],
                                )
                            )
                    try:
                        context["odds"] = await client.fetch("event_odds_1x2", event_id=event_id)
                        if context["odds"] is None:
                            LOGGER.debug("1X2 odds unavailable for %s", display_key)
                    except Exception:
                        LOGGER.debug("Could not retrieve 1X2 odds for %s", display_key, exc_info=True)

                    # Resolve eligibility from odds and the local derby list
                    # before requesting referee or historical discipline data.
                    eligible = worth_watching_qualifier(display_key, context) if worth_watching_qualifier else True
                    if eligible:
                        try:
                            context["referee"] = await client.fetch_match_referee(
                                event_id,
                                event_payload=event.get("event_data"),
                            )
                        except Exception:
                            LOGGER.debug("Could not retrieve referee data for %s", display_key, exc_info=True)
                    contexts[display_key] = context

                # Ordinary form/streak data still serves the existing analysis
                # pipeline and is independent of worth_watching eligibility.
                if include_streaks:
                    streaks = await client.fetch_team_streaks(
                        event_id,
                        home_team=home_team,
                        away_team=away_team,
                    )
                    results[display_key] = streaks
                    LOGGER.info("Retrieved streaks for %s", display_key)
            except Exception:
                LOGGER.exception("Could not retrieve match data for %s", display_key)

        # Team statistics are needed to discover high-discipline-risk games,
        # so enrich every fixture context before applying the final OR filter.
        if include_context and contexts:
            try:
                if statistics_tasks:
                    await asyncio.gather(*statistics_tasks.values(), return_exceptions=True)
                await enrich_qualifying_team_statistics(client, contexts, set(contexts))
            except Exception:
                LOGGER.warning("Could not enrich team-season disciplinary statistics", exc_info=True)

    LOGGER.info(
        "Retrieved team streaks for %d of %d games",
        len(results),
        len(fixture_list),
    )
    return results, contexts


async def enrich_cached_team_statistics(
    contexts: dict[str, Any],
    qualifying_games: set[str],
    *,
    statistics_cache_dir: str | Path | None = None,
    request_interval: float = 2.0,
    request_jitter: float = 1.5,
    request_burst_size: int = 5,
    request_burst_pause: float = 12.0,
    request_backoff_base: float = 3.0,
    request_max_retries: int = 4,
) -> None:
    """Add aggregate stats to an older cached context using the normal client."""
    client = SofascoreClient(
        request_interval=request_interval,
        request_jitter=request_jitter,
        request_burst_size=request_burst_size,
        request_burst_pause=request_burst_pause,
        request_backoff_base=request_backoff_base,
        request_max_retries=request_max_retries,
        statistics_cache_dir=statistics_cache_dir,
    )
    async with client:
        await enrich_qualifying_team_statistics(client, contexts, qualifying_games)


if __name__ == "__main__":
    import argparse
    import asyncio
    import json

    async def _main() -> None:
        parser = argparse.ArgumentParser(
            description="Test SofaScore team streaks by match name."
        )
        parser.add_argument("home_team")
        parser.add_argument("away_team")
        args = parser.parse_args()

        result = await fetch_team_streaks([f"{args.home_team} - {args.away_team}"])
        print(json.dumps(result, indent=2, ensure_ascii=False))

    asyncio.run(_main())
