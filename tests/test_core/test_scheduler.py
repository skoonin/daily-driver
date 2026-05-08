"""Tests for scheduler config merging, job building, and plist rendering.

Pure-function tests — no launchctl, no filesystem LaunchAgents dir. The
install/uninstall launchctl-wrapping paths are exercised via integration tests
in `tests/test_integrations/test_launchd.py`.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from rich.console import Console

from daily_driver.core import scheduler


@dataclass
class _FakeConfig:
    scheduler: dict | None = None


@dataclass
class _FakeWorkspace:
    root: Path
    state_dir: Path
    config: _FakeConfig
    version: str = "0.1.0"
    logger: logging.Logger = logging.getLogger("test.scheduler")
    console: Console = Console(stderr=True)

    @property
    def ephemeral_dir(self) -> Path:
        return self.state_dir / "state"

    @classmethod
    def make(cls, root: Path, scheduler_cfg: dict | None = None) -> _FakeWorkspace:
        return cls(
            root=root,
            state_dir=root / ".daily-driver",
            config=_FakeConfig(scheduler=scheduler_cfg),
        )


# ---------------------------------------------------------------------------
# build_jobs + config merge
# ---------------------------------------------------------------------------


class TestBuildJobs:
    def test_defaults_produce_both_jobs(self, tmp_path: Path) -> None:
        ws = _FakeWorkspace.make(tmp_path)
        jobs = scheduler.build_jobs(ws)
        labels = [j.label for j in jobs]
        assert "com.daily-driver.checkin" in labels
        assert "com.daily-driver.scrape-jobs" in labels

    def test_user_override_replaces_defaults(self, tmp_path: Path) -> None:
        ws = _FakeWorkspace.make(
            tmp_path, scheduler_cfg={"checkin": {"times": ["09:30"]}}
        )
        jobs = scheduler.build_jobs(ws)
        checkin = next(j for j in jobs if j.label == "com.daily-driver.checkin")
        assert checkin.context["times"] == [{"hour": 9, "minute": 30}]

    def test_empty_checkin_times_omits_job(self, tmp_path: Path) -> None:
        ws = _FakeWorkspace.make(tmp_path, scheduler_cfg={"checkin": {"times": []}})
        jobs = scheduler.build_jobs(ws)
        assert all(j.label != "com.daily-driver.checkin" for j in jobs)

    def test_missing_scrape_time_omits_job(self, tmp_path: Path) -> None:
        ws = _FakeWorkspace.make(tmp_path, scheduler_cfg={"scrape_jobs": {}})
        jobs = scheduler.build_jobs(ws)
        assert all(j.label != "com.daily-driver.scrape-jobs" for j in jobs)

    def test_log_paths_land_in_state_logs(self, tmp_path: Path) -> None:
        ws = _FakeWorkspace.make(tmp_path)
        jobs = scheduler.build_jobs(ws)
        checkin = next(j for j in jobs if j.label == "com.daily-driver.checkin")
        expected_logs = ws.ephemeral_dir / "logs"
        assert checkin.context["stdout_path"] == str(
            expected_logs / "launchd-checkin.out"
        )
        assert checkin.context["stderr_path"] == str(
            expected_logs / "launchd-checkin.err"
        )

    def test_program_arguments_pass_workspace_flag(self, tmp_path: Path) -> None:
        ws = _FakeWorkspace.make(tmp_path)
        jobs = scheduler.build_jobs(ws)
        for job in jobs:
            args = job.context["program_arguments"]
            assert "--workspace" in args
            assert str(tmp_path) in args

    def test_invalid_time_raises(self, tmp_path: Path) -> None:
        ws = _FakeWorkspace.make(
            tmp_path, scheduler_cfg={"checkin": {"times": ["25:00"]}}
        )
        with pytest.raises(scheduler.SchedulerError):
            scheduler.build_jobs(ws)


# ---------------------------------------------------------------------------
# render_plist — produces valid XML with expected keys
# ---------------------------------------------------------------------------


class TestRenderPlist:
    def test_checkin_plist_parses_as_xml(self, tmp_path: Path) -> None:
        ws = _FakeWorkspace.make(tmp_path)
        jobs = scheduler.build_jobs(ws)
        checkin = next(j for j in jobs if j.label == "com.daily-driver.checkin")

        xml_str = scheduler.render_plist(checkin)
        root = ET.fromstring(xml_str)

        # Walk <dict> children as key/value pairs.
        top = root.find("dict")
        assert top is not None
        pairs = dict(_plist_dict_pairs(top))
        assert pairs["Label"].text == "com.daily-driver.checkin"

    def test_scrape_jobs_plist_contains_single_calendar_interval(
        self, tmp_path: Path
    ) -> None:
        ws = _FakeWorkspace.make(tmp_path)
        jobs = scheduler.build_jobs(ws)
        scrape = next(j for j in jobs if j.label == "com.daily-driver.scrape-jobs")

        xml_str = scheduler.render_plist(scrape)
        assert "<key>StartCalendarInterval</key>" in xml_str
        assert "<key>Hour</key>" in xml_str
        assert "<key>Minute</key>" in xml_str

    def test_checkin_renders_multiple_time_dicts(self, tmp_path: Path) -> None:
        ws = _FakeWorkspace.make(
            tmp_path,
            scheduler_cfg={"checkin": {"times": ["09:00", "13:00", "17:30"]}},
        )
        jobs = scheduler.build_jobs(ws)
        checkin = next(j for j in jobs if j.label == "com.daily-driver.checkin")
        xml_str = scheduler.render_plist(checkin)
        # Three nested dicts under StartCalendarInterval array.
        assert xml_str.count("<key>Hour</key>") == 3


def _plist_dict_pairs(dict_elem):
    """Yield (key_text, value_element) pairs from an Apple plist <dict>."""
    children = list(dict_elem)
    for i in range(0, len(children), 2):
        k = children[i]
        v = children[i + 1] if i + 1 < len(children) else None
        if k.tag == "key":
            yield k.text, v


# ---------------------------------------------------------------------------
# Non-macOS platform guard
# ---------------------------------------------------------------------------


class TestPlatformGuard:
    def test_install_raises_on_non_darwin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _FakeWorkspace.make(tmp_path)
        monkeypatch.setattr(sys, "platform", "linux")
        with pytest.raises(scheduler.SchedulerError, match="macOS-only"):
            scheduler.install_all(ws)

    def test_uninstall_raises_on_non_darwin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _FakeWorkspace.make(tmp_path)
        monkeypatch.setattr(sys, "platform", "linux")
        with pytest.raises(scheduler.SchedulerError, match="macOS-only"):
            scheduler.uninstall_all(ws)


# ---------------------------------------------------------------------------
# install_all + uninstall_all — launchctl fully mocked out
# ---------------------------------------------------------------------------


class TestInstallUninstall:
    def _patch_launchd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> dict:
        """Redirect launchd integration to a tmp agents dir; record calls."""
        from daily_driver.integrations import launchd as launchd_int

        fake_agents = tmp_path / "LaunchAgents"
        fake_agents.mkdir()
        calls: dict[str, list] = {"load": [], "unload": [], "remove": []}

        monkeypatch.setattr(launchd_int, "launch_agents_dir", lambda: fake_agents)
        monkeypatch.setattr(
            launchd_int,
            "load",
            lambda label: calls["load"].append(label),
        )
        monkeypatch.setattr(
            launchd_int,
            "unload",
            lambda label: calls["unload"].append(label),
        )

        real_remove = launchd_int.remove

        def spy_remove(label: str) -> bool:
            result = real_remove(label)
            if result:
                calls["remove"].append(label)
            return result

        monkeypatch.setattr(launchd_int, "remove", spy_remove)
        return calls

    def test_install_writes_plists_and_calls_launchctl(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        ws = _FakeWorkspace.make(tmp_path)
        calls = self._patch_launchd(monkeypatch, tmp_path)

        installed = scheduler.install_all(ws)

        assert set(installed) == {
            "com.daily-driver.checkin",
            "com.daily-driver.scrape-jobs",
        }
        assert set(calls["load"]) == set(installed)
        # State mirror written
        state_dir = ws.ephemeral_dir / "launchd"
        assert (state_dir / "com.daily-driver.checkin.plist").exists()
        assert (state_dir / "com.daily-driver.scrape-jobs.plist").exists()
        # Log dir pre-created
        assert (ws.ephemeral_dir / "logs").is_dir()

    def test_install_is_idempotent_unload_before_load(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        ws = _FakeWorkspace.make(tmp_path)
        calls = self._patch_launchd(monkeypatch, tmp_path)

        scheduler.install_all(ws)
        scheduler.install_all(ws)

        # Each install triggers unload then load for every job.
        assert calls["unload"].count("com.daily-driver.checkin") == 2
        assert calls["load"].count("com.daily-driver.checkin") == 2

    def test_uninstall_removes_plists_and_clears_state_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        ws = _FakeWorkspace.make(tmp_path)
        self._patch_launchd(monkeypatch, tmp_path)

        scheduler.install_all(ws)
        removed = scheduler.uninstall_all(ws)

        assert set(removed) == {
            "com.daily-driver.checkin",
            "com.daily-driver.scrape-jobs",
        }
        assert not (ws.ephemeral_dir / "launchd").exists()

    def test_uninstall_always_removes_state_mirror(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The --keep-state flag was removed in v0.1.x; mirror is always cleaned up."""
        monkeypatch.setattr(sys, "platform", "darwin")
        ws = _FakeWorkspace.make(tmp_path)
        self._patch_launchd(monkeypatch, tmp_path)

        scheduler.install_all(ws)
        scheduler.uninstall_all(ws)

        state_dir = ws.ephemeral_dir / "launchd"
        assert not state_dir.exists()
