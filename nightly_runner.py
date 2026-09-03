import logging
import subprocess
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


def main():
    logger.info("Nightly runner started.")
    logger.info("Starting game-analyst --date tomorrow...")

    try:
        subprocess.run(
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

        subprocess.run(
            ["git", "add", "."],
            cwd=PROJECT_DIR,
            check=True,
        )

        logger.info("Running git commit -m 'nightly results'")

        commit = subprocess.run(
            ["git", "commit", "-m", "nightly results"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
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

        subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=PROJECT_DIR,
            check=True,
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

    subprocess.run(["git", "add", "data"], cwd=BETMAN_DIR, check=True)
    commit = subprocess.run(
        ["git", "commit", "-m", "daily report update"],
        cwd=BETMAN_DIR,
        capture_output=True,
        text=True,
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

    subprocess.run(["git", "push", "origin", "main"], cwd=BETMAN_DIR, check=True)


if __name__ == "__main__":
    main()
