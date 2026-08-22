from __future__ import annotations

import argparse
import json
import logging
import math
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

# ============================================================
# Configuration
# ============================================================

GENERAL_WEIGHT = 1.00
H2H_WEIGHT = 1.45

CONFLICT_PENALTY = 45.0
MISSING_H2H_PENALTY = 6.0
MISSING_GENERAL_TEAM_PENALTY = 3.0

H2H_AGREEMENT_BONUS = 12.0
TEAM_AGREEMENT_BONUS = 8.0

FIRST_SCORE_CONCEDE_BONUS = 6.0
FIRST_HALF_RESULT_BONUS = 5.0

CONFLICT_SECONDARY_FACTOR = 0.30
CONFLICT_REMAINDER_FACTOR = 0.10
H2H_CONFLICT_MULTIPLIER = 1.35

RATIO_PRIOR_STRENGTH = 2.0
RATIO_PRIOR_MEAN = 0.50

BILATERAL_CATEGORIES = {
    "goals_ou",
    "cards_ou",
    "corners_ou",
    "btts",
}

TEAM_SPECIFIC_CATEGORIES = {
    "first_to_score",
    "first_to_concede",
    "first_half_winner",
    "first_half_loser",
    "no_losses",
    "wins",
    "losses",
    "no_wins",
    "no_goals_conceded",
    "no_goals_scored",
    "no_clean_sheet",
}


# ============================================================
# Data structures
# ============================================================


@dataclass
class Evidence:
    market: str
    category: str
    direction: str | None
    confidence: float
    section: str
    team: str | None
    raw_value: str
    name: str
    sample_size: int | None = None


@dataclass
class Candidate:
    home: str
    away: str
    market: str
    category: str

    evidences: list[Evidence] = field(default_factory=list)
    conflicting_evidences: list[Evidence] = field(default_factory=list)
    supporting_evidences: list[Evidence] = field(default_factory=list)

    score: float = 0.0
    confidence: float = 0.0
    penalties: list[str] = field(default_factory=list)
    bonuses: list[str] = field(default_factory=list)


# ============================================================
# Parsing
# ============================================================


OU_PATTERN = re.compile(
    r"^(More than|Less than)\s+(\d+(?:\.\d+)?)\s+(goals|corners|cards)$",
    re.IGNORECASE,
)

RATIO_PATTERN = re.compile(r"^(\d+)\s*/\s*(\d+)$")
INTEGER_PATTERN = re.compile(r"^\d+$")


def parse_statistic_value(value: str) -> tuple[float, int | None]:
    """Convert a Sofascore streak value into confidence and sample size."""

    value = value.strip()

    ratio = RATIO_PATTERN.match(value)

    if ratio:
        numerator = int(ratio.group(1))
        denominator = int(ratio.group(2))

        if denominator == 0:
            return 0.0, 0

        adjusted_rate = (numerator + RATIO_PRIOR_MEAN * RATIO_PRIOR_STRENGTH) / (
            denominator + RATIO_PRIOR_STRENGTH
        )

        return max(0.0, min(1.0, adjusted_rate)), denominator

    integer = INTEGER_PATTERN.match(value)

    if integer:
        streak = int(value)

        if streak <= 0:
            return 0.0, streak

        confidence = 0.55 + 0.40 * (1 - math.exp(-streak / 5))
        return min(confidence, 0.95), streak

    return 0.0, None


