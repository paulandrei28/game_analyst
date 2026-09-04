from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .analyzer import Analyzer

LOGGER = logging.getLogger(__name__)


class AnalysisGenerator:
    """Application-level pipeline for loading, analyzing and saving results."""

    def __init__(
        self,
        analyzer: Analyzer | None = None,
        enabled_markets: tuple[str, ...] | None = None,
    ):
        self.analyzer = analyzer or Analyzer(enabled_markets=enabled_markets)

    def generate(
        self,
        data: dict[str, dict[str, Any]],
        prediction_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """Run the analyzer and return its complete, flat result set."""
        return self.analyzer.analyze(data, prediction_threshold=prediction_threshold)

    def generate_from_file(
        self,
        input_path: str | Path,
        prediction_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """Load a team-streak JSON file and analyze it."""
        path = Path(input_path)
        LOGGER.info("Reading input file: %s", path)

        try:
            with path.open("r", encoding="utf-8") as streaks_file:
                data = json.load(streaks_file)
        except (OSError, json.JSONDecodeError):
            LOGGER.exception("Could not read or parse input file: %s", path)
            raise

        LOGGER.info("Loaded %d matches", len(data))
        predictions = self.generate(data, prediction_threshold=prediction_threshold)
        LOGGER.info("Generated %d predictions", len(predictions))
        return predictions

    @staticmethod
    def output_date(input_path: str | Path) -> str:
        """Use the YYYYMMDD suffix from the input file when available."""
        name = Path(input_path).name
        date_match = re.search(r"(\d{8})(?=\.json$)", name)
        return date_match.group(1) if date_match else datetime.now().strftime("%Y%m%d")

    def build_payload(
        self,
        *,
        date: str,
        predictions: list[dict[str, Any]],
        fixture_metadata: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Attach league metadata and construct the canonical web payload."""
        enriched: list[dict[str, Any]] = []
        for prediction in predictions:
            game = f"{prediction['home']} - {prediction['away']}"
            league = fixture_metadata.get(game)
            if league is None:
                LOGGER.warning("Missing league metadata for %s; using Unknown", game)
                league = {"id": None, "name": "Unknown"}
            enriched.append({**prediction, "league": {"id": league.get("id"), "name": league.get("name") or "Unknown"}})
        return {"date": date, "predictions": enriched}

    def save_json(self, payload: dict[str, Any], output_path: str | Path) -> Path:
        """Persist the canonical flat analysis payload."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        LOGGER.info("Analysis written to %s", path)
        return path
