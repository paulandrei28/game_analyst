from __future__ import annotations

import logging
from datetime import date
from typing import Any

try:
    from .sofascore_client import SofascoreClient
except ImportError:  # pragma: no cover
    from sofascore_client import SofascoreClient

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
    The page is indexed once per client session, then each cached fixture is
    matched by home/away team names. No date-based SofaScore lookup is used.
    """
    del target_date, sport, inverse

    fixture_list = _as_games(games)
    results: dict[str, Any] = {}
    if not fixture_list:
        LOGGER.warning("No games supplied to fetch_team_streaks")
        return results

    client = SofascoreClient(
        request_interval=request_interval,
        request_jitter=request_jitter,
        request_burst_size=request_burst_size,
        request_burst_pause=request_burst_pause,
        request_backoff_base=request_backoff_base,
        request_max_retries=request_max_retries,
    )

    async with client:
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

                streaks = await client.fetch_team_streaks(
                    event_id,
                    home_team=home_team,
                    away_team=away_team,
                )
                results[display_key] = streaks
                LOGGER.info("Retrieved streaks for %s", display_key)
            except Exception:
                LOGGER.exception("Could not retrieve team streaks for %s", display_key)

    LOGGER.info(
        "Retrieved team streaks for %d of %d games",
        len(results),
        len(fixture_list),
    )
    return results


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
