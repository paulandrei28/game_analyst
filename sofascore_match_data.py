"""Standalone SofaScore match odds and referee inspection runner.

Example: python sofascore_match_data.py "AD Ceuta FC" "Real Sociedad II" --date 2026-10-14
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from fractions import Fraction
from typing import Any

try:
    from .config import load_config
    from .sofascore_client import SofascoreClient, SofascoreHTTPError
    from .sofascore_statistics import parse_team_statistics
except ImportError:  # pragma: no cover
    from config import load_config
    from sofascore_client import SofascoreClient, SofascoreHTTPError
    from sofascore_statistics import parse_team_statistics

LOGGER = logging.getLogger(__name__)
OUTCOMES = {"1": "Home win (1)", "x": "Draw (X)", "2": "Away win (2)"}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _decimal_odds(decimal: Any, fractional: Any) -> tuple[str, str]:
    """Prefer SofaScore decimal odds, otherwise convert fractional odds to decimal."""
    if decimal not in (None, ""):
        try:
            value = Decimal(str(decimal)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return f"{value:.2f}", "decimal"
        except InvalidOperation:
            pass
    if fractional not in (None, ""):
        try:
            value = Decimal(Fraction(str(fractional)).numerator) / Decimal(
                Fraction(str(fractional)).denominator
            ) + Decimal(1)
            value = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return f"{value:.2f}", "European decimal (converted from fractional)"
        except (ValueError, ZeroDivisionError, InvalidOperation):
            return str(fractional), "fractional"
    return "Not available", "Not available"


def _number_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum_statistic(rows: list[Any], keys: tuple[str, ...]) -> int | float | None:
    values = [
        number
        for row in rows
        if isinstance(row, dict)
        for key in keys
        if (number := _number_or_none(row.get(key))) is not None
    ]
    if not values:
        return None
    total = sum(values)
    return int(total) if total.is_integer() else total


def _markets(payload: Any) -> list[dict[str, Any]]:
    """Return market objects from known SofaScore response wrappers."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("markets", "odds", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def parse_1x2_odds(payload: Any) -> list[dict[str, str]]:
    """Extract only the full-time 1X2 market, retaining its source per outcome."""
    parsed: dict[str, dict[str, str]] = {}
    for market in _markets(payload):
        market_id = str(market.get("marketId", market.get("market_id", market.get("id", ""))))
        market_name = str(market.get("marketName", market.get("market_name", market.get("name", ""))))
        normalized_name = market_name.lower()
        if market_id and market_id != "1":
            continue
        if normalized_name and not any(
            marker in normalized_name for marker in ("full time", "fulltime", "1x2", "match result", "winner")
        ):
            continue
        choices = market.get("choices", market.get("outcomes", []))
        if not isinstance(choices, list):
            continue
        source = market.get("source") or market.get("bookmaker") or market.get("provider") or {}
        source = _mapping(source)
        common_source = (
            source.get("name")
            or source.get("title")
            or market.get("sourceName")
            or market.get("bookmakerName")
            or (f"Source ID {market['sourceId']}" if market.get("sourceId") is not None else "Not available")
        )
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            label = str(choice.get("name", choice.get("label", choice.get("choice", "")))).strip().lower()
            if label not in OUTCOMES:
                continue
            decimal = choice.get("decimalValue", choice.get("decimal_value", choice.get("decimalOdds")))
            fractional = choice.get("fractionalValue", choice.get("fractional_value", choice.get("fractionalOdds", choice.get("initialFractionalValue"))))
            value, odds_format = _decimal_odds(decimal, fractional)
            choice_source = choice.get("source") or choice.get("bookmaker") or {}
            choice_source = _mapping(choice_source)
            source_name = (
                choice_source.get("name")
                or choice_source.get("title")
                or choice.get("sourceName")
                or common_source
            )
            parsed[label] = {"label": OUTCOMES[label], "odds": value, "format": odds_format, "source": str(source_name)}
    return [parsed[key] for key in ("1", "x", "2") if key in parsed]


