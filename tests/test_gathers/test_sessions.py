"""Tests for ``daily_driver.gathers.sessions``."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from daily_driver.gathers.sessions import Session, gather_sessions
from daily_driver.integrations import opencode

# The CLI passes naive local datetimes (start of day).
_SINCE = datetime(2026, 11, 25, 0, 0)
_UNTIL = datetime(2026, 11, 26, 0, 0)


def _utc_iso(local: datetime) -> str:
    """A naive local time as the UTC ISO string Claude Code writes."""
    return (
        local.astimezone().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    )


def _ms(local: datetime) -> int:
    return int(local.timestamp() * 1000)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return home


@pytest.fixture
def no_opencode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opencode, "list_sessions", lambda query: None)


def _write_claude(
    root: Path,
    session_id: str,
    *,
    started: datetime,
    updated: datetime,
    cwd: str | None = "/Users/me/git/proj",
    records: list[dict] | None = None,
    name: str | None = None,
) -> Path:
    """Write a transcript shaped like Claude Code's, with its mtime set."""
    folder = root / "projects" / "-Users-me-git-proj"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name or f"{session_id}.jsonl")
    lines: list[dict] = list(records or [])
    user: dict = {
        "type": "user",
        "sessionId": session_id,
        "timestamp": _utc_iso(started),
    }
    if cwd is not None:
        user["cwd"] = cwd
    lines.insert(0, user)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    os.utime(path, (updated.timestamp(), updated.timestamp()))
    return path


def _opencode_page(rows: list[dict], next_cursor: str | None = None) -> str:
    return json.dumps({"data": rows, "cursor": {"previous": None, "next": next_cursor}})


def _opencode_row(
    session_id: str,
    *,
    created: datetime,
    updated: datetime,
    title: str | None = "Fix the thing",
    directory: str = "/Users/me/git/proj",
) -> dict:
    row: dict = {
        "id": session_id,
        "projectID": "p1",
        "time": {"created": _ms(created), "updated": _ms(updated)},
        "location": {"directory": directory},
    }
    if title is not None:
        row["title"] = title
    return row


# --- Claude Code transcripts ---


def test_claude_session_in_window(home: Path, no_opencode: None) -> None:
    path = _write_claude(
        home / ".claude",
        "abc",
        started=datetime(2026, 11, 25, 9, 0),
        updated=datetime(2026, 11, 25, 11, 30),
    )

    sessions = gather_sessions(_SINCE, _UNTIL)

    assert len(sessions) == 1
    session = sessions[0]
    assert isinstance(session, Session)
    assert session.tool == "claude"
    assert session.id == "abc"
    assert session.directory == "/Users/me/git/proj"
    assert session.path == str(path)
    assert session.started == datetime(2026, 11, 25, 9, 0).astimezone()
    assert session.updated == datetime(2026, 11, 25, 11, 30).astimezone()
    assert session.started.tzinfo is not None


def test_claude_session_outside_window_is_dropped(
    home: Path, no_opencode: None
) -> None:
    _write_claude(
        home / ".claude",
        "old",
        started=datetime(2026, 11, 20, 9, 0),
        updated=datetime(2026, 11, 20, 10, 0),
    )
    _write_claude(
        home / ".claude",
        "future",
        started=datetime(2026, 11, 26, 9, 0),
        updated=datetime(2026, 11, 26, 10, 0),
    )

    assert gather_sessions(_SINCE, _UNTIL) == []


def test_claude_session_continued_into_window_is_kept(
    home: Path, no_opencode: None
) -> None:
    _write_claude(
        home / ".claude",
        "overnight",
        started=datetime(2026, 11, 24, 22, 0),
        updated=datetime(2026, 11, 25, 1, 0),
    )

    assert [s.id for s in gather_sessions(_SINCE, _UNTIL)] == ["overnight"]


