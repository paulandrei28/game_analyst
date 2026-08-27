import unittest

from analyzer import (
    Analyzer,
    Candidate,
    Evidence,
    display_market,
    extract_evidence,
    parse_market,
    parse_statistic_value,
    prediction_value,
    score_candidate,
)


class AnalyzerHelpersTests(unittest.TestCase):
    def test_parse_statistic_value_supports_ratio_streak_and_invalid_value(self):
        confidence, sample_size = parse_statistic_value(" 3/5 ")
        self.assertAlmostEqual(confidence, 4 / 7)
        self.assertEqual(sample_size, 5)

        confidence, sample_size = parse_statistic_value("5")
        self.assertGreater(confidence, 0.55)
        self.assertEqual(sample_size, 5)

        self.assertEqual(parse_statistic_value("unknown"), (0.0, None))
        self.assertEqual(parse_statistic_value("2/0"), (0.0, 0))

    def test_parse_market_canonicalizes_supported_markets(self):
        self.assertEqual(
            parse_market("More than 2.5 goals", None),
            ("goals_ou_2.5_over", "goals_ou", "over"),
        )
        self.assertEqual(
            parse_market("Without clean sheet", "away"),
            ("no_clean_sheet_away", "no_clean_sheet", "away"),
        )
        self.assertEqual(
            parse_market("Both Teams Scoring", None),
            ("btts_yes", "btts", "yes"),
        )
        self.assertIsNone(parse_market("Possession", "home"))
        self.assertIsNone(parse_market("Wins", None))

    def test_extract_evidence_deduplicates_supported_statistics(self):
        data = {
            "general": [
                {"name": "Wins", "team": "home", "value": "4"},
                {"name": "Wins", "team": "home", "value": "4"},
                {"name": "Possession", "team": "home", "value": "60%"},
            ],
            "head2head": [],
        }
        evidence = extract_evidence(data)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].market, "wins_home")
        self.assertEqual(evidence[0].sample_size, 4)

    def test_analyze_filters_to_enabled_market_categories(self):
        analyzer = Analyzer(
            {
                "Home FC - Away FC": {
                    "general": [
                        {"name": "Wins", "team": "home", "value": "5"},
                        {"name": "Both teams scoring", "team": None, "value": "4/5"},
                    ],
                    "head2head": [],
                }
            },
            enabled_markets=("btts",),
        )

        predictions = analyzer.analyze(top_n=10)

        self.assertEqual(len(predictions), 1)
        self.assertEqual(predictions[0]["market"], "both teams scoring")

    def test_score_candidate_combines_evidence_and_prediction_value(self):
        evidence = Evidence(
            market="wins_home",
            category="wins",
            direction="home",
            confidence=0.8,
            section="general",
            team="home",
            raw_value="6",
            name="Wins",
            sample_size=6,
        )
        candidate = Candidate("Home FC", "Away FC", "wins_home", "wins", [evidence])

        score_candidate(candidate)

        self.assertGreater(candidate.score, 0)
        self.assertAlmostEqual(prediction_value(candidate), candidate.score * candidate.confidence)
        self.assertEqual(display_market(candidate), "Home FC wins")


class AnalyzerIntegrationTests(unittest.TestCase):
    def test_analyze_ignores_malformed_match_and_ranks_supported_market(self):
        analyzer = Analyzer(
            {
                "not a match": {},
                "Home FC - Away FC": {
                    "general": [
                        {"name": "Wins", "team": "home", "value": "5"},
                        {"name": "Losses", "team": "away", "value": "4"},
                    ],
                    "head2head": [],
                },
            }
        )

        predictions = analyzer.analyze(top_n=1)

        self.assertEqual(len(predictions), 1)
        self.assertEqual(predictions[0]["rank"], 1)
        self.assertEqual(predictions[0]["market"], "Home FC wins")
        self.assertEqual(predictions[0]["home"], "Home FC")

    def test_group_predictions_by_game_sorts_groups_by_top_prediction(self):
        predictions = [
            {"home": "A", "away": "B", "prediction": 2},
            {"home": "C", "away": "D", "prediction": 8},
            {"home": "A", "away": "B", "prediction": 1},
        ]

        grouped = Analyzer.group_predictions_by_game(predictions)

        self.assertEqual(list(grouped), ["C - D", "A - B"])
        self.assertEqual(len(grouped["A - B"]), 2)

    def test_prediction_threshold_filters_before_top_n_and_reports_exclusions(self):
        data = {
            "Home FC - Away FC": {
                "general": [
                    {"name": "Wins", "team": "home", "value": "5"},
                ],
                "head2head": [],
            }
        }
        analyzer = Analyzer(data)
        unfiltered = analyzer.analyze(top_n=1)

        filtered = analyzer.analyze(
            top_n=20,
            prediction_threshold=unfiltered[0]["prediction"] + 1,
        )

        self.assertEqual(filtered, [])
        self.assertEqual(analyzer.threshold_excluded_count, 1)


if __name__ == "__main__":
    unittest.main()