def parse_market(
    name: str,
    team: str | None,
) -> tuple[str, str, str | None] | None:
    """Convert a Sofascore property into a canonical market definition."""

    name_lower = name.lower().strip()

    match = OU_PATTERN.match(name)

    if match:
        operator = match.group(1).lower()
        threshold = match.group(2)
        category_raw = match.group(3).lower()

        direction = "over" if operator == "more than" else "under"
        category = f"{category_raw}_ou"
        market_id = f"{category_raw}_ou_{threshold}_{direction}"

        return market_id, category, direction

    team_specific = {
        "first to score": "first_to_score",
        "first half winner": "first_half_winner",
        "first to concede": "first_to_concede",
        "first half loser": "first_half_loser",
        "no losses": "no_losses",
        "wins": "wins",
        "losses": "losses",
        "no wins": "no_wins",
        "no goals conceded": "no_goals_conceded",
        "no goals scored": "no_goals_scored",
        "no clean sheet": "no_clean_sheet",
        "without clean sheet": "no_clean_sheet",
    }

    if name_lower in team_specific and team in {"home", "away"}:
        canonical = team_specific[name_lower]
        return f"{canonical}_{team}", canonical, team

    match_markets = {
        "both teams scoring": (
            "btts_yes",
            "btts",
            "yes",
        ),
    }

    if name_lower in match_markets:
        return match_markets[name_lower]

    return None


# ============================================================
# Evidence extraction
# ============================================================


def extract_evidence(
    match_data: dict[str, Any],
) -> list[Evidence]:
    """Extract supported statistics from general and H2H sections."""

    evidence: list[Evidence] = []

    for section_name in ("general", "head2head"):
        stats = match_data.get(section_name, [])

        for stat in stats:
            name = stat.get("name", "").strip()
            value = str(stat.get("value", "")).strip()
            team = stat.get("team")

            parsed = parse_market(name, team)

            if not parsed:
                continue

            market, category, direction = parsed
            confidence, sample_size = parse_statistic_value(value)

            evidence.append(
                Evidence(
                    market=market,
                    category=category,
                    direction=direction,
                    confidence=confidence,
                    section=section_name,
                    team=team,
                    raw_value=value,
                    name=name,
                    sample_size=sample_size,
                )
            )

    return evidence


# ============================================================
# Utility
# ============================================================


def weighted_confidence(evidence: Evidence) -> float:
    """Apply a non-linear weight to evidence confidence."""

    return evidence.confidence**1.5


def market_side(evidence: Evidence) -> str | None:
    """Return the team side encoded in a team-specific market."""

    if evidence.team in {"home", "away"}:
        return evidence.team

    return None


def has_general_team_evidence(
    general: list[Evidence],
    team: str,
) -> bool:
    """Check whether general evidence covers a specific team."""

    return any(e.team in {team, "both"} for e in general)


def is_bilateral_market(candidate: Candidate) -> bool:
    """Return whether a market benefits from evidence from both teams."""

    return candidate.category in BILATERAL_CATEGORIES


# ============================================================
# Candidate generation
# ============================================================


def generate_candidates(
    home: str,
    away: str,
    evidences: list[Evidence],
) -> list[Candidate]:
    """Create one candidate for each supported market direction."""

    candidates = []
    markets: dict[str, list[Evidence]] = {}

    for evidence in evidences:
        markets.setdefault(evidence.market, []).append(evidence)

    for market, supporting in markets.items():
        candidate = Candidate(
            home=home,
            away=away,
            market=market,
            category=supporting[0].category,
            evidences=supporting.copy(),
        )

        for evidence in evidences:
            if evidence in supporting:
                continue

            if are_opposite_markets(supporting[0], evidence):
                candidate.conflicting_evidences.append(evidence)
            elif supporting_relationship(supporting[0], evidence):
                candidate.supporting_evidences.append(evidence)

        candidates.append(candidate)

    return candidates


# ============================================================
# Evidence relationships
# ============================================================


def supporting_relationship(
    candidate_evidence: Evidence,
    other_evidence: Evidence,
) -> bool:
    """Return whether one evidence record directly supports another."""

    candidate_side = market_side(candidate_evidence)
    other_side = market_side(other_evidence)

    if not candidate_side or not other_side:
        return False

    if (
        candidate_evidence.category == "first_to_score"
        and other_evidence.category == "first_to_concede"
    ):
        return candidate_side != other_side

    if (
        candidate_evidence.category == "first_to_concede"
        and other_evidence.category == "first_to_score"
    ):
        return candidate_side != other_side

    if (
        candidate_evidence.category == "first_half_winner"
        and other_evidence.category == "first_half_loser"
    ):
        return candidate_side != other_side

    if (
        candidate_evidence.category == "first_half_loser"
        and other_evidence.category == "first_half_winner"
    ):
        return candidate_side != other_side

    return False


