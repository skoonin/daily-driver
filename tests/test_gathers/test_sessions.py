from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from daily_driver.gathers.sessions import ClaudeSession, gather_sessions

_SINCE = datetime(2026, 4, 10, 0, 0, 0)
_UNTIL = datetime(2026, 4, 20, 23, 59, 59)


def _write_session(
    home: Path,
    project_slug: str,
    session_id: str,
    *,
    timestamp: str | None = "2026-04-15T10:00:00Z",
    cwd: str | None = "/home/user/proj",
    extra_pre_lines: list[dict] | None = None,
) -> Path:
    """Write a JSONL session file mirroring Claude Code's on-disk format."""
    project_dir = home / ".claude" / "projects" / project_slug
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{session_id}.jsonl"

    lines: list[dict] = list(extra_pre_lines or [])
    if timestamp is not None or cwd is not None:
        entry: dict = {"type": "user", "sessionId": session_id}
        if timestamp is not None:
            entry["timestamp"] = timestamp
        if cwd is not None:
            entry["cwd"] = cwd
        lines.append(entry)

    with path.open("w", encoding="utf-8") as fp:
        for line in lines:
            fp.write(json.dumps(line) + "\n")
    return path


def test_gather_sessions_returns_empty_if_projects_dir_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert gather_sessions(_SINCE, _UNTIL) == []


def test_gather_sessions_walks_jsonl_files(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _write_session(
        tmp_path, "-Users-me-proj-a", "sess-a", timestamp="2026-04-11T08:00:00Z"
    )
    _write_session(
        tmp_path, "-Users-me-proj-b", "sess-b", timestamp="2026-04-12T09:00:00Z"
    )
    _write_session(
        tmp_path, "-Users-me-proj-a", "sess-c", timestamp="2026-04-13T10:00:00Z"
    )

    sessions = gather_sessions(_SINCE, _UNTIL)

    assert len(sessions) == 3
    assert all(isinstance(s, ClaudeSession) for s in sessions)
    assert {s.session_id for s in sessions} == {"sess-a", "sess-b", "sess-c"}


def test_gather_sessions_derives_session_id_from_filename(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _write_session(
        tmp_path,
        "-Users-me-proj",
        "a9431c28-7c85-45ad-992e-397490914739",
        timestamp="2026-04-15T10:00:00Z",
    )

    sessions = gather_sessions(_SINCE, _UNTIL)

    assert len(sessions) == 1
    assert sessions[0].session_id == "a9431c28-7c85-45ad-992e-397490914739"


def test_gather_sessions_skips_pre_timestamp_setup_lines(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Real Claude Code files have a few header lines (customTitle, agentSetting,
    # permissionMode, snapshot) before the first line carrying a timestamp.
    pre = [
        {"type": "customTitle", "sessionId": "sess-1", "customTitle": "x"},
        {"type": "agentSetting", "sessionId": "sess-1", "agentSetting": {}},
        {"type": "permissionMode", "sessionId": "sess-1", "permissionMode": "default"},
    ]
    _write_session(
        tmp_path,
        "-Users-me-proj",
        "sess-1",
        timestamp="2026-04-15T10:00:00Z",
        cwd="/projects/foo",
        extra_pre_lines=pre,
    )

    sessions = gather_sessions(_SINCE, _UNTIL)

    assert len(sessions) == 1
    assert sessions[0].cwd == "/projects/foo"


def test_gather_sessions_filters_by_window(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _write_session(
        tmp_path, "-Users-me-proj", "before", timestamp="2026-04-05T08:00:00Z"
    )
    _write_session(
        tmp_path, "-Users-me-proj", "inside", timestamp="2026-04-15T08:00:00Z"
    )
    _write_session(
        tmp_path, "-Users-me-proj", "after", timestamp="2026-04-25T08:00:00Z"
    )

    sessions = gather_sessions(_SINCE, _UNTIL)

    assert len(sessions) == 1
    assert sessions[0].session_id == "inside"


def test_gather_sessions_skips_files_with_no_timestamp(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # File exists but never has a `timestamp` line — skip silently.
    _write_session(
        tmp_path,
        "-Users-me-proj",
        "no-ts",
        timestamp=None,
        cwd=None,
        extra_pre_lines=[{"type": "customTitle", "customTitle": "x"}],
    )
    _write_session(tmp_path, "-Users-me-proj", "good", timestamp="2026-04-15T09:00:00Z")

    sessions = gather_sessions(_SINCE, _UNTIL)

    assert len(sessions) == 1
    assert sessions[0].session_id == "good"


def test_gather_sessions_tolerates_malformed_jsonl_lines(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    project_dir = tmp_path / ".claude" / "projects" / "-Users-me-proj"
    project_dir.mkdir(parents=True)
    path = project_dir / "sess-malformed.jsonl"
    path.write_text(
        "this is not json\n"
        + json.dumps({"type": "customTitle"})
        + "\n"
        + json.dumps({"type": "user", "timestamp": "2026-04-15T10:00:00Z", "cwd": "/x"})
        + "\n",
        encoding="utf-8",
    )

    sessions = gather_sessions(_SINCE, _UNTIL)

    assert len(sessions) == 1
    assert sessions[0].session_id == "sess-malformed"
    assert sessions[0].cwd == "/x"


def test_gather_sessions_message_count_defaults_to_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _write_session(tmp_path, "-Users-me-proj", "sess", timestamp="2026-04-15T10:00:00Z")

    sessions = gather_sessions(_SINCE, _UNTIL)

    assert len(sessions) == 1
    assert sessions[0].message_count == 0
