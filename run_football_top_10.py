"""Run the football Top 10 skill through Codex CLI."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PICK_LINE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")
MATCH_HEADING = re.compile(r"^\s*###\s+(.+?)\s*$")


def _normalize_pick_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _normalize_market_text(value: str) -> str:
    normalized = _normalize_pick_text(value)
    normalized = re.sub(r"^more than\s+", "over ", normalized)
    normalized = re.sub(r"^less than\s+", "under ", normalized)
    if normalized in {"both teams scoring", "both teams to score", "btts"}:
        return "btts"
    return normalized


def validate_ai_pick(
    ai_pick: str,
    candidates: list[dict] | None = None,
) -> str | None:
    candidate_keys = None
    if candidates is not None:
        candidate_keys = {
            (
                _normalize_pick_text(f"{item.get('home', '')} - {item.get('away', '')}"),
                _normalize_market_text(str(item.get("market", ""))),
            )
            for item in candidates
            if isinstance(item, dict)
        }

    ranks: list[int] = []
    current_match: str | None = None
    selected_keys: set[tuple[str, str]] = set()
    selected_market_kinds: dict[str, set[str]] = {}
    for line in ai_pick.splitlines():
        if not line.strip():
            continue
        heading = MATCH_HEADING.fullmatch(line)
        if heading:
            current_match = _normalize_pick_text(heading.group(1))
            continue

        pick = PICK_LINE.fullmatch(line)
        if pick is None:
            return "Codex response must contain only match headings and numbered picks."
        if current_match is None:
            return "Each Codex pick must appear under its match heading."

        ranks.append(int(pick.group(1)))
        normalized_market = _normalize_market_text(pick.group(2))
        market_kind = None
        if normalized_market == "btts":
            market_kind = "btts"
        elif normalized_market.endswith(" no clean sheet") or normalized_market == "no clean sheet":
            market_kind = "no_clean_sheet"
        previous_kinds = selected_market_kinds.setdefault(current_match, set())
        if market_kind == "btts" and "no_clean_sheet" in previous_kinds:
            return (
                "Codex selected both teams scoring and a no clean sheet pick for the same match; "
                "analysis was not changed."
            )
        if market_kind == "no_clean_sheet" and "btts" in previous_kinds:
            return (
                "Codex selected both teams scoring and a no clean sheet pick for the same match; "
                "analysis was not changed."
            )
        if market_kind is not None:
            previous_kinds.add(market_kind)

        selected_key = (current_match, normalized_market)
        if selected_key in selected_keys:
            return "Codex repeated a pick; analysis was not changed."
        selected_keys.add(selected_key)
        if candidate_keys is not None:
            if selected_key not in candidate_keys:
                return (
                    "Codex returned a pick that does not match an analyzer candidate; "
                    "analysis was not changed."
                )

    if not ranks:
        return "Codex returned no numbered picks; analysis was not changed."
    if len(ranks) > 10:
        return f"Codex returned {len(ranks)} picks; the limit is 10. Analysis was not changed."
    expected = list(range(1, len(ranks) + 1))
    if ranks != expected:
        return "Codex pick ranks must be consecutive from 1; analysis was not changed."
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", nargs="?", help="Analysis date in YYYYMMDD format")
    parser.add_argument("--model", default="gpt-5.6-luna", help="Codex model (default: gpt-5.6-luna)")
    parser.add_argument("--analysis-file", type=Path, help="Use this analysis JSON instead of the default output path")
    args = parser.parse_args()

    if args.date is None:
        source_name = args.analysis_file.name if args.analysis_file else ""
        source_date = re.fullmatch(r"analysis_(\d{8})\.json", source_name)
        args.date = (
            source_date.group(1)
            if source_date
            else date.today().strftime("%Y%m%d")
        )
    if len(args.date) != 8 or not args.date.isdigit():
        parser.error("date must use YYYYMMDD format")
    try:
        datetime.strptime(args.date, "%Y%m%d")
    except ValueError:
        parser.error("date must be a valid calendar date in YYYYMMDD format")

    analysis = (args.analysis_file or ROOT / "output" / "analysis" / f"analysis_{args.date}.json").resolve()
    if not analysis.is_file():
        parser.error(f"analysis file not found: {analysis}")
    try:
        payload = json.loads(analysis.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"could not read analysis JSON: {exc}")
    if not isinstance(payload, dict) or not isinstance(payload.get("predictions"), list):
        parser.error("analysis JSON must contain a predictions list")
    if not payload["predictions"]:
        print("No analyzer candidates; no AI picks generated.")
        return 0

    cli = shutil.which("codex.exe") or shutil.which("codex.cmd") or shutil.which("codex")
    if not cli:
        parser.error("Codex CLI was not found on PATH")

    prompt = (
        f"Use the $football-top-10 skill to select predictions for {args.date}. "
        "The analyser candidates and their evidence are below. Select from these candidates only. "
        "Return no more than 10 picks (or all eligible candidates if fewer), numbered consecutively from 1. "
        "Count the numbered pick lines before responding and ensure there are no more than 10. "
        "Follow the skill and return only the ranked Top 10 grouped by match.\n\n"
        + json.dumps(payload["predictions"], ensure_ascii=False)
    )
    with tempfile.TemporaryDirectory(prefix="football_top_10_") as temp_dir:
        response_path = Path(temp_dir) / "ai_pick.txt"
        command = [
            cli, "exec", "--ephemeral", "--sandbox", "read-only", "--cd", str(ROOT),
            "--model", args.model, "-c", "model_reasoning_effort=low",
            "--output-last-message", str(response_path), "-",
        ]
        if os.name == "nt" and cli.lower().endswith((".cmd", ".bat")):
            result = subprocess.run(
                subprocess.list2cmdline(command), cwd=ROOT, input=prompt,
                text=True, encoding="utf-8", shell=True, check=False,
            )
        else:
            result = subprocess.run(
                command, cwd=ROOT, input=prompt, text=True, encoding="utf-8", check=False
            )
        if result.returncode:
            return result.returncode
        if not response_path.is_file():
            print("Codex completed without writing a final response.", file=sys.stderr)
            return 1
        ai_pick = response_path.read_text(encoding="utf-8").strip()
        if not ai_pick:
            print("Codex returned an empty response; analysis was not changed.", file=sys.stderr)
            return 1
        validation_error = validate_ai_pick(ai_pick, payload["predictions"])
        if validation_error:
            print(validation_error, file=sys.stderr)
            return 1

    payload["ai_pick"] = ai_pick
    temporary_analysis = analysis.with_suffix(analysis.suffix + ".tmp")
    temporary_analysis.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary_analysis.replace(analysis)
    print(ai_pick)
    return 0


if __name__ == "__main__":
    sys.exit(main())
