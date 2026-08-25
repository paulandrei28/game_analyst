from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from .generate_analysis import AnalysisGenerator
from .report_generator import HumanReadableReport
from .sofascore_upcoming_scraper import SofascoreUpcomingScraper
from .team_streaks import fetch_team_streaks

LOGGER = logging.getLogger(__name__)


async def run_pipeline(
    *,
    headless: bool = True,
    top_n: int = 20,
    output_dir: str | Path = Path(__file__).resolve().parent,
) -> dict[str, Path]:
    """Scrape, enrich, analyze, and save the daily match artifacts."""
    if top_n < 1:
        raise ValueError("top_n must be at least 1")

    output_root = Path(output_dir)
    streaks_dir = output_root / "team_streaks"
    json_analysis_dir = output_root / "analysis"
    report_dir = output_root / "report"
    streaks_dir.mkdir(parents=True, exist_ok=True)
    json_analysis_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    output_date = datetime.now().strftime("%Y%m%d")
    streaks_path = streaks_dir / f"team_streaks_{output_date}.json"
    analysis_path = json_analysis_dir / f"analysis_{output_date}.json"
    report_path = report_dir / f"report_{output_date}.md"

    streaks = _load_cached_streaks(streaks_path)
    if streaks is None:
        LOGGER.info("No valid team streak cache for today; collecting fresh data")
        scraper = SofascoreUpcomingScraper(headless=headless)
        games = await scraper.get_upcoming_games()
        LOGGER.info("Scraper returned %d games", len(games))

        LOGGER.info("Fetching team streaks")
        streaks = await fetch_team_streaks(games)
        _save_json(streaks, streaks_path)
        LOGGER.info("Team streaks written to %s", streaks_path)
    else:
        LOGGER.info("Using cached team streaks from %s", streaks_path)

    generator = AnalysisGenerator()
    predictions, grouped_predictions = generator.generate(streaks, top_n=top_n)
    generator.save_json(grouped_predictions, analysis_path)

    report_generator = HumanReadableReport()
    report_generator.save(
        predictions,
        str(report_path),
        title=f"Match Analysis Report - {output_date}",
    )
    LOGGER.info("Report written to %s", report_path)

    return {
        "streaks": streaks_path,
        "analysis": analysis_path,
        "report": report_path,
    }


def _load_cached_streaks(path: Path) -> dict | None:
    """Load today's streak cache, treating missing or invalid JSON as a miss."""
    if not path.is_file():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("Could not read team streak cache: %s", path)
        return None

    if not isinstance(data, dict):
        LOGGER.warning("Ignoring invalid team streak cache: %s", path)
        return None
    return data


def _save_json(data: dict, path: Path) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="game_analyst",
        description="Scrape upcoming football matches and generate analysis.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser while scraping Sofascore.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=20,
        help="Number of predictions to retain (default: 20).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Root directory for generated history files (default: project directory).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        artifacts = asyncio.run(
            run_pipeline(
                headless=not args.headed,
                top_n=args.top_n,
                output_dir=args.output_dir,
            )
        )
    except Exception:
        LOGGER.exception("Analysis pipeline failed")
        raise SystemExit(1)

    for artifact_type, path in artifacts.items():
        LOGGER.info("%s: %s", artifact_type.capitalize(), path)
