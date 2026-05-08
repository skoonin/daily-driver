from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from daily_driver.core.clock import now
from daily_driver.core.logging import get_logger, log_query_window

log = get_logger(__name__)


class ClaudeSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    started_at: datetime
    cwd: str | None = None
    message_count: int = 0


def _projects_root() -> Path:
    return Path.home() / ".claude" / "projects"


def _scan_session_file(path: Path) -> tuple[datetime | None, str | None]:
    """Return (started_at, cwd) by reading lines until both are populated.

    started_at comes from the first JSONL line carrying a ``timestamp``;
    cwd from the first line carrying a ``cwd``. Real Claude Code session
    files put both on the first user-input line, so the scan terminates
    after a handful of lines rather than reading the whole file.
    """
    started_at: datetime | None = None
    cwd: str | None = None

    try:
        with path.open("r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if started_at is None and "timestamp" in obj:
                    try:
                        started_at = datetime.fromisoformat(
                            str(obj["timestamp"]).replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        pass
                if cwd is None and "cwd" in obj:
                    val = obj.get("cwd")
                    if val is not None:
                        cwd = str(val)
                if started_at is not None and cwd is not None:
                    break
    except OSError as exc:
        log.warning("sessions: could not read %s: %s", path, exc)
        return (None, None)

    return (started_at, cwd)


def gather_sessions(
    since: datetime, until: datetime | None = None
) -> list[ClaudeSession]:
    """Return Claude Code sessions whose start falls in [since, until).

    Walks ``~/.claude/projects/*/*.jsonl`` — the on-disk session store
    Claude Code writes one file per session to. Returns ``[]`` if the
    directory does not exist.
    """
    cutoff = until if until is not None else now()
    log_query_window(log, f"sessions ({_projects_root()})", since, cutoff)
    root = _projects_root()
    if not root.is_dir():
        return []

    sessions: list[ClaudeSession] = []

    for jsonl in root.glob("*/*.jsonl"):
        started_at, cwd = _scan_session_file(jsonl)
        if started_at is None:
            continue

        # CLI passes naive local datetimes; JSONL timestamps are aware (Z).
        # Convert aware -> local naive to match the comparison frame.
        if since.tzinfo is None and started_at.tzinfo is not None:
            started_at = started_at.astimezone().replace(tzinfo=None)

        if not (since <= started_at < cutoff):
            continue

        sessions.append(
            ClaudeSession(
                session_id=jsonl.stem,
                started_at=started_at,
                cwd=cwd,
            )
        )

    sessions.sort(key=lambda s: s.started_at)
    return sessions
