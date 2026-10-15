import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from game_analyst.sofascore_client import SofascoreClient


class FetchMatchRefereeTests(unittest.TestCase):
    def test_unwraps_nested_event_and_uses_embedded_referee_if_profile_is_empty(self):
        client = SofascoreClient.__new__(SofascoreClient)
        client.fetch = AsyncMock(side_effect=[{}, {"statistics": []}])
        embedded_referee = {
            "id": 222742,
            "name": "Joao Pedro Pinheiro",
            "games": 389,
            "yellowCards": 1821,
            "redCards": 47,
        }
        event_payload = {"event": {"referee": embedded_referee}}

        result = asyncio.run(
            client.fetch_match_referee(16943989, event_payload=event_payload)
        )

        self.assertEqual(result["referee"], embedded_referee)
        self.assertEqual(result["statistics"], {"statistics": []})
        client.fetch.assert_any_await("referee", referee_id=222742)

    def test_missing_referee_profile_still_falls_back_to_event_referee(self):
        client = SofascoreClient.__new__(SofascoreClient)
        client.fetch = AsyncMock(side_effect=[None, None])
        embedded_referee = {"id": 12, "games": 10, "yellowCards": 30, "redCards": 0}

        result = asyncio.run(
            client.fetch_match_referee(
                1, event_payload={"event": {"referee": embedded_referee}}
            )
        )

        self.assertEqual(result["referee"], embedded_referee)


if __name__ == "__main__":
    unittest.main()
