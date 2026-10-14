import unittest

from analyzer import Analyzer, Candidate, apply_referee_card_adjustment


def context(career=(3.0, 100, 200), competition=(3.2, 10, 22)):
    def row(values):
        average, games, cards = values
        return {"games": games, "yellowCards": cards, "redCards": 0}

    event = {"tournament": {"uniqueTournament": {"id": 7}}}
    referee = {
        "referee": {"games": career[1], "yellowCards": career[2], "redCards": 0},
        "statistics": {"statistics": [{"uniqueTournament": {"id": 7}, **row(competition)}]},
    }
    return {"event": event, "referee": referee, "odds": None}


class RefereeCardScoringTests(unittest.TestCase):
    def candidate(self, direction="under", score=10.0, category="cards_ou"):
        market = f"cards_ou_4.5_{direction}" if category == "cards_ou" else "goals_ou_2.5_under"
        return Candidate("Home", "Away", market, category, score=score)

    def test_low_referee_supports_under_and_penalizes_over(self):
        under = self.candidate()
        explanation = apply_referee_card_adjustment(under, context())
        self.assertGreater(under.score, 10)
        self.assertIn("supports Under 4.5", explanation)

        over = self.candidate("over")
        apply_referee_card_adjustment(over, context())
        self.assertLess(over.score, 10)
        self.assertTrue(over.penalties)

    def test_high_referee_supports_over_and_penalizes_under(self):
        high = context((5.0, 100, 500), (4.8, 10, 48))
        over = self.candidate("over")
        apply_referee_card_adjustment(over, high)
        self.assertGreater(over.score, 10)
        under = self.candidate()
        apply_referee_card_adjustment(under, high)
        self.assertLess(under.score, 10)

    def test_near_threshold_has_small_effect_and_conflict_is_skipped(self):
        near = self.candidate()
        apply_referee_card_adjustment(near, context((3.95, 100, 395), (3.98, 100, 398)))
        self.assertLessEqual(near.score - 10, 0.5)
        conflict = self.candidate()
        explanation = apply_referee_card_adjustment(
            conflict, context((3.0, 100, 300), (5.0, 100, 500))
        )
        self.assertEqual(conflict.score, 10)
        self.assertIn("disagree", explanation)

    def test_one_average_gets_reduced_weight_and_malformed_or_missing_is_safe(self):
        only_career = {"referee": {"referee": {"games": 100, "yellowCards": 300, "redCards": 0}}}
        candidate = self.candidate()
        apply_referee_card_adjustment(candidate, only_career)
        self.assertGreater(candidate.score, 10)
        self.assertLess(candidate.score, 15)
        only_competition = {
            "event": {"tournament": {"uniqueTournament": {"id": 7}}},
            "referee": {
                "referee": None,
                "statistics": {"statistics": [{"uniqueTournament": {"id": 7}, "appearances": 100, "yellowCards": 300, "redCards": 0}]},
            },
        }
        comp_only = self.candidate()
        apply_referee_card_adjustment(comp_only, only_competition)
        self.assertGreater(comp_only.score, 10)
        for value in (None, {}, {"referee": None}, {"referee": {"referee": {"games": "bad"}}}):
            missing = self.candidate()
            explanation = apply_referee_card_adjustment(missing, value)
            self.assertEqual(missing.score, 10)
            self.assertIn("no adjustment", explanation)

    def test_scores_are_clamped_and_other_markets_are_unchanged(self):
        low = context((10, 100, 100), (10, 100, 100))
        clamped = self.candidate("over", score=1)
        apply_referee_card_adjustment(clamped, low, penalty=1000)
        self.assertEqual(clamped.score, 0)
        unrelated = self.candidate(category="goals_ou")
        apply_referee_card_adjustment(unrelated, low)
        self.assertEqual(unrelated.score, 10)

    def test_missing_odds_does_not_break_full_prediction_generation(self):
        data = {"Home - Away": {"general": [{"name": "Less than 4.5 cards", "team": "home", "value": "4/5"}], "head2head": []}}
        result = Analyzer(data).analyze(match_context={"Home - Away": {"odds": None}})
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
