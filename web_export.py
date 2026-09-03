from __future__ import annotations

import json
import re
from pathlib import Path

ANALYSIS_PATTERN = re.compile(r"^analysis_(?P<date>\d{8})\.json$")


def export_reports(
    output_dir: str | Path,
    destination_dir: str | Path,
    *,
    include_analysis: bool = True,
) -> list[dict[str, str]]:
    """Publish formatted daily analysis JSON and write the Pages index."""
    source_root = Path(output_dir)
    destination_root = Path(destination_dir)
    source_analysis = source_root / "analysis"
    destination_reports = destination_root / "data" / "reports"
    destination_analysis = destination_root / "data" / "analysis"
    destination_reports.mkdir(parents=True, exist_ok=True)

    for stale_report in destination_reports.glob("*.md"):
        stale_report.unlink()
    if destination_analysis.is_dir():
        for stale_analysis in destination_analysis.glob("*.json"):
            stale_analysis.unlink()

    entries: list[dict[str, str]] = []
    for source_path in sorted(source_analysis.glob("analysis_*.json")):
        match = ANALYSIS_PATTERN.match(source_path.name)
        if not match:
            continue
        report_date = match.group("date")
        report_name = f"{report_date}.json"
        data = json.loads(source_path.read_text(encoding="utf-8"))
        destination_path = destination_reports / report_name
        destination_path.write_text(
            json.dumps(
                {"date": report_date, "predictions": data},
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        entry = {
            "date": report_date,
            "report": f"data/reports/{report_name}",
        }
        entries.append(entry)

    entries.sort(key=lambda entry: entry["date"], reverse=True)
    index_path = destination_root / "data" / "reports.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_index = index_path.with_suffix(".json.tmp")
    temporary_index.write_text(
        json.dumps({"reports": entries}, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_index.replace(index_path)
    return entries
