from __future__ import annotations

from datetime import date
from pathlib import Path

from daily_driver.core.plan import CalendarEvent, read_plan_time_blocks

_DAY = date(2026, 6, 30)


def _write_plan(tmp_path: Path, frontmatter: str) -> Path:
    path = tmp_path / "plan.md"
    path.write_text(f"---\n{frontmatter}---\n\nbody\n", encoding="utf-8")
    return path


def test_populated_plan_yields_seconds_of_day_events(tmp_path: Path) -> None:
    plan = _write_plan(
        tmp_path,
        "plan_items:\n"
        '  - id: pi-001\n    text: "job-003 - follow up"\n'
        '    time_block: "10:00-10:30"\n    status: planned\n'
        '  - id: pi-003\n    text: "Doctor appointment"\n'
        '    time_block: "14:00-15:00"\n    status: planned\n',
    )

    events = read_plan_time_blocks(plan, day=_DAY)

    assert events == [
        CalendarEvent(
            title="job-003 - follow up",
            start_seconds=10 * 3600,
            end_seconds=10 * 3600 + 30 * 60,
            notes="daily-plan:2026-06-30",
        ),
        CalendarEvent(
            title="Doctor appointment",
            start_seconds=14 * 3600,
            end_seconds=15 * 3600,
            notes="daily-plan:2026-06-30",
        ),
    ]


def test_empty_plan_items_is_clean_no_op(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, "plan_items: []\ncarry_forward: []\n")
    assert read_plan_time_blocks(plan, day=_DAY) == []


def test_null_time_block_is_skipped(tmp_path: Path) -> None:
    plan = _write_plan(
        tmp_path,
        "plan_items:\n"
        '  - id: pi-001\n    text: "Morning medication"\n'
        "    time_block: null\n    status: planned\n",
    )
    assert read_plan_time_blocks(plan, day=_DAY) == []


def test_malformed_time_block_is_skipped_not_crashed(tmp_path: Path) -> None:
    plan = _write_plan(
        tmp_path,
        "plan_items:\n"
        '  - id: pi-001\n    text: "Bad block"\n'
        '    time_block: "9am-10am"\n    status: planned\n'
        '  - id: pi-002\n    text: "Out of range"\n'
        '    time_block: "25:00-26:00"\n    status: planned\n'
        '  - id: pi-003\n    text: "Reversed"\n'
        '    time_block: "11:00-10:00"\n    status: planned\n'
        '  - id: pi-004\n    text: "Good"\n'
        '    time_block: "09:00-09:30"\n    status: planned\n',
    )

    events = read_plan_time_blocks(plan, day=_DAY)

    assert len(events) == 1
    assert events[0].title == "Good"


def test_missing_file_is_no_op(tmp_path: Path) -> None:
    assert read_plan_time_blocks(tmp_path / "absent.md", day=_DAY) == []


def test_no_frontmatter_is_no_op(tmp_path: Path) -> None:
    path = tmp_path / "plan.md"
    path.write_text("just a body, no frontmatter\n", encoding="utf-8")
    assert read_plan_time_blocks(path, day=_DAY) == []
