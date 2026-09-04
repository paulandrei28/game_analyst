from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from .config import AppConfig, DEFAULT_CONFIG_PATH, load_config
from .generate_analysis import AnalysisGenerator
from .report_generator import HumanReadableReport
from .fixtures_scraper import (
    DATE_OPTIONS,
    load_or_fetch_fixtures_with_metadata,
    resolve_date,
)
from .team_streaks import fetch_team_streaks

LOGGER = logging.getLogger(__name__)


async def run_pipeline(
    *,
    date_option: str | None = None,
    output_dir: str | Path | None = None,
    prediction_threshold: float | None = None,
    config: AppConfig | None = None,
) -> dict[str, Path]:
    """Scrape, enrich, analyze, and save the daily match artifacts."""
    settings = config or load_config()
    date_option = settings.date if date_option is None else date_option
    output_dir = settings.output_dir if output_dir is None else output_dir
    if prediction_threshold is None:
        prediction_threshold = settings.prediction_threshold

    target_date = resolve_date(date_option)
    output_root = Path(output_dir)
    streaks_dir = output_root / "team_streaks"
    json_analysis_dir = output_root / "analysis"
    report_dir = output_root / "report"
    streaks_dir.mkdir(parents=True, exist_ok=True)
    json_analysis_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    output_date = target_date.strftime("%Y%m%d")
    streaks_path = streaks_dir / f"team_streaks_{output_date}.json"
    analysis_path = json_analysis_dir / f"analysis_{output_date}.json"
    report_path = report_dir / f"report_{output_date}.md"

    streaks = _load_cached_streaks(streaks_path)
    if streaks is None:
        # League metadata is cached independently so the historical streak
        # cache remains its original game-to-stats mapping.
        games, fixture_metadata, _ = load_or_fetch_fixtures_with_metadata(
            date_option,
            output_dir=output_root,
            allowed_league_ids=settings.allowed_league_ids,
            api_timeout_seconds=settings.api_timeout_seconds,
        )
        LOGGER.info("Fixture metadata contains %d games", len(games))
        LOGGER.info(
            "No valid team streak cache for %s; collecting fresh data", date_option
        )
        LOGGER.info("Scraper returned %d games", len(games))
        if not games:
            raise RuntimeError("No fixtures found; stopping the pipeline")

        LOGGER.info("Fetching team streaks")
        streaks = await fetch_team_streaks(
            games,
            request_interval=settings.sofascore_request_interval_seconds,
            request_jitter=settings.sofascore_request_jitter_seconds,
            request_burst_size=settings.sofascore_request_burst_size,
            request_burst_pause=settings.sofascore_request_burst_pause_seconds,
            request_backoff_base=settings.sofascore_request_backoff_base_seconds,
            request_max_retries=settings.sofascore_request_max_retries,
        )
        if not streaks:
            raise RuntimeError("No team streaks found; stopping the pipeline")

        _save_json(streaks, streaks_path)
        LOGGER.info("Team streaks written to %s", streaks_path)
    else:
        LOGGER.info("Using cached team streaks from %s", streaks_path)
        try:
            _, fixture_metadata, _ = load_or_fetch_fixtures_with_metadata(
                date_option,
                output_dir=output_root,
                allowed_league_ids=settings.allowed_league_ids,
                api_timeout_seconds=settings.api_timeout_seconds,
            )
        except Exception:
            # A historical streak file is still publishable when its separate
            # metadata cache cannot be reconstructed. Predictions get the
            # explicit Unknown marker in build_payload.
            LOGGER.warning("Fixture metadata unavailable; publishing Unknown leagues", exc_info=True)
            fixture_metadata = {}

    generator = AnalysisGenerator(enabled_markets=settings.enabled_markets)
    predictions = generator.generate(
        streaks,
        prediction_threshold=prediction_threshold,
    )
    payload = generator.build_payload(
        date=output_date,
        predictions=predictions,
        fixture_metadata=fixture_metadata,
    )
    generator.save_json(payload, analysis_path)

    report_generator = HumanReadableReport()
    threshold_notice = None
    excluded_count = getattr(generator.analyzer, "threshold_excluded_count", 0)
    if (
        prediction_threshold is not None
        and isinstance(excluded_count, int)
        and excluded_count
    ):
        threshold_notice = (
            f"Prediction threshold {prediction_threshold:.2f} excluded "
            f"{excluded_count} prediction(s); only {len(predictions)} met the threshold."
        )
    report_generator.save(
        predictions,
        str(report_path),
        title=f"Match Analysis Report - {output_date}",
        threshold_notice=threshold_notice,
    )

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
    if not data:
        path.unlink()
        LOGGER.error("Empty team streak cache removed: %s", path)
        raise RuntimeError("No team streaks found; stopping the pipeline")
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
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--date", choices=DATE_OPTIONS)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Root directory for generated history files (default: project directory).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_config(args.config)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        artifacts = asyncio.run(
            run_pipeline(
                date_option=args.date or settings.date,
                output_dir=(
                    settings.output_dir if args.output_dir is None else args.output_dir
                ),
                prediction_threshold=settings.prediction_threshold,
                config=settings,
            )
        )
    except Exception:
        LOGGER.exception("Analysis pipeline failed")
        raise SystemExit(1)

    for artifact_type, path in artifacts.items():
        LOGGER.info("%s: %s", artifact_type.capitalize(), path)
