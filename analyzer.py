from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

# ============================================================
# Configuration
# ============================================================

GENERAL_WEIGHT = 1.00
H2H_WEIGHT = 1.45

# How much a very strong contradiction should hurt.
CONFLICT_PENALTY = 45.0

# Small penalty when a strong general statistic has no H2H support.
MISSING_H2H_PENALTY = 6.0

# Extra reward when general + H2H agree.
H2H_AGREEMENT_BONUS = 12.0

# Reward when both teams' general statistics support the same market.
TEAM_AGREEMENT_BONUS = 8.0


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


@dataclass
class Candidate:
    home: str
    away: str
    market: str
    category: str

    evidences: list[Evidence] = field(default_factory=list)
    conflicting_evidences: list[Evidence] = field(default_factory=list)

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


def parse_confidence(value: str) -> float:
    """
    Converts:

        10/10 -> 1.00
        8/10  -> 0.80
        6/7   -> 0.857

    A plain streak:

        10 -> ~0.89
        8  -> ~0.86
        4  -> ~0.75

    A plain number in Sofascore is interpreted as a consecutive
    streak, NOT as '10 out of 10'.
    """

    value = value.strip()

    ratio = RATIO_PATTERN.match(value)

    if ratio:
        numerator = int(ratio.group(1))
        denominator = int(ratio.group(2))

        if denominator == 0:
            return 0.0

        ratio_value = numerator / denominator

        return max(0.0, min(1.0, ratio_value))

    integer = INTEGER_PATTERN.match(value)

    if integer:
        streak = int(value)

        if streak <= 0:
            return 0.0

        # Diminishing return for streak length.
        #
        # 4  -> ~0.75
        # 8  -> ~0.85
        # 10 -> ~0.89
        # 15 -> ~0.98
        confidence = 0.55 + 0.45 * (1 - math.exp(-streak / 5))

        return min(confidence, 0.99)

    return 0.0


def parse_market(name: str, team: str | None) -> tuple[str, str, str | None] | None:
    """
    Returns:

        market_id
        category
        direction

    Examples:

        "More than 2.5 goals"
            -> ("goals_ou_2.5_over", "goals_ou", "over")

        "Less than 4.5 cards"
            -> ("cards_ou_4.5_under", "cards_ou", "under")

        "First to score"
            -> ("first_to_score_home", "first_to_score", "home")

        "No losses"
            -> ("no_losses_home", "team_result", "home")
    """

    name_lower = name.lower().strip()

    # --------------------------------------------------------
    # Over / Under markets
    # --------------------------------------------------------

    match = OU_PATTERN.match(name)

    if match:
        operator = match.group(1).lower()
        threshold = match.group(2)
        category_raw = match.group(3).lower()

        direction = "over" if operator == "more than" else "under"

        category = f"{category_raw}_ou"

        market_id = f"{category_raw}_ou_{threshold}_{direction}"

        return market_id, category, direction

    # --------------------------------------------------------
    # Team-specific markets
    # --------------------------------------------------------

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

    if name_lower in team_specific:
        canonical = team_specific[name_lower]

        if team in {"home", "away"}:
            return (
                f"{canonical}_{team}",
                canonical,
                team,
            )

    # --------------------------------------------------------
    # Match-level markets
    # --------------------------------------------------------

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
# Convert raw JSON into evidence
# ============================================================


def extract_evidence(
    match_data: dict[str, Any],
) -> list[Evidence]:

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

            confidence = parse_confidence(value)

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
                )
            )

    return evidence


# ============================================================
# Utility
# ============================================================


def weighted_confidence(evidence: Evidence) -> float:
    """
    Strongly rewards high percentages.

    100% -> 100%
    90%  -> ~85%
    80%  -> ~72%
    70%  -> ~59%

    This makes 10/10 considerably stronger than 7/10.
    """

    return evidence.confidence**1.5


def same_market(a: Evidence, b: Evidence) -> bool:
    return a.market == b.market


def are_opposites(a: Evidence, b: Evidence) -> bool:

    # Over vs under
    if (
        a.category.endswith("_ou")
        and b.category == a.category
        and a.direction != b.direction
    ):
        return True

    # First to score etc.
    opposite_categories = {
        ("first_to_score", "first_to_concede"),
        ("first_half_winner", "first_half_loser"),
    }

    if (
        a.category,
        b.category,
    ) in opposite_categories:
        return True

    if (
        b.category,
        a.category,
    ) in opposite_categories:
        return True

    return False