def parse_referee(event_payload: Any, detail_payload: Any = None, stats_payload: Any = None) -> dict[str, Any]:
    """Collect referee career totals and the matching tournament's career totals."""
    event = _mapping(event_payload)
    if isinstance(event.get("event"), dict):
        event = event["event"]
    referee = _mapping(event.get("referee"))
    details = _mapping(detail_payload)
    if isinstance(details.get("referee"), dict):
        details = details["referee"]
    stats_root = _mapping(stats_payload)
    tournament = _mapping(event.get("tournament"))
    unique_tournament = _mapping(tournament.get("uniqueTournament"))
    tournament_id = unique_tournament.get("id", tournament.get("id"))
    tournament_name = unique_tournament.get("name", tournament.get("name"))
    rows = stats_root.get("statistics", [])
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        rows = []
    current_competition = next(
        (
            _mapping(row)
            for row in rows
            if str(_mapping(_mapping(row).get("uniqueTournament")).get("id")) == str(tournament_id)
            and tournament_id is not None
        ),
        {},
    )
    name = details.get("name") or referee.get("name")
    identity = details.get("id") or referee.get("id")
    fields = {
        "Games officiated": ("games", "appearances", "matches", "matchesOfficiated"),
        "Yellow cards": ("yellowCards", "yellow_cards"),
        "Red cards": ("redCards", "red_cards"),
        "Second-yellow dismissals": ("yellowRedCards", "secondYellowCards", "second_yellow_cards"),
        "Penalties": ("penalty", "penalties"),
    }
    result: dict[str, Any] = {"Name": name, "ID": identity, "Competition": tournament_name}
    for label, keys in fields.items():
        result[f"Career {label}"] = next((details[key] for key in keys if details.get(key) is not None), None)
        result[f"Competition {label}"] = next((current_competition[key] for key in keys if current_competition.get(key) is not None), None)
    if result["Career Penalties"] is None:
        result["Career Penalties"] = _sum_statistic(rows, ("penalty", "penalties"))

    rate_fields = {
        "Yellow cards per game": "Yellow cards",
        "Red cards per game": "Red cards",
        "Second-yellow dismissals per game": "Second-yellow dismissals",
        "Penalties per game": "Penalties",
    }
    for scope in ("Career", "Competition"):
        games = _number_or_none(result[f"{scope} Games officiated"])
        for rate_label, count_label in rate_fields.items():
            count = _number_or_none(result[f"{scope} {count_label}"])
            result[f"{scope} {rate_label}"] = (
                f"{count / games:.2f}" if count is not None and games is not None and games > 0 else None
            )
        yellows = _number_or_none(result[f"{scope} Yellow cards"])
        reds = _number_or_none(result[f"{scope} Red cards"])
        result[f"{scope} Total cards per game"] = (
            f"{(yellows + reds) / games:.2f}"
            if yellows is not None and reds is not None and games is not None and games > 0
            else None
        )
    result["Country"] = _mapping(details.get("country")).get("name") or _mapping(referee.get("country")).get("name")
    result["Statistics scope"] = (
        "Career totals from referee profile; competition totals cover all available seasons in that tournament. Career penalties are summed across available tournament rows."
        if details or current_competition
        else None
    )
    return result


def _show(value: Any) -> str:
    return "Not available" if value is None or value == "" else str(value)


def _format_number(value: Any, decimals: int = 1) -> str:
    number = _number_or_none(value)
    if number is None:
        return _show(value)
    return f"{number:.{decimals}f}".rstrip("0").rstrip(".")


