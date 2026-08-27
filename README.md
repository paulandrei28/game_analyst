# Game Analyst

A small football match-analysis pipeline built around API-Football and Sofascore data. It finds fixtures in selected competitions, retrieves team streak statistics, ranks supported betting-style markets, and writes both machine-readable and human-readable results.

## Process

`game_analyst` is the application package and `pipeline.py` contains its workflow:

1. Look for the selected date's team-streak cache in `output/team_streaks/`.
2. If the cache is missing or invalid, load the selected date's fixtures from `output/fixtures/` or API-Football.
3. Fetch each match's team streaks through `sofascore_wrapper` and save them as JSON.
4. Analyze the streak data with the scoring logic in `analyzer.py`.
5. Save grouped predictions as JSON and render a Markdown report.

When the selected date's streak file already exists and contains valid JSON, steps 2 and 3 are skipped. Fixture lists are cached as newline-separated text files, which avoids repeating API-Football requests.

## Project layout

- `pipeline.py` - command-line entry point and workflow coordinator.
- `fixtures_scraper.py` - API-Football fixture fetcher with relative-date selection and text caching.
- `team_streaks.py` - Sofascore API integration for match streak data.
- `analyzer.py` - evidence extraction, candidate scoring, relationships, and ranking.
- `generate_analysis.py` - application wrapper for analysis and JSON persistence.
- `report_generator.py` - Markdown report rendering and persistence.
- `output/fixtures/` - cached newline-separated fixture lists.
- `output/team_streaks/` - cached daily streak input files.
- `output/analysis/` and `output/report/` - generated prediction JSON and Markdown reports.

The history directories are generated data and are ignored by Git.

## Requirements

- Python 3.10 or newer
- `requests`
- The `sofascore_wrapper` package

Install the Python dependencies in the active environment:

```powershell
pip install -r requirements.txt
```

For a reusable installation, install the project itself from its directory:

```powershell
pip install -e .
```

Set `API_FOOTBALL_API_KEY` before fetching fixtures. The API-Football free plan limits fixture queries to `yesterday`, `today`, or `tomorrow`.

## GitHub Actions

The workflow in `.github/workflows/daily-report.yml` generates the daily report
automatically at 06:00 UTC and uploads only the Markdown report as a workflow
artifact. It can also be started manually from the **Actions** tab.

Add `API_FOOTBALL_API_KEY` as a repository secret under **Settings > Secrets
and variables > Actions** before running the workflow. Reports are retained for
30 days and can be downloaded from the workflow run's **Artifacts** section.

## Run

From the directory containing the `game_analyst` folder:

```powershell
python -m game_analyst
```

The module entry point keeps imports and generated output paths consistent. It
stores the default history files inside the project's `output/` directory,
regardless of the directory from which the command is launched.

Useful options:

```text
--config PATH          TOML configuration file (default: config.toml)
--top-n N              Keep the N highest-ranked predictions (default: 20)
--date VALUE           Fetch yesterday, today, or tomorrow (default: today)
--output-dir PATH      Store history directories below PATH
```

## Configuration

The checked-in [config.toml](config.toml) contains the application defaults.
Edit `fixtures.leagues` to control which competitions are fetched. Add one
`name = api_id` line for a new competition, or comment out an existing line to
exclude it. The other supported settings are:

```toml
[pipeline]
date = "today"
output_dir = "output"

[analysis]
top_n = 20
# prediction_threshold = 100.0
enabled_markets = [
	"goals_ou", "corners_ou", "cards_ou",
	"btts",
	"first_to_score", "first_to_concede",
	"first_half_winner", "first_half_loser",
	"no_losses", "wins", "losses", "no_wins",
	"no_goals_conceded", "no_goals_scored", "no_clean_sheet",
]

[fixtures]
api_timeout_seconds = 10.0

[fixtures.leagues]
premier-league = 39
# bundesliga = 78

[sofascore]
request_interval_seconds = 5.0
```

`prediction_threshold` is optional. When enabled, predictions with a computed
prediction value below it are excluded before `top_n` is applied. The report
will say how many predictions were excluded when that reduces the result set.
`enabled_markets` controls which supported market categories can appear in the
final ranking. Comment out entries in the TOML array to disable them. The three
`*_ou` categories include all available over/under thresholds for goals, corners,
and cards. If `enabled_markets` is omitted, all supported categories remain
enabled.
Pass another file with `--config PATH` when maintaining multiple configurations.

For example:

```powershell
python -m game_analyst --date tomorrow --top-n 10
```

After installing the project, the equivalent console command is:

```powershell
game-analyst --date tomorrow --top-n 10
```

The default run creates:

- `team_streaks_history/team_streaks_YYYYMMDD.json`
- `analysis_history/analysis_YYYYMMDD.json`
- `analysis_history/analysis_YYYYMMDD.md`

## Data and limitations

- The fixture module filters results to a configured set of top tournaments and accepts only the three API-supported relative dates.
- Team lookup and streak requests are rate-limited to be considerate of the upstream service.
- Predictions are heuristic rankings based on the available streak evidence; they are not guarantees or financial advice.
- If API-Football, Sofascore, or the wrapper API changes its response format, the fixture or streak integration may need updating.

## Development checks

Run the unit tests with:

```powershell
python -m unittest discover -s tests -v
```

Compile the application modules with:

```powershell
python -m py_compile __init__.py __main__.py pipeline.py fixtures_scraper.py team_streaks.py analyzer.py generate_analysis.py report_generator.py
```

The full pipeline requires network access, an API-Football key, and valid Sofascore responses. The analysis and report stages can also be exercised independently with an existing streak JSON file.
