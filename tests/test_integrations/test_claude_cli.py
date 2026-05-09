from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from daily_driver.integrations.claude_cli import ClaudeNotFoundError, available, invoke


def _make_popen_stub(stdout="output text", stderr="", rc=0):
    """Return a factory that produces a fake Popen whose communicate() returns canned output."""

    def _popen(args, **kw):
        proc = MagicMock()
        proc.communicate.return_value = (stdout, stderr)
        proc.returncode = rc
        return proc

    return _popen


def test_available_true_when_claude_on_path(monkeypatch):
    monkeypatch.setattr(
        "daily_driver.integrations.claude_cli.shutil.which",
        lambda _: "/usr/local/bin/claude",
    )

    assert available() is True


def test_available_false_when_missing(monkeypatch):
    monkeypatch.setattr(
        "daily_driver.integrations.claude_cli.shutil.which", lambda _: None
    )

    assert available() is False


def test_invoke_raises_when_claude_missing(monkeypatch):
    monkeypatch.setattr(
        "daily_driver.integrations.claude_cli.shutil.which", lambda _: None
    )

    with pytest.raises(ClaudeNotFoundError):
        invoke("hi")


def test_invoke_builds_basic_args(monkeypatch):
    monkeypatch.setattr(
        "daily_driver.integrations.claude_cli.shutil.which",
        lambda _: "/usr/local/bin/claude",
    )
    captured = {}

    def _popen(args, **kw):
        captured["args"] = args
        proc = MagicMock()
        proc.communicate.return_value = ("response", "")
        proc.returncode = 0
        return proc

    monkeypatch.setattr("daily_driver.integrations.claude_cli.subprocess.Popen", _popen)

    result = invoke("hello")

    assert captured["args"] == ["claude", "hello"]
    assert result == "response"


def test_invoke_builds_full_args(monkeypatch):
    monkeypatch.setattr(
        "daily_driver.integrations.claude_cli.shutil.which",
        lambda _: "/usr/local/bin/claude",
    )
    captured = {}

    def _popen(args, **kw):
        captured["args"] = args
        proc = MagicMock()
        proc.communicate.return_value = ("", "")
        proc.returncode = 0
        return proc

    monkeypatch.setattr("daily_driver.integrations.claude_cli.subprocess.Popen", _popen)

    invoke("prompt", agent="work-planner", session_name="my-sess", headless=True)

    assert captured["args"] == [
        "claude",
        "-p",
        "--agent",
        "work-planner",
        "-n",
        "my-sess",
        "prompt",
    ]


def test_invoke_prompt_precedes_add_dir(monkeypatch):
    """Regression: --add-dir is variadic; trailing prompt was absorbed.

    Symptom (review §8): summary --range week errored "Input must be provided
    either through stdin or as a prompt argument when using --print" because
    the prompt landed AFTER --add-dir and got eaten as another directory.
    """
    monkeypatch.setattr(
        "daily_driver.integrations.claude_cli.shutil.which",
        lambda _: "/usr/local/bin/claude",
    )
    captured = {}

    def _popen(args, **kw):
        captured["args"] = args
        proc = MagicMock()
        proc.communicate.return_value = ("", "")
        proc.returncode = 0
        return proc

    monkeypatch.setattr("daily_driver.integrations.claude_cli.subprocess.Popen", _popen)

    from pathlib import Path

    invoke("PROMPT", headless=True, add_dirs=[Path("/tmp/ws")])

    args = captured["args"]
    assert "PROMPT" in args
    assert "--add-dir" in args
    # Prompt index must come BEFORE --add-dir so claude doesn't absorb it.
    assert args.index("PROMPT") < args.index("--add-dir")


def test_invoke_passes_input_text(monkeypatch):
    monkeypatch.setattr(
        "daily_driver.integrations.claude_cli.shutil.which",
        lambda _: "/usr/local/bin/claude",
    )
    captured = {}

    def _popen(args, **kw):
        proc = MagicMock()

        def _communicate(input=None, timeout=None):
            captured["input"] = input
            return ("", "")

        proc.communicate.side_effect = _communicate
        proc.returncode = 0
        return proc

    monkeypatch.setattr("daily_driver.integrations.claude_cli.subprocess.Popen", _popen)

    invoke("p", input_text="stdin data")

    assert captured["input"] == "stdin data"


def test_invoke_propagates_called_process_error(monkeypatch):
    monkeypatch.setattr(
        "daily_driver.integrations.claude_cli.shutil.which",
        lambda _: "/usr/local/bin/claude",
    )

    def _popen(args, **kw):
        proc = MagicMock()
        proc.communicate.return_value = ("", "error")
        proc.returncode = 1
        return proc

    monkeypatch.setattr("daily_driver.integrations.claude_cli.subprocess.Popen", _popen)

    with pytest.raises(subprocess.CalledProcessError):
        invoke("prompt")


def test_invoke_propagates_timeout(monkeypatch):
    monkeypatch.setattr(
        "daily_driver.integrations.claude_cli.shutil.which",
        lambda _: "/usr/local/bin/claude",
    )

    def _popen(args, **kw):
        proc = MagicMock()
        proc.communicate.side_effect = subprocess.TimeoutExpired(cmd=args, timeout=5)
        return proc

    monkeypatch.setattr("daily_driver.integrations.claude_cli.subprocess.Popen", _popen)

    with pytest.raises(subprocess.TimeoutExpired):
        invoke("prompt", timeout=5)


def test_invoke_kills_process_on_timeout(monkeypatch):
    """Regression: TimeoutExpired must call proc.kill() before re-raising."""
    monkeypatch.setattr(
        "daily_driver.integrations.claude_cli.shutil.which",
        lambda _: "/usr/local/bin/claude",
    )

    kill_called = []
    wait_called = []

    def _popen(args, **kw):
        proc = MagicMock()
        proc.communicate.side_effect = subprocess.TimeoutExpired(cmd=args, timeout=5)

        def _kill():
            kill_called.append(True)

        def _wait():
            wait_called.append(True)

        proc.kill.side_effect = _kill
        proc.wait.side_effect = _wait
        return proc

    monkeypatch.setattr("daily_driver.integrations.claude_cli.subprocess.Popen", _popen)

    with pytest.raises(subprocess.TimeoutExpired):
        invoke("prompt", timeout=5)

    assert kill_called, "proc.kill() must be called before re-raising TimeoutExpired"
    assert wait_called, "proc.wait() must be called to reap the process"


def test_invoke_converts_file_not_found_to_claude_not_found(monkeypatch):
    # which returns a path (passes the guard), but Popen raises FileNotFoundError (race condition)
    monkeypatch.setattr(
        "daily_driver.integrations.claude_cli.shutil.which",
        lambda _: "/usr/local/bin/claude",
    )

    def _popen(args, **kw):
        raise FileNotFoundError("No such file: claude")

    monkeypatch.setattr("daily_driver.integrations.claude_cli.subprocess.Popen", _popen)

    with pytest.raises(ClaudeNotFoundError):
        invoke("prompt")
