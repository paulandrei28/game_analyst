"""Filter worth-watching fixtures and precompute compact discipline ratings."""
from __future__ import annotations

import math
from typing import Any

try:
    from .sofascore_match_data import parse_1x2_odds, parse_referee
except ImportError:  # pragma: no cover
    from sofascore_match_data import parse_1x2_odds, parse_referee

MIN_WORTH_WATCHING_ODDS = 1.90
MIN_WORTH_WATCHING_LONG_ODDS = 1.90
MAX_WORTH_WATCHING_FAVORITE_PROBABILITY = 0.60
RISK_LOW_MAX = 39
RISK_MEDIUM_MAX = 64
SAMPLE_PRIOR_MATCHES = 8
RISK_SCORE_WEIGHTS = {
    "penalty": {"referee": 0.35, "team": 0.65},
    "red": {"referee": 0.30, "team_red": 0.45, "team_yellow_support": 0.15, "team_fouls": 0.10},
    "yellow": {"referee": 0.25, "team": 0.55, "team_fouls": 0.20},
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


def valid_competitive_odds(raw_context: Any) -> dict[str, float] | None:
    """Return complete numeric 1X2 odds only when the market is competitive."""
    numeric = _valid_1x2_odds(raw_context)
    return numeric if numeric is not None and _market_is_eligible(numeric) else None


def _market_is_eligible(numeric: dict[str, float]) -> bool:
    shortest = min(numeric.values())
    implied = [1 / value for value in numeric.values()]
    normalized = [value / sum(implied) for value in implied]
    return (
        shortest >= MIN_WORTH_WATCHING_ODDS
        and max(normalized) <= MAX_WORTH_WATCHING_FAVORITE_PROBABILITY
    ) or shortest >= MIN_WORTH_WATCHING_LONG_ODDS


def _valid_1x2_odds(raw_context: Any) -> dict[str, float] | None:
    raw = raw_context if isinstance(raw_context, dict) else {}
    labels = {entry.get("label"): _rate(entry.get("odds")) for entry in parse_1x2_odds(raw.get("odds"))}
    values = {
        "home_win": labels.get("Home win (1)"),
        "draw": labels.get("Draw (X)"),
        "away_win": labels.get("Away win (2)"),
    }
    if any(value is None or value <= 1 for value in values.values()):
        return None
    return {key: float(value) for key, value in values.items()}


def qualifies_for_worth_watching(raw_context: Any, *, derby: bool = False) -> bool:
    """Cheap selection check used before fetching disciplinary enrichment."""
    return worth_watching_odds(raw_context, derby=derby) is not None


def worth_watching_odds(raw_context: Any, *, derby: bool = False) -> dict[str, float] | None:
    """Return validated odds for an eligible market or verified derby."""
    odds = _valid_1x2_odds(raw_context)
    if odds is None:
        return None
    if _market_is_eligible(odds) or derby:
        return odds
    return None


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
) -> tuple[dict[str, float | None], int]:
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
    return {"penalty": pen, "red": red, "yellow": yellow}, int(sample or 0)


