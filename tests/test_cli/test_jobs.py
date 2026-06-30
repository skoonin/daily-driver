"""CLI tests for the ``jobs`` subcommand.

Network and source-specific behavior is NOT covered here — these tests
exercise argparse wiring, workspace resolution, config loading, and the
backfill short-circuit against a mocked ``daily_driver.plugins.job_search.scraper`` module.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


def _init_workspace(tmp_path: Path, *, scraper_enabled: bool | None = None) -> Path:
    """Create a workspace. If scraper_enabled is set, inject plugins.job_search."""
    from daily_driver.core.workspace import Workspace

    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    Workspace.init(ws_root)
    if scraper_enabled is not None:
        enabled_str = "true" if scraper_enabled else "false"
        (ws_root / ".dd-config.yaml").write_text(
            "daily_driver:\n"
            "  output_dir: .\n"
            "tracker:\n"
            "  default_category: task\n"
            "  categories:\n"
            "    task:\n"
            "      required: [title]\n"
            "plugins:\n"
            "  job_search:\n"
            "    scraper:\n"
            f"      enabled: {enabled_str}\n"
        )
    return ws_root


# ---------------------------------------------------------------------------
# Help / top-level
# ---------------------------------------------------------------------------


def test_jobs_help_exits_0() -> None:
    from daily_driver.cli.cli import app

    with pytest.raises(SystemExit) as exc:
        app(["jobs", "--help"])

    assert exc.value.code == 0


def test_jobs_help_lists_core_actions(capsys: pytest.CaptureFixture[str]) -> None:
    from daily_driver.cli.cli import app

    with pytest.raises(SystemExit):
        app(["jobs", "--help"])

    captured = capsys.readouterr()
    combined = (captured.out + captured.err).lower()
    assert "run" in combined
    assert "status" in combined
    assert "prune" in combined
    assert "backfill" in combined


def test_jobs_no_action_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bare ``jobs`` with no action returns 2 and prints usage."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    rc = app(["--workspace", str(ws), "jobs"])

    assert rc == 2


def test_jobs_run_help_exits_0() -> None:
    from daily_driver.cli.cli import app

    with pytest.raises(SystemExit) as exc:
        app(["jobs", "run", "--help"])

    assert exc.value.code == 0


def test_jobs_status_help_exits_0() -> None:
    from daily_driver.cli.cli import app

    with pytest.raises(SystemExit) as exc:
        app(["jobs", "status", "--help"])

    assert exc.value.code == 0


# ---------------------------------------------------------------------------
# jobs run — workspace / config validation
# ---------------------------------------------------------------------------


def test_jobs_run_missing_workspace_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from daily_driver.cli.cli import app

    monkeypatch.chdir(tmp_path)

    rc = app(["--workspace", str(tmp_path / "missing"), "jobs", "run"])

    assert rc == 1


def test_jobs_run_no_job_search_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fresh workspace with no plugins.job_search config exits 1."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(["--workspace", str(ws), "jobs", "run"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "plugins.job_search" in captured.err


def test_jobs_run_scraper_disabled_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=False)

    rc = app(["--workspace", str(ws), "jobs", "run"])

    captured = capsys.readouterr()
    assert rc == 0
    assert "Scraper disabled" in captured.err


def test_jobs_backfill_dispatches(tmp_path: Path) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    with patch("daily_driver.plugins.job_search.scraper.run_backfill") as mock_backfill:
        rc = app(["--workspace", str(ws), "jobs", "backfill"])

    assert rc == 0
    assert mock_backfill.called


def test_jobs_run_rejects_backfill_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`jobs run --backfill` is deleted; argparse rejects the unknown flag."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    with pytest.raises(SystemExit) as exc:
        app(["--workspace", str(ws), "jobs", "run", "--backfill"])

    assert exc.value.code == 2
    err = capsys.readouterr().err.lower()
    assert "backfill" in err and "unrecognized" in err


def test_jobs_backfill_dry_run_passes_flag(tmp_path: Path) -> None:
    """`-n/--dry-run` propagates as dry_run=True to run_backfill."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    with patch("daily_driver.plugins.job_search.scraper.run_backfill") as mock_backfill:
        rc = app(["--workspace", str(ws), "jobs", "backfill", "--dry-run"])

    assert rc == 0
    assert mock_backfill.call_args.kwargs.get("dry_run") is True


