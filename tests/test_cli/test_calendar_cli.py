"""CLI-level tests for `calendar sync` dispatch.

Mocks the osascript layer so tests pass on non-darwin CI. Injection-safety and
the platform guard are covered by test_integrations/test_calendar_write.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from daily_driver.core import clock
from daily_driver.core.plan import CalendarEvent

_FROZEN = datetime(2026, 6, 30, 9, 15, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _freeze_clock() -> None:
    clock.FROZEN_TIME = _FROZEN
    yield
    clock.FROZEN_TIME = None


def _init_workspace(tmp_path: Path, *, sync_enabled: bool) -> Path:
    from daily_driver.core.workspace import Workspace

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    Workspace.init(ws)
    if sync_enabled:
        with (ws / ".dd-config.yaml").open("a", encoding="utf-8") as handle:
            handle.write(
                '\ncalendar:\n  sync_enabled: true\n  plan_calendar_name: "Plan"\n'
            )
    return ws


def _write_plan(ws: Path) -> None:
    plan = ws / "2026" / "06" / "2026-06-30-plan.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(
        "---\n"
        "date: 2026-06-30\n"
        "plan_items:\n"
        '  - id: pi-001\n    text: "Deep work"\n'
        '    time_block: "10:00-11:00"\n    status: planned\n'
        "---\n\nbody\n",
        encoding="utf-8",
    )


def test_calendar_sync_writes_events(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations.calendar_write import CalendarWriteResult

    ws = _init_workspace(tmp_path, sync_enabled=True)
    _write_plan(ws)

    with patch(
        "daily_driver.integrations.calendar_write.write_day",
        return_value=CalendarWriteResult(ok=True, written=1),
    ) as mock_write:
        rc = app(["--workspace", str(ws), "calendar", "sync"])

    assert rc == 0
    mock_write.assert_called_once()
    call = mock_write.call_args
    assert call[0][0] == "Plan"
    events = call[0][2]
    assert events == [
        CalendarEvent(
            title="Deep work",
            start_seconds=36000,
            end_seconds=39600,
            notes="daily-plan:2026-06-30",
        )
    ]
    assert "Synced 1" in capsys.readouterr().out


def test_calendar_sync_disabled_is_no_op(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, sync_enabled=False)
    _write_plan(ws)

    with patch("daily_driver.integrations.calendar_write.write_day") as mock_write:
        rc = app(["--workspace", str(ws), "calendar", "sync"])

    assert rc == 0
    mock_write.assert_not_called()
    assert "disabled" in capsys.readouterr().out.lower()


def test_calendar_sync_no_time_blocks_is_no_op(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, sync_enabled=True)
    # No plan file written -> no events.

    with patch("daily_driver.integrations.calendar_write.write_day") as mock_write:
        rc = app(["--workspace", str(ws), "calendar", "sync"])

    assert rc == 0
    mock_write.assert_not_called()
    assert "No plan time blocks" in capsys.readouterr().out


def test_bare_calendar_prints_usage_and_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, sync_enabled=True)

    rc = app(["--workspace", str(ws), "calendar"])

    assert rc == 2
    assert "usage" in (capsys.readouterr().err.lower())
