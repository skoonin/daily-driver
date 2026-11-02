"""Tests for the `summary` subcommand.

Covers: range parsing, detail levels, --json output, --no-clipboard,
claude headless invocation, and error paths.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_workspace(tmp_path: Path) -> Path:
    """A workspace with today's notes, so `--range today` has a file to read."""
    from daily_driver.core.clock import today
    from daily_driver.core.workspace import Workspace, daily_file

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    workspace = Workspace.init(ws)
    notes = daily_file(workspace.output_dir, today(), "notes")
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text("- did things\n", encoding="utf-8")
    return ws


# ---------------------------------------------------------------------------
# Range parser unit tests
# ---------------------------------------------------------------------------


class TestParseRange:
    def setup_method(self):
        from daily_driver.core.summary import parse_range

        self.parse_range = parse_range

    def test_today(self):
        start, end = self.parse_range("today")
        assert start == end == date.today()

    def test_yesterday(self):
        from datetime import timedelta

        start, end = self.parse_range("yesterday")
        expected = date.today() - timedelta(days=1)
        assert start == end == expected

    def test_week(self):
        start, end = self.parse_range("week")
        assert start <= end
        # span should be at most 6 days
        assert (end - start).days <= 6

    def test_month(self):
        start, end = self.parse_range("month")
        assert start.day == 1
        assert start.month == date.today().month

    def test_explicit_iso_date_single(self):
        start, end = self.parse_range("2025-03-10")
        assert start == end == date(2025, 3, 10)

    def test_explicit_iso_range(self):
        start, end = self.parse_range("2025-03-10:2025-03-17")
        assert start == date(2025, 3, 10)
        assert end == date(2025, 3, 17)

    def test_invalid_range_raises(self):
        with pytest.raises(ValueError, match="range"):
            self.parse_range("not-a-range")

    def test_inverted_range_raises(self):
        with pytest.raises(ValueError, match="start.*end"):
            self.parse_range("2025-03-17:2025-03-10")


# ---------------------------------------------------------------------------
# --json path
# ---------------------------------------------------------------------------


def _stub_provider(monkeypatch: pytest.MonkeyPatch, answer: str = "Summary output\n"):
    """Replace the provider call; returns the list of recorded calls."""
    from daily_driver.integrations import clipboard, provider

    calls: list[dict[str, object]] = []

    def fake_invoke(chosen, task, prompt, **kwargs):
        calls.append({"provider": chosen, "task": task, "prompt": prompt, **kwargs})
        return answer

    monkeypatch.setattr(provider, "invoke", fake_invoke)
    monkeypatch.setattr(clipboard, "available", lambda: False)
    return calls


def _add_config(ws: Path, text: str) -> None:
    cfg_path = ws / ".dd-config.yaml"
    cfg_path.write_text(cfg_path.read_text() + text, encoding="utf-8")


def test_summary_json_skips_the_provider_and_emits_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    calls = _stub_provider(monkeypatch)

    rc = app(["--workspace", str(ws), "summary", "--range", "today", "--json"])

    assert rc == 0
    assert calls == []
    bundle = json.loads(capsys.readouterr().out)
    assert bundle["schema"] == 1
    assert "data" in bundle


# ---------------------------------------------------------------------------
# Default (headless) path
# ---------------------------------------------------------------------------


def test_summary_runs_as_an_agent_task_in_the_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.tasks import SUMMARY

    ws = _init_workspace(tmp_path)
    calls = _stub_provider(monkeypatch)

    rc = app(["--workspace", str(ws), "summary", "--range", "today"])

    assert rc == 0
    assert "Summary output" in capsys.readouterr().out
    (call,) = calls
    assert call["provider"].name == "claude"  # type: ignore[attr-defined]
    assert call["task"] == SUMMARY
    assert call["workspace"] == ws
    assert call["timeout"] is None
    assert call["model"] is None