def build_worth_watching(
    contexts: dict[str, Any], leagues: dict[str, Any],
    derby_by_game: dict[str, bool],
) -> dict[str, dict[str, Any]]:
    """Precompute a compact disciplinary context for qualifying fixtures."""
    output: dict[str, dict[str, Any]] = {}
    for game, context in contexts.items():
        is_derby = bool(derby_by_game.get(game, False))
        league = leagues.get(game)
        # A verified derby is an independent inclusion path, but still needs
        # complete valid odds for the required output schema.
        odds = worth_watching_odds(context, derby=is_derby)
        if not isinstance(league, dict) or odds is None:
            continue
        event = _event_data(context)
        baseline = context.get("competition_statistics_baseline", {})
        baseline = baseline if isinstance(baseline, dict) else {}
        baseline_counts = context.get("competition_statistics_baseline_team_counts", {})
        baseline_counts = baseline_counts if isinstance(baseline_counts, dict) else {}
        scores, referee_sample = _referee_scores(context, event, baseline)
        teams = context.get("team_statistics", {})
        teams = teams if isinstance(teams, dict) else {}
        team_rows = [teams.get("home"), teams.get("away")]

        def team_score(metric: str, baseline_key: str) -> float | None:
            baseline_rate = baseline.get(baseline_key)
            observed = []
            for row in team_rows:
                if not isinstance(row, dict):
                    continue
                score = _score_rate(row.get(metric), row.get("matches"), baseline_rate)
                if score is not None:
                    observed.append(score)
            return sum(observed) / len(observed) if observed else None

        team_penalty = team_score("penalties_per_match", "penalties_per_match")
        team_red = team_score("red_cards_per_match", "red_cards_per_match")
        team_second_yellow = team_score("second_yellow_cards_per_match", "second_yellow_cards_per_match")
        team_yellow = team_score("yellow_cards_per_match", "yellow_cards_per_match")
        team_fouls = team_score("fouls_per_match", "fouls_per_match")

        def blend(parts: list[tuple[float | None, float]]) -> float | None:
            available = [(value, weight) for value, weight in parts if value is not None]
            total = sum(weight for _, weight in available)
            return sum(value * weight for value, weight in available) / total if total else None

        weights = RISK_SCORE_WEIGHTS
        game_penalty_score = blend([
            (scores["penalty"], weights["penalty"]["referee"]),
            (team_penalty, weights["penalty"]["team"]),
        ])
        yellow_support = min(team_yellow, 55.0) if team_yellow is not None else None
        second_yellow_support = min(team_second_yellow, 55.0) if team_second_yellow is not None else None
        team_dismissal_support = (
            sum(value for value in (yellow_support, second_yellow_support) if value is not None)
            / sum(value is not None for value in (yellow_support, second_yellow_support))
            if yellow_support is not None or second_yellow_support is not None else None
        )
        red_parts = [
            (scores["red"], weights["red"]["referee"]),
            (team_red, weights["red"]["team_red"]),
            (team_dismissal_support, weights["red"]["team_yellow_support"]),
        ]
        # Yellow cards and fouls support red-card risk, but cannot alone imply
        # high red risk. Fouls are added only when a source eventually provides them.
        if team_fouls is not None:
            red_parts.append((min(team_fouls, 55.0), weights["red"]["team_fouls"]))
        game_red_score = blend(red_parts)
        yellow_parts = [(scores["yellow"], weights["yellow"]["referee"]),
                        (team_yellow, weights["yellow"]["team"])]
        if team_fouls is not None:
            yellow_parts.append((team_fouls, weights["yellow"]["team_fouls"]))
        game_yellow_score = blend(yellow_parts)

        def team_coverage(metric: str) -> tuple[bool, int]:
            rows = [row for row in team_rows if isinstance(row, dict) and row.get(metric) is not None]
            return bool(rows), sum(int(_rate(row.get("matches")) or 0) for row in rows)

        pen_team_available, pen_matches = team_coverage("penalties_per_match")
        red_team_available, red_matches = team_coverage("red_cards_per_match")
        yellow_team_available, yellow_matches = team_coverage("yellow_cards_per_match")
        second_yellow_available, second_yellow_matches = team_coverage("second_yellow_cards_per_match")
        foul_team_available, foul_matches = team_coverage("fouls_per_match")

        coverage = {
            "penalty": {
                "referee": scores["penalty"] is not None, "referee_matches": referee_sample,
                "teams": pen_team_available, "team_matches": pen_matches,
                "baseline": baseline.get("penalties_per_match") is not None,
                "baseline_teams": baseline_counts.get("penalties_per_match", 0),
            },
            "red_card": {
                "referee": scores["red"] is not None, "referee_matches": referee_sample,
                "teams": red_team_available, "team_matches": red_matches,
                "second_yellows": second_yellow_available,
                "second_yellow_matches": second_yellow_matches,
                "baseline": baseline.get("red_cards_per_match") is not None,
                "baseline_teams": baseline_counts.get("red_cards_per_match", 0),
            },
            "yellow_card": {
                "referee": scores["yellow"] is not None, "referee_matches": referee_sample,
                "teams": yellow_team_available, "team_matches": yellow_matches,
                "fouls": foul_team_available, "foul_matches": foul_matches,
                "baseline": baseline.get("yellow_cards_per_match") is not None,
                "baseline_teams": baseline_counts.get("yellow_cards_per_match", 0),
            },
        }
        output[game] = {
            "league": {"id": league.get("id"), "name": league.get("name") or "Unknown"},
            "odds": odds,
            "derby": is_derby,
            "stats": {
                "penalty_probability": classify_risk(game_penalty_score),
                "red_card_probability": classify_risk(game_red_score),
                "yellow_card_probability": classify_risk(game_yellow_score),
                "high_yellow_card_tendency": (
                    game_yellow_score >= 65 if game_yellow_score is not None else None
                ),
            },
            "risk_scores": {
                "penalty": round(game_penalty_score, 1) if game_penalty_score is not None else None,
                "red_card": round(game_red_score, 1) if game_red_score is not None else None,
                "yellow_card": round(game_yellow_score, 1) if game_yellow_score is not None else None,
            },
            "evidence_coverage": coverage,
        }
    return output