def supporting_evidence_bonus(candidate: Candidate) -> float:
    """Reward complementary evidence that points to the same outcome."""

    relationships: dict[str, float] = {}

    for candidate_evidence in candidate.evidences:
        for other_evidence in candidate.supporting_evidences:
            if not supporting_relationship(candidate_evidence, other_evidence):
                continue

            strength = min(
                weighted_confidence(candidate_evidence),
                weighted_confidence(other_evidence),
            )

            if {
                candidate_evidence.category,
                other_evidence.category,
            } == {"first_to_score", "first_to_concede"}:
                relationship = "first_to_score_concede"
            else:
                relationship = "first_half_result"

            relationships[relationship] = max(
                relationships.get(relationship, 0.0),
                strength,
            )

    bonus = 0.0
    bonus += (
        relationships.get("first_to_score_concede", 0.0) * FIRST_SCORE_CONCEDE_BONUS
    )
    bonus += relationships.get("first_half_result", 0.0) * FIRST_HALF_RESULT_BONUS

    return bonus


def are_opposite_markets(
    a: Evidence,
    b: Evidence,
) -> bool:
    """Return whether two evidence records directly contradict each other."""

    if a.category.endswith("_ou") and b.category == a.category:
        return a.direction != b.direction

    a_side = market_side(a)
    b_side = market_side(b)

    if not a_side or not b_side:
        return False

    if a.category == "first_to_score" and b.category == "first_to_score":
        return a_side != b_side

    if a.category == "first_to_concede" and b.category == "first_to_concede":
        return a_side != b_side

    if a.category == "first_half_winner" and b.category == "first_half_winner":
        return a_side != b_side

    if a.category == "first_half_loser" and b.category == "first_half_loser":
        return a_side != b_side

    logical_opposites = {
        frozenset({"wins", "no_wins"}),
        frozenset({"losses", "no_losses"}),
        frozenset({"no_goals_conceded", "no_clean_sheet"}),
    }

    if frozenset({a.category, b.category}) in logical_opposites:
        return a_side == b_side

    return False


# ============================================================
# Conflict penalties
# ============================================================


def conflict_strengths(candidate: Candidate) -> list[tuple[float, Evidence]]:
    """Calculate weighted strengths for contradictory evidence."""

    conflicts: list[tuple[float, Evidence]] = []
    general = [e for e in candidate.evidences if e.section == "general"]

    for evidence in candidate.conflicting_evidences:
        strength = weighted_confidence(evidence)
        weight = H2H_WEIGHT if evidence.section == "head2head" else GENERAL_WEIGHT
        multiplier = 1.0

        if evidence.section == "head2head":
            has_home_support = any(
                e.team == "home"
                and e.category == candidate.category
                and e.direction == candidate.evidences[0].direction
                for e in general
            )
            has_away_support = any(
                e.team == "away"
                and e.category == candidate.category
                and e.direction == candidate.evidences[0].direction
                for e in general
            )

            if has_home_support and has_away_support:
                multiplier = H2H_CONFLICT_MULTIPLIER

        conflicts.append(
            (
                CONFLICT_PENALTY * strength * weight * multiplier,
                evidence,
            )
        )

    conflicts.sort(key=lambda item: item[0], reverse=True)
    return conflicts