def test_summary_prompt_lists_the_files_in_range(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    month = ws / "2026" / "05"
    month.mkdir(parents=True)
    notes = month / "2026-05-04-notes.md"
    notes.write_text("- shipped the thing\n", encoding="utf-8")
    outside = month / "2026-05-20-plan.md"
    outside.write_text("later\n", encoding="utf-8")
    calls = _stub_provider(monkeypatch)

    rc = app(["--workspace", str(ws), "summary", "--range", "2026-05-01:2026-05-07"])

    assert rc == 0
    prompt = str(calls[0]["prompt"])
    assert str(notes) in prompt
    assert str(outside) not in prompt


def test_summary_routes_to_the_provider_claiming_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _add_config(ws, "\nai:\n  providers:\n    opencode:\n      tasks: [summary]\n")
    calls = _stub_provider(monkeypatch)

    rc = app(["--workspace", str(ws), "summary", "--range", "today", "--model", "x/y"])

    assert rc == 0
    assert calls[0]["provider"].name == "opencode"  # type: ignore[attr-defined]
    assert calls[0]["model"] == "x/y"


def test_summary_on_an_http_provider_is_a_config_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _add_config(
        ws,
        "\nai:\n  providers:\n    lmstudio:\n      model: m\n"
        "      tasks: [summary]\n",
    )
    calls = _stub_provider(monkeypatch)

    rc = app(["--workspace", str(ws), "summary", "--range", "today"])

    # A config error is a usage error (exit 2), as for every other bad config.
    assert rc == 2
    assert calls == []
    assert "text tasks only" in capsys.readouterr().err


def test_summary_shows_start_and_finish_lines_when_piped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _stub_provider(monkeypatch)

    rc = app(["--workspace", str(ws), "summary", "--range", "today"])

    captured = capsys.readouterr()
    assert rc == 0
    assert "summary via claude" in captured.err
    assert "summary via claude" not in captured.out


def test_summary_copies_to_clipboard_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import clipboard

    ws = _init_workspace(tmp_path)
    _stub_provider(monkeypatch, "summary text\n")
    monkeypatch.setattr(clipboard, "available", lambda: True)
    copied: list[str] = []
    monkeypatch.setattr(clipboard, "copy", lambda t: copied.append(t))

    rc = app(["--workspace", str(ws), "summary", "--range", "today"])

    assert rc == 0
    assert copied == ["summary text"]


def test_summary_no_clipboard_suppresses_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import clipboard

    ws = _init_workspace(tmp_path)
    _stub_provider(monkeypatch, "x\n")
    monkeypatch.setattr(clipboard, "available", lambda: True)
    calls: list[str] = []
    monkeypatch.setattr(clipboard, "copy", lambda t: calls.append(t))

    rc = app(["--workspace", str(ws), "summary", "--range", "today", "--no-clipboard"])

    assert rc == 0
    assert calls == []


# ---------------------------------------------------------------------------
# --detail flag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("detail", ["low", "med", "high"])
def test_summary_detail_levels_accepted(
    detail: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    calls = _stub_provider(monkeypatch, "ok\n")

    rc = app(
        ["--workspace", str(ws), "summary", "--range", "today", "--detail", detail]
    )

    assert rc == 0
    assert f"{detail}-detail" in str(calls[0]["prompt"])


def test_summary_detail_invalid_exits_2(tmp_path: Path) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        app(
            ["--workspace", str(ws), "summary", "--range", "today", "--detail", "ultra"]
        )

    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# --match keyword filter
# ---------------------------------------------------------------------------


def test_summary_match_filters_included_in_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    calls = _stub_provider(monkeypatch, "result\n")

    rc = app(
        [
            "--workspace",
            str(ws),
            "summary",
            "--range",
            "today",
            "--match",
            "python",
            "--match",
            "sre",
        ]
    )

    assert rc == 0
    assert "python, sre" in str(calls[0]["prompt"])


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_summary_missing_range_exits_2(tmp_path: Path) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        app(["--workspace", str(ws), "summary"])

    assert exc_info.value.code == 2


def test_summary_program_not_found_exits_1(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    _add_config(
        ws,
        "\nai:\n  providers:\n    mine:\n      agent: [dd-no-such-program]\n"
        "      tasks: [summary]\n",
    )

    rc = app(["--workspace", str(ws), "summary", "--range", "today"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "dd-no-such-program not found on PATH" in err
    assert "give summary to another provider under ai.providers" in err


@pytest.mark.parametrize("value", ["0", "-5"])
def test_summary_timeout_below_one_exits_2(
    tmp_path: Path, value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        app(["--workspace", str(ws), "summary", "--range", "today", "--timeout", value])

    assert exc_info.value.code == 2
    assert "--timeout must be >= 1" in capsys.readouterr().err


def test_summary_timeout_names_both_ways_to_raise_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import provider

    ws = _init_workspace(tmp_path)

    def raise_timeout(*args, **kwargs):
        raise provider.AITimeoutError(
            "claude timed out after 900s", provider="claude", timeout_seconds=900
        )

    monkeypatch.setattr(provider, "invoke", raise_timeout)

    rc = app(["--workspace", str(ws), "summary", "--range", "today"])

    assert rc == 1
    assert (
        "summary via claude timed out after 900s; raise with --timeout or "
        "ai.providers.claude.timeout"
    ) in capsys.readouterr().err


def test_summary_has_no_session_or_agent_flags(tmp_path: Path) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    for flag in ("--session-name", "--agent"):
        with pytest.raises(SystemExit) as exc_info:
            app(["--workspace", str(ws), "summary", "--range", "today", flag, "x"])
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# standup subcommand is gone
# ---------------------------------------------------------------------------


def test_standup_subcommand_no_longer_registered() -> None:
    """Verify `standup` was removed from the CLI."""
    from daily_driver.cli.cli import _COMMANDS

    names = [name for name, _ in _COMMANDS]
    assert "standup" not in names
    assert "summary" in names


def test_summary_with_no_files_makes_no_ai_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    (ws / "tracker.yaml").unlink(missing_ok=True)
    calls = _stub_provider(monkeypatch)

    rc = app(["--workspace", str(ws), "summary", "--range", "2020-01-01"])

    assert rc == 0
    assert calls == []
    assert "no daily notes, plans or tracker" in capsys.readouterr().err