# ============================================================
# Candidate generation
# ============================================================


def generate_candidates(
    home: str,
    away: str,
    evidences: list[Evidence],
) -> list[Candidate]:

    candidates = []

    # Each exact market becomes a prediction candidate.
    markets = {}

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

        # Find opposite evidence anywhere in this match.
        for evidence in evidences:

            if evidence in supporting:
                continue

            if are_opposite_markets(
                supporting[0],
                evidence,
            ):
                candidate.conflicting_evidences.append(evidence)

        candidates.append(candidate)

    return candidates


# ============================================================
# Conflicting evidence penalty
# ============================================================


def conflicting_evidence_penalty(
    candidate: Candidate,
) -> float:

    if not candidate.conflicting_evidences:
        return 0.0

    penalty = 0.0

    for evidence in candidate.conflicting_evidences:

        strength = weighted_confidence(evidence)

        weight = H2H_WEIGHT if evidence.section == "head2head" else GENERAL_WEIGHT

        conflict_penalty = 45.0 * strength * weight

        penalty += conflict_penalty

        candidate.penalties.append(
            (
                f"general conflict: "
                f"{evidence.name} "
                f"{evidence.raw_value} "
                f"({evidence.team}) "
                f"(-{conflict_penalty:.1f})"
            )
        )

    return penalty


# ============================================================
# Scoring
# ============================================================


def score_candidate(candidate: Candidate) -> None:

    # Reset everything so repeated scoring cannot duplicate data.
    candidate.score = 0.0
    candidate.bonuses.clear()
    candidate.penalties.clear()

    evidences = candidate.evidences

    general = [e for e in evidences if e.section == "general"]

    h2h = [e for e in evidences if e.section == "head2head"]

    # ========================================================
    # BASE CONFIDENCE
    # ========================================================

    weighted_sum = 0.0
    total_weight = 0.0

    for evidence in evidences:

        weight = H2H_WEIGHT if evidence.section == "head2head" else GENERAL_WEIGHT

        strength = weighted_confidence(evidence)

        weighted_sum += strength * weight
        total_weight += weight

    if total_weight:
        base_confidence = weighted_sum / total_weight
    else:
        base_confidence = 0.0

    candidate.confidence = base_confidence

    # ========================================================
    # START SCORE
    # ========================================================

    # Don't start at 100.
    #
    # This leaves room for bonuses AND penalties.
    candidate.score = base_confidence * 70

    # ========================================================
    # H2H AGREEMENT
    # ========================================================

    if general and h2h:

        general_conf = max(weighted_confidence(e) for e in general)

        h2h_conf = max(weighted_confidence(e) for e in h2h)

        agreement_strength = min(
            general_conf,
            h2h_conf,
        )

        bonus = 18.0 * agreement_strength

        candidate.score += bonus

        candidate.bonuses.append(f"general + H2H agreement (+{bonus:.1f})")

    # ========================================================
    # MISSING H2H
    # ========================================================

    if general and not h2h:

        strongest_general = max(weighted_confidence(e) for e in general)

        penalty = 8.0 * strongest_general

        candidate.score -= penalty

        candidate.penalties.append(f"no H2H confirmation (-{penalty:.1f})")

    # ========================================================
    # HOME + AWAY AGREEMENT
    # ========================================================

    general_home = [e for e in general if e.team == "home"]

    general_away = [e for e in general if e.team == "away"]

    if general_home and general_away:

        # Only compare evidence pointing to the same market.
        for home_e in general_home:

            for away_e in general_away:

                if (
                    home_e.category == away_e.category
                    and home_e.direction == away_e.direction
                ):

                    agreement_strength = min(
                        weighted_confidence(home_e),
                        weighted_confidence(away_e),
                    )

                    bonus = 10.0 * agreement_strength

                    candidate.score += bonus

                    candidate.bonuses.append(f"home + away agreement (+{bonus:.1f})")

    # ========================================================
    # H2H VS GENERAL CONFLICT
    # ========================================================

    h2h_conflict = cross_section_conflict_penalty(candidate)

    candidate.score -= h2h_conflict

    # ========================================================
    # SAME-SECTION CONFLICT
    # ========================================================

    same_section_conflict = contradiction_penalty(candidate)

    candidate.score -= same_section_conflict

    # ========================================================
    # CONFLICTING EVIDENCE
    # ========================================================

    candidate.score -= conflicting_evidence_penalty(candidate)

    # ========================================================
    # FINAL
    # ========================================================

    candidate.score = max(
        0.0,
        candidate.score,
    )


