"""Filter worth-watching fixtures and precompute compact discipline ratings."""
from __future__ import annotations

import math
from typing import Any

try:
    from .sofascore_match_data import parse_1x2_odds, parse_referee
except ImportError:  # pragma: no cover
    from sofascore_match_data import parse_1x2_odds, parse_referee

MIN_WORTH_WATCHING_HOME_ODDS = 1.40
MAX_WORTH_WATCHING_HOME_ODDS = 1.65
RISK_LOW_MAX = 39
RISK_MEDIUM_MAX = 64
SAMPLE_PRIOR_MATCHES = 8
RISK_SCORE_WEIGHTS = {
    "penalty": {"referee": 0.35, "team": 0.65},
    "red": {"referee": 0.30, "team_red": 0.45, "team_yellow_support": 0.15, "team_fouls": 0.10},
    "yellow": {"referee": 0.25, "team": 0.60, "team_fouls": 0.15},
}


def _event_data(context: dict[str, Any]) -> dict[str, Any]:
    event = context.get("event")
    if not isinstance(event, dict):
        return {}
    nested = event.get("event")
    return nested if isinstance(nested, dict) else event


def _rate(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def classify_risk(score: float | None) -> str:
    """Map a deterministic 0–100 risk index to its public category."""
    if score is None:
        return "insufficient_data"
    if score <= RISK_LOW_MAX:
        return "low"
    if score <= RISK_MEDIUM_MAX:
        return "medium"
    return "high"


def _match_odds(raw_context: Any) -> dict[str, float]:
    raw = raw_context if isinstance(raw_context, dict) else {}
    labels = {entry.get("label"): _rate(entry.get("odds")) for entry in parse_1x2_odds(raw.get("odds"))}
    values = {
        "home_win": labels.get("Home win (1)"),
        "draw": labels.get("Draw (X)"),
        "away_win": labels.get("Away win (2)"),
    }
    return {key: float(value) for key, value in values.items() if value is not None and value > 1}


def _odds_qualify(odds: dict[str, float]) -> bool:
    home = odds.get("home_win")
    return home is not None and MIN_WORTH_WATCHING_HOME_ODDS <= home <= MAX_WORTH_WATCHING_HOME_ODDS


def qualifies_for_worth_watching(raw_context: Any, *, derby: bool = False) -> bool:
    """Cheap odds/derby check used before fetching referee details."""
    return derby or _odds_qualify(_match_odds(raw_context))


def _score_rate(rate: Any, sample: Any, baseline: Any) -> float | None:
    observed = _rate(rate)
    baseline_rate = _rate(baseline)
    if observed is None or baseline_rate is None or baseline_rate <= 0:
        return None
    matches = int(_rate(sample) or 0)
    if matches <= 0:
        return None
    adjusted = (
        (observed * matches + baseline_rate * SAMPLE_PRIOR_MATCHES)
        / (matches + SAMPLE_PRIOR_MATCHES)
    )
    ratio = adjusted / baseline_rate
    if ratio < .8: return 25.0
    if ratio < 1.0: return 38.0
    if ratio < 1.15: return 50.0
    if ratio < 1.30: return 68.0
    return 82.0


def _referee_scores(
    raw: dict[str, Any], event: dict[str, Any], baselines: dict[str, Any]
) -> dict[str, float | None]:
    payload = raw.get("referee") if isinstance(raw.get("referee"), dict) else {}
    detail = payload.get("referee") if isinstance(payload.get("referee"), dict) else {}
    if isinstance(detail.get("referee"), dict):
        detail = detail["referee"]
    parsed = parse_referee(event, detail, payload.get("statistics"))
    competition_games = _rate(parsed.get("Competition Games officiated"))
    career_games = _rate(parsed.get("Career Games officiated"))
    scope = "Competition" if (competition_games or 0) >= 10 else "Career"
    sample = competition_games if scope == "Competition" else career_games
    # Team aggregate rates are per team appearance; referee rates are per
    # match, so their comparable competition baselines are twice the team mean.
    referee_baselines = {
        key: (_rate(value) * 2 if _rate(value) is not None else None)
        for key, value in baselines.items()
    }
    pen = _score_rate(parsed.get(f"{scope} Penalties per game"), sample,
                      referee_baselines.get("penalties_per_match"))
    red_rate = parsed.get(f"{scope} Red cards per game")
    second_yellow_rate = parsed.get(f"{scope} Second-yellow dismissals per game")
    red = _score_rate(red_rate, sample, referee_baselines.get("red_cards_per_match"))
    second_yellow = _score_rate(second_yellow_rate, sample,
                                referee_baselines.get("second_yellows_per_match"))
    if red is not None and second_yellow is not None:
        red = .75 * red + .25 * second_yellow
    yellow = _score_rate(parsed.get(f"{scope} Yellow cards per game"), sample,
                         referee_baselines.get("yellow_cards_per_match"))
    return {"penalty": pen, "red": red, "yellow": yellow}


def _team_risk_score(
    team_rows: list[Any], metric: str, baseline: Any
) -> float | None:
    scores = [
        score
        for row in team_rows
        if isinstance(row, dict)
        if (score := _score_rate(row.get(metric), row.get("matches"), baseline)) is not None
    ]
    # One unusually high team rate is enough to raise a match-level warning.
    return max(scores) if scores else None


def _weighted_risk_score(
    parts: list[tuple[float | None, float, bool]],
) -> float | None:
    available = [(score, weight) for score, weight, _ in parts if score is not None]
    reserved_weight = sum(
        weight for score, weight, reserve_missing in parts
        if score is None and reserve_missing
    )
    expected_weight = sum(weight for _, weight in available) + reserved_weight
    if not available or not expected_weight:
        return None
    return sum(score * weight for score, weight in available) / expected_weight


def _discipline_risk_scores(
    context: dict[str, Any], event: dict[str, Any], baseline: dict[str, Any]
) -> dict[str, float | None]:
    referee_scores = _referee_scores(context, event, baseline)
    team_statistics = context.get("team_statistics", {})
    team_statistics = team_statistics if isinstance(team_statistics, dict) else {}
    team_rows = [team_statistics.get("home"), team_statistics.get("away")]

    team_penalty = _team_risk_score(
        team_rows, "penalties_per_match", baseline.get("penalties_per_match")
    )
    team_red = _team_risk_score(
        team_rows, "red_cards_per_match", baseline.get("red_cards_per_match")
    )
    team_second_yellow = _team_risk_score(
        team_rows,
        "second_yellow_cards_per_match",
        baseline.get("second_yellow_cards_per_match"),
    )
    team_yellow = _team_risk_score(
        team_rows, "yellow_cards_per_match", baseline.get("yellow_cards_per_match")
    )
    team_fouls = _team_risk_score(
        team_rows, "fouls_per_match", baseline.get("fouls_per_match")
    )

    weights = RISK_SCORE_WEIGHTS
    penalty = _weighted_risk_score([
        (referee_scores["penalty"], weights["penalty"]["referee"], True),
        (team_penalty, weights["penalty"]["team"], False),
    ])

    dismissal_support = [
        min(score, 55.0)
        for score in (team_yellow, team_second_yellow)
        if score is not None
    ]
    team_dismissal = (
        sum(dismissal_support) / len(dismissal_support)
        if dismissal_support
        else None
    )
    red = _weighted_risk_score([
        (referee_scores["red"], weights["red"]["referee"], True),
        (team_red, weights["red"]["team_red"], False),
        (team_dismissal, weights["red"]["team_yellow_support"], False),
        (min(team_fouls, 55.0) if team_fouls is not None else None,
         weights["red"]["team_fouls"], False),
    ])
    yellow = _weighted_risk_score([
        (referee_scores["yellow"], weights["yellow"]["referee"], True),
        (team_yellow, weights["yellow"]["team"], False),
        (team_fouls, weights["yellow"]["team_fouls"], False),
    ])
    return {"penalty": penalty, "red": red, "yellow": yellow}


def build_worth_watching(
    contexts: dict[str, Any], leagues: dict[str, Any],
    derby_by_game: dict[str, bool],
) -> dict[str, dict[str, Any]]:
    """Precompute a compact disciplinary context for qualifying fixtures."""
    output: dict[str, dict[str, Any]] = {}
    for game, context in contexts.items():
        is_derby = bool(derby_by_game.get(game, False))
        league = leagues.get(game)
        odds = _match_odds(context)
        if not isinstance(league, dict):
            continue
        event = _event_data(context)
        baseline = context.get("competition_statistics_baseline", {})
        baseline = baseline if isinstance(baseline, dict) else {}
        risk_scores = _discipline_risk_scores(context, event, baseline)
        game_penalty_score = risk_scores["penalty"]
        game_red_score = risk_scores["red"]
        game_yellow_score = risk_scores["yellow"]

        odds_qualify = _odds_qualify(odds)
        high_penalty = game_penalty_score is not None and game_penalty_score >= 65
        high_red = game_red_score is not None and game_red_score >= 65
        high_yellow = game_yellow_score is not None and game_yellow_score >= 65
        if not (odds_qualify or is_derby or high_penalty or high_red or high_yellow):
            continue

        output[game] = {
            "league": {"id": league.get("id"), "name": league.get("name") or "Unknown"},
            "odds": odds,
            "derby": is_derby,
            "stats": {
                "penalty_probability": classify_risk(game_penalty_score),
                "red_card_probability": classify_risk(game_red_score),
                "high_yellow_card_tendency": (
                    high_yellow if game_yellow_score is not None else None
                ),
            },
        }
    return output


