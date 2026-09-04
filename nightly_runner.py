import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

from web_export import export_reports

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
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
    )

    for line in result.stdout.splitlines():
        logger.info("[%s] %s", command[0], line)
    for line in result.stderr.splitlines():
        logger.error("[%s] %s", command[0], line)

    if check and result.returncode:
        raise subprocess.CalledProcessError(
            result.returncode,
            result.args,
            output=result.stdout,
            stderr=result.stderr,
        )
    return result


def main():
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
        export_reports(PROJECT_DIR / "output", BETMAN_DIR)

        logger.info("Running git add .")

        _run_logged(
            ["git", "add", "."],
            cwd=PROJECT_DIR,
        )

        logger.info("Running git commit -m 'nightly results'")

        commit = _run_logged(
            ["git", "commit", "-m", "nightly results"],
            cwd=PROJECT_DIR,
            check=False,
        )

        if commit.returncode == 0:
            logger.info("Git commit created successfully.")
            logger.info(commit.stdout.strip())
        else:
            # git commit returns 1 when there is nothing to commit.
            if "nothing to commit" in commit.stdout.lower():
                logger.info("Nothing to commit.")
            else:
                logger.error("Git commit failed.")
                logger.error(commit.stdout.strip())
                logger.error(commit.stderr.strip())
                raise subprocess.CalledProcessError(
                    commit.returncode,
                    commit.args,
                    output=commit.stdout,
                    stderr=commit.stderr,
                )

        logger.info("Running git push origin main.")

        _run_logged(
            ["git", "push", "origin", "main"],
            cwd=PROJECT_DIR,
        )

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


def _publish_pages_repository() -> None:
    if not (BETMAN_DIR / ".git").is_dir():
        raise RuntimeError(
            f"Pages repository was not found at {BETMAN_DIR}. "
            "Place the betman checkout beside game_analyst."
        )

    _run_logged(["git", "add", "data"], cwd=BETMAN_DIR)
    commit = _run_logged(
        ["git", "commit", "-m", "daily report update"],
        cwd=BETMAN_DIR,
        check=False,
    )
    if commit.returncode not in (0, 1):
        raise subprocess.CalledProcessError(
            commit.returncode,
            commit.args,
            output=commit.stdout,
            stderr=commit.stderr,
        )
    if commit.returncode == 1 and "nothing to commit" not in commit.stdout.lower():
        raise subprocess.CalledProcessError(
            commit.returncode,
            commit.args,
            output=commit.stdout,
            stderr=commit.stderr,
        )

    _run_logged(["git", "push", "origin", "main"], cwd=BETMAN_DIR)


if __name__ == "__main__":
    main()
