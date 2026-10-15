from __future__ import annotations

import json
import re
from pathlib import Path

ANALYSIS_PATTERN = re.compile(r"^analysis_(?P<date>\d{8})\.json$")


def _latest_analysis(source_analysis: Path) -> tuple[Path, str] | None:
    latest: tuple[Path, str] | None = None
    for path in source_analysis.glob("analysis_*.json"):
        match = ANALYSIS_PATTERN.match(path.name)
        if match is None:
            continue
        report_date = match.group("date")
        if latest is None or report_date > latest[1]:
            latest = path, report_date
    return latest


def _write_index(path: Path, entries: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps({"reports": entries}, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def export_reports(
    output_dir: str | Path,
    destination_dir: str | Path,
) -> list[dict[str, str]]:
    """Publish the newest analysis JSON and update the Pages index.

    Only the most recently generated report is copied; older reports already
    published in a previous run are left untouched (see archive_oldest_report
    for trimming the accumulated history).
    """
    source_root = Path(output_dir)
    destination_root = Path(destination_dir)
    source_analysis = source_root / "analysis"
    destination_reports = destination_root / "data" / "reports"
    destination_reports.mkdir(parents=True, exist_ok=True)

    index_path = destination_root / "data" / "reports.json"
    entries: list[dict[str, str]] = []
    if index_path.is_file():
        entries = json.loads(index_path.read_text(encoding="utf-8")).get("reports", [])

    latest = _latest_analysis(source_analysis)
    if latest is not None:
        latest_path, latest_date = latest
        report_name = f"{latest_date}.json"
        data = json.loads(latest_path.read_text(encoding="utf-8"))
        payload = (
            data
            if isinstance(data, dict) and {"date", "predictions"} <= set(data)
            else {"date": latest_date, "predictions": data}
        )
        destination_path = destination_reports / report_name
        destination_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        entries = [entry for entry in entries if entry["date"] != latest_date]
        entries.append({"date": latest_date, "report": f"data/reports/{report_name}"})

    entries.sort(key=lambda entry: entry["date"], reverse=True)
    _write_index(index_path, entries)
    return entries


def archive_oldest_report(destination_dir: str | Path) -> dict[str, str] | None:
    """Move the oldest report out of data/reports into data/archive and
    drop it from reports.json so the site only lists live reports."""
    destination_root = Path(destination_dir)
    destination_archive = destination_root / "data" / "archive"
    index_path = destination_root / "data" / "reports.json"

    if not index_path.is_file():
        return None

    index_data = json.loads(index_path.read_text(encoding="utf-8"))
    entries: list[dict[str, str]] = index_data.get("reports", [])
    if not entries:
        return None

    oldest_entry = min(entries, key=lambda entry: entry["date"])
    report_path = destination_root / oldest_entry["report"]
    if report_path.is_file():
        destination_archive.mkdir(parents=True, exist_ok=True)
        report_path.replace(destination_archive / report_path.name)

    remaining_entries = [entry for entry in entries if entry is not oldest_entry]
    _write_index(index_path, remaining_entries)

    return oldest_entry