def test_claude_title_prefers_last_custom_title(home: Path, no_opencode: None) -> None:
    _write_claude(
        home / ".claude",
        "titled",
        started=datetime(2026, 11, 25, 9, 0),
        updated=datetime(2026, 11, 25, 10, 0),
        records=[
            {"type": "ai-title", "aiTitle": "AI first", "sessionId": "titled"},
            {"type": "custom-title", "customTitle": "Mine old", "sessionId": "titled"},
            {"type": "ai-title", "aiTitle": "AI last", "sessionId": "titled"},
            {"type": "custom-title", "customTitle": "Mine new", "sessionId": "titled"},
        ],
    )

    assert gather_sessions(_SINCE, _UNTIL)[0].title == "Mine new"


def test_claude_title_falls_back_to_last_ai_title(
    home: Path, no_opencode: None
) -> None:
    _write_claude(
        home / ".claude",
        "ai",
        started=datetime(2026, 11, 25, 9, 0),
        updated=datetime(2026, 11, 25, 10, 0),
        records=[
            {"type": "ai-title", "aiTitle": "AI first", "sessionId": "ai"},
            {"type": "ai-title", "aiTitle": "AI last", "sessionId": "ai"},
        ],
    )

    assert gather_sessions(_SINCE, _UNTIL)[0].title == "AI last"


def test_claude_untitled_session_has_no_title(home: Path, no_opencode: None) -> None:
    _write_claude(
        home / ".claude",
        "plain",
        started=datetime(2026, 11, 25, 9, 0),
        updated=datetime(2026, 11, 25, 10, 0),
    )

    assert gather_sessions(_SINCE, _UNTIL)[0].title is None


def test_claude_orphaned_transcript_is_skipped(home: Path, no_opencode: None) -> None:
    _write_claude(
        home / ".claude",
        "ghost",
        started=datetime(2026, 11, 25, 9, 0),
        updated=datetime(2026, 11, 25, 10, 0),
        name="ghost.orphaned-1764000000-ab12.jsonl",
    )

    assert gather_sessions(_SINCE, _UNTIL) == []


def test_claude_file_without_timestamp_is_skipped(
    home: Path, no_opencode: None
) -> None:
    folder = home / ".claude" / "projects" / "-x"
    folder.mkdir(parents=True)
    path = folder / "empty.jsonl"
    path.write_text("not json\n" + json.dumps({"type": "summary"}) + "\n")
    stamp = datetime(2026, 11, 25, 10, 0).timestamp()
    os.utime(path, (stamp, stamp))

    assert gather_sessions(_SINCE, _UNTIL) == []


def test_claude_config_dir_is_honored(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_opencode: None
) -> None:
    config_dir = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    _write_claude(
        config_dir,
        "moved",
        started=datetime(2026, 11, 25, 9, 0),
        updated=datetime(2026, 11, 25, 10, 0),
    )
    _write_claude(
        home / ".claude",
        "default-home",
        started=datetime(2026, 11, 25, 9, 0),
        updated=datetime(2026, 11, 25, 10, 0),
    )

    assert [s.id for s in gather_sessions(_SINCE, _UNTIL)] == ["moved"]


def test_no_claude_folder_gives_no_rows(home: Path, no_opencode: None) -> None:
    assert gather_sessions(_SINCE, _UNTIL) == []


# --- opencode ---


