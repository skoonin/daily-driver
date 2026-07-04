"""Archive dedup splits by WHY a row left jobs.csv (plan phase 3, PR-3b).

User-triaged exits suppress re-discovery (URL forever; the (company, role)
fallback key for REOPEN_KEY_SUPPRESSION_DAYS). Verification-closed exits
suppress nothing: a false-positive closure must be able to resurface, and a
re-discovered closed URL is announced loudly as a reopen.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from typing import Any

import pytest

from daily_driver.plugins.job_search import jobs_archive
from daily_driver.plugins.job_search.scraper import runner
from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER
from tests.test_plugins.job_search.scraper.test_run_resilience import (
    _read_csv,
    _scraped,
    _us_remote_plugin,
)


def _archive_row(
    url: str,
    company: str,
    status: str,
    *,
    date_closed: str = "",
    date_verified: str = "",
) -> dict[str, str]:
    return {
        "Status": status,
        "Company": company,
        "Role": "SRE",
        "Link": url,
        "Date Found": "2026-01-01",
        "Date Verified": date_verified,
        "Date Closed": date_closed,
        "Source": "greenhouse",
    }


def _write_archive(jobs_csv: Path, rows: list[dict[str, str]]) -> None:
    archive = jobs_archive.archive_path_for(jobs_csv)
    with open(archive, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CANONICAL_HEADER)
        writer.writeheader()
        writer.writerows(rows)


def test_closed_rows_suppress_nothing_and_land_on_the_watch(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    _write_archive(
        csv_path,
        [_archive_row("https://x/1", "Acme", "closed", date_closed="2026-06-20")],
    )

    urls, keys, watch = jobs_archive.load_archive_dedup(csv_path)

    assert urls == set()  # re-discoverable
    assert keys == set()  # same-role repost also re-discoverable
    assert watch == {"https://x/1": "2026-06-20"}


def test_triaged_rows_suppress_url_forever_and_key_for_45_days(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "jobs.csv"
    recent = dt.date.today() - dt.timedelta(days=10)
    old = dt.date.today() - dt.timedelta(days=90)
    _write_archive(
        csv_path,
        [
            _archive_row(
                "https://x/recent",
                "RecentCo",
                "dropped",
                date_verified=recent.isoformat(),
            ),
            _archive_row(
                "https://x/old", "OldCo", "dropped", date_verified=old.isoformat()
            ),
        ],
    )

    urls, keys, watch = jobs_archive.load_archive_dedup(csv_path)

    assert urls == {"https://x/recent", "https://x/old"}  # URLs: forever
    assert any("recentco" in k for k in keys)  # within 45d: key suppresses
    assert not any("oldco" in k for k in keys)  # past 45d: key released
    assert watch == {}


def test_run_rediscovers_closed_job_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    """A scrape returning a verification-closed URL re-adds it as a new row
    and announces the reopen -- the false-positive-closure healing path."""
    import logging

    csv_path = tmp_path / "jobs.csv"
    _write_archive(
        csv_path,
        [_archive_row("https://x/1", "Acme", "closed", date_closed="2026-06-20")],
    )
    reopened = [_scraped("https://x/1", "Acme", comp="$100k")]

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", reopened)
        return reopened, [], [("remoteok", reopened)]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    with caplog.at_level(logging.WARNING):
        rc = runner.run(_us_remote_plugin(), tmp_path, tmp_path, no_enrich=True)
    assert rc == 0

    rows = _read_csv(csv_path)
    assert [r["Link"] for r in rows] == ["https://x/1"]  # re-added
    assert any(
        "Reopened (previously closed 2026-06-20)" in r.getMessage()
        for r in caplog.records
    ), [r.getMessage() for r in caplog.records]


def test_run_never_rediscovers_triaged_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user-triaged archived URL stays buried -- 'never pull this again'."""
    csv_path = tmp_path / "jobs.csv"
    _write_archive(
        csv_path,
        [
            _archive_row(
                "https://x/1",
                "Acme",
                "dropped",
                date_verified=dt.date.today().isoformat(),
            )
        ],
    )
    seen_again = [_scraped("https://x/1", "Acme", comp="$100k")]

    def fake_scrape(
        ctx: Any, *_a: Any, on_source_result: Any = None, **_kw: Any
    ) -> Any:
        if on_source_result is not None:
            on_source_result("remoteok", seen_again)
        return seen_again, [], [("remoteok", seen_again)]

    monkeypatch.setattr(runner, "run_all_scrapers", fake_scrape)

    rc = runner.run(_us_remote_plugin(), tmp_path, tmp_path, no_enrich=True)
    assert rc == 0
    assert _read_csv(csv_path) == []  # deduped out, nothing appended
