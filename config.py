from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - only used on Python 3.10
    import tomli as tomllib  # type: ignore

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.toml"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"


@dataclass(frozen=True)
class AppConfig:
    date: str = "today"
    top_n: int = 20
    prediction_threshold: float | None = None
    output_dir: Path = DEFAULT_OUTPUT_DIR
    league_ids: dict[str, int] | None = None
    enabled_leagues: tuple[str, ...] = ()
    api_timeout_seconds: float = 10.0
    sofascore_request_interval_seconds: float = 5.0

    @property
    def allowed_league_ids(self) -> set[int]:
        league_ids = self.league_ids or {}
        return {league_ids[name] for name in self.enabled_leagues}


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """Load application settings from TOML and validate user-facing values."""
    config_path = Path(path)
    try:
        with config_path.open("rb") as config_file:
            raw: dict[str, Any] = tomllib.load(config_file)
    except FileNotFoundError:
        return AppConfig()

    fixtures = raw.get("fixtures", {})
    league_ids = {
        str(name): int(league_id)
        for name, league_id in fixtures.get(
            "leagues", fixtures.get("league_ids", {})
        ).items()
    }
    analysis = raw.get("analysis", {})
    pipeline = raw.get("pipeline", {})
    sofascore = raw.get("sofascore", {})

    enabled_leagues = tuple(fixtures.get("enabled_leagues", league_ids))
    unknown_leagues = sorted(set(enabled_leagues) - set(league_ids))
    if unknown_leagues:
        raise ValueError(f"Unknown league names in config: {', '.join(unknown_leagues)}")

    top_n = int(analysis.get("top_n", 20))
    if top_n < 1:
        raise ValueError("analysis.top_n must be at least 1")

    threshold_value = analysis.get("prediction_threshold")
    prediction_threshold = None if threshold_value is None else float(threshold_value)
    if prediction_threshold is not None and prediction_threshold < 0:
        raise ValueError("analysis.prediction_threshold must be non-negative")

    output_dir = Path(pipeline.get("output_dir", "output"))
    if not output_dir.is_absolute():
        output_dir = config_path.parent / output_dir

    api_timeout = float(fixtures.get("api_timeout_seconds", 10.0))
    request_interval = float(sofascore.get("request_interval_seconds", 5.0))
    if api_timeout <= 0 or request_interval < 0:
        raise ValueError("API timeout must be positive and request interval cannot be negative")

    return AppConfig(
        date=str(pipeline.get("date", "today")),
        top_n=top_n,
        prediction_threshold=prediction_threshold,
        output_dir=output_dir,
        league_ids=league_ids,
        enabled_leagues=enabled_leagues,
        api_timeout_seconds=api_timeout,
        sofascore_request_interval_seconds=request_interval,
    )