def contradiction_penalty(
    candidate: Candidate,
) -> float:

    evidences = candidate.evidences

    penalty = 0.0

    for i, first in enumerate(evidences):

        for second in evidences[i + 1 :]:

            # Cross-section conflicts are handled separately.
            if first.section != second.section:
                continue

            if not are_opposite_markets(
                first,
                second,
            ):
                continue

            first_strength = weighted_confidence(first)
            second_strength = weighted_confidence(second)

            conflict_strength = min(
                first_strength,
                second_strength,
            )

            pair_penalty = CONFLICT_PENALTY * conflict_strength

            penalty += pair_penalty

            candidate.penalties.append(
                (
                    f"conflict: "
                    f"{first.name} {first.raw_value} "
                    f"vs "
                    f"{second.name} {second.raw_value} "
                    f"(-{pair_penalty:.1f})"
                )
            )

    return penalty

    evidences = candidate.evidences

    penalty = 0.0

    for i, first in enumerate(evidences):

        for second in evidences[i + 1 :]:

            if not are_opposites(first, second):
                continue

            first_strength = weighted_confidence(first)
            second_strength = weighted_confidence(second)

            # ------------------------------------------------
            # Importance of section
            # ------------------------------------------------

            first_weight = (
                H2H_WEIGHT if first.section == "head2head" else GENERAL_WEIGHT
            )

            second_weight = (
                H2H_WEIGHT if second.section == "head2head" else GENERAL_WEIGHT
            )

            # Strongest contradictory evidence matters.
            conflict_strength = min(first_strength, second_strength)

            section_strength = min(first_weight, second_weight)

            pair_penalty = CONFLICT_PENALTY * conflict_strength * section_strength

            penalty += pair_penalty

            candidate.penalties.append(
                (
                    f"conflict: "
                    f"{first.name} {first.raw_value} "
                    f"vs "
                    f"{second.name} {second.raw_value} "
                    f"(-{pair_penalty:.1f})"
                )
            )

    return penalty


# ============================================================
# Human-readable market
# ============================================================


def display_market(
    candidate: Candidate,
) -> str:

    category = candidate.category

    # --------------------------------------------------------
    # Over / under
    # --------------------------------------------------------

    if "_ou" in category:

        parts = candidate.market.split("_")

        # e.g.
        # goals_ou_2.5_over

        sport_type = parts[0]
        threshold = parts[2]
        direction = parts[3]

        operator = "more than" if direction == "over" else "less than"

        return f"{operator} " f"{threshold} " f"{sport_type}"

    # --------------------------------------------------------
    # Team-specific markets
    # --------------------------------------------------------

    if category in {
        "first_to_score",
        "first_half_winner",
        "first_to_concede",
        "first_half_loser",
        "no_losses",
        "wins",
        "losses",
        "no_wins",
        "no_goals_conceded",
        "no_goals_scored",
        "no_clean_sheet",
    }:

        side = candidate.market.split("_")[-1]

        team_name = candidate.home if side == "home" else candidate.away

        readable = {
            "first_to_score": "first to score",
            "first_half_winner": "first half winner",
            "first_to_concede": "first to concede",
            "first_half_loser": "first half loser",
            "no_losses": "no losses",
            "wins": "wins",
            "losses": "losses",
            "no_wins": "no wins",
            "no_goals_conceded": "no goals conceded",
            "no_goals_scored": "no goals scored",
            "no_clean_sheet": "no clean sheet",
        }

        return f"{team_name} " f"{readable.get(category, category)}"

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

    results = []

    for match_name, match_data in data.items():

        if " - " not in match_name:
            continue

        home, away = match_name.split(" - ", 1)

        evidences = extract_evidence(match_data)

        candidates = generate_candidates(
            home,
            away,
            evidences,
        )

        for candidate in candidates:

            score_candidate(candidate)

            results.append(
                {
                    "home": candidate.home,
                    "away": candidate.away,
                    "market": display_market(candidate),
                    # Main ranking metric
                    "score": round(candidate.score, 2),
                    # Underlying statistical confidence
                    "confidence": round(
                        candidate.confidence * 100,
                        2,
                    ),
                    "evidence": [
                        {
                            "section": e.section,
                            "name": e.name,
                            "value": e.raw_value,
                            "team": e.team,
                        }
                        for e in candidate.evidences
                    ],
                    "bonuses": candidate.bonuses,
                    "penalties": candidate.penalties,
                }
            )

    # ========================================================
    # RANKING
    # ========================================================
    #
    # 1. Highest overall score
    # 2. Highest confidence when scores are equal
    #
    results.sort(
        key=lambda x: (
            x["score"],
            x["confidence"],
        ),
        reverse=True,
    )

    # Add ranking position
    for rank, prediction in enumerate(
        results[:top_n],
        start=1,
    ):
        prediction["rank"] = rank

    return results[:top_n]


