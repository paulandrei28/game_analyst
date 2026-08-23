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

    def __init__(self, analyzer: Analyzer | None = None):
        self.analyzer = analyzer or Analyzer()

    def generate(
        self,
        data: dict[str, dict[str, Any]],
        top_n: int = 20,
    ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        """Run the analyzer and return flat and grouped results."""
        predictions = self.analyzer.analyze(data, top_n=top_n)
        grouped = self.analyzer.group_predictions_by_game(predictions)
        return predictions, grouped

    def generate_from_file(
        self,
        input_path: str | Path,
        top_n: int = 20,
    ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
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
        predictions, grouped = self.generate(data, top_n=top_n)
        LOGGER.info("Generated %d predictions", len(predictions))
        return predictions, grouped

    @staticmethod
    def output_date(input_path: str | Path) -> str:
        """Use the YYYYMMDD suffix from the input file when available."""
        name = Path(input_path).name
        date_match = re.search(r"(\d{8})(?=\.json$)", name)
        return date_match.group(1) if date_match else datetime.now().strftime("%Y%m%d")

    def save_json(
        self,
        grouped_predictions: dict[str, list[dict[str, Any]]],
        output_path: str | Path,
    ) -> Path:
        """Persist grouped predictions as the existing JSON format."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(grouped_predictions, indent=4),
            encoding="utf-8",
        )
        LOGGER.info("Analysis written to %s", path)
        return path