def test_jobs_backfill_limit_passes_through(tmp_path: Path) -> None:
    """`--limit N` propagates as limit=N to run_backfill."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    with patch("daily_driver.plugins.job_search.scraper.run_backfill") as mock_backfill:
        rc = app(["--workspace", str(ws), "jobs", "backfill", "--limit", "7"])

    assert rc == 0
    assert mock_backfill.call_args.kwargs.get("limit") == 7


def test_jobs_backfill_limit_defaults_none(tmp_path: Path) -> None:
    """Without --limit, run_backfill receives limit=None (use config caps)."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    with patch("daily_driver.plugins.job_search.scraper.run_backfill") as mock_backfill:
        rc = app(["--workspace", str(ws), "jobs", "backfill"])

    assert rc == 0
    assert mock_backfill.call_args.kwargs.get("limit") is None


def test_jobs_backfill_limit_zero_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--limit 0 would mean full spend (budget<=0 sentinel); reject it (exit 2)."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    with pytest.raises(SystemExit) as exc:
        app(["--workspace", str(ws), "jobs", "backfill", "--limit", "0"])

    assert exc.value.code == 2
    assert ">= 1" in capsys.readouterr().err


def test_jobs_backfill_limit_negative_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A negative --limit is rejected at the parser (exit 2)."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    with pytest.raises(SystemExit) as exc:
        app(["--workspace", str(ws), "jobs", "backfill", "--limit", "-5"])

    assert exc.value.code == 2
    assert ">= 1" in capsys.readouterr().err


def test_jobs_backfill_help_states_limit_minimum(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The --limit help text states the minimum of 1."""
    from daily_driver.cli.cli import app

    with pytest.raises(SystemExit):
        app(["jobs", "backfill", "--help"])
    assert "minimum 1" in capsys.readouterr().out


def test_jobs_run_dry_run_passes_flag(tmp_path: Path) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    with patch(
        "daily_driver.plugins.job_search.scraper.run", return_value=0
    ) as mock_run:
        rc = app(["--workspace", str(ws), "jobs", "run", "--dry-run"])

    assert rc == 0
    assert mock_run.called
    _, kwargs = mock_run.call_args
    assert kwargs.get("dry_run") is True


def test_jobs_run_no_enrich_passes_flag(tmp_path: Path) -> None:
    """`--no-enrich` propagates as no_enrich=True to scraper.run."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    with patch(
        "daily_driver.plugins.job_search.scraper.run", return_value=0
    ) as mock_run:
        rc = app(["--workspace", str(ws), "jobs", "run", "--no-enrich"])

    assert rc == 0
    assert mock_run.called
    _, kwargs = mock_run.call_args
    assert kwargs.get("no_enrich") is True


def test_jobs_run_no_enrich_defaults_false(tmp_path: Path) -> None:
    """Without the flag, no_enrich is False on the default path."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    with patch(
        "daily_driver.plugins.job_search.scraper.run", return_value=0
    ) as mock_run:
        rc = app(["--workspace", str(ws), "jobs", "run", "--dry-run"])

    assert rc == 0
    _, kwargs = mock_run.call_args
    assert kwargs.get("no_enrich") is False


def test_jobs_run_no_enrich_with_dry_run_composes(tmp_path: Path) -> None:
    """`--no-enrich --dry-run` is accepted (redundant, not an error)."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    with patch(
        "daily_driver.plugins.job_search.scraper.run", return_value=0
    ) as mock_run:
        rc = app(["--workspace", str(ws), "jobs", "run", "--no-enrich", "--dry-run"])

    assert rc == 0
    _, kwargs = mock_run.call_args
    assert kwargs.get("no_enrich") is True
    assert kwargs.get("dry_run") is True