TEAM_STAT_SECTIONS = {
    "Season summary": (
        ("Sofascore rating", "avgRating", "number"),
        ("Games", "matches", "number"),
        ("Goals scored", "goalsScored", "number"),
        ("Goals conceded", "goalsConceded", "number"),
        ("Assists", "assists", "number"),
    ),
    "Running performance": (
        ("Distance covered per game", "kilometersCoveredPerGame", "number"),
        ("Sprints per game", "numberOfSprintsPerGame", "number"),
    ),
    "Attacking": (
        ("Goals per game", "goals_scored_per_match", "number"),
        ("Penalty goals", "penaltyGoals", "number"),
        ("Free kick goals", "freeKickGoals", "number"),
        ("Goals from inside the box / shots", "goalsFromInsideTheBox", "fraction:shotsFromInsideTheBox"),
        ("Goals from outside the box / shots", "goalsFromOutsideTheBox", "fraction:shotsFromOutsideTheBox"),
        ("Left-footed goals", "leftFootGoals", "number"),
        ("Right-footed goals", "rightFootGoals", "number"),
        ("Headed goals", "headedGoals", "number"),
        ("Big chances per game", "bigChances", "per_game"),
        ("Big chances missed per game", "bigChancesMissed", "per_game"),
        ("Total shots per game", "total_shots_per_match", "number"),
        ("Shots on target per game", "shots_on_target_per_match", "number"),
        ("Shots off target per game", "shotsOffTarget", "per_game"),
        ("Blocked shots per game", "blockedScoringAttempt", "per_game"),
        ("Successful dribbles per game", "successfulDribbles", "per_game"),
        ("Corners per game", "corners_per_match", "number"),
        ("Free kicks per game", "freeKicks", "per_game"),
        ("Hit woodwork", "hitWoodwork", "number"),
        ("Counter attacks", "fastBreaks", "number"),
    ),
    "Passing": (
        ("Ball possession", "averageBallPossession", "percent"),
        ("Accurate passes", "accuratePasses", "percent:accuratePassesPercentage"),
        ("Accurate own-half passes", "accurateOwnHalfPasses", "percent:accurateOwnHalfPassesPercentage"),
        ("Accurate opposition-half passes", "accurateOppositionHalfPasses", "percent:accurateOppositionHalfPassesPercentage"),
        ("Accurate long balls", "accurateLongBalls", "percent:accurateLongBallsPercentage"),
        ("Accurate crosses", "accurateCrosses", "percent:accurateCrossesPercentage"),
    ),
    "Defending": (
        ("Clean sheets", "cleanSheets", "number"),
        ("Goals conceded per game", "goals_conceded_per_match", "number"),
        ("Tackles per game", "tackles", "per_game"),
        ("Interceptions per game", "interceptions", "per_game"),
        ("Clearances per game", "clearances", "per_game"),
        ("Saves per game", "saves", "per_game"),
        ("Balls recovered per game", "ballRecovery", "per_game"),
        ("Errors leading to a shot", "errorsLeadingToShot", "number"),
        ("Errors leading to a goal", "errorsLeadingToGoal", "number"),
        ("Penalties committed", "penalties_conceded", "number"),
        ("Clearances off the line", "clearancesOffLine", "number"),
        ("Last-man tackles", "lastManTackles", "number"),
    ),
    "Other": (
        ("Duels won per game", "duelsWon", "per_game"),
        ("Ground duels won per game", "groundDuelsWon", "per_game"),
        ("Aerial duels won per game", "aerialDuelsWon", "per_game"),
        ("Possession lost per game", "possessionLost", "per_game"),
        ("Throw-ins per game", "throwIns", "per_game"),
        ("Goal kicks per game", "goalKicks", "per_game"),
        ("Offsides per game", "offsides", "per_game"),
        ("Fouls per game", "fouls_per_match", "number"),
        ("Yellow cards per game", "yellow_cards_per_match", "number"),
        ("Red cards per game", "red_cards_per_match", "number"),
    ),
}


