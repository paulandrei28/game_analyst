import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sofascore_match_data import _decimal_odds, parse_1x2_odds, parse_referee
from sofascore_client import SofascoreSearcher


class MatchDataParserTests(unittest.TestCase):
    def test_romania_relative_date_labels_resolve_to_correct_day(self):
        today = date.today()
        for label in ("Azi", "Mâine", "maine"):
            with self.subTest(label=label):
                result = SofascoreSearcher._extract_date_from_text(label)
                expected = today + timedelta(days=1) if label.lower() != "azi" else today
                self.assertEqual(result, expected)

    def test_odds_only_returns_full_time_1x2_and_keeps_source(self):
        payload = {"markets": [
            {"marketId": 1, "marketName": "Full time", "sourceId": 99, "choices": [
                {"name": "1", "fractionalValue": "11/10", "sourceId": 99},
                {"name": "X", "fractionalValue": "11/5", "sourceId": 99},
                {"name": "2", "fractionalValue": "5/2", "sourceId": 99},
            ]},
            {"marketId": 2, "marketName": "Over/Under", "choices": [{"name": "Over 2.5", "decimalValue": "1.9"}]},
        ]}
        result = parse_1x2_odds(payload)
        self.assertEqual([item["odds"] for item in result], ["2.10", "3.20", "3.50"])
        self.assertTrue(all("European decimal" in item["format"] for item in result))
        self.assertEqual([item["source"] for item in result], ["Source ID 99"] * 3)

    def test_decimal_odds_are_converted_from_fractional_correctly(self):
        self.assertEqual(_decimal_odds(None, "13/5"), ("3.60", "European decimal (converted from fractional)"))
        self.assertEqual(_decimal_odds(1.9, "9/10"), ("1.90", "decimal"))

    def test_missing_market_outcomes_are_not_fabricated(self):
        self.assertEqual(parse_1x2_odds({"markets": []}), [])

    def test_referee_profile_stats_are_labeled_aggregate_and_missing_values_stay_missing(self):
        result = parse_referee(
            {"event": {"referee": {"id": 7, "name": "A Ref"}, "tournament": {"uniqueTournament": {"id": 44, "name": "Target League"}}}},
            {"referee": {"id": 7, "name": "A Ref", "country": {"name": "France"}, "games": 347, "yellowCards": 1398, "redCards": 65, "yellowRedCards": 36}},
            {"statistics": [
                {"uniqueTournament": {"id": 851, "name": "International Friendlies"}, "appearances": 1, "yellowCards": 0},
                {"uniqueTournament": {"id": 44, "name": "Target League"}, "appearances": 26, "yellowCards": 88, "redCards": 4, "yellowRedCards": 3, "penalty": 6},
            ]},
        )
        self.assertEqual(result["Career Games officiated"], 347)
        self.assertEqual(result["Career Yellow cards"], 1398)
        self.assertEqual(result["Career Red cards"], 65)
        self.assertEqual(result["Career Second-yellow dismissals"], 36)
        self.assertEqual(result["Career Penalties"], 6)
        self.assertEqual(result["Career Yellow cards per game"], "4.03")
        self.assertEqual(result["Career Total cards per game"], "4.22")
        self.assertEqual(result["Career Penalties per game"], "0.02")
        self.assertEqual(result["Competition"], "Target League")
        self.assertEqual(result["Competition Games officiated"], 26)
        self.assertEqual(result["Competition Yellow cards"], 88)
        self.assertEqual(result["Competition Penalties"], 6)
        self.assertEqual(result["Competition Yellow cards per game"], "3.38")
        self.assertEqual(result["Competition Total cards per game"], "3.54")
        self.assertEqual(result["Competition Penalties per game"], "0.23")
        self.assertEqual(result["Country"], "France")
        self.assertIn("all available seasons", result["Statistics scope"])

    def test_missing_referee_keeps_identity_and_statistics_unavailable(self):
        result = parse_referee({})
        self.assertIsNone(result["Name"])
        self.assertIsNone(result["ID"])
        self.assertIsNone(result["Career Games officiated"])
        self.assertIsNone(result["Competition Games officiated"])
        self.assertIsNone(result["Statistics scope"])


if __name__ == "__main__":
    unittest.main()
