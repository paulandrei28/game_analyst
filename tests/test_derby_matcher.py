import unittest
from unittest.mock import patch

import derby_matcher
from derby_matcher import DerbyMatcher


class DerbyMatcherTests(unittest.TestCase):
    def test_aliases_handle_accents_punctuation_and_club_suffixes(self):
        matcher = DerbyMatcher([["Sao Paulo", "Corinthians"]])
        self.assertTrue(matcher.is_derby("FC S\u00e3o Paulo II", "Corinthians II"))
        self.assertTrue(matcher.is_derby("FC S" + "\u00c3\u00a3o Paulo II", "Corinthians II"))
        self.assertEqual(matcher.resolve("S. Paulo"), "sao paulo")

    def test_pair_is_order_independent_and_non_pair_is_false(self):
        matcher = DerbyMatcher([["River Plate", "Boca Juniors"], ["River Plate", "Racing Club"]])
        self.assertTrue(matcher.is_derby("Boca Juniors", "River Plate"))
        self.assertFalse(matcher.is_derby("Boca Juniors", "Racing Club"))

    def test_loads_the_provided_scoped_derby_list(self):
        matcher = DerbyMatcher.from_file()
        self.assertTrue(matcher.is_derby("Tottenham Hotspur", "Arsenal"))
        self.assertTrue(matcher.is_derby("River Plate", "Boca Juniors"))

    def test_bounded_fuzzy_match_handles_small_api_typo(self):
        matcher = DerbyMatcher([["Manchester United", "Liverpool"]])
        self.assertTrue(matcher.is_derby("Man Utd", "Liverpool"))
        self.assertTrue(matcher.is_derby("Manchster United", "Liverpool"))

    def test_ambiguous_abbreviation_is_not_guessed(self):
        matcher = DerbyMatcher([
            ["Real Madrid", "Atletico Madrid"],
            ["Racing Madrid", "Getafe"],
        ])
        self.assertIsNone(matcher.resolve("RM"))
        self.assertFalse(matcher.is_derby("RM", "Atletico Madrid"))

    def test_fuzzy_comparisons_are_bounded(self):
        pairs = [[f"Club {index:03d}", f"Opponent {index:03d}"] for index in range(250)]
        matcher = DerbyMatcher(pairs, fuzzy_candidate_limit=4)
        real = derby_matcher.SequenceMatcher
        calls = 0

        def counted(*args, **kwargs):
            nonlocal calls
            calls += 1
            return real(*args, **kwargs)

        with patch.object(derby_matcher, "SequenceMatcher", side_effect=counted):
            self.assertEqual(matcher.resolve("Clu 249"), "club 249")
        self.assertLessEqual(calls, 12)


if __name__ == "__main__":
    unittest.main()
