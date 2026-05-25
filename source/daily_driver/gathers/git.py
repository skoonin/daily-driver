from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from daily_driver.core.clock import now
from daily_driver.core.logging import get_logger, log_query_window
from daily_driver.integrations import git as git_integration

log = get_logger(__name__)


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
    log_query_window(
        log, f"git ({repo_root})", since, until if until is not None else now()
    )
    stdout = git_integration.log_commits(repo_root, since, until)
    if stdout is None:
        return []

    commits: list[GitCommit] = []
    for line in stdout.splitlines():
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
