from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from daily_driver.integrations import playwright as pw
from daily_driver.integrations.playwright import (
    PlaywrightError,
    install_browser,
    probe_browser,
)


def _dry_run_stdout(location: Path) -> str:
    return (
        f"Firefox 148.0.2 (playwright firefox v1511)\n"
        f"  Install location:    {location}\n"
        f"  Download url:        https://example/firefox.zip\n"
    )


def _run_stub(*, stdout: str = "", stderr: str = "", rc: int = 0):
    def _run(args, **kw):
        proc = MagicMock()
        proc.stdout = stdout
        proc.stderr = stderr
        proc.returncode = rc
        return proc

    return _run


def test_probe_reports_a_downloaded_build_with_no_error(monkeypatch, tmp_path):
    browser_dir = tmp_path / "firefox-1511"
    browser_dir.mkdir()
    monkeypatch.setattr(
        subprocess, "run", _run_stub(stdout=_dry_run_stdout(browser_dir))
    )

    probe = probe_browser()

    assert probe.installed is True
    assert probe.playwright_error is None


def test_probe_reports_a_missing_build_as_no_error(monkeypatch, tmp_path):
    """Playwright answered; the build is simply not downloaded. The remedy is
    to install the browser, so this must NOT read as a playwright failure."""
    browser_dir = tmp_path / "firefox-1511"
    monkeypatch.setattr(
        subprocess, "run", _run_stub(stdout=_dry_run_stdout(browser_dir))
    )

    probe = probe_browser()

    assert probe.installed is False
    assert probe.playwright_error is None


def test_probe_reports_an_unrunnable_playwright(monkeypatch):
    def _boom(args, **kw):
        raise FileNotFoundError(args[0])

    monkeypatch.setattr(subprocess, "run", _boom)

    probe = probe_browser()

    assert probe.installed is False
    assert probe.playwright_error is not None


def test_probe_surfaces_stderr_from_a_failing_dry_run(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        _run_stub(stderr="ModuleNotFoundError: No module named 'playwright'", rc=1),
    )

    probe = probe_browser()

    assert probe.installed is False
    assert "No module named 'playwright'" in (probe.playwright_error or "")


def test_probe_takes_the_last_line_of_a_traceback(monkeypatch):
    """The first line of a traceback is boilerplate; the last line names the
    failure. Grabbing the wrong end would surface "Traceback (most recent call
    last):" as the whole explanation."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _run_stub(
            stderr=(
                "Traceback (most recent call last):\n"
                '  File "<frozen runpy>", line 198, in _run_module_as_main\n'
                "ModuleNotFoundError: No module named 'playwright'\n"
            ),
            rc=1,
        ),
    )

    probe = probe_browser()

    assert probe.playwright_error == "ModuleNotFoundError: No module named 'playwright'"


def test_probe_falls_back_to_stdout_when_stderr_is_blank(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        _run_stub(
            stderr="   \n", stdout="playwright: unknown browser 'firefox'\n", rc=1
        ),
    )

    probe = probe_browser()

    assert probe.playwright_error == "playwright: unknown browser 'firefox'"


def test_probe_reports_an_unparseable_dry_run(monkeypatch):
    """Playwright answered but named no install location for the engine."""
    monkeypatch.setattr(subprocess, "run", _run_stub(stdout="nothing useful here\n"))

    probe = probe_browser()

    assert probe.installed is False
    assert probe.playwright_error is not None


def test_probe_picks_firefox_when_another_entry_precedes(monkeypatch, tmp_path):
    # A transitive entry (ffmpeg) listed before firefox must not be mistaken
    # for the firefox build path.
    ffmpeg_dir = tmp_path / "ffmpeg-1011"  # deliberately not created
    firefox_dir = tmp_path / "firefox-1511"
    firefox_dir.mkdir()
    stdout = (
        f"FFmpeg (playwright ffmpeg v1011)\n"
        f"  Install location:    {ffmpeg_dir}\n"
        f"Firefox 148.0.2 (playwright firefox v1511)\n"
        f"  Install location:    {firefox_dir}\n"
    )
    monkeypatch.setattr(subprocess, "run", _run_stub(stdout=stdout))

    assert probe_browser().installed is True


def test_install_firefox_runs_install_command(monkeypatch):
    captured = {}

    def _run(args, **kw):
        captured["args"] = args
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        return proc

    monkeypatch.setattr(subprocess, "run", _run)

    install_browser()

    assert captured["args"] == pw._install_cmd("firefox")


def test_install_firefox_raises_on_nonzero(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _run_stub(rc=1, stderr="download failed"))

    with pytest.raises(PlaywrightError) as exc:
        install_browser()
    assert exc.value.returncode == 1
    assert exc.value.stderr == "download failed"


def test_install_browser_passes_engine_through(monkeypatch):
    captured = {}

    def _run(args, **kw):
        captured["args"] = args
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        return proc

    monkeypatch.setattr(subprocess, "run", _run)

    install_browser("chromium")

    assert captured["args"] == pw._install_cmd("chromium")
    assert captured["args"][-1] == "chromium"


def test_probe_matches_the_engine_specific_dir(monkeypatch, tmp_path):
    # chromium build present; the chromium_headless_shell sibling must not be
    # mistaken for it (both names start with "chromium").
    shell_dir = tmp_path / "chromium_headless_shell-1234"  # deliberately uncreated
    chromium_dir = tmp_path / "chromium-1234"
    chromium_dir.mkdir()
    stdout = (
        f"chromium_headless_shell (playwright build v1234)\n"
        f"  Install location:    {shell_dir}\n"
        f"Chromium 999 (playwright build v1234)\n"
        f"  Install location:    {chromium_dir}\n"
    )
    monkeypatch.setattr(subprocess, "run", _run_stub(stdout=stdout))

    assert probe_browser("chromium").installed is True
