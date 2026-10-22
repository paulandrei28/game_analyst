"""SofaScore team-season disciplinary statistics normalization and enrichment."""
from __future__ import annotations

import math
import logging
from typing import Any

try:
    from .sofascore_client import SofascoreClient
except ImportError:  # pragma: no cover - direct standalone script execution
    from sofascore_client import SofascoreClient

BASELINE_MIN_TEAMS = 4
LOGGER = logging.getLogger(__name__)


def _event_data(context: Any) -> dict[str, Any]:
    if not isinstance(context, dict):
        return {}
    event = context.get("event")
    if not isinstance(event, dict):
        return {}
    nested = event.get("event")
    return nested if isinstance(nested, dict) else event


def team_statistics_request_keys(
    context: Any,
) -> tuple[tuple[str, str] | None, dict[str, tuple[Any, Any, Any, str]]]:
    event = _event_data(context)
    tournament = event.get("tournament") if isinstance(event.get("tournament"), dict) else {}
    unique_tournament = (
        tournament.get("uniqueTournament")
        if isinstance(tournament.get("uniqueTournament"), dict)
        else {}
    )
    tournament_id = unique_tournament.get("id", tournament.get("id"))
    season = event.get("season") if isinstance(event.get("season"), dict) else {}
    season_id = season.get("id")
    if tournament_id is None or season_id is None:
        return None, {}

    competition_key = (str(tournament_id), str(season_id))
    team_keys = {
        side: (team["id"], tournament_id, season_id, "overall")
        for side in ("home", "away")
        if isinstance((team := event.get(f"{side}Team")), dict)
        and team.get("id") is not None
    }
    return competition_key, team_keys


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _first_number(stats: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in stats:
            value = _number(stats.get(key))
            if value is not None:
                return value
    return None


def _per_match_rate(value: float | None, matches: float | int | None) -> float | None:
    if value is None or matches is None or matches <= 0:
        return None
    return value / matches


def _penalty_total(won: float | None, conceded: float | None) -> float | None:
    if won is not None and conceded is not None:
        return won + conceded
    return won if conceded is None else conceded


def parse_team_statistics(payload: Any) -> dict[str, Any]:
    """Normalize SofaScore's ``{statistics: {...}}`` season response.

    Missing keys stay ``None``. SofaScore does not expose every metric for all
    competitions, so rates are calculated only when both metric and matches
    are present and the denominator is greater than zero.
    """
    root = payload if isinstance(payload, dict) else {}
    statistics = root.get("statistics", root)
    if isinstance(statistics, dict) and isinstance(statistics.get("statistics"), dict):
        statistics = statistics["statistics"]
    if not isinstance(statistics, dict):
        statistics = {}

    matches = _first_number(statistics, ("matches", "appearances", "matchesPlayed"))
    if matches is not None and matches.is_integer():
        matches = int(matches)
    yellow = _first_number(statistics, ("yellowCards", "yellow_cards"))
    red = _first_number(statistics, ("redCards", "red_cards"))
    second_yellow = _first_number(statistics, ("yellowRedCards", "secondYellowCards", "second_yellow_cards"))
    fouls = _first_number(statistics, ("fouls", "foulsCommitted"))
    penalties_won = _first_number(statistics, ("penaltyWon", "penaltiesWon", "penaltiesTaken"))
    penalties_conceded = _first_number(
        statistics, ("penaltyConceded", "penaltiesConceded", "penaltiesCommited", "penaltiesCommitted")
    )
    penalty_goals = _first_number(statistics, ("penaltyGoals", "penaltyGoalsScored"))
    penalty_goals_conceded = _first_number(statistics, ("penaltyGoalsConceded",))

    penalties = _penalty_total(penalties_won, penalties_conceded)
    # Keep numeric SofaScore fields so newly supported season stats remain
    # available to analysis without another API request or parser rewrite.
    parsed: dict[str, Any] = {
        key: number
        for key, value in statistics.items()
        if (number := _number(value)) is not None
    }
    parsed.update({
        "matches": matches,
        "fouls": fouls,
        "yellow_cards": yellow,
        "red_cards": red,
        "second_yellow_cards": second_yellow,
        "penalties_won": penalties_won,
        "penalties_conceded": penalties_conceded,
        "penalty_goals": penalty_goals,
        "penalty_goals_conceded": penalty_goals_conceded,
        "fouls_per_match": _per_match_rate(fouls, matches),
        "yellow_cards_per_match": _per_match_rate(yellow, matches),
        "red_cards_per_match": _per_match_rate(red, matches),
        "second_yellow_cards_per_match": _per_match_rate(second_yellow, matches),
        "penalties_per_match": _per_match_rate(penalties, matches),
        "penalties_won_per_match": _per_match_rate(penalties_won, matches),
        "penalties_conceded_per_match": _per_match_rate(penalties_conceded, matches),
    })

    # SofaScore uses both totals and already-normalized per-game fields,
    # depending on the competition. Prefer explicit per-game values.
    rate_fields = {
        "goals_scored_per_match": (("goalsPerGame", "goalsScoredPerGame"), ("goalsScored", "goals")),
        "goals_conceded_per_match": (("goalsConcededPerGame",), ("goalsConceded",)),
        "corners_per_match": (("cornersPerGame", "corners_per_match"), ("corners",)),
        "total_shots_per_match": (("totalShotsPerGame", "shotsPerGame"), ("totalShots", "shots")),
        "shots_on_target_per_match": (("shotsOnTargetPerGame",), ("shotsOnTarget",)),
        "fouls_per_match": (("foulsPerGame",), ("fouls", "foulsCommitted")),
        "yellow_cards_per_match": (("yellowCardsPerGame",), ("yellowCards", "yellow_cards")),
        "red_cards_per_match": (("redCardsPerGame",), ("redCards", "red_cards")),
        "second_yellow_cards_per_match": (("yellowRedCardsPerGame",), ("yellowRedCards", "secondYellowCards", "second_yellow_cards")),
    }
    for output, (rate_keys, total_keys) in rate_fields.items():
        explicit_rate = _first_number(statistics, rate_keys)
        if explicit_rate is not None:
            parsed[output] = explicit_rate
        elif output not in {"fouls_per_match", "yellow_cards_per_match", "red_cards_per_match", "second_yellow_cards_per_match"}:
            parsed[output] = _per_match_rate(_first_number(statistics, total_keys), matches)
    return parsed


async def enrich_qualifying_team_statistics(
    client: SofascoreClient,
    contexts: dict[str, Any],
    qualifying_games: set[str],
) -> None:
    """Fetch unique team-season stats and attach team and competition rates.

    The client owns keyed memory/disk caching and transport pacing. Competition
    baselines use unique teams collected across the supplied fixtures.
    """
    team_payloads: dict[tuple[str, str, str, str], dict[str, Any] | None] = {}
    game_keys: dict[str, tuple[str, str] | None] = {}
    teams_by_competition: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}

    for game in qualifying_games:
        context = contexts.get(game)
        if not isinstance(context, dict):
            game_keys[game] = None
            continue
        competition_key, request_keys = team_statistics_request_keys(context)
        if competition_key is None:
            game_keys[game] = None
            context["team_statistics"] = {"home": None, "away": None}
            continue

        game_keys[game] = competition_key
        team_rows: dict[str, dict[str, Any] | None] = {}
        for side in ("home", "away"):
            request_key = request_keys.get(side)
            if request_key is None:
                team_rows[side] = None
                continue
            cache_key = (
                str(request_key[0]),
                str(request_key[1]),
                str(request_key[2]),
                request_key[3],
            )
            if cache_key not in team_payloads:
                try:
                    payload = await client.fetch_team_statistics(
                        request_key[0], request_key[1], request_key[2],
                        statistics_type=request_key[3],
                    )
                except Exception:
                    LOGGER.warning(
                        "SofaScore team statistics unavailable for key %s",
                        cache_key,
                        exc_info=True,
                    )
                    payload = None
                team_payloads[cache_key] = (
                    parse_team_statistics(payload) if payload is not None else None
                )
            team_rows[side] = team_payloads[cache_key]
            if team_payloads[cache_key] is not None:
                teams_by_competition.setdefault(competition_key, {})[
                    cache_key[0]
                ] = team_payloads[cache_key]
        context["team_statistics"] = team_rows

    baselines: dict[tuple[str, str], dict[str, float | None]] = {}
    counts: dict[tuple[str, str], dict[str, int]] = {}
    metrics = (
        "fouls_per_match", "yellow_cards_per_match", "red_cards_per_match",
        "second_yellow_cards_per_match", "penalties_per_match",
    )
    for competition, teams in teams_by_competition.items():
        baselines[competition] = {}
        counts[competition] = {}
        for metric in metrics:
            values = [row[metric] for row in teams.values() if row.get(metric) is not None]
            counts[competition][metric] = len(values)
            baselines[competition][metric] = (
                sum(values) / len(values) if len(values) >= BASELINE_MIN_TEAMS else None
            )

    for game, competition in game_keys.items():
        context = contexts.get(game)
        if not isinstance(context, dict):
            continue
        context["competition_statistics_baseline"] = baselines.get(competition, {}) if competition else {}
        context["competition_statistics_baseline_team_counts"] = counts.get(competition, {}) if competition else {}
