"""Per-board durable checkpointing for the multi-board sources.

The discovery matched cache turned greenhouse/ashby/lever into walks over
hundreds of boards, so the board sources now extend the jobspy per-unit
checkpoint pattern: each finished board/account hands its matched rows to
``ctx.checkpoint`` as it completes. These tests pin the unit semantics at the
adapter level (one call per board with exactly that board's rows; empty boards
skipped; CheckpointAborted stops the source) and the orchestrator wiring
(a checkpointed board source skips the end-of-source append, so nothing
double-appends; a crash after board A still leaves A's rows in jobs.csv).
"""

from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper import runner, scrape_all
from daily_driver.plugins.job_search.scraper.runner import (
    CheckpointAborted,
    ScrapeContext,
)
from daily_driver.plugins.job_search.scraper.sources import ashby as ashby_module
from daily_driver.plugins.job_search.scraper.sources import (
    greenhouse as greenhouse_module,
)
from daily_driver.plugins.job_search.scraper.sources import lever as lever_module
from daily_driver.plugins.job_search.scraper.sources import workable as workable_module
from daily_driver.plugins.job_search.scraper.sources import workday as workday_module


def _plugin(sources: dict[str, Any]) -> JobSearchPlugin:
    return JobSearchPlugin.model_validate(
        {
            "roles": ["Engineer"],
            "scraper": {"enabled": True, "timeout": 1, "max_retries": 0},
            "sources": sources,
        }
    )


def _ctx(
    sources: dict[str, Any],
    checkpoint: Any = None,
) -> ScrapeContext:
    ctx = ScrapeContext(plugin=_plugin(sources))
    if checkpoint is not None:
        ctx = replace(ctx, checkpoint=checkpoint)
    return ctx


def _recorder() -> tuple[list[list[dict[str, Any]]], Any]:
    batches: list[list[dict[str, Any]]] = []

    def checkpoint(batch: list[dict[str, Any]]) -> None:
        batches.append(list(batch))

    return batches, checkpoint


def _gh_response(jobs: list[dict[str, Any]]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"jobs": jobs}
    return resp


def _gh_job(board: str, n: int) -> dict[str, Any]:
    return {
        "title": f"Platform Engineer {n}",
        "absolute_url": f"https://boards.greenhouse.io/{board}/{n}",
        "location": {"name": "Remote"},
        "content": "",
    }


# ── Adapter-level unit semantics (greenhouse as the representative) ──────────


def test_greenhouse_checkpoints_each_board_with_its_own_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        board = url.rsplit("/jobs", 1)[0].rsplit("/", 1)[1]
        return _gh_response([_gh_job(board, 1), _gh_job(board, 2)])

    monkeypatch.setattr(greenhouse_module, "_api_get", fake_api_get)
    monkeypatch.setattr(greenhouse_module, "_http_session", lambda cfg: MagicMock())

    batches, checkpoint = _recorder()
    ctx = _ctx({"greenhouse": {"greenhouse_boards": ["alpha", "beta"]}}, checkpoint)
    jobs = greenhouse_module.scrape_greenhouse(ctx)

    # One checkpoint per board, each carrying exactly that board's rows.
    assert len(batches) == 2
    assert [j["source"] for j in batches[0]] == ["Greenhouse (alpha)"] * 2
    assert [j["source"] for j in batches[1]] == ["Greenhouse (beta)"] * 2
    # The end-of-source return still carries the full list (manifest/dedup).
    assert len(jobs) == 4


