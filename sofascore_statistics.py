"""SofaScore team-season disciplinary statistics normalization and enrichment."""
from __future__ import annotations

import math
import logging
from typing import Any

from .sofascore_client import SofascoreClient

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


def parse_team_statistics(payload: Any) -> dict[str, float | int | None]:
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

    def per_match(value: float | None) -> float | None:
        return value / matches if value is not None and matches is not None and matches > 0 else None

    penalties = (
        penalties_won + penalties_conceded
        if penalties_won is not None and penalties_conceded is not None
        else penalties_won if penalties_conceded is None
        else penalties_conceded
    )
    return {
        "matches": matches,
        "fouls": fouls,
        "yellow_cards": yellow,
        "red_cards": red,
        "second_yellow_cards": second_yellow,
        "penalties_won": penalties_won,
        "penalties_conceded": penalties_conceded,
        "penalty_goals": penalty_goals,
        "penalty_goals_conceded": penalty_goals_conceded,
        "fouls_per_match": per_match(fouls),
        "yellow_cards_per_match": per_match(yellow),
        "red_cards_per_match": per_match(red),
        "second_yellow_cards_per_match": per_match(second_yellow),
        "penalties_per_match": per_match(penalties),
        "penalties_won_per_match": per_match(penalties_won),
        "penalties_conceded_per_match": per_match(penalties_conceded),
    }


async def enrich_qualifying_team_statistics(
    client: SofascoreClient,
    contexts: dict[str, Any],
    qualifying_games: set[str],
) -> None:
    """Fetch unique team-season stats and attach team and competition rates.

    The client owns keyed memory/disk caching and transport pacing. Competition
    baselines use unique qualifying teams already fetched in this run.
    """
    team_payloads: dict[tuple[str, str, str, str], dict[str, float | int | None] | None] = {}
    game_keys: dict[str, tuple[str, str] | None] = {}
    teams_by_competition: dict[tuple[str, str], dict[str, dict[str, float | int | None]]] = {}

    for game in qualifying_games:
        context = contexts.get(game)
        if not isinstance(context, dict):
            game_keys[game] = None
            continue
        event = _event_data(context)
        tournament = event.get("tournament") if isinstance(event.get("tournament"), dict) else {}
        unique_tournament = tournament.get("uniqueTournament") if isinstance(tournament.get("uniqueTournament"), dict) else {}
        tournament_id = unique_tournament.get("id", tournament.get("id"))
        season = event.get("season") if isinstance(event.get("season"), dict) else {}
        season_id = season.get("id")
        if tournament_id is None or season_id is None:
            game_keys[game] = None
            context["team_statistics"] = {"home": None, "away": None}
            continue

        competition_key = (str(tournament_id), str(season_id))
        game_keys[game] = competition_key
        team_rows: dict[str, dict[str, float | int | None] | None] = {}
        for side in ("home", "away"):
            team = event.get(f"{side}Team")
            team_id = team.get("id") if isinstance(team, dict) else None
            if team_id is None:
                team_rows[side] = None
                continue
            key = (str(team_id), str(tournament_id), str(season_id), "overall")
            if key not in team_payloads:
                try:
                    payload = await client.fetch_team_statistics(
                        team_id, tournament_id, season_id, statistics_type="overall"
                    )
                except Exception:
                    LOGGER.warning("SofaScore team statistics unavailable for key %s", key, exc_info=True)
                    payload = None
                team_payloads[key] = parse_team_statistics(payload) if payload is not None else None
            team_rows[side] = team_payloads[key]
            if team_payloads[key] is not None:
                teams_by_competition.setdefault(competition_key, {})[str(team_id)] = team_payloads[key]
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
