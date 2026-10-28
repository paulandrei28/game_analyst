"""Shared subprocess and Pages publishing helpers for runner scripts."""
from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _read_and_log(stream, logger: logging.Logger, command_name: str, level: int) -> str:
    chunks: list[str] = []
    for line in stream:
        chunks.append(line)
        logger.log(level, "[%s] %s", command_name, line.rstrip("\r\n"))
    return "".join(chunks)


def run_logged(
    command: list[str],
    *,
    cwd: Path,
    logger: logging.Logger,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    with subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        bufsize=1,
    ) as process:
        assert process.stdout is not None
        assert process.stderr is not None
        with ThreadPoolExecutor(max_workers=2) as readers:
            stdout_future = readers.submit(
                _read_and_log, process.stdout, logger, command[0], logging.INFO
            )
            stderr_future = readers.submit(
                _read_and_log, process.stderr, logger, command[0], logging.ERROR
            )
            returncode = process.wait()
            stdout = stdout_future.result()
            stderr = stderr_future.result()

    result = subprocess.CompletedProcess(command, returncode, stdout, stderr)

    if check and result.returncode:
        raise subprocess.CalledProcessError(
            result.returncode,
            result.args,
            output=stdout,
            stderr=stderr,
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
            output=commit.stdout or "",
            stderr=commit.stderr or "",
        )
    if (
        commit.returncode == 1
        and "nothing to commit" not in (commit.stdout or "").lower()
    ):
        raise subprocess.CalledProcessError(
            commit.returncode,
            commit.args,
            output=commit.stdout or "",
            stderr=commit.stderr or "",
        )

    run_command(["git", "push", "origin", "main"], cwd=repository_dir)