def test_greenhouse_board_with_no_matches_not_checkpointed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty batch is never handed to the sink (mirrors jobspy's
    ``if unit_new`` guard): no pointless lock/append churn per silent board."""

    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        if "/quiet/" in url:
            return _gh_response([{"title": "Director, Marketing"}])
        return _gh_response([_gh_job("loud", 1)])

    monkeypatch.setattr(greenhouse_module, "_api_get", fake_api_get)
    monkeypatch.setattr(greenhouse_module, "_http_session", lambda cfg: MagicMock())

    batches, checkpoint = _recorder()
    greenhouse_module.scrape_greenhouse(
        _ctx({"greenhouse": {"greenhouse_boards": ["quiet", "loud"]}}, checkpoint)
    )

    assert len(batches) == 1
    assert batches[0][0]["source"] == "Greenhouse (loud)"


def test_greenhouse_checkpoint_aborted_stops_at_failing_board(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persist failure stops the source AT the failing board: no further
    fetches against a dead disk, and the rows gathered so far are returned
    (the orchestrator has already marked the source failed)."""
    fetched: list[str] = []

    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        fetched.append(url)
        return _gh_response([_gh_job("any", len(fetched))])

    monkeypatch.setattr(greenhouse_module, "_api_get", fake_api_get)
    monkeypatch.setattr(greenhouse_module, "_http_session", lambda cfg: MagicMock())

    def checkpoint(batch: list[dict[str, Any]]) -> None:
        raise CheckpointAborted("disk gone")

    jobs = greenhouse_module.scrape_greenhouse(
        _ctx({"greenhouse": {"greenhouse_boards": ["one", "two"]}}, checkpoint)
    )

    assert len(fetched) == 1  # board two never fetched
    assert len(jobs) == 1


def test_greenhouse_failed_board_checkpoints_nothing_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed board contributes no batch; the surviving boards' batches are
    already persisted when PartialSourceError raises at end-of-source."""
    from daily_driver.plugins.job_search.scraper.context import PartialSourceError

    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        return None if "/down/" in url else _gh_response([_gh_job("ok", 1)])

    monkeypatch.setattr(greenhouse_module, "_api_get", fake_api_get)
    monkeypatch.setattr(greenhouse_module, "_http_session", lambda cfg: MagicMock())

    batches, checkpoint = _recorder()
    with pytest.raises(PartialSourceError) as excinfo:
        greenhouse_module.scrape_greenhouse(
            _ctx({"greenhouse": {"greenhouse_boards": ["ok", "down"]}}, checkpoint)
        )

    assert len(batches) == 1
    # The exception still carries the full gathered list for the normal
    # degraded path; those rows are the ones already checkpointed.
    assert excinfo.value.jobs == batches[0]


# ── The other board adapters share the per-unit contract ─────────────────────


def test_ashby_checkpoints_per_board(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        board = url.rsplit("/", 1)[1]
        resp = MagicMock()
        resp.json.return_value = {
            "jobs": [
                {
                    "title": "Platform Engineer",
                    "location": "Remote",
                    "jobUrl": f"https://jobs.ashbyhq.com/{board}/1",
                    "descriptionPlain": "",
                    "isListed": True,
                }
            ]
        }
        return resp

    monkeypatch.setattr(ashby_module, "_api_get", fake_api_get)
    monkeypatch.setattr(ashby_module, "_http_session", lambda cfg: MagicMock())

    batches, checkpoint = _recorder()
    ashby_module.scrape_ashby(_ctx({"ashby": {"ashby_boards": ["a", "b"]}}, checkpoint))

    assert [b[0]["source"] for b in batches] == ["Ashby (a)", "Ashby (b)"]


def test_lever_checkpoints_per_board(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        resp = MagicMock()
        resp.json.return_value = [
            {
                "text": "Platform Engineer",
                "hostedUrl": f"https://jobs.lever.co/x/{url.count('a')}",
                "categories": {"location": "Remote"},
                "descriptionPlain": "",
            }
        ]
        return resp

    monkeypatch.setattr(lever_module, "_api_get", fake_api_get)
    monkeypatch.setattr(lever_module, "_http_session", lambda cfg: MagicMock())

    batches, checkpoint = _recorder()
    lever_module.scrape_lever(_ctx({"lever": {"lever_boards": ["a", "b"]}}, checkpoint))

    assert [b[0]["source"] for b in batches] == ["Lever (a)", "Lever (b)"]


def test_workable_checkpoints_per_account(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        slug = url.rsplit("/", 1)[1]
        resp = MagicMock()
        resp.json.return_value = {
            "name": slug.title(),
            "jobs": [
                {
                    "title": "Platform Engineer",
                    "url": f"https://apply.workable.com/{slug}/j/1",
                    "city": "",
                    "country": "",
                }
            ],
        }
        return resp

    monkeypatch.setattr(workable_module, "_api_get", fake_api_get)
    monkeypatch.setattr(workable_module, "_http_session", lambda cfg: MagicMock())

    batches, checkpoint = _recorder()
    workable_module.scrape_workable(
        _ctx({"workable": {"workable_accounts": ["a", "b"]}}, checkpoint)
    )

    assert [b[0]["source"] for b in batches] == ["Workable (a)", "Workable (b)"]


def test_workday_checkpoints_per_board_including_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial workday board's rows are kept by design, so they checkpoint
    too -- the durable record and the in-memory result stay identical."""
    calls: list[str] = []

    def fake_api_post(session: Any, url: str, ctx: Any, *, json: Any, **kw: Any) -> Any:
        calls.append(url)
        tenant = url.split("//", 1)[1].split(".", 1)[0]
        if tenant == "brokenco" and json["offset"] > 0:
            return None  # pagination breaks after page 1 -> partial board
        resp = MagicMock()
        resp.json.return_value = {
            "total": 25 if tenant == "brokenco" else 1,
            "jobPostings": [
                {
                    "title": f"Platform Engineer {json['offset']}",
                    "externalPath": f"/job/{tenant}-{json['offset']}",
                    "locationsText": "Remote",
                }
            ]
            * (20 if tenant == "brokenco" else 1),
        }
        return resp

    monkeypatch.setattr(workday_module, "_api_post", fake_api_post)
    monkeypatch.setattr(workday_module, "_http_session", lambda cfg: MagicMock())

    batches, checkpoint = _recorder()
    from daily_driver.plugins.job_search.scraper.context import PartialSourceError

    boards = [
        {"tenant": "brokenco", "host": "wd1", "site": "careers"},
        {"tenant": "healthyco", "host": "wd1", "site": "careers"},
    ]
    with pytest.raises(PartialSourceError):
        workday_module.scrape_workday(
            _ctx({"workday": {"workday_boards": boards}}, checkpoint)
        )

    # Both boards checkpointed: the partial one with its kept rows, then the
    # healthy one.
    assert len(batches) == 2
    assert batches[0][0]["source"] == "Workday (brokenco)"
    assert batches[1][0]["source"] == "Workday (healthyco)"


