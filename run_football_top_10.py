"""Run the football Top 10 skill through Codex CLI."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", nargs="?", default="20261023", help="Analysis date in YYYYMMDD format")
    parser.add_argument("--model", default="gpt-5.6-luna", help="Codex model (default: gpt-5.6-luna)")
    parser.add_argument("--analysis-file", type=Path, help="Use this analysis JSON instead of the default output path")
    args = parser.parse_args()

    if len(args.date) != 8 or not args.date.isdigit():
        parser.error("date must use YYYYMMDD format")

    analysis = (args.analysis_file or ROOT / "output" / "analysis" / f"analysis_{args.date}.json").resolve()
    if not analysis.is_file():
        parser.error(f"analysis file not found: {analysis}")
    try:
        payload = json.loads(analysis.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"could not read analysis JSON: {exc}")
    if not isinstance(payload, dict) or not isinstance(payload.get("predictions"), list):
        parser.error("analysis JSON must contain a predictions list")

    cli = shutil.which("codex.exe") or shutil.which("codex.cmd") or shutil.which("codex")
    if not cli:
        parser.error("Codex CLI was not found on PATH")

    prompt = (
        f"Use the $football-top-10 skill to select predictions for {args.date}. "
        "The analyser candidates and their evidence are below. Select from these candidates only. "
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
                text=True, shell=True, check=False,
            )
        else:
            result = subprocess.run(command, cwd=ROOT, input=prompt, text=True, check=False)
        if result.returncode:
            return result.returncode
        if not response_path.is_file():
            print("Codex completed without writing a final response.", file=sys.stderr)
            return 1
        ai_pick = response_path.read_text(encoding="utf-8").strip()
        if not ai_pick:
            print("Codex returned an empty response; analysis was not changed.", file=sys.stderr)
            return 1

    payload["ai_pick"] = ai_pick
    temporary_analysis = analysis.with_suffix(analysis.suffix + ".tmp")
    temporary_analysis.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary_analysis.replace(analysis)
    print(ai_pick)
    return 0


if __name__ == "__main__":
    sys.exit(main())
