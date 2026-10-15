import unittest
from copy import deepcopy

from match_context import build_worth_watching, classify_risk, qualifies_for_worth_watching


def raw_context(*, referee_yellows=500, referee_reds=10, competition_yellows=100,
                competition_reds=5, career_games=100, competition_games=20,
                career_penalties=5, competition_penalties=3,
                odds=(2.7, 3.0, 2.8), referee=True):
    event_data = {
        "tournament": {"name": "League", "uniqueTournament": {"id": 7, "name": "League"}},
        "season": {"id": 2026},
        "homeTeam": {"id": 1},
        "awayTeam": {"id": 2},
        "referee": {"id": 88, "name": "Official"} if referee else None,
    }
    choices = [
        {"name": label, "decimalValue": price}
        for label, price in zip(("1", "x", "2"), odds)
    ] if odds is not None else []
    return {
        "event": event_data,
        "referee": {
            "referee": {
                "id": 88, "name": "Official", "games": career_games,
                "yellowCards": referee_yellows, "redCards": referee_reds,
                "penalties": career_penalties,
            } if referee else None,
            "statistics": {
                "statistics": [{
                    "uniqueTournament": {"id": 7}, "appearances": competition_games,
                    "yellowCards": competition_yellows, "redCards": competition_reds,
                    "penalty": competition_penalties,
                }]
            } if referee else None,
        },
        "odds": {"markets": [{"marketId": "1", "marketName": "1X2", "choices": choices}]} if odds is not None else None,
    }


class MatchContextTests(unittest.TestCase):
    def test_odds_filter_uses_only_home_above_1_9_and_no_higher_than_away(self):
        self.assertTrue(qualifies_for_worth_watching(raw_context(odds=(2.7, 3.0, 2.8))))
        self.assertFalse(qualifies_for_worth_watching(raw_context(odds=(1.9, 3.0, 4.0))))
        self.assertFalse(qualifies_for_worth_watching(raw_context(odds=(2.0, 3.0, 1.9))))
        self.assertFalse(qualifies_for_worth_watching(raw_context(odds=(2.0, 3.0))))

    def test_classification_boundaries_and_compact_filtered_object(self):
        self.assertEqual([classify_risk(v) for v in (39, 40, 64, 65)],
                         ["low", "medium", "medium", "high"])
        self.assertEqual(classify_risk(None), "insufficient_data")
        game = "Roma - Real Madrid"
        result = build_worth_watching({game: raw_context()},
                                      {game: {"id": 123, "name": "UEFA Champions League"}},
                                      {game: False})
        self.assertEqual(result[game]["league"], {"id": 123, "name": "UEFA Champions League"})
        self.assertEqual(result[game]["odds"], {"home_win": 2.7, "draw": 3.0, "away_win": 2.8})
        self.assertEqual(set(result[game]["stats"]), {
            "penalty_probability", "red_card_probability",
            "high_yellow_card_tendency"})
        self.assertEqual(result[game]["stats"]["penalty_probability"], "insufficient_data")
        self.assertIsNone(result[game]["stats"]["high_yellow_card_tendency"])
        self.assertFalse(result[game]["derby"])
        self.assertEqual(set(result[game]), {"league", "odds", "derby", "stats"})
        stats_context = deepcopy(raw_context())
        stats_context.update({
            "team_statistics": {
                "home": {"matches": 20, "yellow_cards_per_match": 2.0,
                         "red_cards_per_match": 0.05, "penalties_per_match": 0.05,
                         "fouls_per_match": None},
                "away": {"matches": 20, "yellow_cards_per_match": 8.0,
                         "red_cards_per_match": 0.25, "penalties_per_match": 0.30,
                         "fouls_per_match": None},
            },
            "competition_statistics_baseline": {
                "yellow_cards_per_match": 5.0, "red_cards_per_match": 0.15,
                "penalties_per_match": 0.20, "fouls_per_match": None,
            },
            "competition_statistics_baseline_team_counts": {
                "yellow_cards_per_match": 8, "red_cards_per_match": 8,
                "penalties_per_match": 8,
            },
        })
        combined = build_worth_watching(
            {game: stats_context},
            {game: {"id": 123, "name": "UEFA Champions League", "season": 2026}},
            {game: False})
        self.assertTrue(combined[game]["stats"]["high_yellow_card_tendency"])
        self.assertEqual(build_worth_watching({game: raw_context(odds=(1.4, 5, 6))},
                                              {game: {"id": 1, "name": "L"}}, {}), {})
        derby_result = build_worth_watching(
            {game: raw_context(odds=(1.5, 4.0, 8.0))},
            {game: {"id": 123, "name": "League"}}, {game: True})
        self.assertIn(game, derby_result)
        self.assertTrue(derby_result[game]["derby"])

    def test_high_team_discipline_risk_is_independent_of_odds_and_derby(self):
        game = "Roma - Real Madrid"
        context = raw_context(odds=(1.5, 4.0, 8.0))
        context["team_statistics"] = {
            "home": {"matches": 100, "yellow_cards_per_match": 8.0,
                     "red_cards_per_match": 0.25, "penalties_per_match": 0.30},
            "away": {"matches": 100, "yellow_cards_per_match": 8.0,
                     "red_cards_per_match": 0.25, "penalties_per_match": 0.30},
        }
        context["competition_statistics_baseline"] = {
            "yellow_cards_per_match": 5.0, "red_cards_per_match": 0.15,
            "penalties_per_match": 0.20,
        }
        result = build_worth_watching(
            {game: context}, {game: {"id": 1, "name": "League"}}, {game: False}
        )
        self.assertIn(game, result)
        self.assertTrue(result[game]["stats"]["high_yellow_card_tendency"])

    def test_team_only_discipline_estimates_are_retained_at_lower_risk(self):
        game = "Roma - Real Madrid"
        context = raw_context(odds=(2.0, 3.5, 2.2), referee=False)
        context["team_statistics"] = {
            "home": {"matches": 100, "yellow_cards_per_match": 8.0,
                     "red_cards_per_match": 0.25, "penalties_per_match": 0.30},
            "away": {"matches": 100, "yellow_cards_per_match": 8.0,
                     "red_cards_per_match": 0.25, "penalties_per_match": 0.30},
        }
        context["competition_statistics_baseline"] = {
            "yellow_cards_per_match": 5.0, "red_cards_per_match": 0.15,
            "penalties_per_match": 0.20,
        }
        result = build_worth_watching(
            {game: context}, {game: {"id": 1, "name": "League"}}, {game: False}
        )
        self.assertIn(game, result)
        self.assertNotEqual(result[game]["stats"]["penalty_probability"], "insufficient_data")
        self.assertNotEqual(result[game]["stats"]["red_card_probability"], "insufficient_data")
        self.assertFalse(result[game]["stats"]["high_yellow_card_tendency"])


if __name__ == "__main__":
    unittest.main()
