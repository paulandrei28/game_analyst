from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

# ============================================================
# Configuration
# ============================================================

# Section weights are used throughout scoring, agreements,
# relationship strength and conflict penalties.
GENERAL_WEIGHT = 1.00
H2H_WEIGHT = 1.00

# Direct contradiction penalty.
PENALTY_MINOR = 20.0
PENALTY_MAJOR = 50.0

CONFLICT_PENALTY = PENALTY_MAJOR
CONFLICT_SECONDARY_FACTOR = 0.30
CONFLICT_REMAINDER_FACTOR = 0.10
H2H_CONFLICT_MULTIPLIER = 1.35

# ------------------------------------------------------------
# Bonus tiers.
#
# Every relationship/agreement bonus below resolves to one of these
# four tidy values. The tier reflects how directly the supporting
# evidence confirms the candidate outcome:
#   WEAK    - a same-direction but distinct signal (e.g. a nearby
#             O/U threshold, or one side of a stability pattern)
#   MEDIUM  - a single complementary relationship between two stats
#   STRONG  - a well-established direct/equivalent relationship, or
#             general+H2H agreement
#   MAJOR   - a strong two-sided pattern where both teams line up
#             the same way (BTTS, both-clean-sheet, etc.)
#
# Named constants below keep their descriptive name at each call
# site for readability; their magnitude always comes from a tier.
# ------------------------------------------------------------

BONUS_WEAK = 5.0
BONUS_MEDIUM = 10.0
BONUS_STRONG = 15.0
BONUS_MAJOR = 20.0

# Positive confirmation bonuses.
H2H_AGREEMENT_BONUS = BONUS_STRONG
TEAM_AGREEMENT_BONUS = BONUS_MEDIUM

# Direct/equivalent relationship weights.
FIRST_SCORE_CONCEDE_BONUS = BONUS_STRONG
FIRST_HALF_RESULT_BONUS = BONUS_MEDIUM
WIN_LOSS_BONUS = BONUS_MEDIUM
NO_GOAL_CONCEDED_SCORED_BONUS = BONUS_MEDIUM

# Underlying-event relationship weights.
NO_CLEAN_SHEET_SCORING_BONUS = BONUS_MEDIUM
BOTH_NO_CLEAN_SHEET_BTTS_BONUS = BONUS_MAJOR
BOTH_CLEAN_SHEET_UNDER_BONUS = BONUS_MAJOR
BOTH_NO_GOALS_SCORED_UNDER_BONUS = BONUS_MAJOR
CLEAN_SHEET_PLUS_NO_SCORING_UNDER_BONUS = BONUS_MAJOR
SCORING_PATTERN_BTTS_BONUS = BONUS_MEDIUM
SCORING_PATTERN_OVER_BONUS = BONUS_MEDIUM
FIRST_SCORE_FIRST_HALF_BONUS = BONUS_MEDIUM
NO_LOSS_WIN_BONUS = BONUS_WEAK
NO_WIN_LOSS_BONUS = BONUS_WEAK

# Different threshold, same-direction evidence is useful, but weaker
# than an exact same-market confirmation.
THRESHOLD_CONSISTENCY_BONUS = BONUS_WEAK
MAX_THRESHOLD_DISTANCE = 1.5

# How repeated relationships accumulate. There is intentionally no
# hard cap; independent supporting relationships have diminishing returns.
RELATIONSHIP_DIMINISHING_FACTORS = (
    1.00,
    0.65,
    0.40,
    0.25,
    0.15,
    0.10,
)

# Relationship conflicts are softer than direct market contradictions.
RELATIONSHIP_CONFLICT_PENALTY = PENALTY_MINOR

# Evidence score multiplier. Score is intentionally uncapped.
EVIDENCE_SCORE_MULTIPLIER = 30.0

# Sample size influence. Larger streaks get more weight, while the
# logarithm prevents very large samples from dominating linearly.
SAMPLE_SIZE_WEIGHT = 0.35

RATIO_PRIOR_STRENGTH = 2.0
RATIO_PRIOR_MEAN = 0.50

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


@dataclass(frozen=True)
class RelationshipMatch:
    name: str
    weight: float
    evidences: tuple[Evidence, ...]


# ============================================================

