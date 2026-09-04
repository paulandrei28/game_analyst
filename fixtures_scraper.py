from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path

import requests

LOGGER = logging.getLogger(__name__)

DATE_OPTIONS = ("yesterday", "today", "tomorrow")

BASE_URL = "https://v3.football.api-sports.io/fixtures"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def resolve_date(date_option: str, *, today: date | None = None) -> date:
    """Resolve one of the API's supported relative date options."""
    if date_option not in DATE_OPTIONS:
        allowed = ", ".join(DATE_OPTIONS)
        raise ValueError(f"date must be one of: {allowed}")

    reference_date = today or date.today()
    offset = {"yesterday": -1, "today": 0, "tomorrow": 1}[date_option]
    return reference_date + timedelta(days=offset)


def get_filtered_matches(
    target_date: str,
    *,
    api_key: str | None = None,
    allowed_league_ids: set[int] | None = None,
    api_timeout_seconds: float = 10.0,
) -> list[dict]:
    """Fetch fixtures for an ISO date and keep only configured top leagues."""
    key = api_key or os.getenv("API_FOOTBALL_API_KEY")
    if not key:
        raise RuntimeError("API_FOOTBALL_API_KEY is not set")

    LOGGER.info("Requesting fixtures for date: %s", target_date)
    try:
        response = requests.get(
            BASE_URL,
            headers={"x-apisports-key": key},
            params={"date": target_date},
            timeout=api_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        LOGGER.exception("Could not fetch fixtures for %s", target_date)
        raise

    api_errors = payload.get("errors", [])
    if api_errors:
        LOGGER.warning("API returned internal error flags: %s", api_errors)

    paging = payload.get("paging", {})
    LOGGER.info(
        "Total raw fixtures returned by API: %s | Pages: %s/%s",
        payload.get("results", 0),
        paging.get("current", 1),
        paging.get("total", 1),
    )
    matches = payload.get("response", [])
    if allowed_league_ids is None:
        try:
            from .config import load_config
        except ImportError:
            from config import load_config
        allowed_league_ids = load_config().allowed_league_ids

    filtered_matches = [
        match
        for match in matches
        if match.get("league", {}).get("id") in allowed_league_ids
    ]
    LOGGER.info(
        "Fixtures remaining after top-tournament filter: %d", len(filtered_matches)
    )
    return filtered_matches


def format_fixtures(matches: list[dict]) -> list[str]:
    """Convert API fixture objects to the format consumed by team_streaks."""
    fixtures = []
    for match in matches:
        home_team = match.get("teams", {}).get("home", {}).get("name")
        away_team = match.get("teams", {}).get("away", {}).get("name")
        if home_team and away_team:
            fixtures.append(f"{home_team} - {away_team}")
    return fixtures


def cache_path(output_dir: str | Path, target_date: date) -> Path:
    return Path(output_dir) / "fixtures" / f"fixtures_{target_date:%Y%m%d}.txt"


def metadata_cache_path(output_dir: str | Path, target_date: date) -> Path:
    """Return the date-specific cache that retains fixture league metadata."""
    return Path(output_dir) / "fixtures" / f"fixtures_{target_date:%Y%m%d}.json"


def fixture_metadata(matches: list[dict]) -> dict[str, dict[str, int | str | None]]:
    """Map a formatted game name to the league information returned by the API."""
    metadata: dict[str, dict[str, int | str]] = {}
    for match in matches:
        home = match.get("teams", {}).get("home", {}).get("name")
        away = match.get("teams", {}).get("away", {}).get("name")
        league = match.get("league", {})
        if home and away:
            metadata[f"{home} - {away}"] = {
                "id": league.get("id"),
                "name": league.get("name") or "Unknown",
            }
    return metadata


def load_or_fetch_fixtures_with_metadata(
    date_option: str = "today",
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    api_key: str | None = None,
    allowed_league_ids: set[int] | None = None,
    api_timeout_seconds: float = 10.0,
) -> tuple[list[str], dict[str, dict[str, int | str | None]], Path]:
    """Load fixtures and their league metadata, reusing a date-specific cache.

    The legacy newline-delimited fixture cache remains supported through
    :func:`load_or_fetch_fixtures`; this JSON cache is additive.
    """
    target_date = resolve_date(date_option)
    path = metadata_cache_path(output_dir, target_date)
    if path.is_file():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            metadata = cached["fixtures"]
            if not isinstance(metadata, dict) or not metadata:
                raise ValueError("empty or invalid fixtures metadata")
            fixtures = list(metadata)
        except (OSError, ValueError, KeyError, json.JSONDecodeError, TypeError):
            LOGGER.warning("Ignoring invalid fixture metadata cache: %s", path)
        else:
            LOGGER.info("Using cached fixture metadata from %s", path)
            return fixtures, metadata, path

    matches = get_filtered_matches(
        target_date.isoformat(),
        api_key=api_key,
        allowed_league_ids=allowed_league_ids,
        api_timeout_seconds=api_timeout_seconds,
    )
    metadata = fixture_metadata(matches)
    if not metadata:
        LOGGER.error("No fixtures found for %s; stopping before writing a cache", date_option)
        raise RuntimeError(f"No fixtures found for {date_option}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fixtures": metadata}, indent=2, ensure_ascii=False), encoding="utf-8")
    LOGGER.info("Fixture metadata written to %s", path)
    return list(metadata), metadata, path


def load_or_fetch_fixtures(
    date_option: str = "today",
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    api_key: str | None = None,
    allowed_league_ids: set[int] | None = None,
    api_timeout_seconds: float = 10.0,
) -> tuple[list[str], Path]:
    """Load a cached fixture list or fetch and cache it for the requested date."""
    target_date = resolve_date(date_option)
    path = cache_path(output_dir, target_date)
    if path.is_file():
        fixtures = [
            line for line in path.read_text(encoding="utf-8").splitlines() if line
        ]
        if not fixtures:
            path.unlink()
            LOGGER.error("Empty fixture cache removed: %s", path)
            raise RuntimeError(f"No fixtures found for {date_option}")

        LOGGER.info("Using cached fixtures from %s", path)
        return fixtures, path

    fixtures = format_fixtures(
        get_filtered_matches(
            target_date.isoformat(),
            api_key=api_key,
            allowed_league_ids=allowed_league_ids,
            api_timeout_seconds=api_timeout_seconds,
        )
    )
    if not fixtures:
        LOGGER.error("No fixtures found for %s; stopping before writing a cache", date_option)
        raise RuntimeError(f"No fixtures found for {date_option}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(fixtures) + "\n", encoding="utf-8")
    LOGGER.info("Fixtures written to %s", path)
    return fixtures, path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch top football fixtures from API-Football."
    )
    parser.add_argument("date", choices=DATE_OPTIONS, nargs="?", default="today")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Root directory containing the fixtures cache.",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    fixtures, path = load_or_fetch_fixtures(args.date, output_dir=args.output_dir)
    print(f"Fixtures for {args.date} ({len(fixtures)}):")
    print("\n".join(fixtures))
    LOGGER.info("Fixture cache: %s", path)


if __name__ == "__main__":
    main()