def _team_stat_value(stats: dict[str, Any], key: str, mode: str) -> str | None:
    value = stats.get(key)
    if value is None:
        return None
    if mode == "per_game":
        matches = _number_or_none(stats.get("matches"))
        number = _number_or_none(value)
        if matches is None or matches <= 0 or number is None:
            return None
        return _format_number(number / matches)
    if mode.startswith("fraction:"):
        denominator = stats.get(mode.partition(":")[2])
        return f"{_format_number(value, 0)} / {_format_number(denominator, 0)}" if denominator is not None else None
    if mode.startswith("percent:"):
        percentage = stats.get(mode.partition(":")[2])
        suffix = f" ({_format_number(percentage)}%)" if percentage is not None else ""
        return f"{_format_number(value, 0)}{suffix}"
    if mode == "percent":
        return f"{_format_number(value)}%"
    return _format_number(value)


def format_team_season_statistics(name: str, stats: Any) -> list[str]:
    """Format a team season-stat row as readable labeled sections."""
    lines = [str(name)]
    if not isinstance(stats, dict):
        return [*lines, "  Statistics unavailable"]
    for section, fields in TEAM_STAT_SECTIONS.items():
        values = [
            (label, formatted)
            for label, key, mode in fields
            if (formatted := _team_stat_value(stats, key, mode)) is not None
        ]
        if not values:
            continue
        lines.append(f"  {section}")
        lines.extend(f"    {label}: {value}" for label, value in values)
    return lines


def _request_error(exc: Exception) -> str:
    if isinstance(exc, SofascoreHTTPError):
        body = str(exc.body)[:500] if exc.body is not None else "Not available"
        return f"status={exc.status_code}, path={exc.path}, body={body}"
    return str(exc)


