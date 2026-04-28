from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from daily_driver.gathers.sessions import ClaudeSession, gather_sessions

_SINCE = datetime(2026, 4, 10, 0, 0, 0)
_UNTIL = datetime(2026, 4, 20, 23, 59, 59)


def _write_index(tmp_path: Path, data) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "sessions-index.json").write_text(json.dumps(data), encoding="utf-8")


def _session(
    session_id="sess-1",
    started_at="2026-04-15T10:00:00",
    cwd="/home/user",
    message_count=5,
):
    return {
        "session_id": session_id,
        "started_at": started_at,
        "cwd": cwd,
        "message_count": message_count,
    }


def test_gather_sessions_returns_empty_if_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = gather_sessions(_SINCE, _UNTIL)

    assert result == []


def test_gather_sessions_parses_list_json(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    entries = [
        _session("sess-1", "2026-04-11T08:00:00"),
        _session("sess-2", "2026-04-12T09:00:00"),
        _session("sess-3", "2026-04-13T10:00:00"),
    ]
    _write_index(tmp_path, entries)

    sessions = gather_sessions(_SINCE, _UNTIL)

    assert len(sessions) == 3
    assert all(isinstance(s, ClaudeSession) for s in sessions)
    ids = {s.session_id for s in sessions}
    assert ids == {"sess-1", "sess-2", "sess-3"}


def test_gather_sessions_parses_wrapped_json(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    entries = [
        _session("sess-1", "2026-04-11T08:00:00"),
        _session("sess-2", "2026-04-12T09:00:00"),
        _session("sess-3", "2026-04-13T10:00:00"),
    ]
    _write_index(tmp_path, {"sessions": entries})

    sessions = gather_sessions(_SINCE, _UNTIL)

    assert len(sessions) == 3
    ids = {s.session_id for s in sessions}
    assert ids == {"sess-1", "sess-2", "sess-3"}


def test_gather_sessions_tolerates_field_name_variants(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    entries = [
        # camelCase variants
        {
            "sessionId": "camel-sess",
            "startedAt": "2026-04-14T11:00:00",
            "workingDirectory": "/projects/foo",
            "messageCount": 3,
        },
        # snake_case with "id" and "created_at"
        {
            "id": "snake-sess",
            "created_at": "2026-04-16T12:00:00",
            "cwd": "/projects/bar",
            "message_count": 7,
        },
    ]
    _write_index(tmp_path, entries)

    sessions = gather_sessions(_SINCE, _UNTIL)

    assert len(sessions) == 2
    ids = {s.session_id for s in sessions}
    assert "camel-sess" in ids
    assert "snake-sess" in ids


def test_gather_sessions_filters_by_window(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    entries = [
        _session("before", "2026-04-05T08:00:00"),  # before window
        _session("inside", "2026-04-15T08:00:00"),  # in window
        _session("after", "2026-04-25T08:00:00"),  # after window
    ]
    _write_index(tmp_path, entries)

    sessions = gather_sessions(_SINCE, _UNTIL)

    assert len(sessions) == 1
    assert sessions[0].session_id == "inside"


def test_gather_sessions_skips_malformed_entry(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    entries = [
        # missing both id and started_at
        {"cwd": "/some/path"},
        _session("good-sess", "2026-04-15T09:00:00"),
    ]
    _write_index(tmp_path, entries)

    sessions = gather_sessions(_SINCE, _UNTIL)

    assert len(sessions) == 1
    assert sessions[0].session_id == "good-sess"
