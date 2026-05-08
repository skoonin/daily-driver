from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from daily_driver.core.clock import now
from daily_driver.core.logging import get_logger

log = get_logger(__name__)


class ClaudeSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    started_at: datetime
    cwd: str | None = None
    message_count: int = 0


def _projects_root() -> Path:
    return Path.home() / ".claude" / "projects"


def _normalize_for_compare(dt: datetime, ref: datetime) -> datetime:
    """Match dt's timezone-awareness to ref's so they can be compared.

    The CLI hands us naive datetimes (date + time.min); JSONL timestamps are
    typically aware (ISO with Z). Coerce to ref's awareness rather than
    failing the comparison.
    """
    if ref.tzinfo is None and dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    if ref.tzinfo is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=ref.tzinfo)
    return dt


def _scan_session_file(path: Path) -> tuple[datetime | None, str | None, int]:
    """Return (started_at, cwd, message_count) by walking the JSONL.

    started_at = first line carrying a `timestamp` field.
    cwd        = first line carrying a `cwd` field.
    message_count = count of lines with a top-level `role` key.
    """
    started_at: datetime | None = None
    cwd: str | None = None
    message_count = 0

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
                if "role" in obj:
                    message_count += 1
    except OSError as exc:
        log.warning("sessions: could not read %s: %s", path, exc)
        return (None, None, 0)

    return (started_at, cwd, message_count)


def gather_sessions(
    since: datetime, until: datetime | None = None
) -> list[ClaudeSession]:
    """Return Claude Code sessions whose start falls in [since, until).

    Walks ``~/.claude/projects/*/*.jsonl`` — the on-disk session store
    Claude Code writes one file per session to. Returns ``[]`` if the
    directory does not exist.
    """
    root = _projects_root()
    if not root.is_dir():
        return []

    cutoff = until if until is not None else now()
    sessions: list[ClaudeSession] = []

    for jsonl in root.glob("*/*.jsonl"):
        session_id = jsonl.stem
        started_at, cwd, message_count = _scan_session_file(jsonl)
        if started_at is None:
            continue

        ref_started = _normalize_for_compare(started_at, since)
        if not (since <= ref_started < cutoff):
            continue

        sessions.append(
            ClaudeSession(
                session_id=session_id,
                started_at=ref_started,
                cwd=cwd,
                message_count=message_count,
            )
        )

    sessions.sort(key=lambda s: s.started_at)
    return sessions
