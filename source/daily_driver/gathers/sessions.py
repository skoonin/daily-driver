from __future__ import annotations

import json
import os
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from daily_driver.core.logging import get_logger, log_query_window
from daily_driver.integrations import opencode

log = get_logger(__name__)


class Session(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: Literal["claude", "opencode"]
    id: str
    started: AwareDatetime
    updated: AwareDatetime
    directory: str | None = None
    title: str | None = None
    path: str | None = None


def _claude_projects_root() -> Path:
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(config_dir) if config_dir else Path.home() / ".claude"
    return base / "projects"


def _scan_claude_file(
    path: Path,
) -> tuple[datetime | None, str | None, str | None]:
    """Return (started, directory, title) for one Claude Code transcript.

    Title records repeat through a file and the last one wins, so the scan
    reads to the end; only lines that can matter are parsed because
    transcripts can reach tens of megabytes.
    """
    started: datetime | None = None
    directory: str | None = None
    custom_title: str | None = None
    ai_title: str | None = None

    try:
        # A torn write can leave invalid bytes; one bad line must not lose the file.
        with path.open("r", encoding="utf-8", errors="replace") as transcript:
            for line in transcript:
                needs_header = started is None or directory is None
                if not needs_header and '-title"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                if started is None and "timestamp" in record:
                    try:
                        started = datetime.fromisoformat(
                            str(record["timestamp"]).replace("Z", "+00:00")
                        )
                    except ValueError:
                        pass
                if directory is None and record.get("cwd") is not None:
                    directory = str(record["cwd"])
                custom = record.get("customTitle")
                if isinstance(custom, str) and custom:
                    custom_title = custom
                ai = record.get("aiTitle")
                if isinstance(ai, str) and ai:
                    ai_title = ai
    except OSError as exc:
        log.warning("sessions: could not read %s: %s", path, exc)
        return (None, None, None)

    return (started, directory, custom_title or ai_title)


def _claude_sessions(since: datetime, until: datetime) -> list[Session]:
    root = _claude_projects_root()
    if not root.is_dir():
        return []

    since_ts = since.timestamp()
    until_ts = until.timestamp()
    sessions: list[Session] = []
    # One session can have a transcript in two project folders; keep the newest.
    newest: dict[str, tuple[float, Path]] = {}

    for transcript in root.glob("*/*.jsonl"):
        if ".orphaned-" in transcript.name:
            continue
        try:
            updated_ts = transcript.stat().st_mtime
        except OSError as exc:
            log.warning("sessions: could not stat %s: %s", transcript, exc)
            continue
        if updated_ts < since_ts:
            continue
        kept = newest.get(transcript.stem)
        if kept is None or updated_ts > kept[0]:
            newest[transcript.stem] = (updated_ts, transcript)

    for updated_ts, transcript in newest.values():
        started, directory, title = _scan_claude_file(transcript)
        if started is None or started.timestamp() >= until_ts:
            continue

        sessions.append(
            Session(
                tool="claude",
                id=transcript.stem,
                started=started.astimezone(),
                updated=datetime.fromtimestamp(updated_ts).astimezone(),
                directory=directory,
                title=title,
                path=str(transcript),
            )
        )
    return sessions


def _opencode_row(row: object) -> Session | None:
    if not isinstance(row, dict):
        return None
    session_id = row.get("id")
    times = row.get("time")
    if not isinstance(session_id, str) or not isinstance(times, dict):
        return None
    try:
        started = datetime.fromtimestamp(int(times["created"]) / 1000).astimezone()
        updated = datetime.fromtimestamp(int(times["updated"]) / 1000).astimezone()
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None

    location = row.get("location")
    directory = location.get("directory") if isinstance(location, dict) else None
    title = row.get("title")
    return Session(
        tool="opencode",
        id=session_id,
        started=started,
        updated=updated,
        directory=directory if isinstance(directory, str) else None,
        title=title if isinstance(title, str) and title else None,
    )


def _opencode_sessions(since: datetime, until: datetime) -> list[Session]:
    since_ts = since.timestamp()
    until_ts = until.timestamp()
    sessions: list[Session] = []
    seen_cursors: set[str] = set()
    seen_ids: set[str] = set()
    # Top-level sessions only; the default listing includes subagent children.
    query = "parentID=null"

    while True:
        output = opencode.list_sessions(query)
        if output is None:
            break
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            payload = None
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or not isinstance(rows, list):
            log.warning("sessions: unexpected output from opencode session list")
            break

        page = [session for session in map(_opencode_row, rows) if session]
        for session in page:
            # A session updated mid-paging can reappear on a later page.
            if (
                session.id not in seen_ids
                and session.started.timestamp() < until_ts
                and session.updated.timestamp() >= since_ts
            ):
                seen_ids.add(session.id)
                sessions.append(session)

        cursor = payload.get("cursor")
        next_cursor = cursor.get("next") if isinstance(cursor, dict) else None
        reaches_since = bool(page) and (
            min(session.updated.timestamp() for session in page) >= since_ts
        )
        if not next_cursor or next_cursor in seen_cursors or not reaches_since:
            break
        seen_cursors.add(next_cursor)
        query = urllib.parse.urlencode({"parentID": "null", "cursor": next_cursor})

    return sessions


def gather_sessions(since: datetime, until: datetime) -> list[Session]:
    """Return Claude Code and opencode sessions overlapping [since, until).

    A session is kept when it started before `until` and was last updated at
    or after `since`. Rows from both tools are merged, newest update first;
    a tool with no data adds nothing and never hides the other.
    """
    log_query_window(log, "sessions", since, until)
    sessions = _claude_sessions(since, until) + _opencode_sessions(since, until)
    sessions.sort(key=lambda session: session.updated, reverse=True)
    return sessions