def test_jobs_prune_rejects_no_enrich(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--no-enrich` is a run-only flag; prune must reject it."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    with pytest.raises(SystemExit) as exc:
        app(
            [
                "--workspace",
                str(ws),
                "jobs",
                "prune",
                "--older-than",
                "month",
                "--no-enrich",
            ]
        )

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "no-enrich" in err


def test_jobs_run_list_sources_prints_registry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)
    rc = app(["--workspace", str(ws), "jobs", "run", "--list-sources"])

    assert rc == 0
    out = capsys.readouterr().out
    # Site-named selectors: linkedin/indeed are listed; the retired `jobspy`
    # aggregator id must not appear in the user surface.
    assert "remoteok" in out
    assert "linkedin" in out
    assert "indeed" in out
    assert "jobspy" not in out


def test_jobs_run_empty_sources_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--sources ',,, '` parses to [] and must hard-fail rather than silently run zero."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)
    rc = app(["--workspace", str(ws), "jobs", "run", "--sources", ",,, ,"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "empty list" in err


def test_jobs_run_unknown_source_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)
    rc = app(["--workspace", str(ws), "jobs", "run", "--sources", "no_such_source"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown source" in err


def test_jobs_run_sources_override_passes_to_scrape(tmp_path: Path) -> None:
    """`--sources remoteok` propagates as sources_override to scraper.run."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    with patch(
        "daily_driver.plugins.job_search.scraper.run", return_value=0
    ) as mock_run:
        rc = app(
            [
                "--workspace",
                str(ws),
                "jobs",
                "run",
                "--sources",
                "remoteok,greenhouse",
                "--dry-run",
            ]
        )

    assert rc == 0
    assert mock_run.called
    _, kwargs = mock_run.call_args
    assert kwargs.get("sources_override") == ["remoteok", "greenhouse"]
    assert kwargs.get("dry_run") is True


def test_jobs_run_site_named_selector_accepted(tmp_path: Path) -> None:
    """`-S linkedin` is a valid selector and propagates as sources_override."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    with patch(
        "daily_driver.plugins.job_search.scraper.run", return_value=0
    ) as mock_run:
        rc = app(["--workspace", str(ws), "jobs", "run", "-S", "linkedin", "--dry-run"])

    assert rc == 0
    _, kwargs = mock_run.call_args
    assert kwargs.get("sources_override") == ["linkedin"]


def test_jobs_run_retired_jobspy_selector_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The retired `jobspy` aggregator id is no longer a valid selector."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)
    rc = app(["--workspace", str(ws), "jobs", "run", "--sources", "jobspy"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown source" in err


def test_jobs_run_json_suppresses_live_and_emits_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`jobs run --json` forces suppress_live and prints jobs-last-run.json to stdout."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)
    manifest = {
        "started_at": "2026-06-10T00:00:00+00:00",
        "sources_ok": ["remoteok"],
        "sources_failed": [],
        "new_jobs": 4,
        "interrupted": False,
        "phase_reached": "complete",
    }

    def fake_run(*_a, **_kw):  # type: ignore[no-untyped-def]
        # The runner writes the manifest on every non-dry-run exit; emulate that.
        (ws / "jobs-last-run.json").write_text(json.dumps(manifest), encoding="utf-8")
        return 0

    with patch(
        "daily_driver.plugins.job_search.scraper.run", side_effect=fake_run
    ) as mock_run:
        rc = app(["--workspace", str(ws), "jobs", "run", "--json"])

    assert rc == 0
    _, kwargs = mock_run.call_args
    assert kwargs.get("suppress_live") is True
    payload = json.loads(capsys.readouterr().out)
    assert payload["new_jobs"] == 4
    assert payload["phase_reached"] == "complete"


def test_jobs_run_without_json_does_not_suppress_live(tmp_path: Path) -> None:
    """Default run leaves suppress_live False so the live block renders on a TTY."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    with patch(
        "daily_driver.plugins.job_search.scraper.run", return_value=0
    ) as mock_run:
        rc = app(["--workspace", str(ws), "jobs", "run", "--dry-run"])

    assert rc == 0
    _, kwargs = mock_run.call_args
    assert kwargs.get("suppress_live") is False


def test_emit_run_manifest_unreadable_warns_and_prints_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unreadable manifest emits an empty JSON object on stdout AND a stderr
    warning naming the path, so a consumer can tell "I/O error" from "nothing
    to report"."""
    from daily_driver.core.console import Console
    from daily_driver.plugins.job_search.cli import _emit_run_manifest

    Console.setup_for_user(quiet=False, verbose=False, no_color=True)
    # No jobs-last-run.json exists in tmp_path -> read_text raises OSError.
    _emit_run_manifest(tmp_path)

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {}
    assert "jobs-last-run.json" in captured.err
    assert "could not read run manifest" in captured.err.lower()


def test_jobs_run_json_with_dry_run_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--json --dry-run` both own stdout (machine JSON vs human table) and a
    dry-run writes no manifest, so the combination would corrupt the JSON
    contract. argparse must reject it (exit 2) before any run."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    with pytest.raises(SystemExit) as exc:
        app(["--workspace", str(ws), "jobs", "run", "--json", "--dry-run"])

    assert exc.value.code == 2
    err = capsys.readouterr().err.lower()
    assert "not allowed with" in err or "not allowed with argument" in err


def test_jobs_run_json_interrupt_emits_manifest_and_exits_130(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """On Ctrl-C with --json, the interrupted manifest (written by run() before
    re-raising) is re-emitted to stdout, and the exit code stays 130."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)
    manifest = {
        "started_at": "2026-06-11T00:00:00+00:00",
        "sources_ok": [],
        "sources_failed": [],
        "new_jobs": 2,
        "interrupted": True,
        "phase_reached": "enrichment",
    }

    def boom(*_a, **_kw):  # type: ignore[no-untyped-def]
        # The run() wrapper writes the interrupted manifest before re-raising;
        # emulate that, then raise to unwind through the CLI's interrupt arm.
        (ws / "jobs-last-run.json").write_text(json.dumps(manifest), encoding="utf-8")
        raise KeyboardInterrupt

    with patch("daily_driver.plugins.job_search.scraper.run", side_effect=boom):
        rc = app(["--workspace", str(ws), "jobs", "run", "--json"])

    assert rc == 130
    payload = json.loads(capsys.readouterr().out)
    assert payload["interrupted"] is True
    assert payload["phase_reached"] == "enrichment"


def test_jobs_run_json_emits_manifest_on_non_interrupt_crash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-KeyboardInterrupt crash with --json must still emit the on-disk
    interrupted manifest to stdout, honoring the documented --json contract
    (previously only the Ctrl-C arm re-emitted, so a crash left stdout empty)."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)
    manifest = {
        "started_at": "2026-06-11T00:00:00+00:00",
        "sources_ok": [],
        "sources_failed": [],
        "new_jobs": 1,
        "interrupted": True,
        "phase_reached": "enrichment",
    }

    def boom(*_a, **_kw):  # type: ignore[no-untyped-def]
        # The run() wrapper writes the interrupted manifest before re-raising any
        # crash; emulate that, then raise a non-KI exception.
        (ws / "jobs-last-run.json").write_text(json.dumps(manifest), encoding="utf-8")
        raise RuntimeError("wave-1 error re-raised")

    with patch("daily_driver.plugins.job_search.scraper.run", side_effect=boom):
        rc = app(["--workspace", str(ws), "jobs", "run", "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["interrupted"] is True
    assert payload["new_jobs"] == 1


def test_jobs_run_json_list_sources_emits_json_array(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`jobs run --json --list-sources` must emit a JSON array on stdout, not bare
    source names, so a --json consumer never gets plain text."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)
    rc = app(["--workspace", str(ws), "jobs", "run", "--json", "--list-sources"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "remoteok" in payload
    assert "jobspy" not in payload


# ---------------------------------------------------------------------------
# jobs status
# ---------------------------------------------------------------------------


def test_jobs_status_no_run_yet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    rc = app(["--workspace", str(ws), "jobs", "status"])

    assert rc == 0
    captured = capsys.readouterr()
    assert "No scraper run recorded" in captured.out


def test_jobs_status_json_no_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    rc = app(["--workspace", str(ws), "jobs", "status", "--json"])

    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema"] == 1
    assert payload["data"]["last_run"] is None
    assert payload["data"]["job_counts"] == {}
    assert payload["data"]["awaiting_action"] == 0


@pytest.mark.parametrize("verbosity_flag", ["-q", "-v", "-vv"])
def test_jobs_status_json_stays_parseable_with_verbosity_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], verbosity_flag: str
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)
    rc = app(["--workspace", str(ws), verbosity_flag, "jobs", "status", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == 1
    assert "data" in payload


def test_jobs_status_json_with_last_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    last_run = {
        "started_at": "2026-04-22T08:00:00+00:00",
        "finished_at": "2026-04-22T08:05:00+00:00",
        "sources_ok": ["remoteok"],
        "sources_failed": [],
        "new_jobs": 3,
        "enriched_fit_notes": 3,
    }
    (ws / "jobs-last-run.json").write_text(json.dumps(last_run), encoding="utf-8")

    csv_content = "Status,Company,Role\napplied,Acme,SRE\ninterviewing,Corp,DevOps\nskipped,Bad,Role\n"
    (ws / "jobs.csv").write_text(csv_content, encoding="utf-8")

    rc = app(["--workspace", str(ws), "jobs", "status", "--json"])

    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    data = payload["data"]
    assert payload["schema"] == 1
    assert data["last_run"]["new_jobs"] == 3
    assert data["job_counts"]["applied"] == 1
    assert data["job_counts"]["interviewing"] == 1
    assert data["awaiting_action"] == 2


def test_jobs_status_missing_workspace_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from daily_driver.cli.cli import app

    monkeypatch.chdir(tmp_path)

    rc = app(["--workspace", str(tmp_path / "missing"), "jobs", "status"])

    assert rc == 1


# ---------------------------------------------------------------------------
# prune subcommand
# ---------------------------------------------------------------------------


def _seed_jobs_csv(ws: Path, rows: list[dict]) -> Path:
    import csv

    from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER

    p = ws / "jobs.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=CANONICAL_HEADER,
            quoting=csv.QUOTE_MINIMAL,
            extrasaction="ignore",
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return p


def test_prune_dry_run_lists_candidates_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)
    csv_path = _seed_jobs_csv(
        ws,
        [
            {
                "Status": "rejected",
                "Company": "OldCo",
                "Role": "SRE",
                "Date Last Seen": "2026-01-01",
                "Link": "https://x/1",
            },
            {
                "Status": "applied",
                "Company": "ActiveCo",
                "Role": "SRE",
                "Date Last Seen": "2026-01-01",
                "Link": "https://x/2",
            },
        ],
    )

    rc = app(
        [
            "--workspace",
            str(ws),
            "jobs",
            "prune",
            "--older-than",
            "2026-04-01",
            "--dry-run",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "OldCo" in out
    assert "ActiveCo" not in out
    # File untouched.
    assert "OldCo" in csv_path.read_text()
    assert not (ws / "jobs.archive.csv").exists()


def test_prune_moves_rows_to_archive(tmp_path: Path) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)
    _seed_jobs_csv(
        ws,
        [
            {
                "Status": "rejected",
                "Company": "OldCo",
                "Role": "SRE",
                "Date Last Seen": "2026-01-01",
                "Link": "https://x/1",
            },
            {
                "Status": "applied",
                "Company": "ActiveCo",
                "Role": "SRE",
                "Date Last Seen": "2026-01-01",
                "Link": "https://x/2",
            },
        ],
    )

    rc = app(
        [
            "--workspace",
            str(ws),
            "jobs",
            "prune",
            "--older-than",
            "2026-04-01",
        ]
    )
    assert rc == 0
    assert (ws / "jobs.archive.csv").exists()
    assert "OldCo" in (ws / "jobs.archive.csv").read_text()
    csv_text = (ws / "jobs.csv").read_text()
    assert "OldCo" not in csv_text
    assert "ActiveCo" in csv_text


def test_prune_status_filter(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)
    _seed_jobs_csv(
        ws,
        [
            {
                "Status": "applied",
                "Company": "OldApplied",
                "Role": "SRE",
                "Date Last Seen": "2026-01-01",
                "Link": "https://x/1",
            },
        ],
    )

    rc = app(
        [
            "--workspace",
            str(ws),
            "jobs",
            "prune",
            "--older-than",
            "2026-04-01",
            "--status",
            "applied",
            "--dry-run",
        ]
    )
    assert rc == 0
    assert "OldApplied" in capsys.readouterr().out


def test_prune_invalid_spec_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)
    _seed_jobs_csv(ws, [])

    rc = app(
        [
            "--workspace",
            str(ws),
            "jobs",
            "prune",
            "--older-than",
            "garbage",
        ]
    )
    assert rc == 2
    assert "invalid date spec" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Ctrl-C / KeyboardInterrupt at the CLI boundary
# ---------------------------------------------------------------------------


def test_jobs_run_keyboard_interrupt_exits_130(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl-C during a run prints a clean message to stderr and returns 130."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    def boom(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise KeyboardInterrupt

    with patch("daily_driver.plugins.job_search.scraper.run", side_effect=boom):
        rc = app(["--workspace", str(ws), "jobs", "run"])

    captured = capsys.readouterr()
    assert rc == 130, f"expected exit code 130 (SIGINT), got {rc}"
    # No Python traceback should reach the user on a normal Ctrl-C.
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
    # Some user-facing acknowledgement of the interrupt.
    assert "interrupt" in captured.err.lower()


def test_jobs_run_keyboard_interrupt_no_traceback_with_verbose(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Even with -v, Ctrl-C should not surface a traceback."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    def boom(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise KeyboardInterrupt

    with patch("daily_driver.plugins.job_search.scraper.run", side_effect=boom):
        rc = app(["--workspace", str(ws), "-v", "jobs", "run"])

    captured = capsys.readouterr()
    assert rc == 130
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_jobs_backfill_keyboard_interrupt_exits_130(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl-C during backfill should return 130 and print a clean message."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    def boom(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise KeyboardInterrupt

    with patch(
        "daily_driver.plugins.job_search.scraper.run_backfill", side_effect=boom
    ):
        rc = app(["--workspace", str(ws), "jobs", "backfill"])

    captured = capsys.readouterr()
    assert rc == 130
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_archive_dedup_loaded_at_scrape_start(tmp_path: Path) -> None:
    """load_archive_dedup unions URLs/keys from jobs.archive.csv."""
    from daily_driver.plugins.job_search.jobs_archive import load_archive_dedup

    ws = _init_workspace(tmp_path, scraper_enabled=True)
    csv_path = ws / "jobs.csv"
    archive = ws / "jobs.archive.csv"

    import csv

    from daily_driver.plugins.job_search.scraper.csv_io import CANONICAL_HEADER

    with open(archive, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CANONICAL_HEADER, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        w.writerow(
            {
                "Status": "rejected",
                "Company": "Pruned",
                "Role": "SRE",
                "Link": "https://archived/1",
            }
        )

    urls, keys = load_archive_dedup(csv_path)
    assert "https://archived/1" in urls
    assert any("pruned" in k for k in keys)


def test_jobs_backfill_passes_ai_block_to_run_backfill(tmp_path: Path) -> None:
    """Regression: jobs `backfill` must pass the workspace's typed `AIConfig`
    (shared provider blocks) plus the plugin's enrichment routing to enrichment.
    Without this, every backfill call silently defaults to claude regardless of
    plugins.job_search.enrichment.provider.
    """
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)
    # Inject an explicit ollama config so the test fails loudly if it's dropped.
    (ws / ".dd-config.yaml").write_text(
        "daily_driver:\n"
        "  output_dir: .\n"
        "tracker:\n"
        "  default_category: task\n"
        "  categories:\n"
        "    task:\n"
        "      required: [title]\n"
        "ai:\n"
        "  ollama:\n"
        "    endpoint: http://localhost:11434\n"
        "    timeout: 60\n"
        "plugins:\n"
        "  job_search:\n"
        "    scraper:\n"
        "      enabled: true\n"
        "    enrichment:\n"
        "      provider: ollama\n"
        "      model: qwen2.5:32b\n"
    )

    with patch("daily_driver.plugins.job_search.scraper.run_backfill") as mock_backfill:
        rc = app(["--workspace", str(ws), "jobs", "backfill"])

    assert rc == 0
    assert mock_backfill.called
    plugin = mock_backfill.call_args.args[0]
    assert plugin.enrichment.provider == "ollama"
    assert plugin.enrichment.model == "qwen2.5:32b"
    # The shared provider block still flows through for connection/tuning.
    ai = mock_backfill.call_args.kwargs["ai"]
    assert ai.ollama.timeout == 60


def test_jobs_run_scrape_passes_ai_block_to_run(tmp_path: Path) -> None:
    """Same regression for the live scrape path: routing must flow through."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)
    (ws / ".dd-config.yaml").write_text(
        "daily_driver:\n"
        "  output_dir: .\n"
        "tracker:\n"
        "  default_category: task\n"
        "  categories:\n"
        "    task:\n"
        "      required: [title]\n"
        "plugins:\n"
        "  job_search:\n"
        "    scraper:\n"
        "      enabled: true\n"
        "    enrichment:\n"
        "      provider: ollama\n"
        "      model: qwen2.5:32b\n"
    )

    with patch(
        "daily_driver.plugins.job_search.scraper.run", return_value=0
    ) as mock_run:
        rc = app(["--workspace", str(ws), "jobs", "run", "--dry-run"])

    assert rc == 0
    plugin = mock_run.call_args.args[0]
    assert plugin.enrichment.provider == "ollama"
    assert plugin.enrichment.model == "qwen2.5:32b"


# ---------------------------------------------------------------------------
# Interrupt exit codes (130 SIGINT / 143 SIGTERM) and status recovery line
# ---------------------------------------------------------------------------


def test_jobs_run_sigint_exits_130(tmp_path: Path) -> None:
    """A Ctrl-C (KeyboardInterrupt, no SIGTERM recorded) exits 130."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    def boom(*_a: object, **_kw: object) -> int:
        raise KeyboardInterrupt

    with patch("daily_driver.plugins.job_search.scraper.run", side_effect=boom):
        rc = app(["--workspace", str(ws), "jobs", "run"])

    assert rc == 130


def test_jobs_run_sigterm_exits_143(tmp_path: Path) -> None:
    """SIGTERM routes through the graceful path and exits 143 (128 + 15).

    The run stub fires the installed SIGTERM handler (which records the signal
    and raises KeyboardInterrupt), so the CLI sees a SIGTERM-flavored interrupt.
    """
    import os
    import signal

    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    def deliver_sigterm(*_a: object, **_kw: object) -> int:
        # The CLI installed the run-scoped SIGTERM handler before calling run();
        # delivering the signal here exercises that handler on the main thread.
        os.kill(os.getpid(), signal.SIGTERM)
        return 0  # unreachable: the handler raises KeyboardInterrupt

    with patch(
        "daily_driver.plugins.job_search.scraper.run", side_effect=deliver_sigterm
    ):
        rc = app(["--workspace", str(ws), "jobs", "run"])

    assert rc == 143


def test_jobs_status_shows_recovery_line_when_interrupted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """jobs status prints the backfill recovery line for an interrupted run."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)
    (ws / "jobs-last-run.json").write_text(
        json.dumps(
            {
                "started_at": "2026-06-10T00:00:00+00:00",
                "new_jobs": 12,
                "sources_ok": ["remoteok"],
                "sources_failed": [],
                "interrupted": True,
                "phase_reached": "enrichment",
            }
        ),
        encoding="utf-8",
    )

    rc = app(["--workspace", str(ws), "jobs", "status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "interrupted during enrichment" in out
    assert "jobs backfill" in out
    assert "jobs run --backfill" not in out


# ---------------------------------------------------------------------------
# jobs promote — CLI wiring
# ---------------------------------------------------------------------------


def _seed_promote_csv(ws_root: Path) -> Path:
    return _seed_jobs_csv(
        ws_root,
        [
            {
                "Company": "Acme Corp",
                "Role": "SRE",
                "Status": "interviewing",
                "Link": "https://jobs.example.com/acme/1",
                "Source": "linkedin",
            }
        ],
    )


def test_jobs_promote_help_exits_0() -> None:
    from daily_driver.cli.cli import app

    with pytest.raises(SystemExit) as exc:
        app(["jobs", "promote", "--help"])
    assert exc.value.code == 0


def test_jobs_promote_creates_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.core.tracker import Tracker
    from daily_driver.core.workspace import Workspace

    ws = _init_workspace(tmp_path)
    _seed_promote_csv(ws)

    rc = app(
        ["--workspace", str(ws), "jobs", "promote", "https://jobs.example.com/acme/1"]
    )
    err = " ".join(capsys.readouterr().err.split())
    assert rc == 0
    # Resolved status is always named in the success line.
    assert "Promoted job-001 [interviewing]: Acme Corp -- SRE" in err

    entries = Tracker(Workspace.discover_or_fail(override=ws)).list(category="job")
    assert len(entries) == 1
    assert entries[0].title == "Acme Corp -- SRE"


def test_jobs_promote_idempotent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _seed_promote_csv(ws)
    url = "https://jobs.example.com/acme/1"

    assert app(["--workspace", str(ws), "jobs", "promote", url]) == 0
    capsys.readouterr()
    rc = app(["--workspace", str(ws), "jobs", "promote", url])
    err = capsys.readouterr().err
    assert rc == 0
    assert "already promoted as job-001" in err


def test_jobs_promote_no_match_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _seed_promote_csv(ws)

    rc = app(["--workspace", str(ws), "jobs", "promote", "nope-co"])
    assert rc == 1
    assert "no jobs.csv row matched" in capsys.readouterr().err


def test_jobs_promote_dry_run_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.core.tracker import Tracker
    from daily_driver.core.workspace import Workspace

    ws = _init_workspace(tmp_path)
    _seed_promote_csv(ws)

    rc = app(["--workspace", str(ws), "jobs", "promote", "-n", "acme"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "would create job entry" in err
    entries = Tracker(Workspace.discover_or_fail(override=ws)).list(category="job")
    assert entries == []


def test_jobs_promote_blank_status_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _seed_jobs_csv(
        ws,
        [
            {
                "Company": "Hooli",
                "Role": "SRE",
                "Status": "",
                "Link": "https://jobs.example.com/hooli/7",
                "Source": "linkedin",
            }
        ],
    )

    rc = app(["--workspace", str(ws), "jobs", "promote", "Hooli"])
    # Rich may soft-wrap the stderr line at the test console width; collapse
    # whitespace so the assertion is independent of wrap position.
    err = " ".join(capsys.readouterr().err.split())
    assert rc == 0
    assert "row has no status; recorded as 'applied'" in err
    assert "Promoted job-001 [applied]: Hooli -- SRE" in err


def test_jobs_promote_unknown_status_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _seed_jobs_csv(
        ws,
        [
            {
                "Company": "Initech",
                "Role": "Engineer",
                "Status": "shortlisted",
                "Link": "https://jobs.example.com/initech/8",
                "Source": "linkedin",
            }
        ],
    )

    rc = app(["--workspace", str(ws), "jobs", "promote", "Initech"])
    err = " ".join(capsys.readouterr().err.split())
    assert rc == 0
    assert (
        "row status 'shortlisted' not in the job lifecycle; recorded as 'applied'"
        in err
    )


def test_jobs_promote_no_link_row_notes_and_dedups(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.core.tracker import Tracker
    from daily_driver.core.workspace import Workspace

    ws = _init_workspace(tmp_path)
    _seed_jobs_csv(
        ws,
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

    rc = app(["--workspace", str(ws), "jobs", "promote", "Hooli"])
    err = " ".join(capsys.readouterr().err.split())
    assert rc == 0
    assert "(row has no Link)" in err

    # Re-promote of the same blank-Link row is a no-op (company/role dedup key).
    rc = app(["--workspace", str(ws), "jobs", "promote", "Hooli"])
    err = " ".join(capsys.readouterr().err.split())
    assert rc == 0
    assert "already promoted as job-001" in err
    entries = Tracker(Workspace.discover_or_fail(override=ws)).list(category="job")
    assert len(entries) == 1
