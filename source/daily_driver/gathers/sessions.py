from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

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


def _extract(obj: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in obj:
            return obj[key]
    return default


def gather_sessions(
    since: datetime, until: datetime | None = None
) -> list[ClaudeSession]:
    """Parse ~/.claude/sessions-index.json for sessions in window. [] if file missing."""
    path = Path.home() / ".claude" / "sessions-index.json"
    if not path.exists():
        return []

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("sessions: could not load sessions-index.json: %s", exc)
        return []

    # Accept both a bare list and {"sessions": [...]}
    if isinstance(raw, dict):
        entries = raw.get("sessions", [])
    elif isinstance(raw, list):
        entries = raw
    else:
        log.warning("sessions: unexpected sessions-index.json format")
        return []

    cutoff = until if until is not None else now()
    sessions: list[ClaudeSession] = []

    for entry in entries:
        if not isinstance(entry, dict):
            log.warning("sessions: skipping non-dict entry: %r", str(entry)[:80])
            continue

        try:
            session_id = _extract(entry, "session_id", "id", "sessionId", default=None)
            if session_id is None:
                log.warning(
                    "sessions: entry missing id field, skipping: %r", str(entry)[:80]
                )
                continue

            raw_ts = _extract(
                entry,
                "started_at",
                "startedAt",
                "created_at",
                "timestamp",
                default=None,
            )
            if raw_ts is None:
                log.warning(
                    "sessions: entry missing timestamp for %r, skipping", session_id
                )
                continue

            started_at = datetime.fromisoformat(str(raw_ts))
            cwd = _extract(entry, "cwd", "workingDirectory", default=None)
            message_count = int(
                _extract(entry, "message_count", "messageCount", default=0) or 0
            )
        except Exception as exc:
            log.warning("sessions: malformed session entry, skipping: %s", exc)
            continue

        if not (since <= started_at < cutoff):
            continue

        sessions.append(
            ClaudeSession(
                session_id=str(session_id),
                started_at=started_at,
                cwd=str(cwd) if cwd is not None else None,
                message_count=message_count,
            )
        )

    return sessions