def cross_section_conflict_penalty(
    candidate: Candidate,
) -> float:

    general = [e for e in candidate.evidences if e.section == "general"]

    h2h = [e for e in candidate.evidences if e.section == "head2head"]

    if not general or not h2h:
        return 0.0

    penalty = 0.0

    for g in general:
        for h in h2h:

            if not are_opposite_markets(g, h):
                continue

            g_strength = weighted_confidence(g)
            h_strength = weighted_confidence(h)

            # H2H gets more importance because it is
            # specifically historical evidence between
            # these two teams.
            h2h_strength = h_strength * H2H_WEIGHT

            # If both teams' general evidence agree,
            # increase the contradiction.
            supporting_general = [
                x
                for x in general
                if x.category == g.category and x.direction == g.direction
            ]

            team_factor = 1.0

            teams = {x.team for x in supporting_general if x.team in {"home", "away"}}

            if teams == {"home", "away"}:
                team_factor = 1.35

            conflict_strength = g_strength * h2h_strength * team_factor

            pair_penalty = 60.0 * conflict_strength

            penalty += pair_penalty

            candidate.penalties.append(
                (
                    "H2H conflict: "
                    f"general {g.name} {g.raw_value} "
                    f"vs H2H {h.name} {h.raw_value} "
                    f"(-{pair_penalty:.1f})"
                )
            )

    return penalty


def are_opposite_markets(
    a: Evidence,
    b: Evidence,
) -> bool:

    # --------------------------------------------------------
    # Over / Under
    # --------------------------------------------------------

    if a.category.endswith("_ou") and b.category == a.category:
        return a.direction != b.direction

    # --------------------------------------------------------
    # First to score
    # --------------------------------------------------------

    if a.category == "first_to_score" and b.category == "first_to_score":
        return (
            a.team in {"home", "away"}
            and b.team in {"home", "away"}
            and a.team != b.team
        )

    # --------------------------------------------------------
    # First-half winner
    # --------------------------------------------------------

    if a.category == "first_half_winner" and b.category == "first_half_winner":
        return (
            a.team in {"home", "away"}
            and b.team in {"home", "away"}
            and a.team != b.team
        )

    return False


from collections import OrderedDict


def group_predictions_by_game(
    predictions: list[dict],
) -> dict[str, list[dict]]:

    grouped = OrderedDict()

    for prediction in predictions:
        game = f"{prediction['home']} - " f"{prediction['away']}"

        grouped.setdefault(game, []).append(prediction)

    # Keep games ordered by the best prediction
    grouped = dict(
        sorted(
            grouped.items(),
            key=lambda item: item[1][0]["score"],
            reverse=True,
        )
    )

    return grouped


# ============================================================
# Example
# ============================================================

if __name__ == "__main__":

    with open("streaks.json", "r", encoding="utf-8") as streaks_file:
        data = json.load(streaks_file)

    predictions = analyze(data, top_n=20)

    grouped_predictions = group_predictions_by_game(predictions)

    print(json.dumps(grouped_predictions, indent=4))
