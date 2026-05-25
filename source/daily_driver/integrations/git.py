"""Subprocess wrapper for the `git` CLI.

The only place in the codebase that shells out to `git`. Callers in
`gathers/` receive raw stdout (or ``None`` for the "no usable repo" cases)
and own all parsing; they never import `subprocess`.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from daily_driver.core.logging import get_logger

log = get_logger(__name__)

_git_warned = False


class GitCommandError(RuntimeError):
    """`git` exited non-zero for a reason other than "not a git repository".

    Mirrors the `returncode` / `stderr` fields callers used to read off
    `subprocess.CalledProcessError`, so the gather layer can surface the
    real failure without importing `subprocess`.
    """

    def __init__(self, returncode: int, cmd: list[str], stderr: str = "") -> None:
        super().__init__(f"git exited {returncode}")
        self.returncode = returncode
        self.cmd = cmd
        self.stderr = stderr


def available() -> bool:
    """True if `git` is on PATH; warns once when it is missing."""
    global _git_warned
    if shutil.which("git"):
        return True
    if not _git_warned:
        log.warning("git binary not found on PATH; skipping git gather")
        _git_warned = True
    return False


def log_commits(
    repo_root: Path, since: datetime, until: datetime | None = None
) -> str | None:
    """Run `git log` for the window and return its raw NUL-formatted stdout.

    Returns ``None`` when there is nothing usable to parse:
      - `git` is not on PATH,
      - `repo_root` is not a git repository,
      - the command times out (30s bound).

    Raises ``GitCommandError`` on any other non-zero exit so genuine git
    failures stay visible rather than masquerading as "no commits".
    """
    if not available():
        return None

    cmd = [
        "git",
        "-C",
        str(repo_root),
        "log",
        "--since",
        since.isoformat(),
        *(["--until", until.isoformat()] if until else []),
        "--pretty=format:%h%x00%aI%x00%an%x00%s",
        "--no-color",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=30
        )
    except subprocess.TimeoutExpired:
        log.warning(
            "git: log command timed out after 30s for %s; returning no commits",
            repo_root,
        )
        return None

    if result.returncode != 0:
        if "not a git repository" in result.stderr:
            return None
        raise GitCommandError(result.returncode, cmd, result.stderr)

    return result.stdout