def conflicting_evidence_penalty(candidate: Candidate) -> float:
    """Apply a capped, diminishing penalty for contradictory evidence."""

    conflicts = conflict_strengths(candidate)

    if not conflicts:
        return 0.0

    penalty = 0.0

    for index, (base_penalty, evidence) in enumerate(conflicts):
        if index == 0:
            applied_penalty = base_penalty
        elif index == 1:
            applied_penalty = base_penalty * CONFLICT_SECONDARY_FACTOR
        else:
            applied_penalty = base_penalty * CONFLICT_REMAINDER_FACTOR

        penalty += applied_penalty

        candidate.penalties.append(
            (
                f"{evidence.section} conflict: "
                f"{evidence.name} "
                f"{evidence.raw_value} "
                f"({evidence.team}) "
                f"(-{applied_penalty:.1f})"
            )
        )

    return penalty


# ============================================================
# Scoring
# ============================================================


def score_candidate(candidate: Candidate) -> None:
    """Calculate the final score for a prediction candidate."""

    candidate.score = 0.0
    candidate.bonuses.clear()
    candidate.penalties.clear()

    evidences = candidate.evidences
    general = [e for e in evidences if e.section == "general"]
    h2h = [e for e in evidences if e.section == "head2head"]

    weighted_sum = 0.0
    total_weight = 0.0

    for evidence in evidences:
        weight = H2H_WEIGHT if evidence.section == "head2head" else GENERAL_WEIGHT
        strength = weighted_confidence(evidence)

        weighted_sum += strength * weight
        total_weight += weight

    base_confidence = weighted_sum / total_weight if total_weight else 0.0
    candidate.confidence = base_confidence
    candidate.score = base_confidence * 70

    if general and h2h:
        general_conf = max(weighted_confidence(e) for e in general)
        h2h_conf = max(weighted_confidence(e) for e in h2h)
        agreement_strength = min(general_conf, h2h_conf)
        bonus = H2H_AGREEMENT_BONUS * agreement_strength

        candidate.score += bonus
        candidate.bonuses.append(f"general + H2H agreement (+{bonus:.1f})")

    if general and not h2h:
        strongest_general = max(weighted_confidence(e) for e in general)
        penalty = MISSING_H2H_PENALTY * strongest_general

        candidate.score -= penalty
        candidate.penalties.append(f"no H2H confirmation (-{penalty:.1f})")

    general_home = [e for e in general if e.team == "home"]
    general_away = [e for e in general if e.team == "away"]

    if general_home and general_away:
        best_team_agreement = 0.0

        for home_e in general_home:
            for away_e in general_away:
                if (
                    home_e.category == away_e.category
                    and home_e.direction == away_e.direction
                ):
                    best_team_agreement = max(
                        best_team_agreement,
                        min(
                            weighted_confidence(home_e),
                            weighted_confidence(away_e),
                        ),
                    )

        if best_team_agreement > 0:
            bonus = TEAM_AGREEMENT_BONUS * best_team_agreement
            candidate.score += bonus
            candidate.bonuses.append(f"home + away agreement (+{bonus:.1f})")

    elif is_bilateral_market(candidate) and (general_home or general_away):
        missing_team = "away" if general_home else "home"
        candidate.score -= MISSING_GENERAL_TEAM_PENALTY
        candidate.penalties.append(
            f"missing {missing_team} general confirmation "
            f"(-{MISSING_GENERAL_TEAM_PENALTY:.1f})"
        )

    relationship_bonus = supporting_evidence_bonus(candidate)

    if relationship_bonus > 0:
        candidate.score += relationship_bonus

        candidate.bonuses.append(f"complementary evidence (+{relationship_bonus:.1f})")

    candidate.score -= conflicting_evidence_penalty(candidate)

    candidate.score = max(0.0, candidate.score)


# ============================================================
# Human-readable market
# ============================================================


