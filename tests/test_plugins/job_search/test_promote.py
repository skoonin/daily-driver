"""Tests for the jobs-to-tracker promote bridge (core logic)."""

from __future__ import annotations

from pathlib import Path

import pytest

from daily_driver.core.tracker import Tracker
from daily_driver.core.workspace import Workspace
from daily_driver.plugins.job_search.promote import (
    PromoteError,
    promote,
)
from daily_driver.plugins.job_search.scraper.csv_io import (
    CANONICAL_HEADER,
    append_rows,
)


def _write_jobs_csv(jobs_csv: Path, rows: list[dict[str, str]]) -> None:
    full = [{col: row.get(col, "") for col in CANONICAL_HEADER} for row in rows]
    append_rows(jobs_csv, CANONICAL_HEADER, full)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace.init(tmp_path)


@pytest.fixture
def jobs_csv(workspace: Workspace) -> Path:
    path = workspace.output_dir / "jobs.csv"
    _write_jobs_csv(
        path,
        [
            {
                "Company": "Acme Corp",
                "Role": "SRE",
                "Status": "interviewing",
                "Link": "https://jobs.example.com/acme/1",
                "Source": "linkedin",
            },
            {
                "Company": "Globex",
                "Role": "Platform Engineer",
                "Status": "found",
                "Link": "https://jobs.example.com/globex/2",
                "Source": "indeed",
            },
            {
                "Company": "Globex Labs",
                "Role": "DevOps",
                "Status": "",
                "Link": "https://jobs.example.com/globexlabs/3",
                "Source": "greenhouse",
            },
        ],
    )
    return path


def test_promote_by_url_creates_entry(workspace: Workspace, jobs_csv: Path) -> None:
    tracker = Tracker(workspace)
    result = promote(tracker, jobs_csv, "https://jobs.example.com/acme/1")

    assert result.created is True
    assert result.entry is not None
    assert result.entry.category == "job"
    assert result.entry.title == "Acme Corp -- SRE"
    # Recognized job status carries through unchanged.
    assert result.entry.status == "interviewing"
    assert result.entry.extras == {
        "url": "https://jobs.example.com/acme/1",
        "company": "Acme Corp",
        "role": "SRE",
        "source": "linkedin",
    }
    assert result.entry.link == "https://jobs.example.com/acme/1"


def test_promote_by_unique_company_substring(
    workspace: Workspace, jobs_csv: Path
) -> None:
    tracker = Tracker(workspace)
    # "acme" uniquely (case-insensitively) matches Acme Corp.
    result = promote(tracker, jobs_csv, "acme")
    assert result.created is True
    assert result.entry is not None
    assert result.entry.title == "Acme Corp -- SRE"


def test_promote_ambiguous_company_raises(workspace: Workspace, jobs_csv: Path) -> None:
    tracker = Tracker(workspace)
    # "globex" substring matches both Globex and Globex Labs.
    with pytest.raises(PromoteError) as exc:
        promote(tracker, jobs_csv, "globex")
    msg = str(exc.value)
    assert "2 rows" in msg
    assert "Globex" in msg


def test_promote_no_match_raises(workspace: Workspace, jobs_csv: Path) -> None:
    tracker = Tracker(workspace)
    with pytest.raises(PromoteError) as exc:
        promote(tracker, jobs_csv, "nonexistent-co")
    assert "no jobs.csv row matched" in str(exc.value)


def test_promote_blank_status_falls_back_to_applied(
    workspace: Workspace, jobs_csv: Path
) -> None:
    tracker = Tracker(workspace)
    result = promote(tracker, jobs_csv, "https://jobs.example.com/globexlabs/3")
    assert result.created is True
    assert result.entry is not None
    assert result.entry.status == "applied"
    # Blank Status is a silent state claim unless surfaced — flag it.
    assert result.status_fallback is True
    assert result.raw_status == ""


def test_promote_unknown_status_falls_back_and_flags(
    workspace: Workspace,
) -> None:
    tracker = Tracker(workspace)
    path = tracker._workspace.output_dir / "jobs.csv"  # type: ignore[attr-defined]
    _write_jobs_csv(
        path,
        [
            {
                "Company": "Initech",
                "Role": "Engineer",
                "Status": "shortlisted",
                "Link": "https://jobs.example.com/initech/9",
                "Source": "linkedin",
            }
        ],
    )
    result = promote(tracker, path, "https://jobs.example.com/initech/9")
    assert result.created is True
    assert result.entry is not None
    assert result.entry.status == "applied"
    assert result.status_fallback is True
    # The raw value is normalized for the warning, preserving meaning.
    assert result.raw_status == "shortlisted"