def test_workday_graceful_stop_mid_board_checkpoints_fetched_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A graceful stop mid-pagination keeps the in-flight board's fetched rows:
    they checkpoint before the early return. A checkpointed source's
    end-of-source append is skipped, so without this checkpoint the stop path
    would silently drop rows the pre-checkpoint code used to keep."""
    ctx_holder: dict[str, Any] = {}

    def fake_api_post(session: Any, url: str, ctx: Any, *, json: Any, **kw: Any) -> Any:
        # Ask for a stop after the first page lands: the next page-loop
        # iteration must checkpoint the rows fetched so far and return.
        ctx_holder["ctx"].stop_event.set()
        resp = MagicMock()
        resp.json.return_value = {
            "total": 40,
            "jobPostings": [
                {
                    "title": "Platform Engineer",
                    "externalPath": f"/job/x-{json['offset']}",
                    "locationsText": "Remote",
                }
            ]
            * 20,
        }
        return resp

    monkeypatch.setattr(workday_module, "_api_post", fake_api_post)
    monkeypatch.setattr(workday_module, "_http_session", lambda cfg: MagicMock())

    batches, checkpoint = _recorder()
    ctx = _ctx(
        {
            "workday": {
                "workday_boards": [
                    {"tenant": "bigco", "host": "wd1", "site": "careers"}
                ]
            }
        },
        checkpoint,
    )
    ctx_holder["ctx"] = ctx
    jobs = workday_module.scrape_workday(ctx)

    assert len(jobs) == 20  # page one's rows returned
    assert len(batches) == 1  # ...and checkpointed before the stop return
    assert len(batches[0]) == 20


# ── Orchestrator wiring: no double-append, durability through the sink ───────


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_checkpointed_board_source_skips_end_of_source_append(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scrape_all binds ctx.checkpoint for the board sources; a source that
    checkpointed must NOT also route through on_source_result (else every row
    would append twice)."""

    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        return _gh_response([_gh_job("solo", 1)])

    monkeypatch.setattr(greenhouse_module, "_api_get", fake_api_get)
    monkeypatch.setattr(greenhouse_module, "_http_session", lambda cfg: MagicMock())

    checkpointed: list[tuple[str, int]] = []
    appended: list[str] = []

    ctx = ScrapeContext(plugin=_plugin({"greenhouse": {"greenhouse_boards": ["solo"]}}))
    all_jobs, failed, results = runner.run_all_scrapers(
        ctx,
        sources_override=["greenhouse"],
        on_source_result=lambda sid, jobs: appended.append(sid),
        on_source_checkpoint=lambda sid, batch: checkpointed.append((sid, len(batch))),
    )

    assert checkpointed == [("greenhouse", 1)]
    assert appended == []  # end-of-source append skipped
    assert failed == []
    assert len(all_jobs) == 1


