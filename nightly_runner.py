import json
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

from web_export import archive_oldest_report, export_reports

try:
    from .runner_support import publish_pages_repository, run_logged
except ImportError:  # pragma: no cover
    from runner_support import publish_pages_repository, run_logged

PROJECT_DIR = Path(__file__).resolve().parent
BETMAN_DIR = PROJECT_DIR.parent / "betman"

LOG_DIR = PROJECT_DIR / "output" / "nightly_log"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR / f"nightly_{datetime.now():%Y%m%d}.log"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger(__name__)


def _run_logged(
    command: list[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run_logged(command, cwd=cwd, logger=logger, check=check)


def _put_pc_to_sleep() -> None:
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "Add-Type -AssemblyName System.Windows.Forms; "
            "[System.Windows.Forms.Application]::SetSuspendState("
            "[System.Windows.Forms.PowerState]::Suspend,"
            "$false,"
            "$false"
            ")",
        ],
        check=True,
    )


def _publish_pages_repository() -> None:
    publish_pages_repository(BETMAN_DIR, _run_logged)


def main() -> None:
    logger.info("Nightly runner started; waiting 10 minutes for system connections.")
    time.sleep(10 * 60)
    logger.info("Startup buffer complete.")
    logger.info("Starting game-analyst --date tomorrow...")

    try:
        _run_logged(
            [
                "game-analyst",
                "--date",
                "tomorrow",
            ],
            cwd=PROJECT_DIR,
            check=True,
        )

        logger.info("Analysis finished successfully.")

        logger.info("Exporting reports to %s", BETMAN_DIR)
        reports_index = BETMAN_DIR / "data" / "reports.json"
        previously_published_dates = set()
        if reports_index.is_file():
            previous_entries = json.loads(
                reports_index.read_text(encoding="utf-8")
            ).get("reports", [])
            previously_published_dates = {
                entry["date"] for entry in previous_entries
            }

        published_entries = export_reports(PROJECT_DIR / "output", BETMAN_DIR)
        uploaded_new_report = any(
            entry["date"] not in previously_published_dates
            for entry in published_entries
        )
        if uploaded_new_report:
            archive_oldest_report(BETMAN_DIR)
        else:
            logger.info("No new report was uploaded; skipping oldest-report cleanup.")

        _publish_pages_repository()
        logger.info("Nightly results and Pages site pushed successfully.")

    except subprocess.CalledProcessError as exc:
        logger.exception(
            "Command failed with exit code %s.",
            exc.returncode,
        )
        raise

    except Exception:
        logger.exception("Unexpected error while running nightly analysis.")
        raise

    finally:
        logger.info("Nightly runner finished.")
        _put_pc_to_sleep()


if __name__ == "__main__":
    main()