def display_market(candidate: Candidate) -> str:
    """Convert a canonical candidate into a readable market label."""

    category = candidate.category

    if "_ou" in category:
        parts = candidate.market.split("_")
        sport_type = parts[0]
        threshold = parts[2]
        direction = parts[3]
        operator = "more than" if direction == "over" else "less than"

        return f"{operator} {threshold} {sport_type}"

    if category in TEAM_SPECIFIC_CATEGORIES:
        side = candidate.market.split("_")[-1]
        team_name = candidate.home if side == "home" else candidate.away

        readable = {
            "first_to_score": "first to score",
            "first_to_concede": "first to concede",
            "first_half_winner": "first half winner",
            "first_half_loser": "first half loser",
            "no_losses": "no losses",
            "wins": "wins",
            "losses": "losses",
            "no_wins": "no wins",
            "no_goals_conceded": "no goals conceded",
            "no_goals_scored": "no goals scored",
            "no_clean_sheet": "no clean sheet",
        }

        return f"{team_name} {readable.get(category, category)}"

    if category == "btts":
        return "both teams scoring"

    return candidate.market


# ============================================================
# Main analyzer
# ============================================================


def analyze(
    data: dict[str, dict[str, Any]],
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """Analyze all matches and return the highest-ranked candidates."""

    results = []

    for match_name, match_data in data.items():
        if " - " not in match_name:
            continue

        home, away = match_name.split(" - ", 1)
        evidences = extract_evidence(match_data)
        candidates = generate_candidates(home, away, evidences)

        for candidate in candidates:
            score_candidate(candidate)

            results.append(
                {
                    "home": candidate.home,
                    "away": candidate.away,
                    "market": display_market(candidate),
                    "score": round(candidate.score, 2),
                    "confidence": round(candidate.confidence * 100, 2),
                    "evidence": [
                        {
                            "section": evidence.section,
                            "name": evidence.name,
                            "value": evidence.raw_value,
                            "team": evidence.team,
                        }
                        for evidence in candidate.evidences
                    ],
                    "supporting_evidence": [
                        {
                            "section": evidence.section,
                            "name": evidence.name,
                            "value": evidence.raw_value,
                            "team": evidence.team,
                        }
                        for evidence in candidate.supporting_evidences
                    ],
                    "bonuses": candidate.bonuses,
                    "penalties": candidate.penalties,
                }
            )

    results.sort(
        key=lambda item: (
            item["score"],
            item["confidence"],
        ),
        reverse=True,
    )

    for rank, prediction in enumerate(results[:top_n], start=1):
        prediction["rank"] = rank

    return results[:top_n]


def group_predictions_by_game(
    predictions: list[dict],
) -> dict[str, list[dict]]:
    """Group ranked predictions by match while preserving global order."""

    grouped = OrderedDict()

    for prediction in predictions:
        game = f"{prediction['home']} - {prediction['away']}"
        grouped.setdefault(game, []).append(prediction)

    return dict(
        sorted(
            grouped.items(),
            key=lambda item: item[1][0]["score"],
            reverse=True,
        )
    )


# ============================================================
# Command-line entry point
# ============================================================


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze team streaks data.")
    parser.add_argument(
        "file_path",
        nargs="?",
        help="Path to the team streaks JSON file.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    input_path = Path(args.file_path)
    LOGGER.info("Reading input file: %s", input_path)

    try:
        with input_path.open("r", encoding="utf-8") as streaks_file:
            data = json.load(streaks_file)
    except (OSError, json.JSONDecodeError):
        LOGGER.exception("Could not read or parse input file: %s", input_path)
        raise

    LOGGER.info("Loaded %d matches", len(data))

    predictions = analyze(data, top_n=20)
    LOGGER.info("Generated %d predictions", len(predictions))
    grouped_predictions = group_predictions_by_game(predictions)

    date_match = re.search(r"(\d{8})(?=\.json$)", input_path.name)
    output_date = (
        date_match.group(1) if date_match else datetime.now().strftime("%Y%m%d")
    )

    analysis_history = Path("analysis_history")
    analysis_history.mkdir(parents=True, exist_ok=True)
    output_path = analysis_history / f"analysis_{output_date}.json"
    output_path.write_text(
        json.dumps(grouped_predictions, indent=4),
        encoding="utf-8",
    )
    LOGGER.info("Analysis written to %s", output_path)
