# Game Analyst

Game Analyst is a Python pipeline that retrieves football fixtures, collects
team streak statistics, ranks supported prediction markets, and produces JSON
and Markdown reports.

## How it works

1. Resolve the requested relative date: `yesterday`, `today`, or `tomorrow`.
2. Fetch configured API-Football fixtures and store fixture metadata.
3. Retrieve team streak data from Sofascore.
4. Generate and rank prediction candidates from the available evidence.
5. Write an analysis payload and a human-readable report.

The pipeline reuses date-specific fixture metadata and team-streak data from
`output/` when available.

## Project layout

- `pipeline.py` - command-line entry point and workflow coordinator.
- `fixtures_scraper.py` - API-Football fixture retrieval and metadata caching.
- `team_streaks.py` - Sofascore streak-data integration.
- `analyzer.py` - evidence extraction, candidate scoring, and ranking.
- `generate_analysis.py` - analysis payload construction and persistence.
- `report_generator.py` - Markdown report rendering.
- `web_export.py` - export of analysis payloads for a static web client.
- `nightly_runner.py` - scheduled-run helper.
- `output/fixtures/` - fixture metadata cache.
- `output/team_streaks/` - team-streak cache.
- `output/analysis/` - JSON analysis payloads.
- `output/report/` - Markdown reports.

## Requirements

- Python 3.10 or newer
- An API-Football API key
- Dependencies listed in `requirements.txt`

Install dependencies:

```powershell
pip install -r requirements.txt
```

To install the command-line entry point:

```powershell
pip install -e .
```

Set the API key before running the pipeline:

```powershell
$env:API_FOOTBALL_API_KEY = "your-api-key"
```

## Run

From the directory containing the `game_analyst` package:

```powershell
python -m game_analyst --date tomorrow
```

After installation, use:

```powershell
game-analyst --date tomorrow
```

Available options:

```text
--config PATH          TOML configuration file (default: config.toml)
--date VALUE           yesterday, today, or tomorrow
--output-dir PATH      Root directory for generated output
```

## Configuration

`config.toml` controls the pipeline date, output location, prediction filters,
fixture competitions, and Sofascore request pacing.

```toml
[pipeline]
date = "today"
output_dir = "output"

[analysis]
prediction_threshold = 65.0
enabled_markets = ["goals_ou", "btts", "wins"]

[fixtures]
api_timeout_seconds = 10.0

[fixtures.leagues]
premier-league = 39
laliga = 140

[sofascore]
request_interval_seconds = 5.0
request_jitter_seconds = 5.0
request_burst_size = 5
request_burst_pause_seconds = 30.0
request_backoff_base_seconds = 60.0
request_max_retries = 2
```

`prediction_threshold` excludes predictions below the specified computed
prediction value. `enabled_markets` limits the categories included in ranking.
If it is omitted, all supported categories are enabled.

## Output

Each run writes date-stamped files below the selected output directory:

- `team_streaks/team_streaks_YYYYMMDD.json`
- `analysis/analysis_YYYYMMDD.json`
- `report/report_YYYYMMDD.md`

Analysis JSON has a flat payload shape:

```json
{
  "date": "YYYYMMDD",
  "predictions": [
    {
      "rank": 1,
      "home": "Home Team",
      "away": "Away Team",
      "market": "Home Team wins",
      "score": 0,
      "confidence": 0,
      "prediction": 0,
      "league": {"id": 39, "name": "Premier League"}
    }
  ]
}
```

Predictions are sorted by score, then prediction value and confidence. The
Markdown report includes the ranked overview and supporting evidence.

## Development checks

Run the test suite:

```powershell
python -m unittest discover -s tests -v
```

Compile the application modules:

```powershell
python -m py_compile __init__.py __main__.py pipeline.py fixtures_scraper.py team_streaks.py analyzer.py generate_analysis.py report_generator.py web_export.py nightly_runner.py
```

The complete pipeline requires network access, a valid API key, and valid
responses from API-Football and Sofascore.
