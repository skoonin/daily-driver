"""CLI tests for the ``scrape-jobs`` subcommand.

Network and source-specific behavior is NOT covered here — these tests
exercise argparse wiring, workspace resolution, config loading, and the
backfill short-circuit against a mocked ``daily_driver.scraper`` module.

All invocations now use the ``scrape-jobs run`` / ``scrape-jobs status``
subcommand form introduced in the Wave 2b refactor.
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


def test_scrape_jobs_help_exits_0() -> None:
    from daily_driver.cli.cli import app

    with pytest.raises(SystemExit) as exc:
        app(["scrape-jobs", "--help"])

    assert exc.value.code == 0


def test_scrape_jobs_no_action_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bare ``scrape-jobs`` with no action returns 2 and prints usage."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    rc = app(["--workspace", str(ws), "scrape-jobs"])

    assert rc == 2


def test_scrape_jobs_run_help_exits_0() -> None:
    from daily_driver.cli.cli import app

    with pytest.raises(SystemExit) as exc:
        app(["scrape-jobs", "run", "--help"])

    assert exc.value.code == 0


def test_scrape_jobs_status_help_exits_0() -> None:
    from daily_driver.cli.cli import app

    with pytest.raises(SystemExit) as exc:
        app(["scrape-jobs", "status", "--help"])

    assert exc.value.code == 0


# ---------------------------------------------------------------------------
# scrape-jobs run — workspace / config validation
# ---------------------------------------------------------------------------


def test_scrape_jobs_run_missing_workspace_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from daily_driver.cli.cli import app

    monkeypatch.chdir(tmp_path)

    rc = app(["--workspace", str(tmp_path / "missing"), "scrape-jobs", "run"])

    assert rc == 1


def test_scrape_jobs_run_no_job_search_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fresh workspace with no plugins.job_search config exits 1."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    rc = app(["--workspace", str(ws), "scrape-jobs", "run"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "plugins.job_search" in captured.err


def test_scrape_jobs_run_scraper_disabled_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=False)

    rc = app(["--workspace", str(ws), "scrape-jobs", "run"])

    captured = capsys.readouterr()
    assert rc == 0
    assert "Scraper disabled" in captured.out


def test_scrape_jobs_run_backfill_dispatches(tmp_path: Path) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    with patch("daily_driver.scraper.run_backfill") as mock_backfill:
        rc = app(["--workspace", str(ws), "scrape-jobs", "run", "--backfill"])

    assert rc == 0
    assert mock_backfill.called


def test_scrape_jobs_run_dry_run_passes_flag(tmp_path: Path) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    with patch("daily_driver.scraper.run", return_value=0) as mock_run:
        rc = app(["--workspace", str(ws), "scrape-jobs", "run", "--dry-run"])

    assert rc == 0
    assert mock_run.called
    _, kwargs = mock_run.call_args
    assert kwargs.get("dry_run") is True


def test_scrape_jobs_run_legacy_config_yaml_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Legacy config.yaml at workspace root is rejected with a migration error."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    (ws / "config.yaml").write_text(
        "output_dir: .\n" "job_search:\n  scraper:\n    enabled: false\n"
    )

    rc = app(["--workspace", str(ws), "scrape-jobs", "run"])

    assert rc == 1
    captured = capsys.readouterr()
    assert "legacy config file" in captured.err and "configuration.md" in captured.err


# ---------------------------------------------------------------------------
# scrape-jobs status
# ---------------------------------------------------------------------------


def test_scrape_jobs_status_no_run_yet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    rc = app(["--workspace", str(ws), "scrape-jobs", "status"])

    assert rc == 0
    captured = capsys.readouterr()
    assert "No scraper run recorded" in captured.out


def test_scrape_jobs_status_json_no_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path, scraper_enabled=True)

    rc = app(["--workspace", str(ws), "scrape-jobs", "status", "--json"])

    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema"] == 1
    assert payload["data"]["last_run"] is None
    assert payload["data"]["job_counts"] == {}
    assert payload["data"]["awaiting_action"] == 0


def test_scrape_jobs_status_json_with_last_run(
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
        "enriched_product": 2,
        "skipped_below_comp": 0,
    }
    (ws / "jobs-last-run.json").write_text(json.dumps(last_run), encoding="utf-8")

    csv_content = "status,company,role\napplied,Acme,SRE\ninterviewing,Corp,DevOps\nskipped,Bad,Role\n"
    (ws / "jobs.csv").write_text(csv_content, encoding="utf-8")

    rc = app(["--workspace", str(ws), "scrape-jobs", "status", "--json"])

    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    data = payload["data"]
    assert payload["schema"] == 1
    assert data["last_run"]["new_jobs"] == 3
    assert data["job_counts"]["applied"] == 1
    assert data["job_counts"]["interviewing"] == 1
    assert data["awaiting_action"] == 2


def test_scrape_jobs_status_missing_workspace_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from daily_driver.cli.cli import app

    monkeypatch.chdir(tmp_path)

    rc = app(["--workspace", str(tmp_path / "missing"), "scrape-jobs", "status"])

    assert rc == 1