def test_promote_recognized_status_no_fallback(
    workspace: Workspace, jobs_csv: Path
) -> None:
    tracker = Tracker(workspace)
    result = promote(tracker, jobs_csv, "https://jobs.example.com/acme/1")
    assert result.status_fallback is False


def test_promote_is_idempotent_by_url(workspace: Workspace, jobs_csv: Path) -> None:
    tracker = Tracker(workspace)
    first = promote(tracker, jobs_csv, "https://jobs.example.com/acme/1")
    assert first.created is True

    second = promote(tracker, jobs_csv, "https://jobs.example.com/acme/1")
    assert second.created is False
    assert second.already_promoted_id == first.entry.id
    assert second.entry is not None and second.entry.id == first.entry.id

    # No duplicate written.
    assert len(tracker.list(category="job")) == 1


def test_promote_dry_run_writes_nothing(workspace: Workspace, jobs_csv: Path) -> None:
    tracker = Tracker(workspace)
    result = promote(tracker, jobs_csv, "https://jobs.example.com/acme/1", dry_run=True)
    assert result.created is False
    assert result.entry is None
    assert result.title == "Acme Corp -- SRE"
    assert result.status == "interviewing"
    assert tracker.list(category="job") == []


def test_promote_does_not_mutate_jobs_csv(workspace: Workspace, jobs_csv: Path) -> None:
    before = jobs_csv.read_bytes()
    tracker = Tracker(workspace)
    promote(tracker, jobs_csv, "https://jobs.example.com/acme/1")
    assert jobs_csv.read_bytes() == before


def test_promote_empty_csv_raises(workspace: Workspace) -> None:
    tracker = Tracker(workspace)
    missing = workspace.output_dir / "jobs.csv"
    with pytest.raises(PromoteError):
        promote(tracker, missing, "anything")


def test_promote_empty_link_row_sets_has_link_false(
    workspace: Workspace,
) -> None:
    tracker = Tracker(workspace)
    path = workspace.output_dir / "jobs.csv"
    _write_jobs_csv(
        path,
        [
            {
                "Company": "Hooli",
                "Role": "SRE",
                "Status": "applied",
                "Link": "",
                "Source": "referral",
            }
        ],
    )
    result = promote(tracker, path, "Hooli")
    assert result.created is True
    assert result.has_link is False
    assert result.entry is not None
    assert result.entry.extras["url"] == ""


def test_promote_empty_link_row_is_idempotent_on_company_role(
    workspace: Workspace,
) -> None:
    """A blank-Link row re-promotes as a no-op via the (company, role) key."""
    tracker = Tracker(workspace)
    path = workspace.output_dir / "jobs.csv"
    _write_jobs_csv(
        path,
        [
            {
                "Company": "Hooli",
                "Role": "SRE",
                "Status": "applied",
                "Link": "",
                "Source": "referral",
            }
        ],
    )
    first = promote(tracker, path, "Hooli")
    assert first.created is True
    assert first.entry is not None

    second = promote(tracker, path, "Hooli")
    assert second.created is False
    assert second.already_promoted_id == first.entry.id
    assert len(tracker.list(category="job")) == 1


def test_promote_two_distinct_empty_link_rows_both_promote(
    workspace: Workspace,
) -> None:
    """Two blank-Link rows with different company/role both promote (no false dedup)."""
    tracker = Tracker(workspace)
    path = workspace.output_dir / "jobs.csv"
    _write_jobs_csv(
        path,
        [
            {
                "Company": "Hooli",
                "Role": "SRE",
                "Status": "applied",
                "Link": "",
                "Source": "referral",
            },
            {
                "Company": "Pied Piper",
                "Role": "Backend",
                "Status": "applied",
                "Link": "",
                "Source": "referral",
            },
        ],
    )
    a = promote(tracker, path, "Hooli")
    b = promote(tracker, path, "Pied Piper")
    assert a.created is True
    assert b.created is True
    assert a.entry is not None and b.entry is not None
    assert a.entry.id != b.entry.id
    assert len(tracker.list(category="job")) == 2
