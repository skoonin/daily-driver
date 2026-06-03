from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from daily_driver.integrations import playwright as pw
from daily_driver.integrations.playwright import (
    PlaywrightError,
    browser_installed,
    install_browser,
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


def test_firefox_installed_true_when_location_exists(monkeypatch, tmp_path):
    browser_dir = tmp_path / "firefox-1511"
    browser_dir.mkdir()
    monkeypatch.setattr(
        subprocess, "run", _run_stub(stdout=_dry_run_stdout(browser_dir))
    )

    assert browser_installed() is True


def test_firefox_installed_false_when_location_missing(monkeypatch, tmp_path):
    # Dry-run resolves a path, but nothing is downloaded there yet.
    browser_dir = tmp_path / "firefox-1511"
    monkeypatch.setattr(
        subprocess, "run", _run_stub(stdout=_dry_run_stdout(browser_dir))
    )

    assert browser_installed() is False


def test_firefox_installed_picks_firefox_when_other_entry_precedes(
    monkeypatch, tmp_path
):
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

    assert browser_installed() is True


def test_firefox_installed_false_when_dry_run_nonzero(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _run_stub(rc=1))

    assert browser_installed() is False


def test_firefox_installed_false_when_no_location_line(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _run_stub(stdout="no useful output"))

    assert browser_installed() is False


def test_firefox_installed_false_when_playwright_absent(monkeypatch):
    def _raise(args, **kw):
        raise FileNotFoundError("python missing")

    monkeypatch.setattr(subprocess, "run", _raise)

    assert browser_installed() is False


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


def test_browser_installed_matches_engine_specific_dir(monkeypatch, tmp_path):
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

    assert browser_installed("chromium") is True
