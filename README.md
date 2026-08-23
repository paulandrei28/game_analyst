# Game Analyst

A small football match-analysis pipeline built around Sofascore data. It finds today's upcoming matches in selected competitions, retrieves team streak statistics, ranks supported betting-style markets, and writes both machine-readable and human-readable results.

## Process

`game_analyst` is the application package and `pipeline.py` contains its workflow:

1. Look for today's team-streak cache in `team_streaks_history/`.
2. If the cache is missing or invalid, scrape today's upcoming matches with Playwright.
3. Fetch each match's team streaks through `sofascore_wrapper` and save them as JSON.
4. Analyze the streak data with the scoring logic in `analyzer.py`.
5. Save grouped predictions as JSON and render a Markdown report.

When today's streak file already exists and contains valid JSON, steps 2 and 3 are skipped. This avoids launching a browser and avoids repeating the rate-limited Sofascore API requests.

## Project layout

- `pipeline.py` - command-line entry point and workflow coordinator.
- `sofascore_upcoming_scraper.py` - Playwright scraper for today's eligible fixtures.
- `team_streaks.py` - Sofascore API integration for match streak data.
- `analyzer.py` - evidence extraction, candidate scoring, relationships, and ranking.
- `generate_analysis.py` - application wrapper for analysis and JSON persistence.
- `report_generator.py` - Markdown report rendering and persistence.
- `team_streaks_history/` - cached daily streak input files.
- `analysis_history/` - generated prediction JSON and Markdown reports.

The history directories are generated data and are ignored by Git.

## Requirements

- Python 3.10 or newer
- Playwright and its Chromium browser
- The `sofascore_wrapper` package

Install the Python dependencies in the active environment, then install Chromium for Playwright:

```powershell
pip install playwright sofascore-wrapper
playwright install chromium
```

For a reusable installation, install the project itself from its directory:

```powershell
pip install -e .
playwright install chromium
```

The exact package name for the wrapper may vary depending on the version or local distribution used by your environment. The import required by the project is `sofascore_wrapper`.

## Run

From the directory containing the `game_analyst` folder:

```powershell
python -m game_analyst
```

The module entry point keeps imports and generated output paths consistent. It
stores the default history files inside the project directory, regardless of
the directory from which the command is launched.

Useful options:

```text
--top-n N              Keep the N highest-ranked predictions (default: 20)
--headed               Show the browser during scraping
--output-dir PATH      Store history directories below PATH
```

For example:

```powershell
python -m game_analyst --top-n 10
```

After installing the project, the equivalent console command is:

```powershell
game-analyst --top-n 10
```

The default run creates:

- `team_streaks_history/team_streaks_YYYYMMDD.json`
- `analysis_history/analysis_YYYYMMDD.json`
- `analysis_history/analysis_YYYYMMDD.md`

## Data and limitations

- The scraper filters for not-started matches scheduled for the current local date and a configured set of top tournaments.
- Team lookup and streak requests are rate-limited to be considerate of the upstream service.
- Predictions are heuristic rankings based on the available streak evidence; they are not guarantees or financial advice.
- If Sofascore changes its network response format or the wrapper API, the scraper or streak integration may need updating.

## Development checks

Compile the application modules with:

```powershell
python -m py_compile __init__.py __main__.py pipeline.py team_streaks.py sofascore_upcoming_scraper.py analyzer.py generate_analysis.py report_generator.py
```

The full pipeline requires network access, Playwright Chromium, and valid Sofascore responses. The analysis and report stages can also be exercised independently with an existing streak JSON file.
