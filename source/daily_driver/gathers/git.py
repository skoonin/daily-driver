from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from daily_driver.core.logging import get_logger

log = get_logger(__name__)

_git_warned = False


class GitCommit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sha: str
    timestamp: datetime
    author: str
    subject: str


def gather_commits(
    repo_root: Path, since: datetime, until: datetime | None = None
) -> list[GitCommit]:
    """Return commits in [since, until or HEAD). Empty list if not a git repo."""
    global _git_warned
    if not shutil.which("git"):
        if not _git_warned:
            log.warning("git binary not found on PATH; skipping git gather")
            _git_warned = True
        return []

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
        return []

    if result.returncode != 0:
        if "not a git repository" in result.stderr:
            return []
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr
        )

    commits: list[GitCommit] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\x00", 3)
        if len(parts) != 4:
            log.warning("git: unexpected log line format: %r", line[:80])
            continue
        sha, raw_ts, author, subject = parts
        commits.append(
            GitCommit(
                sha=sha,
                timestamp=datetime.fromisoformat(raw_ts),
                author=author,
                subject=subject,
            )
        )
    return commits