async def run(home: str, away: str, target_date: date) -> int:
    settings = load_config()
    client = SofascoreClient(
        request_interval=settings.sofascore_request_interval_seconds,
        request_jitter=settings.sofascore_request_jitter_seconds,
        request_burst_size=settings.sofascore_request_burst_size,
        request_burst_pause=settings.sofascore_request_burst_pause_seconds,
        request_backoff_base=settings.sofascore_request_backoff_base_seconds,
        request_max_retries=settings.sofascore_request_max_retries,
        search_timeout_seconds=settings.sofascore_search_timeout_seconds,
    )
    try:
        async with client:
            match = await client.resolve_event_by_match_name(home, away, target_date=target_date)
            if not match:
                print(f"No SofaScore match found for {home} - {away} on {target_date.isoformat()}.")
                return 2
            event_id = match["event_id"]
            try:
                event = await client.fetch("event", event_id=event_id)
            except Exception as exc:
                LOGGER.exception("Event detail request failed")
                event = None
                print(f"Event details unavailable: {_request_error(exc)}")
            try:
                odds_payload = await client.fetch("event_odds_1x2", event_id=event_id)
                odds = parse_1x2_odds(odds_payload)
                if not odds:
                    LOGGER.warning("No recognized full-time 1X2 outcomes. Response keys=%s", list(_mapping(odds_payload)))
            except Exception as exc:
                LOGGER.exception("1X2 odds request failed")
                odds = []
                print(f"Odds request failed: {_request_error(exc)}")

            event_data = _mapping(event)
            if isinstance(event_data.get("event"), dict):
                event_data = event_data["event"]
            referee_brief = _mapping(event_data.get("referee"))
            referee_details = stats = None
            if referee_brief.get("id") is not None:
                try:
                    referee_details = await client.fetch("referee", referee_id=referee_brief["id"])
                except Exception as exc:
                    LOGGER.warning("Referee profile request failed: %s", exc)
                try:
                    stats = await client.fetch("referee_statistics", referee_id=referee_brief["id"])
                except Exception as exc:
                    LOGGER.warning("Referee statistics request failed: %s", exc)
            referee = parse_referee(event, referee_details, stats)

            tournament = _mapping(_mapping(event_data.get("tournament")).get("uniqueTournament"))
            tournament_id = tournament.get("id", _mapping(event_data.get("tournament")).get("id"))
            season_id = _mapping(event_data.get("season")).get("id")
            team_season_stats: dict[str, dict[str, Any] | None] = {}
            for side in ("home", "away"):
                team = _mapping(event_data.get(f"{side}Team"))
                if team.get("id") is None or tournament_id is None or season_id is None:
                    team_season_stats[side] = None
                    continue
                try:
                    raw_stats = await client.fetch_team_statistics(
                        team["id"], tournament_id, season_id, statistics_type="overall"
                    )
                    team_season_stats[side] = parse_team_statistics(raw_stats) if raw_stats else None
                except Exception as exc:
                    LOGGER.warning("Team season statistics request failed for %s: %s", side, exc)
                    team_season_stats[side] = None

            home_name = _mapping(event_data.get("homeTeam")).get("name")
            away_name = _mapping(event_data.get("awayTeam")).get("name")
            actual_date = match.get("match_date") or target_date
            print("MATCH")
            print(f"Requested: {home} - {away}\nSofaScore: {_show(home_name)} - {_show(away_name)}")
            print(f"Date: {actual_date}\nEvent ID: {event_id}")
            print("\nTEAM SEASON STATISTICS")
            print(f"Competition ID: {_show(tournament_id)} | Season ID: {_show(season_id)}")
            for side, name in (("home", home_name), ("away", away_name)):
                for line in format_team_season_statistics(
                    name or side, team_season_stats.get(side)
                ):
                    print(line)
            print("\n1X2 ODDS")
            labels = {entry["label"]: entry for entry in odds}
            for key in ("Home win (1)", "Draw (X)", "Away win (2)"):
                entry = labels.get(key)
                print(f"{key}: {entry['odds']} ({entry['format']})" if entry else f"{key}: Not available")
            sources = list(dict.fromkeys(entry["source"] for entry in odds))
            print(f"Source/bookmaker: {', '.join(sources) if sources else 'Not available'}")
            print("\nREFEREE")
            for key in ("Name", "ID"):
                print(f"{key}: {_show(referee[key])}")
            print(f"Country: {_show(referee['Country'])}")
            print(f"Statistics scope: {_show(referee['Statistics scope'])}")
            print("Career totals")
            for key in ("Games officiated", "Yellow cards", "Red cards", "Second-yellow dismissals", "Penalties"):
                print(f"{key}: {_show(referee[f'Career {key}'])}")
            for key in ("Yellow cards per game", "Red cards per game", "Second-yellow dismissals per game", "Penalties per game"):
                print(f"{key}: {_show(referee[f'Career {key}'])}")
            print(f"Current competition: {_show(referee['Competition'])}")
            for key in ("Games officiated", "Yellow cards", "Red cards", "Second-yellow dismissals", "Penalties"):
                print(f"{key}: {_show(referee[f'Competition {key}'])}")
            for key in ("Yellow cards per game", "Red cards per game", "Second-yellow dismissals per game", "Penalties per game"):
                print(f"{key}: {_show(referee[f'Competition {key}'])}")
            return 0
    except SofascoreHTTPError as exc:
        LOGGER.exception("SofaScore request failed: %s", exc)
        print(f"SofaScore request failed: status={exc.status_code}, path={exc.path}, body={str(exc.body)[:500]}")
        return 3
    except Exception as exc:
        LOGGER.exception("Standalone match data run failed")
        print(f"Run failed: {exc}")
        return 1
    finally:
        # Also clean up if startup fails before the async context is entered.
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect SofaScore 1X2 odds and referee data for one match.")
    parser.add_argument("home_team")
    parser.add_argument("away_team")
    parser.add_argument("--date", type=date.fromisoformat, help="Requested match date (YYYY-MM-DD); prompted if omitted")
    args = parser.parse_args()
    target_date = args.date or date.fromisoformat(input("Match date (YYYY-MM-DD): ").strip())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    raise SystemExit(asyncio.run(run(args.home_team, args.away_team, target_date)))


if __name__ == "__main__":
    main()
