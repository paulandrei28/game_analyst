from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from .config import AppConfig, DEFAULT_CONFIG_PATH, load_config
from .derby_matcher import DerbyMatcher
from .generate_analysis import AnalysisGenerator
from .match_context import build_worth_watching, qualifies_for_worth_watching
from .report_generator import HumanReadableReport
from .fixtures_scraper import (
    DATE_OPTIONS,
    load_or_fetch_fixtures_with_metadata,
    resolve_date,
)
from .team_streaks import enrich_cached_team_statistics, fetch_team_match_data

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
    derby_matcher = DerbyMatcher.from_file()
    output_root = Path(output_dir)
    streaks_dir = output_root / "team_streaks"
    context_dir = output_root / "match_context"
    json_analysis_dir = output_root / "analysis"
    report_dir = output_root / "report"
    streaks_dir.mkdir(parents=True, exist_ok=True)
    context_dir.mkdir(parents=True, exist_ok=True)
    json_analysis_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    output_date = target_date.strftime("%Y%m%d")
    streaks_path = streaks_dir / f"team_streaks_{output_date}.json"
    progress_path = streaks_dir / f"team_streaks_{output_date}.progress.json"
    context_path = context_dir / f"match_context_{output_date}.json"
    analysis_path = json_analysis_dir / f"analysis_{output_date}.json"
    report_path = report_dir / f"report_{output_date}.md"

    streaks = _load_cached_streaks(streaks_path)
    match_context = _load_cached_json(context_path)
    fixture_games: list[str] | None = None
    if streaks is None:
        # League metadata is cached independently so the historical streak
        # cache remains its original game-to-stats mapping.
        fixture_games, fixture_metadata, _ = load_or_fetch_fixtures_with_metadata(
            date_option,
            output_dir=output_root,
            allowed_league_ids=settings.allowed_league_ids,
            api_timeout_seconds=settings.api_timeout_seconds,
        )
        LOGGER.info("Fixture metadata contains %d games", len(fixture_games))
        LOGGER.info(
            "No valid team streak cache for %s; collecting fresh data",
            date_option,
        )
        LOGGER.info("Scraper returned %d games", len(fixture_games))
        if not fixture_games:
            raise RuntimeError("No fixtures found; stopping the pipeline")

        LOGGER.info("Fetching team streaks from SofaScore")
        partial_streaks, partial_contexts = _load_progress_cache(
            progress_path,
            fixture_games,
        )

        def checkpoint_progress(
            current_streaks: dict[str, Any],
            current_contexts: dict[str, Any],
        ) -> None:
            _save_progress_cache(
                progress_path,
                fixture_games,
                current_streaks,
                current_contexts,
            )

        streaks, match_context = await fetch_team_match_data(
            fixture_games,
            target_date=target_date,
            request_interval=settings.sofascore_request_interval_seconds,
            request_jitter=settings.sofascore_request_jitter_seconds,
            request_burst_size=settings.sofascore_request_burst_size,
            request_burst_pause=settings.sofascore_request_burst_pause_seconds,
            request_backoff_base=settings.sofascore_request_backoff_base_seconds,
            request_max_retries=settings.sofascore_request_max_retries,
            statistics_cache_dir=output_root / "sofascore_statistics",
            worth_watching_qualifier=lambda game, context: _qualifies_for_worth_watching(
                game, context, fixture_metadata, derby_matcher
            ),
            initial_streaks=partial_streaks,
            initial_contexts=partial_contexts,
            progress_callback=checkpoint_progress,
        )
        if not streaks:
            raise RuntimeError("No team streaks found; stopping the pipeline")
        _save_json(streaks, streaks_path)
        LOGGER.info("Team streaks written to %s", streaks_path)
        _save_json(match_context, context_path)
        progress_path.unlink(missing_ok=True)
    else:
        LOGGER.info("Using cached team streaks from %s", streaks_path)
        try:
            fixture_games, fixture_metadata, _ = load_or_fetch_fixtures_with_metadata(
                date_option,
                output_dir=output_root,
                allowed_league_ids=settings.allowed_league_ids,
                api_timeout_seconds=settings.api_timeout_seconds,
            )
        except Exception:
            # A historical streak file is still publishable when its separate
            # metadata cache cannot be reconstructed. Predictions get the
            # explicit Unknown marker in build_payload.
            LOGGER.warning(
                "Fixture metadata unavailable; publishing Unknown leagues",
                exc_info=True,
            )
            fixture_metadata = {}

        if match_context is None:
            try:
                if fixture_games is None:
                    fixture_games, _, _ = load_or_fetch_fixtures_with_metadata(
                        date_option,
                        output_dir=output_root,
                        allowed_league_ids=settings.allowed_league_ids,
                        api_timeout_seconds=settings.api_timeout_seconds,
                    )
                if fixture_games:
                    partial_streaks, partial_contexts = _load_progress_cache(
                        progress_path,
                        fixture_games,
                    )

                    def checkpoint_progress(
                        current_streaks: dict[str, Any],
                        current_contexts: dict[str, Any],
                    ) -> None:
                        _save_progress_cache(
                            progress_path,
                            fixture_games,
                            current_streaks,
                            current_contexts,
                        )

                    _, match_context = await fetch_team_match_data(
                        fixture_games,
                        target_date=target_date,
                        include_streaks=False,
                        include_context=True,
                        request_interval=settings.sofascore_request_interval_seconds,
                        request_jitter=settings.sofascore_request_jitter_seconds,
                        request_burst_size=settings.sofascore_request_burst_size,
                        request_burst_pause=settings.sofascore_request_burst_pause_seconds,
                        request_backoff_base=settings.sofascore_request_backoff_base_seconds,
                        request_max_retries=settings.sofascore_request_max_retries,
                        statistics_cache_dir=output_root / "sofascore_statistics",
                        worth_watching_qualifier=lambda game, context: _qualifies_for_worth_watching(
                            game, context, fixture_metadata, derby_matcher
                        ),
                        initial_streaks=partial_streaks,
                        initial_contexts=partial_contexts,
                        progress_callback=checkpoint_progress,
                    )
                    _save_json(match_context, context_path)
                    progress_path.unlink(missing_ok=True)
            except Exception:
                LOGGER.warning("Match context unavailable; referee scoring will be skipped", exc_info=True)
                match_context = {}

    generator = AnalysisGenerator(
        enabled_markets=settings.enabled_markets,
        referee_card_bonus=settings.referee_card_bonus,
        referee_card_penalty=settings.referee_card_penalty,
    )
    derby_by_game = {
        game: derby_matcher.is_derby(*_split_match(game))
        for game in (match_context or {})
    }
    statistic_games = {
        game for game in (match_context or {}) if game in fixture_metadata
    }
    if any(_missing_team_statistics((match_context or {}).get(game)) for game in statistic_games):
        await enrich_cached_team_statistics(
            match_context or {},
            statistic_games,
            statistics_cache_dir=output_root / "sofascore_statistics",
            request_interval=settings.sofascore_request_interval_seconds,
            request_jitter=settings.sofascore_request_jitter_seconds,
            request_burst_size=settings.sofascore_request_burst_size,
            request_burst_pause=settings.sofascore_request_burst_pause_seconds,
            request_backoff_base=settings.sofascore_request_backoff_base_seconds,
            request_max_retries=settings.sofascore_request_max_retries,
        )
        _save_json(match_context or {}, context_path)

    predictions = generator.generate(
        streaks,
        prediction_threshold=prediction_threshold,
        match_context=match_context or {},
    )
    payload = generator.build_payload(
        date=output_date,
        predictions=predictions,
        fixture_metadata=fixture_metadata,
    )
    payload["worth_watching"] = build_worth_watching(
        match_context or {},
        fixture_metadata,
        derby_by_game,
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


def _load_cached_json(path: Path) -> dict | None:
    """Load an optional JSON object cache, treating invalid JSON as a miss."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _split_match(match_name: str) -> tuple[str, str]:
    home, separator, away = match_name.partition(" - ")
    return (home.strip(), away.strip()) if separator else ("", "")


def _qualifies_for_worth_watching(game: str, context: Any, metadata: dict[str, Any], matcher: DerbyMatcher) -> bool:
    """Avoid referee and aggregate-stat requests for excluded games."""
    if game not in metadata:
        return False
    home, away = _split_match(game)
    return qualifies_for_worth_watching(context, derby=matcher.is_derby(home, away))


def _missing_team_statistics(context: Any) -> bool:
    return not isinstance(context, dict) or not isinstance(context.get("team_statistics"), dict)


def _save_json(data: dict, path: Path) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_progress_cache(
    path: Path,
    fixture_games: list[str],
    *,
    max_age_seconds: float = 6 * 60 * 60,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load recent per-fixture progress for an identical daily fixture list."""
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        saved_at = float(cached["saved_at"])
        cached_fixtures = cached["fixtures"]
        streaks = cached["streaks"]
        contexts = cached["match_context"]
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return {}, {}

    if (
        time.time() - saved_at > max_age_seconds
        or not isinstance(cached_fixtures, list)
        or sorted(cached_fixtures) != sorted(fixture_games)
        or not isinstance(streaks, dict)
        or not isinstance(contexts, dict)
    ):
        LOGGER.info("Ignoring stale or mismatched SofaScore progress cache: %s", path)
        return {}, {}

    LOGGER.info(
        "Resuming SofaScore progress from %s (%d streaks, %d contexts)",
        path,
        len(streaks),
        len(contexts),
    )
    return streaks, contexts


def _save_progress_cache(
    path: Path,
    fixture_games: list[str],
    streaks: dict[str, Any],
    contexts: dict[str, Any],
) -> None:
    """Atomically checkpoint completed fixture data for safe resumption."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(
            {
                "saved_at": time.time(),
                "fixtures": fixture_games,
                "streaks": streaks,
                "match_context": contexts,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    temporary_path.replace(path)


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
                    args.output_dir
                    if args.output_dir is not None
                    else settings.output_dir
                ),
                config=settings,
            )
        )
    except Exception:
        LOGGER.exception("Pipeline failed")
        raise

    for name, path in artifacts.items():
        LOGGER.info("%s: %s", name, path)


if __name__ == "__main__":
    main()
