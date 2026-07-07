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
from daily_driver.core.config_models import SchedulerConfig


@dataclass
class _FakeSchedule:
    day_start: str | None = None
    day_end: str | None = None
    days: str | list[str] = "daily"


@dataclass
class _FakeConfig:
    scheduler: SchedulerConfig | None = None
    schedule: _FakeSchedule = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.schedule is None:
            self.schedule = _FakeSchedule()


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
    def make(
        cls,
        root: Path,
        scheduler_cfg: dict | None = None,
        schedule: _FakeSchedule | None = None,
    ) -> _FakeWorkspace:
        scheduler_model = (
            SchedulerConfig.model_validate(scheduler_cfg)
            if scheduler_cfg is not None
            else None
        )
        return cls(
            root=root,
            state_dir=root / ".daily-driver",
            config=_FakeConfig(
                scheduler=scheduler_model, schedule=schedule or _FakeSchedule()
            ),
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
        assert "com.daily-driver.jobs" in labels

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
        ws = _FakeWorkspace.make(tmp_path, scheduler_cfg={"jobs": {}})
        jobs = scheduler.build_jobs(ws)
        assert all(j.label != "com.daily-driver.jobs" for j in jobs)

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

    def test_user_typed_scheduler_config_flows_through(self, tmp_path: Path) -> None:
        """A user-supplied typed SchedulerConfig drives the built job contexts.

        Closes a coverage gap: no prior test exercised user-config-driven
        scheduling end-to-end (config -> merge -> build_jobs contexts).
        """
        cfg = SchedulerConfig.model_validate(
            {"checkin": {"times": ["09:00"]}, "jobs": {"time": "06:30"}}
        )
        ws = _FakeWorkspace(
            root=tmp_path,
            state_dir=tmp_path / ".daily-driver",
            config=_FakeConfig(scheduler=cfg),
        )
        jobs = scheduler.build_jobs(ws)
        checkin = next(j for j in jobs if j.label == "com.daily-driver.checkin")
        scrape = next(j for j in jobs if j.label == "com.daily-driver.jobs")
        assert checkin.context["times"] == [{"hour": 9, "minute": 0}]
        assert scrape.context["times"] == [{"hour": 6, "minute": 30}]

    def test_legacy_scrape_jobs_key_rejected_at_parse(self) -> None:
        """Stale `scheduler.scrape_jobs:` now fails at config load, not at build.

        `SchedulerConfig(extra="forbid")` rejects the renamed key during
        `Config.model_validate`, before any scheduler code runs.
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="scrape_jobs"):
            SchedulerConfig.model_validate({"scrape_jobs": {"time": "07:00"}})

    def test_schedule_day_start_emits_day_start_job(self, tmp_path: Path) -> None:
        """F4: schedule.day_start present -> com.daily-driver.day-start plist."""
        ws = _FakeWorkspace.make(tmp_path, schedule=_FakeSchedule(day_start="07:30"))
        jobs = scheduler.build_jobs(ws)
        labels = [j.label for j in jobs]
        assert "com.daily-driver.day-start" in labels
        ds = next(j for j in jobs if j.label == "com.daily-driver.day-start")
        assert ds.context["times"] == [{"hour": 7, "minute": 30}]
        assert "day-start" in ds.context["program_arguments"]

    def test_schedule_day_end_emits_day_end_job(self, tmp_path: Path) -> None:
        """F4: schedule.day_end present -> com.daily-driver.day-end plist."""
        ws = _FakeWorkspace.make(tmp_path, schedule=_FakeSchedule(day_end="17:30"))
        jobs = scheduler.build_jobs(ws)
        labels = [j.label for j in jobs]
        assert "com.daily-driver.day-end" in labels
        de = next(j for j in jobs if j.label == "com.daily-driver.day-end")
        assert de.context["times"] == [{"hour": 17, "minute": 30}]
        assert "day-end" in de.context["program_arguments"]

    def test_schedule_omits_unset_entries(self, tmp_path: Path) -> None:
        """F4: schedule.day_start unset -> no day-start plist (no breaking change)."""
        ws = _FakeWorkspace.make(tmp_path)  # default schedule = both None
        jobs = scheduler.build_jobs(ws)
        labels = [j.label for j in jobs]
        assert "com.daily-driver.day-start" not in labels
        assert "com.daily-driver.day-end" not in labels

    def test_schedule_invalid_hhmm_raises(self, tmp_path: Path) -> None:
        ws = _FakeWorkspace.make(tmp_path, schedule=_FakeSchedule(day_start="9:99"))
        with pytest.raises(scheduler.SchedulerError):
            scheduler.build_jobs(ws)

    def test_checkin_days_weekdays_expands_times(self, tmp_path: Path) -> None:
        """checkin.days=weekdays -> one entry per weekday x time, Weekday 1-5."""
        ws = _FakeWorkspace.make(
            tmp_path,
            scheduler_cfg={
                "checkin": {"times": ["09:00", "14:00"], "days": "weekdays"}
            },
        )
        jobs = scheduler.build_jobs(ws)
        checkin = next(j for j in jobs if j.label == "com.daily-driver.checkin")
        entries = checkin.context["times"]
        assert len(entries) == 10  # 2 times x 5 weekdays
        assert {e["weekday"] for e in entries} == {1, 2, 3, 4, 5}
        assert all("hour" in e and "minute" in e for e in entries)

    def test_schedule_days_applies_to_day_start_and_day_end(
        self, tmp_path: Path
    ) -> None:
        ws = _FakeWorkspace.make(
            tmp_path,
            schedule=_FakeSchedule(day_start="09:00", day_end="18:00", days="weekdays"),
        )
        jobs = scheduler.build_jobs(ws)
        for label in ("com.daily-driver.day-start", "com.daily-driver.day-end"):
            job = next(j for j in jobs if j.label == label)
            entries = job.context["times"]
            assert len(entries) == 5
            assert {e["weekday"] for e in entries} == {1, 2, 3, 4, 5}

    def test_jobs_days_list_expands_to_named_weekdays(self, tmp_path: Path) -> None:
        """jobs.days=[sun, wed] -> two entries with launchd Weekday 0 and 3."""
        ws = _FakeWorkspace.make(
            tmp_path, scheduler_cfg={"jobs": {"time": "23:59", "days": ["sun", "wed"]}}
        )
        jobs = scheduler.build_jobs(ws)
        scrape = next(j for j in jobs if j.label == "com.daily-driver.jobs")
        assert scrape.context["times"] == [
            {"hour": 23, "minute": 59, "weekday": 0},
            {"hour": 23, "minute": 59, "weekday": 3},
        ]

    def test_default_days_daily_omits_weekday(self, tmp_path: Path) -> None:
        """No days config -> entries carry no weekday key (fires every day)."""
        ws = _FakeWorkspace.make(tmp_path)
        jobs = scheduler.build_jobs(ws)
        for job in jobs:
            for entry in job.context["times"]:
                assert "weekday" not in entry


class TestParseDays:
    def test_none_and_daily_mean_every_day(self) -> None:
        assert scheduler.parse_days(None) is None
        assert scheduler.parse_days("daily") is None

    def test_weekdays_shortcut(self) -> None:
        assert scheduler.parse_days("weekdays") == [1, 2, 3, 4, 5]

    def test_day_names_map_to_launchd_weekdays(self) -> None:
        assert scheduler.parse_days(["sun", "wed"]) == [0, 3]

    def test_full_names_and_case_accepted(self) -> None:
        assert scheduler.parse_days(["Sunday", "WEDNESDAY"]) == [0, 3]

    def test_duplicates_collapse(self) -> None:
        assert scheduler.parse_days(["mon", "monday", "Mon"]) == [1]

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(scheduler.SchedulerError, match="invalid days"):
            scheduler.parse_days("fortnightly")

    def test_invalid_day_name_raises(self) -> None:
        with pytest.raises(scheduler.SchedulerError, match="invalid day name"):
            scheduler.parse_days(["funday"])

    def test_calendar_entries_daily_passthrough(self) -> None:
        times = [{"hour": 9, "minute": 0}]
        assert scheduler.calendar_entries(times, None) is times

    def test_calendar_entries_expands_cross_product(self) -> None:
        times = [{"hour": 9, "minute": 0}, {"hour": 14, "minute": 0}]
        entries = scheduler.calendar_entries(times, [0, 3])
        assert entries == [
            {"hour": 9, "minute": 0, "weekday": 0},
            {"hour": 14, "minute": 0, "weekday": 0},
            {"hour": 9, "minute": 0, "weekday": 3},
            {"hour": 14, "minute": 0, "weekday": 3},
        ]


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

    def test_jobs_plist_contains_single_calendar_interval(self, tmp_path: Path) -> None:
        ws = _FakeWorkspace.make(tmp_path)
        jobs = scheduler.build_jobs(ws)
        scrape = next(j for j in jobs if j.label == "com.daily-driver.jobs")

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

    def test_daily_jobs_render_without_weekday_key(self, tmp_path: Path) -> None:
        ws = _FakeWorkspace.make(tmp_path)
        for job in scheduler.build_jobs(ws):
            assert "<key>Weekday</key>" not in scheduler.render_plist(job)

    def test_jobs_plist_renders_weekday_entries(self, tmp_path: Path) -> None:
        """days=[sun, wed] renders two calendar dicts with launchd Weekday keys."""
        ws = _FakeWorkspace.make(
            tmp_path, scheduler_cfg={"jobs": {"time": "23:59", "days": ["sun", "wed"]}}
        )
        jobs = scheduler.build_jobs(ws)
        scrape = next(j for j in jobs if j.label == "com.daily-driver.jobs")
        xml_str = scheduler.render_plist(scrape)
        assert xml_str.count("<key>Weekday</key>") == 2
        assert "<integer>0</integer>" in xml_str  # Sunday
        assert "<integer>3</integer>" in xml_str  # Wednesday
        ET.fromstring(xml_str)  # stays valid XML

    def test_checkin_plist_renders_weekdays(self, tmp_path: Path) -> None:
        ws = _FakeWorkspace.make(
            tmp_path,
            scheduler_cfg={"checkin": {"times": ["14:00"], "days": "weekdays"}},
        )
        jobs = scheduler.build_jobs(ws)
        checkin = next(j for j in jobs if j.label == "com.daily-driver.checkin")
        xml_str = scheduler.render_plist(checkin)
        assert xml_str.count("<key>Weekday</key>") == 5
        ET.fromstring(xml_str)


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
            "com.daily-driver.jobs",
        }
        assert set(calls["load"]) == set(installed)
        # State mirror written
        state_dir = ws.ephemeral_dir / "launchd"
        assert (state_dir / "com.daily-driver.checkin.plist").exists()
        assert (state_dir / "com.daily-driver.jobs.plist").exists()
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
            "com.daily-driver.jobs",
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