OU_PATTERN = re.compile(
    r"^(More than|Less than)\s+(\d+(?:\.\d+)?)\s+(goals|corners|cards)$", re.IGNORECASE
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


def extract_evidence(
    match_data: dict[str, Any],
) -> list[Evidence]:
    """Extract supported statistics from general and H2H sections."""

    evidence: list[Evidence] = []

    for section_name in ("general", "head2head"):
        stats = match_data.get(section_name, [])

        for stat in stats:
            name = str(stat.get("name", "")).strip()
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

    return deduplicate_evidence(evidence)


def evidence_key(evidence: Evidence) -> tuple:
    """Return the semantic identity of an evidence record.

    The display name is intentionally excluded because Sofascore can expose
    the same canonical statistic as "No clean sheet" and "Without clean sheet".
    """

    return (
        evidence.market,
        evidence.category,
        evidence.direction,
        evidence.section,
        evidence.team,
        evidence.raw_value,
    )


def deduplicate_evidence(evidences: list[Evidence]) -> list[Evidence]:
    """Remove exact semantic duplicates while preserving first-seen order."""

    unique: list[Evidence] = []
    seen: set[tuple] = set()

    for evidence in evidences:
        key = evidence_key(evidence)
        if key in seen:
            continue
        seen.add(key)
        unique.append(evidence)

    return unique


def section_weight(evidence: Evidence) -> float:
    """Return the configured weight for an evidence source."""

    return H2H_WEIGHT if evidence.section == "head2head" else GENERAL_WEIGHT


def sample_weight(evidence: Evidence) -> float:
    """Weight evidence by the number of matches represented by the streak."""

    if not evidence.sample_size or evidence.sample_size <= 0:
        return 1.0

    return 1.0 + SAMPLE_SIZE_WEIGHT * math.log1p(evidence.sample_size)


def weighted_confidence(evidence: Evidence) -> float:
    """Apply non-linear confidence, sample-size and section weighting."""

    return evidence.confidence**1.5 * sample_weight(evidence) * section_weight(evidence)


def confidence_weight(evidence: Evidence) -> float:
    """Weight used when calculating the reported confidence percentage."""

    return sample_weight(evidence) * section_weight(evidence)


def market_side(evidence: Evidence) -> str | None:
    """Return the team side encoded in a team-specific market."""

    if evidence.team in {"home", "away"}:
        return evidence.team

    return None


def candidate_side(candidate: Candidate) -> str | None:
    """Return the side encoded in a team-specific candidate."""

    if candidate.category in TEAM_SPECIFIC_CATEGORIES:
        side = candidate.market.rsplit("_", 1)[-1]
        if side in {"home", "away"}:
            return side

    return None


def candidate_threshold(candidate: Candidate) -> float | None:
    """Extract an O/U threshold from a canonical market."""

    if candidate.category not in {
        "goals_ou",
        "cards_ou",
        "corners_ou",
    }:
        return None

    parts = candidate.market.split("_")

    try:
        return float(parts[2])
    except (IndexError, ValueError):
        return None


def evidence_for_team(
    evidences: list[Evidence],
    category: str,
    team: str,
) -> list[Evidence]:
    """Return evidence for a specific team/category."""

    return [
        evidence
        for evidence in evidences
        if evidence.category == category and evidence.team == team
    ]


def strongest_evidence(
    evidences: list[Evidence],
) -> Evidence | None:
    """Return the strongest evidence record according to effective strength."""

    return max(evidences, key=weighted_confidence, default=None)


def generate_candidates(
    home: str,
    away: str,
    evidences: list[Evidence],
    enabled_markets: set[str] | frozenset[str] | None = None,
) -> list[Candidate]:
    """Create one candidate for each supported market direction."""

    candidates = []
    markets: dict[str, list[Evidence]] = {}

    for evidence in evidences:
        markets.setdefault(evidence.market, []).append(evidence)

    for market, supporting in markets.items():
        if (
            enabled_markets is not None
            and supporting[0].category not in enabled_markets
        ):
            continue
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

        relationship_matches = deduplicate_relationship_matches(
            find_relationship_matches(candidate, evidences)
        )

        seen_supporting_keys: set[tuple] = set()

        for match in relationship_matches:
            for evidence in match.evidences:
                key = evidence_key(evidence)
                if evidence in candidate.evidences or key in seen_supporting_keys:
                    continue

                seen_supporting_keys.add(key)
                candidate.supporting_evidences.append(evidence)

        for evidence in find_relationship_conflicts(candidate, evidences):
            if (
                evidence not in candidate.evidences
                and evidence not in candidate.conflicting_evidences
            ):
                candidate.conflicting_evidences.append(evidence)

        candidates.append(candidate)

    return candidates


def opposite_side(side: str) -> str:
    return "away" if side == "home" else "home"


def add_pair_relationship(
    matches: list[RelationshipMatch],
    name: str,
    weight: float,
    first: Evidence | None,
    second: Evidence | None,
) -> None:
    """Append a pair relationship when both evidence records exist."""

    if first is None or second is None:
        return

    matches.append(
        RelationshipMatch(
            name=name,
            weight=weight,
            evidences=(first, second),
        )
    )


def pair_combinations(
    left: list[Evidence],
    right: list[Evidence],
) -> list[tuple[Evidence, Evidence]]:
    """Return all pair combinations from two evidence groups."""

    return [(first, second) for first in left for second in right]


def find_relationship_matches(
    candidate: Candidate,
    all_evidences: list[Evidence],
) -> list[RelationshipMatch]:
    """Find evidence relationships that support a candidate's outcome."""

    matches: list[RelationshipMatch] = []
    side = candidate_side(candidate)

    # --------------------------------------------------------
    # Direct complementary relationships.
    # --------------------------------------------------------

    if (
        candidate.category
        in {
            "first_to_score",
            "first_to_concede",
        }
        and side
    ):
        opponent = opposite_side(side)
        opponent_first_concede = strongest_evidence(
            evidence_for_team(all_evidences, "first_to_concede", opponent)
        )

        if candidate.category == "first_to_score":
            add_pair_relationship(
                matches,
                "first to score + opponent first to concede",
                FIRST_SCORE_CONCEDE_BONUS,
                strongest_evidence(
                    evidence_for_team(all_evidences, "first_to_score", side)
                ),
                opponent_first_concede,
            )
        else:
            add_pair_relationship(
                matches,
                "first to concede + opponent first to score",
                FIRST_SCORE_CONCEDE_BONUS,
                strongest_evidence(
                    evidence_for_team(all_evidences, "first_to_concede", side)
                ),
                strongest_evidence(
                    evidence_for_team(all_evidences, "first_to_score", opponent)
                ),
            )

    if candidate.category in {"wins", "losses"} and side:
        opponent = opposite_side(side)

        if candidate.category == "wins":
            add_pair_relationship(
                matches,
                "win + opponent loss",
                WIN_LOSS_BONUS,
                strongest_evidence(evidence_for_team(all_evidences, "wins", side)),
                strongest_evidence(
                    evidence_for_team(all_evidences, "losses", opponent)
                ),
            )
        else:
            add_pair_relationship(
                matches,
                "loss + opponent win",
                WIN_LOSS_BONUS,
                strongest_evidence(evidence_for_team(all_evidences, "losses", side)),
                strongest_evidence(evidence_for_team(all_evidences, "wins", opponent)),
            )

    if candidate.category in {"no_goals_conceded", "no_goals_scored"} and side:
        opponent = opposite_side(side)

        if candidate.category == "no_goals_conceded":
            add_pair_relationship(
                matches,
                "clean-sheet tendency + opponent no scoring",
                NO_GOAL_CONCEDED_SCORED_BONUS,
                strongest_evidence(
                    evidence_for_team(all_evidences, "no_goals_conceded", side)
                ),
                strongest_evidence(
                    evidence_for_team(all_evidences, "no_goals_scored", opponent)
                ),
            )
        else:
            add_pair_relationship(
                matches,
                "no scoring + opponent clean-sheet tendency",
                NO_GOAL_CONCEDED_SCORED_BONUS,
                strongest_evidence(
                    evidence_for_team(all_evidences, "no_goals_scored", side)
                ),
                strongest_evidence(
                    evidence_for_team(all_evidences, "no_goals_conceded", opponent)
                ),
            )

    # --------------------------------------------------------
    # First-half/result relationships.
    # --------------------------------------------------------

    if candidate.category == "first_half_winner" and side:
        opponent = opposite_side(side)

        add_pair_relationship(
            matches,
            "first-half winner + first to score",
            FIRST_SCORE_FIRST_HALF_BONUS,
            strongest_evidence(
                evidence_for_team(all_evidences, "first_half_winner", side)
            ),
            strongest_evidence(
                evidence_for_team(all_evidences, "first_to_score", side)
            ),
        )

        add_pair_relationship(
            matches,
            "first-half winner + opponent first to concede",
            FIRST_HALF_RESULT_BONUS,
            strongest_evidence(
                evidence_for_team(all_evidences, "first_half_winner", side)
            ),
            strongest_evidence(
                evidence_for_team(all_evidences, "first_to_concede", opponent)
            ),
        )

    if candidate.category == "first_to_score" and side:
        opponent = opposite_side(side)

        add_pair_relationship(
            matches,
            "first to score + first-half winner",
            FIRST_SCORE_FIRST_HALF_BONUS,
            strongest_evidence(
                evidence_for_team(all_evidences, "first_to_score", side)
            ),
            strongest_evidence(
                evidence_for_team(all_evidences, "first_half_winner", side)
            ),
        )

    # --------------------------------------------------------
    # Result stability relationships.
    # --------------------------------------------------------

    if candidate.category == "wins" and side:
        add_pair_relationship(
            matches,
            "win + no losses",
            NO_LOSS_WIN_BONUS,
            strongest_evidence(evidence_for_team(all_evidences, "wins", side)),
            strongest_evidence(evidence_for_team(all_evidences, "no_losses", side)),
        )

    if candidate.category == "no_losses" and side:
        add_pair_relationship(
            matches,
            "no losses + wins",
            NO_LOSS_WIN_BONUS,
            strongest_evidence(evidence_for_team(all_evidences, "no_losses", side)),
            strongest_evidence(evidence_for_team(all_evidences, "wins", side)),
        )

    if candidate.category == "losses" and side:
        add_pair_relationship(
            matches,
            "losses + no wins",
            NO_WIN_LOSS_BONUS,
            strongest_evidence(evidence_for_team(all_evidences, "losses", side)),
            strongest_evidence(evidence_for_team(all_evidences, "no_wins", side)),
        )

    if candidate.category == "no_wins" and side:
        add_pair_relationship(
            matches,
            "no wins + losses",
            NO_WIN_LOSS_BONUS,
            strongest_evidence(evidence_for_team(all_evidences, "no_wins", side)),
            strongest_evidence(evidence_for_team(all_evidences, "losses", side)),
        )

    # --------------------------------------------------------
    # Scoring / clean-sheet relationships.
    # --------------------------------------------------------

    if candidate.category == "first_to_score" and side:
        opponent = opposite_side(side)

        add_pair_relationship(
            matches,
            "opponent no clean sheet supports scoring first",
            NO_CLEAN_SHEET_SCORING_BONUS,
            strongest_evidence(
                evidence_for_team(all_evidences, "first_to_score", side)
            ),
            strongest_evidence(
                evidence_for_team(all_evidences, "no_clean_sheet", opponent)
            ),
        )

    if candidate.category == "no_clean_sheet" and side:
        opponent = opposite_side(side)

        add_pair_relationship(
            matches,
            "opponent first to score supports no clean sheet",
            NO_CLEAN_SHEET_SCORING_BONUS,
            strongest_evidence(
                evidence_for_team(all_evidences, "no_clean_sheet", side)
            ),
            strongest_evidence(
                evidence_for_team(all_evidences, "first_to_score", opponent)
            ),
        )

    # --------------------------------------------------------
    # BTTS relationships.
    # --------------------------------------------------------

    if candidate.category == "btts":
        home_no_clean = evidence_for_team(all_evidences, "no_clean_sheet", "home")
        away_no_clean = evidence_for_team(all_evidences, "no_clean_sheet", "away")

        for home_evidence, away_evidence in pair_combinations(
            home_no_clean, away_no_clean
        ):
            matches.append(
                RelationshipMatch(
                    name="both teams no clean sheet → BTTS",
                    weight=BOTH_NO_CLEAN_SHEET_BTTS_BONUS,
                    evidences=(home_evidence, away_evidence),
                )
            )

        home_first_score = evidence_for_team(all_evidences, "first_to_score", "home")
        away_first_score = evidence_for_team(all_evidences, "first_to_score", "away")

        for home_evidence, away_evidence in pair_combinations(
            home_first_score, away_first_score
        ):
            matches.append(
                RelationshipMatch(
                    name="both teams first to score patterns → BTTS",
                    weight=SCORING_PATTERN_BTTS_BONUS,
                    evidences=(home_evidence, away_evidence),
                )
            )

        home_score = evidence_for_team(all_evidences, "first_to_score", "home")
        away_score = evidence_for_team(all_evidences, "first_to_score", "away")
        home_concede = evidence_for_team(all_evidences, "first_to_concede", "home")
        away_concede = evidence_for_team(all_evidences, "first_to_concede", "away")

        for (
            home_evidence,
            away_evidence,
            home_concede_evidence,
            away_concede_evidence,
        ) in [
            (a, b, c, d)
            for a in home_score
            for b in away_score
            for c in home_concede
            for d in away_concede
        ]:
            matches.append(
                RelationshipMatch(
                    name="both teams first-score + first-concede patterns → BTTS",
                    weight=SCORING_PATTERN_BTTS_BONUS * 1.25,
                    evidences=(
                        home_evidence,
                        away_evidence,
                        home_concede_evidence,
                        away_concede_evidence,
                    ),
                )
            )

    # --------------------------------------------------------
    # Goal total relationships.
    # --------------------------------------------------------

    if candidate.category == "goals_ou":
        threshold = candidate_threshold(candidate)

        if threshold is not None and candidate.evidences[0].direction == "under":
            home_clean = evidence_for_team(all_evidences, "no_goals_conceded", "home")
            away_clean = evidence_for_team(all_evidences, "no_goals_conceded", "away")
            home_no_score = evidence_for_team(all_evidences, "no_goals_scored", "home")
            away_no_score = evidence_for_team(all_evidences, "no_goals_scored", "away")

            threshold_factor = (
                1.0 if threshold <= 2.5 else 0.75 if threshold <= 3.5 else 0.50
            )

            for home_evidence, away_evidence in pair_combinations(
                home_clean, away_clean
            ):
                matches.append(
                    RelationshipMatch(
                        name="both teams clean-sheet tendencies → under goals",
                        weight=BOTH_CLEAN_SHEET_UNDER_BONUS * threshold_factor,
                        evidences=(home_evidence, away_evidence),
                    )
                )

            for home_evidence, away_evidence in pair_combinations(
                home_no_score, away_no_score
            ):
                matches.append(
                    RelationshipMatch(
                        name="both teams no scoring tendencies → under goals",
                        weight=BOTH_NO_GOALS_SCORED_UNDER_BONUS * threshold_factor,
                        evidences=(home_evidence, away_evidence),
                    )
                )

            for home_evidence, away_evidence in pair_combinations(
                home_clean, away_no_score
            ):
                matches.append(
                    RelationshipMatch(
                        name="home clean-sheet + away no-scoring tendencies → under goals",
                        weight=CLEAN_SHEET_PLUS_NO_SCORING_UNDER_BONUS
                        * threshold_factor,
                        evidences=(home_evidence, away_evidence),
                    )
                )

            for home_evidence, away_evidence in pair_combinations(
                away_clean, home_no_score
            ):
                matches.append(
                    RelationshipMatch(
                        name="away clean-sheet + home no-scoring tendencies → under goals",
                        weight=CLEAN_SHEET_PLUS_NO_SCORING_UNDER_BONUS
                        * threshold_factor,
                        evidences=(home_evidence, away_evidence),
                    )
                )

        elif threshold is not None and candidate.evidences[0].direction == "over":
            home_no_clean = evidence_for_team(all_evidences, "no_clean_sheet", "home")
            away_no_clean = evidence_for_team(all_evidences, "no_clean_sheet", "away")
            home_first_score = evidence_for_team(
                all_evidences, "first_to_score", "home"
            )
            away_first_score = evidence_for_team(
                all_evidences, "first_to_score", "away"
            )

            if threshold <= 1.5:
                threshold_factor = 1.0
            elif threshold <= 2.5:
                threshold_factor = 0.70
            elif threshold <= 3.5:
                threshold_factor = 0.40
            else:
                threshold_factor = 0.20

            for home_evidence, away_evidence in pair_combinations(
                home_no_clean, away_no_clean
            ):
                matches.append(
                    RelationshipMatch(
                        name="both teams no-clean-sheet tendencies → over goals",
                        weight=SCORING_PATTERN_OVER_BONUS * threshold_factor,
                        evidences=(home_evidence, away_evidence),
                    )
                )

            for home_evidence, away_evidence in pair_combinations(
                home_first_score, away_first_score
            ):
                matches.append(
                    RelationshipMatch(
                        name="both teams first-score tendencies → over goals",
                        weight=SCORING_PATTERN_OVER_BONUS * threshold_factor,
                        evidences=(home_evidence, away_evidence),
                    )
                )

    # --------------------------------------------------------
    # Cross-threshold consistency for goals/cards/corners.
    # --------------------------------------------------------

    if candidate.category.endswith("_ou"):
        candidate_threshold_value = candidate_threshold(candidate)

        if candidate_threshold_value is not None:
            for evidence in all_evidences:
                if evidence.category != candidate.category:
                    continue
                if evidence.direction != candidate.evidences[0].direction:
                    continue
                if evidence in candidate.evidences:
                    continue

                evidence_threshold = _evidence_threshold(evidence)
                if evidence_threshold is None:
                    continue

                distance = abs(evidence_threshold - candidate_threshold_value)
                if 0 < distance <= MAX_THRESHOLD_DISTANCE:
                    strength_factor = 1.0 - (distance / MAX_THRESHOLD_DISTANCE) * 0.5
                    matches.append(
                        RelationshipMatch(
                            name="same-direction nearby threshold confirmation",
                            weight=THRESHOLD_CONSISTENCY_BONUS * strength_factor,
                            evidences=(evidence,),
                        )
                    )

    return [
        match
        for match in matches
        if not all(evidence in candidate.evidences for evidence in match.evidences)
    ]


def _evidence_threshold(evidence: Evidence) -> float | None:
    """Extract an O/U threshold from a single evidence market."""

    if not evidence.category.endswith("_ou"):
        return None

    parts = evidence.market.split("_")

    try:
        return float(parts[2])
    except (IndexError, ValueError):
        return None


def relationship_strength(match: RelationshipMatch) -> float:
    """Calculate the strength of a relationship from its evidence."""

    strengths = [weighted_confidence(evidence) for evidence in match.evidences]

    if not strengths:
        return 0.0

    return min(strengths) * match.weight


def relationship_group_key(match: RelationshipMatch) -> tuple:
    """Group semantically duplicate relationship matches."""

    participant_teams = tuple(
        sorted(
            {
                evidence.team
                for evidence in match.evidences
                if evidence.team in {"home", "away"}
            }
        )
    )

    if len(match.evidences) == 1:
        evidence = match.evidences[0]
        return (
            match.name,
            participant_teams,
            evidence.market,
            evidence.category,
            evidence.direction,
        )

    return (
        match.name,
        participant_teams,
        tuple(
            sorted(
                {
                    (
                        evidence.market,
                        evidence.category,
                        evidence.direction,
                    )
                    for evidence in match.evidences
                }
            )
        ),
    )


def deduplicate_relationship_matches(
    matches: list[RelationshipMatch],
) -> list[RelationshipMatch]:
    """Keep the strongest match for each logical relationship."""

    best: dict[tuple, tuple[float, RelationshipMatch]] = {}

    for match in matches:
        strength = relationship_strength(match)
        key = relationship_group_key(match)

        existing = best.get(key)
        if existing is None or strength > existing[0]:
            best[key] = (strength, match)

    return [
        item[1]
        for item in sorted(
            best.values(),
            key=lambda item: item[0],
            reverse=True,
        )
    ]


def supporting_evidence_bonus(
    candidate: Candidate,
    all_evidences: list[Evidence] | None = None,
) -> float:
    """Reward complementary evidence with diminishing returns and no hard cap."""

    evidences = all_evidences if all_evidences is not None else candidate.evidences
    matches = deduplicate_relationship_matches(
        find_relationship_matches(candidate, evidences)
    )

    if not matches:
        return 0.0

    candidate.supporting_evidences.clear()
    seen_supporting_keys: set[tuple] = set()

    for match in matches:
        for evidence in match.evidences:
            if evidence in candidate.evidences:
                continue

            key = evidence_key(evidence)
            if key in seen_supporting_keys:
                continue

            seen_supporting_keys.add(key)
            candidate.supporting_evidences.append(evidence)

    scored_matches = [(relationship_strength(match), match) for match in matches]
    scored_matches.sort(key=lambda item: item[0], reverse=True)

    total_bonus = 0.0
    for index, (base_bonus, match) in enumerate(scored_matches):
        factor = RELATIONSHIP_DIMINISHING_FACTORS[
            min(index, len(RELATIONSHIP_DIMINISHING_FACTORS) - 1)
        ]
        applied_bonus = base_bonus * factor
        total_bonus += applied_bonus

        candidate.bonuses.append(f"{match.name} (+{applied_bonus:.1f})")

    return total_bonus


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


def find_relationship_conflicts(
    candidate: Candidate,
    evidences: list[Evidence],
) -> list[Evidence]:
    """Find strong underlying-event contradictions not captured by market pairs."""

    conflicts: list[Evidence] = []
    side = candidate_side(candidate)

    if candidate.category == "btts":
        conflicts.extend(
            evidence
            for evidence in evidences
            if evidence.category in {"no_goals_scored", "no_goals_conceded"}
            and evidence.team in {"home", "away"}
        )

    if candidate.category == "first_to_score" and side:
        opponent = opposite_side(side)

        conflicts.extend(
            evidence
            for evidence in evidences
            if (
                (evidence.category == "no_goals_scored" and evidence.team == side)
                or (
                    evidence.category == "no_goals_conceded"
                    and evidence.team == opponent
                )
            )
        )

    if candidate.category == "first_half_winner" and side:
        opponent = opposite_side(side)

        conflicts.extend(
            evidence
            for evidence in evidences
            if (evidence.category == "first_half_loser" and evidence.team == side)
            or (evidence.category == "first_half_winner" and evidence.team == opponent)
        )

    if candidate.category == "goals_ou":
        threshold = candidate_threshold(candidate)

        home_no_clean = evidence_for_team(evidences, "no_clean_sheet", "home")
        away_no_clean = evidence_for_team(evidences, "no_clean_sheet", "away")
        home_clean = evidence_for_team(evidences, "no_goals_conceded", "home")
        away_clean = evidence_for_team(evidences, "no_goals_conceded", "away")
        home_no_score = evidence_for_team(evidences, "no_goals_scored", "home")
        away_no_score = evidence_for_team(evidences, "no_goals_scored", "away")

        if candidate.evidences[0].direction == "under" and threshold is not None:
            if threshold <= 2.5 and home_no_clean and away_no_clean:
                conflicts.extend(home_no_clean + away_no_clean)

        if candidate.evidences[0].direction == "over" and threshold is not None:
            if threshold >= 2.5:
                conflicts.extend(home_clean + away_clean)
                conflicts.extend(home_no_score + away_no_score)

    unique: list[Evidence] = []
    seen: set[int] = set()

    for evidence in conflicts:
        marker = id(evidence)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(evidence)

    return unique


def conflict_strengths(candidate: Candidate) -> list[tuple[float, Evidence, str]]:
    """Calculate weighted strengths for contradictory evidence."""

    conflicts: list[tuple[float, Evidence, str]] = []
    general = [e for e in candidate.evidences if e.section == "general"]

    for evidence in candidate.conflicting_evidences:
        strength = weighted_confidence(evidence)
        penalty_factor = CONFLICT_PENALTY
        description = "direct conflict"

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
                penalty_factor *= H2H_CONFLICT_MULTIPLIER

        is_relationship_conflict = evidence in find_relationship_conflicts(
            candidate, candidate.evidences + candidate.conflicting_evidences
        )

        if is_relationship_conflict and not are_opposite_markets(
            candidate.evidences[0], evidence
        ):
            penalty_factor = RELATIONSHIP_CONFLICT_PENALTY
            description = "underlying-event conflict"

        conflicts.append(
            (
                penalty_factor * strength,
                evidence,
                description,
            )
        )

    conflicts.sort(key=lambda item: item[0], reverse=True)
    return conflicts


def conflicting_evidence_penalty(candidate: Candidate) -> float:
    """Apply diminishing penalties for direct and underlying-event conflicts."""

    conflicts = conflict_strengths(candidate)

    if not conflicts:
        return 0.0

    penalty = 0.0

    for index, (base_penalty, evidence, description) in enumerate(conflicts):
        if index == 0:
            applied_penalty = base_penalty
        elif index == 1:
            applied_penalty = base_penalty * CONFLICT_SECONDARY_FACTOR
        else:
            applied_penalty = base_penalty * CONFLICT_REMAINDER_FACTOR

        penalty += applied_penalty

        candidate.penalties.append(
            (
                f"{evidence.section} {description}: "
                f"{evidence.name} "
                f"{evidence.raw_value} "
                f"({evidence.team}) "
                f"(-{applied_penalty:.1f})"
            )
        )

    return penalty


def calculate_base_confidence(evidences: list[Evidence]) -> float:
    """Calculate the reported confidence percentage from direct evidence."""

    if not evidences:
        return 0.0

    weighted_sum = sum(
        evidence.confidence * confidence_weight(evidence) for evidence in evidences
    )
    total_weight = sum(confidence_weight(evidence) for evidence in evidences)

    if total_weight <= 0:
        return 0.0

    return max(0.0, min(1.0, weighted_sum / total_weight))


def score_candidate(
    candidate: Candidate,
    all_evidences: list[Evidence] | None = None,
) -> None:
    """Calculate the final uncapped score and confidence for a prediction."""

    candidate.score = 0.0
    candidate.bonuses.clear()
    candidate.penalties.clear()

    evidences = all_evidences if all_evidences is not None else candidate.evidences
    general = [e for e in candidate.evidences if e.section == "general"]
    h2h = [e for e in candidate.evidences if e.section == "head2head"]

    candidate.score = sum(
        weighted_confidence(evidence) * EVIDENCE_SCORE_MULTIPLIER
        for evidence in candidate.evidences
    )
    candidate.confidence = calculate_base_confidence(candidate.evidences)

    if general and h2h:
        general_strength = max(weighted_confidence(e) for e in general)
        h2h_strength = max(weighted_confidence(e) for e in h2h)
        agreement_strength = min(general_strength, h2h_strength)
        bonus = H2H_AGREEMENT_BONUS * agreement_strength

        candidate.score += bonus
        candidate.bonuses.append(f"general + H2H agreement (+{bonus:.1f})")

    general_home = [
        e for e in candidate.evidences if e.section == "general" and e.team == "home"
    ]
    general_away = [
        e for e in candidate.evidences if e.section == "general" and e.team == "away"
    ]

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

    relationship_bonus = supporting_evidence_bonus(candidate, evidences)

    if relationship_bonus > 0:
        candidate.score += relationship_bonus

    candidate.score -= conflicting_evidence_penalty(candidate)


def prediction_value(candidate: Candidate) -> float:
    """Combine uncapped score with confidence percentage."""

    return candidate.score * candidate.confidence


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
        side = candidate.market.rsplit("_", 1)[-1]
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


class Analyzer:
    """Reusable analyzer for extracting evidence and ranking predictions."""

    def __init__(
        self,
        data: dict[str, dict[str, Any]] | None = None,
        enabled_markets: tuple[str, ...] | set[str] | None = None,
    ):
        self.data = data or {}
        self.enabled_markets = None if enabled_markets is None else set(enabled_markets)
        self.threshold_excluded_count = 0

    def analyze(
        self,
        data: dict[str, dict[str, Any]] | None = None,
        top_n: int = 10,
        prediction_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """Analyze all matches and return the highest-ranked candidates."""
        if prediction_threshold is not None and prediction_threshold < 0:
            raise ValueError("prediction_threshold must be non-negative")

        source = self.data if data is None else data
        self.threshold_excluded_count = 0
        results = []

        for match_name, match_data in source.items():
            if " - " not in match_name:
                continue
            home, away = match_name.split(" - ", 1)
            evidences = extract_evidence(match_data)
            candidates = generate_candidates(
                home,
                away,
                evidences,
                enabled_markets=self.enabled_markets,
            )
            for candidate in candidates:
                score_candidate(candidate, evidences)
                results.append(
                    {
                        "home": candidate.home,
                        "away": candidate.away,
                        "market": display_market(candidate),
                        "score": round(candidate.score, 2),
                        "confidence": round(candidate.confidence * 100, 2),
                        "prediction": round(prediction_value(candidate), 2),
                        "evidence": [
                            {
                                "section": e.section,
                                "name": e.name,
                                "value": e.raw_value,
                                "team": e.team,
                            }
                            for e in candidate.evidences
                        ],
                        "supporting_evidence": [
                            {
                                "section": e.section,
                                "name": e.name,
                                "value": e.raw_value,
                                "team": e.team,
                            }
                            for e in candidate.supporting_evidences
                        ],
                        "bonuses": candidate.bonuses,
                        "penalties": candidate.penalties,
                    }
                )

        results.sort(
            key=lambda item: (item["prediction"], item["score"], item["confidence"]),
            reverse=True,
        )
        if prediction_threshold is not None:
            eligible_results = [
                item for item in results if item["prediction"] >= prediction_threshold
            ]
            self.threshold_excluded_count = len(results) - len(eligible_results)
            results = eligible_results
        for rank, prediction in enumerate(results[:top_n], start=1):
            prediction["rank"] = rank
        return results[:top_n]

    @staticmethod
    def group_predictions_by_game(
        predictions: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        """Group ranked predictions by match while preserving prediction order."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for prediction in predictions:
            game = f"{prediction['home']} - {prediction['away']}"
            grouped.setdefault(game, []).append(prediction)
        return dict(
            sorted(
                grouped.items(), key=lambda item: item[1][0]["prediction"], reverse=True
            )
        )
