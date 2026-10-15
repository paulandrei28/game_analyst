"""Shared subprocess and Pages publishing helpers for runner scripts."""
from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from pathlib import Path


def run_logged(
    command: list[str],
    *,
    cwd: Path,
    logger: logging.Logger,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)

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


def publish_pages_repository(
    repository_dir: Path,
    run_command: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    if not (repository_dir / ".git").is_dir():
        raise RuntimeError(
            f"Pages repository was not found at {repository_dir}. "
            "Place the betman checkout beside game_analyst."
        )

    run_command(["git", "add", "data"], cwd=repository_dir)
    commit = run_command(
        ["git", "commit", "-m", "daily report update"],
        cwd=repository_dir,
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

    run_command(["git", "push", "origin", "main"], cwd=repository_dir)
