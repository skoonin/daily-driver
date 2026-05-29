from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from daily_driver.integrations import playwright as pw
from daily_driver.integrations.playwright import (
    PlaywrightError,
    firefox_installed,
    install_firefox,
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

    assert firefox_installed() is True


def test_firefox_installed_false_when_location_missing(monkeypatch, tmp_path):
    # Dry-run resolves a path, but nothing is downloaded there yet.
    browser_dir = tmp_path / "firefox-1511"
    monkeypatch.setattr(
        subprocess, "run", _run_stub(stdout=_dry_run_stdout(browser_dir))
    )

    assert firefox_installed() is False


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

    assert firefox_installed() is True


def test_firefox_installed_false_when_dry_run_nonzero(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _run_stub(rc=1))

    assert firefox_installed() is False


def test_firefox_installed_false_when_no_location_line(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _run_stub(stdout="no useful output"))

    assert firefox_installed() is False


def test_firefox_installed_false_when_playwright_absent(monkeypatch):
    def _raise(args, **kw):
        raise FileNotFoundError("python missing")

    monkeypatch.setattr(subprocess, "run", _raise)

    assert firefox_installed() is False


def test_install_firefox_runs_install_command(monkeypatch):
    captured = {}

    def _run(args, **kw):
        captured["args"] = args
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        return proc

    monkeypatch.setattr(subprocess, "run", _run)

    install_firefox()

    assert captured["args"] == pw._INSTALL


def test_install_firefox_raises_on_nonzero(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _run_stub(rc=1, stderr="download failed"))

    with pytest.raises(PlaywrightError) as exc:
        install_firefox()
    assert exc.value.returncode == 1
    assert exc.value.stderr == "download failed"
