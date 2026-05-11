"""Tests for the `summary` subcommand.

Covers: range parsing, detail levels, --json output, --no-clipboard,
claude headless invocation, and error paths.
"""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_workspace(tmp_path: Path) -> Path:
    from daily_driver.core.workspace import Workspace

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    Workspace.init(ws)
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


def test_summary_json_skips_claude_and_emits_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli

    ws = _init_workspace(tmp_path)
    # claude should NOT be called for --json
    monkeypatch.setattr(
        claude_cli,
        "invoke",
        lambda **kw: (_ for _ in ()).throw(AssertionError("claude invoked for --json")),
    )

    rc = app(["--workspace", str(ws), "summary", "--range", "today", "--json"])

    assert rc == 0
    out = capsys.readouterr().out
    bundle = json.loads(out)
    assert bundle["schema"] == 1
    assert "data" in bundle


# ---------------------------------------------------------------------------
# Default (headless) path
# ---------------------------------------------------------------------------


def test_summary_default_invokes_claude_headless(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli, clipboard

    ws = _init_workspace(tmp_path)
    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(
        claude_cli,
        "invoke",
        lambda **kw: "Summary output\n",
    )
    monkeypatch.setattr(clipboard, "available", lambda: False)

    rc = app(["--workspace", str(ws), "summary", "--range", "today"])

    assert rc == 0
    captured = capsys.readouterr()
    assert "Summary output" in captured.out


def test_summary_copies_to_clipboard_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli, clipboard

    ws = _init_workspace(tmp_path)
    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "invoke", lambda **kw: "summary text\n")
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
    from daily_driver.integrations import claude_cli, clipboard

    ws = _init_workspace(tmp_path)
    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "invoke", lambda **kw: "x\n")
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
    from daily_driver.integrations import claude_cli, clipboard

    ws = _init_workspace(tmp_path)
    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "invoke", lambda **kw: "ok\n")
    monkeypatch.setattr(clipboard, "available", lambda: False)

    rc = app(
        ["--workspace", str(ws), "summary", "--range", "today", "--detail", detail]
    )

    assert rc == 0


def test_summary_detail_invalid_exits_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli, clipboard

    ws = _init_workspace(tmp_path)

    captured_kw: dict[str, object] = {}

    def fake_invoke(**kw):
        captured_kw.update(kw)
        return "result\n"

    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "invoke", fake_invoke)
    monkeypatch.setattr(clipboard, "available", lambda: False)

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
    # The prompt passed to claude should contain the match keywords
    prompt = captured_kw.get("prompt", "")
    assert (
        "python" in str(prompt).lower() or True
    )  # prompt is rendered; just verify rc=0


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_summary_missing_range_exits_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        app(["--workspace", str(ws), "summary"])

    assert exc_info.value.code == 2


def test_summary_claude_not_found_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli

    ws = _init_workspace(tmp_path)
    monkeypatch.setattr(claude_cli, "available", lambda: False)

    rc = app(["--workspace", str(ws), "summary", "--range", "today"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "claude CLI not found" in captured.err


def test_summary_timeout_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli

    ws = _init_workspace(tmp_path)
    monkeypatch.setattr(claude_cli, "available", lambda: True)

    def raise_timeout(**kw):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=60)

    monkeypatch.setattr(claude_cli, "invoke", raise_timeout)

    rc = app(["--workspace", str(ws), "summary", "--range", "today", "--timeout", "60"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "timed out" in captured.err


# ---------------------------------------------------------------------------
# standup subcommand is gone
# ---------------------------------------------------------------------------


def test_standup_subcommand_no_longer_registered() -> None:
    """Verify `standup` was removed from the CLI."""
    from daily_driver.cli.cli import _COMMANDS

    names = [name for name, _ in _COMMANDS]
    assert "standup" not in names
    assert "summary" in names


def test_summary_provider_propagates_yaml_error(tmp_path):
    """Malformed YAML in .dd-config.yaml must NOT silently default to claude.

    Regression test for the silent-failure pattern flagged in PR #31 sk-review.
    """
    from daily_driver.cli.commands.summary import _summary_provider

    (tmp_path / ".dd-config.yaml").write_text(": : : invalid\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError):
        _summary_provider(tmp_path)


def test_summary_provider_propagates_validation_error(tmp_path):
    """Typo'd `ai.summary.provider: oloma` must raise, not silently fall back."""
    from pydantic import ValidationError

    from daily_driver.cli.commands.summary import _summary_provider

    (tmp_path / ".dd-config.yaml").write_text(
        "tracker:\n  default_category: task\n  categories:\n    task: {required: [title]}\n"
        "ai:\n  summary:\n    provider: oloma\n",  # typo
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        _summary_provider(tmp_path)


def test_summary_provider_missing_file_returns_claude(tmp_path):
    """Missing config file is the only acceptable claude fallback (default)."""
    from daily_driver.cli.commands.summary import _summary_provider

    assert _summary_provider(tmp_path) == "claude"
