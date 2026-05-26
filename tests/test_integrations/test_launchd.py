"""Tests for the launchd integration wrapper.

launchctl calls are mocked out; we only verify file writes, label→path
resolution, and correct subprocess invocation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from daily_driver.integrations import launchd


class TestPlistPaths:
    def test_plist_path_uses_home_library(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        assert (
            launchd.plist_path("com.daily-driver.checkin")
            == tmp_path / "Library" / "LaunchAgents" / "com.daily-driver.checkin.plist"
        )


class TestWritePlist:
    def test_creates_parent_dir_and_writes_content(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        path = launchd.write_plist("test.label", "<plist/>")
        assert path.read_text(encoding="utf-8") == "<plist/>"
        assert path.parent.is_dir()


class TestRemove:
    def test_returns_false_when_absent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        assert launchd.remove("nonexistent.label") is False

    def test_returns_true_and_deletes_when_present(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        launchd.write_plist("bye.label", "<plist/>")
        path = launchd.plist_path("bye.label")
        assert path.exists()

        assert launchd.remove("bye.label") is True
        assert not path.exists()


class TestLaunchctlShell:
    def test_unload_runs_launchctl_when_file_exists(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        launchd.write_plist("foo.label", "<plist/>")

        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)

            class _R:
                returncode = 0

            return _R()

        monkeypatch.setattr(subprocess, "run", fake_run)
        launchd.unload("foo.label")

        assert calls == [
            ["launchctl", "unload", str(launchd.plist_path("foo.label"))],
        ]

    def test_unload_is_no_op_when_file_absent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        called: list[list[str]] = []
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **kw: called.append(cmd) or type("R", (), {"returncode": 0})(),
        )
        launchd.unload("ghost.label")
        assert called == []

    def test_load_calls_launchctl_and_raises_on_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        recorded: dict = {}

        def fake_run(cmd, **kwargs):
            recorded["cmd"] = cmd
            recorded["kwargs"] = kwargs

            class _R:
                returncode = 0
                stderr = ""

            return _R()

        monkeypatch.setattr(subprocess, "run", fake_run)
        launchd.load("foo.label")
        assert recorded["cmd"][0:2] == ["launchctl", "load"]
        # check=False so non-zero exits surface as LaunchdLoadError, not CalledProcessError
        assert recorded["kwargs"].get("check") is False

        def fake_run_failing(cmd, **kwargs):
            class _R:
                returncode = 5
                stderr = "Load failed: 5: Input/output error"

            return _R()

        monkeypatch.setattr(subprocess, "run", fake_run_failing)
        with pytest.raises(launchd.LaunchdLoadError) as exc_info:
            launchd.load("foo.label")
        assert exc_info.value.label == "foo.label"
        assert exc_info.value.returncode == 5
        assert "Input/output error" in str(exc_info.value)