def test_opencode_rows_in_window(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queries: list[str] = []
    page = _opencode_page(
        [
            _opencode_row(
                "ses_new",
                created=datetime(2026, 11, 25, 8, 0),
                updated=datetime(2026, 11, 25, 9, 0),
                directory="/Users/me/git/other",
            ),
            _opencode_row(
                "ses_overnight",
                created=datetime(2026, 11, 24, 23, 0),
                updated=datetime(2026, 11, 25, 0, 30),
                title=None,
            ),
            _opencode_row(
                "ses_old",
                created=datetime(2026, 11, 20, 8, 0),
                updated=datetime(2026, 11, 20, 9, 0),
            ),
        ]
    )

    def fake(query: str) -> str:
        queries.append(query)
        return page

    monkeypatch.setattr(opencode, "list_sessions", fake)

    sessions = gather_sessions(_SINCE, _UNTIL)

    assert [s.id for s in sessions] == ["ses_new", "ses_overnight"]
    assert all(s.tool == "opencode" for s in sessions)
    assert sessions[0].directory == "/Users/me/git/other"
    assert sessions[0].title == "Fix the thing"
    assert sessions[0].path is None
    assert sessions[0].started == datetime(2026, 11, 25, 8, 0).astimezone()
    assert sessions[1].title is None
    assert queries == ["parentID=null"]


def test_opencode_follows_cursor_while_pages_reach_since(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = {
        "parentID=null": _opencode_page(
            [
                _opencode_row(
                    "ses_1",
                    created=datetime(2026, 11, 25, 9, 0),
                    updated=datetime(2026, 11, 25, 10, 0),
                )
            ],
            next_cursor="c 2",
        ),
        "parentID=null&cursor=c+2": _opencode_page(
            [
                _opencode_row(
                    "ses_2",
                    created=datetime(2026, 11, 25, 1, 0),
                    updated=datetime(2026, 11, 25, 2, 0),
                ),
                _opencode_row(
                    "ses_3",
                    created=datetime(2026, 11, 23, 1, 0),
                    updated=datetime(2026, 11, 23, 2, 0),
                ),
            ],
            next_cursor="c3",
        ),
    }
    queries: list[str] = []

    def fake(query: str) -> str:
        queries.append(query)
        return pages[query]

    monkeypatch.setattr(opencode, "list_sessions", fake)

    sessions = gather_sessions(_SINCE, _UNTIL)

    assert [s.id for s in sessions] == ["ses_1", "ses_2"]
    # The second page already reaches before --since, so the third is never asked for.
    assert queries == ["parentID=null", "parentID=null&cursor=c+2"]


def test_opencode_repeated_cursor_stops(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = _opencode_page(
        [
            _opencode_row(
                "ses_1",
                created=datetime(2026, 11, 25, 9, 0),
                updated=datetime(2026, 11, 25, 10, 0),
            )
        ],
        next_cursor="same",
    )
    queries: list[str] = []

    def fake(query: str) -> str:
        queries.append(query)
        return page

    monkeypatch.setattr(opencode, "list_sessions", fake)

    assert [s.id for s in gather_sessions(_SINCE, _UNTIL)] == ["ses_1"]
    assert len(queries) == 2


@pytest.mark.parametrize(
    "output",
    [
        "not json",
        json.dumps({"error": "nope"}),
        json.dumps({"data": [{"id": "x"}, "junk"]}),
    ],
)
def test_opencode_bad_output_gives_no_rows(
    home: Path, monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    monkeypatch.setattr(opencode, "list_sessions", lambda query: output)

    assert gather_sessions(_SINCE, _UNTIL) == []


def test_opencode_bad_output_warns(
    home: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(opencode, "list_sessions", lambda query: "not json")

    gather_sessions(_SINCE, _UNTIL)

    assert [r for r in caplog.records if r.levelno == logging.WARNING]


# --- both ---


def test_both_tools_merge_newest_first(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_claude(
        home / ".claude",
        "claude-mid",
        started=datetime(2026, 11, 25, 9, 0),
        updated=datetime(2026, 11, 25, 12, 0),
    )
    page = _opencode_page(
        [
            _opencode_row(
                "ses_late",
                created=datetime(2026, 11, 25, 13, 0),
                updated=datetime(2026, 11, 25, 14, 0),
            ),
            _opencode_row(
                "ses_early",
                created=datetime(2026, 11, 25, 7, 0),
                updated=datetime(2026, 11, 25, 8, 0),
            ),
        ]
    )
    monkeypatch.setattr(opencode, "list_sessions", lambda query: page)

    sessions = gather_sessions(_SINCE, _UNTIL)

    assert [(s.tool, s.id) for s in sessions] == [
        ("opencode", "ses_late"),
        ("claude", "claude-mid"),
        ("opencode", "ses_early"),
    ]


def test_failed_opencode_call_keeps_claude_rows(home: Path, no_opencode: None) -> None:
    _write_claude(
        home / ".claude",
        "kept",
        started=datetime(2026, 11, 25, 9, 0),
        updated=datetime(2026, 11, 25, 10, 0),
    )

    assert [s.id for s in gather_sessions(_SINCE, _UNTIL)] == ["kept"]


@pytest.mark.parametrize("newer_folder", ["-a-first", "-z-last"])
def test_claude_session_in_two_folders_keeps_the_newest(
    home: Path, no_opencode: None, newer_folder: str
) -> None:
    # Either copy can be newer, so the result never depends on glob order.
    paths = {}
    for folder in ("-a-first", "-z-last"):
        directory = home / ".claude" / "projects" / folder
        directory.mkdir(parents=True)
        paths[folder] = directory / "same.jsonl"
        paths[folder].write_text(
            json.dumps(
                {
                    "type": "user",
                    "sessionId": "same",
                    "timestamp": _utc_iso(datetime(2026, 11, 25, 9, 0)),
                    "cwd": f"/Users/me/git/{folder}",
                }
            )
            + "\n"
        )
        hour = 12 if folder == newer_folder else 10
        stamp = datetime(2026, 11, 25, hour, 0).timestamp()
        os.utime(paths[folder], (stamp, stamp))

    sessions = gather_sessions(_SINCE, _UNTIL)

    assert [s.id for s in sessions] == ["same"]
    assert sessions[0].path == str(paths[newer_folder])


def test_claude_window_edges(home: Path, no_opencode: None) -> None:
    root = home / ".claude"
    _write_claude(root, "starts-at-until", started=_UNTIL, updated=_UNTIL)
    _write_claude(
        root, "ends-at-since", started=datetime(2026, 11, 24, 9, 0), updated=_SINCE
    )

    assert [s.id for s in gather_sessions(_SINCE, _UNTIL)] == ["ends-at-since"]


def test_opencode_window_edges(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = _opencode_page(
        [
            _opencode_row("ses_starts_at_until", created=_UNTIL, updated=_UNTIL),
            _opencode_row(
                "ses_ends_at_since",
                created=datetime(2026, 11, 24, 9, 0),
                updated=_SINCE,
            ),
        ]
    )
    monkeypatch.setattr(opencode, "list_sessions", lambda query: page)

    assert [s.id for s in gather_sessions(_SINCE, _UNTIL)] == ["ses_ends_at_since"]


def test_claude_transcript_with_invalid_utf8_is_still_read(
    home: Path, no_opencode: None
) -> None:
    path = _write_claude(
        home / ".claude",
        "bad-bytes",
        started=datetime(2026, 11, 25, 9, 0),
        updated=datetime(2026, 11, 25, 10, 0),
    )
    with path.open("ab") as transcript:
        transcript.write(b'{"type": "user", "text": "\xff\xfe"}\n')
    stamp = datetime(2026, 11, 25, 10, 0).timestamp()
    os.utime(path, (stamp, stamp))

    assert [s.id for s in gather_sessions(_SINCE, _UNTIL)] == ["bad-bytes"]


# Each fails fromtimestamp differently by platform; 10**400 always overflows a float.
@pytest.mark.parametrize("bad_ms", [10**20, -(10**14), 10**400])
def test_opencode_row_with_impossible_time_is_skipped(
    home: Path, monkeypatch: pytest.MonkeyPatch, bad_ms: int
) -> None:
    good = _opencode_row(
        "ses_good",
        created=datetime(2026, 11, 25, 9, 0),
        updated=datetime(2026, 11, 25, 10, 0),
    )
    bad = dict(good, id="ses_bad", time={"created": bad_ms, "updated": bad_ms})
    page = _opencode_page([good, bad])
    monkeypatch.setattr(opencode, "list_sessions", lambda query: page)

    assert [s.id for s in gather_sessions(_SINCE, _UNTIL)] == ["ses_good"]


@pytest.mark.parametrize("naive_field", ["started", "updated"])
def test_session_rejects_naive_times(naive_field: str) -> None:
    times = {
        "started": datetime(2026, 11, 25, 9, 0).astimezone(),
        "updated": datetime(2026, 11, 25, 10, 0).astimezone(),
    }
    times[naive_field] = times[naive_field].replace(tzinfo=None)

    with pytest.raises(ValueError):
        Session(tool="claude", id="x", **times)