def test_non_checkpointing_source_still_appends_at_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fast single-call sources keep the end-of-source append path."""
    monkeypatch.setitem(
        scrape_all.SCRAPERS,
        "remoteok",
        lambda ctx: [
            {
                "company": "Acme",
                "role": "Engineer",
                "location": "Remote",
                "url": "https://r/1",
                "source": "remoteok",
                "date_found": "2026-07-04",
            }
        ],
    )
    checkpointed: list[str] = []
    appended: list[str] = []

    ctx = ScrapeContext(plugin=_plugin({"remoteok": True}))
    runner.run_all_scrapers(
        ctx,
        sources_override=["remoteok"],
        on_source_result=lambda sid, jobs: appended.append(sid),
        on_source_checkpoint=lambda sid, batch: checkpointed.append(sid),
    )

    assert checkpointed == []
    assert appended == ["remoteok"]


def test_crash_after_first_board_keeps_its_rows_in_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE durability win: greenhouse crashes on board two, but board one's
    rows are already in jobs.csv via the per-board checkpoint -- before this
    change the whole source's scrape was lost."""
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
    )

    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        if "/two/" in url:
            raise RuntimeError("connection torn down mid-source")
        return _gh_response([_gh_job("one", 1)])

    monkeypatch.setattr(greenhouse_module, "_api_get", fake_api_get)
    monkeypatch.setattr(greenhouse_module, "_http_session", lambda cfg: MagicMock())

    plugin = JobSearchPlugin.model_validate(
        {
            "roles": ["Engineer"],
            "scraper": {"enabled": True, "timeout": 1, "max_retries": 0},
            "locations": {"countries": {"US": []}, "remote": True},
            "sources": {"greenhouse": {"greenhouse_boards": ["one", "two"]}},
        }
    )
    rc = runner.run(
        plugin,
        tmp_path,
        tmp_path,
        no_enrich=True,
        sources_override=["greenhouse"],
    )

    # The source failed (exit 1) but board one's row survived on disk.
    assert rc == 1
    rows = _read_csv(tmp_path / "jobs.csv")
    assert [r["Link"] for r in rows] == ["https://boards.greenhouse.io/one/1"]

    import json

    manifest = json.loads((tmp_path / "jobs-last-run.json").read_text())
    assert "greenhouse" in manifest["sources_failed"]


def test_degraded_checkpointed_source_rows_appear_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A degraded greenhouse run (one board failed) with per-board checkpoints
    must land each surviving row exactly once -- the checkpointed-sources skip
    is what prevents the end-of-source degraded append from double-writing."""
    monkeypatch.setattr(
        "daily_driver.plugins.job_search.jobs_archive.load_archive_dedup",
        lambda _csv_path: (set(), set(), {}),
    )

    def fake_api_get(session: Any, url: str, *a: Any, **kw: Any) -> Any:
        return None if "/down/" in url else _gh_response([_gh_job("ok", 1)])

    monkeypatch.setattr(greenhouse_module, "_api_get", fake_api_get)
    monkeypatch.setattr(greenhouse_module, "_http_session", lambda cfg: MagicMock())

    plugin = JobSearchPlugin.model_validate(
        {
            "roles": ["Engineer"],
            "scraper": {"enabled": True, "timeout": 1, "max_retries": 0},
            "locations": {"countries": {"US": []}, "remote": True},
            "sources": {"greenhouse": {"greenhouse_boards": ["ok", "down"]}},
        }
    )
    rc = runner.run(
        plugin,
        tmp_path,
        tmp_path,
        no_enrich=True,
        sources_override=["greenhouse"],
    )
    assert rc == 0  # degraded, not failed

    rows = _read_csv(tmp_path / "jobs.csv")
    assert [r["Link"] for r in rows] == ["https://boards.greenhouse.io/ok/1"]

    import json

    manifest = json.loads((tmp_path / "jobs-last-run.json").read_text())
    assert "greenhouse" in manifest.get("sources_degraded", [])
