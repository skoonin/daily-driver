from __future__ import annotations

import subprocess

import pytest

from daily_driver.integrations.clipboard import available, copy, paste


def test_available_true_when_both_present(monkeypatch):
    monkeypatch.setattr(
        "daily_driver.integrations.clipboard.shutil.which",
        lambda cmd: f"/usr/bin/{cmd}",
    )

    assert available() is True


def test_available_false_when_pbcopy_missing(monkeypatch):
    def _which(cmd):
        return None if cmd == "pbcopy" else f"/usr/bin/{cmd}"

    monkeypatch.setattr("daily_driver.integrations.clipboard.shutil.which", _which)

    assert available() is False


def test_copy_pipes_text_to_pbcopy(monkeypatch):
    captured = {}

    def _run(args, **kw):
        captured["args"] = args
        captured["kw"] = kw
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr("daily_driver.integrations.clipboard.subprocess.run", _run)

    copy("hello")

    assert captured["args"] == ["pbcopy"]
    assert captured["kw"]["input"] == "hello"
    assert captured["kw"]["check"] is True


def test_paste_returns_pbpaste_stdout(monkeypatch):
    def _run(args, **kw):
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="clip content\n", stderr=""
        )

    monkeypatch.setattr("daily_driver.integrations.clipboard.subprocess.run", _run)

    result = paste()

    assert result == "clip content\n"


def test_copy_raises_called_process_error_on_nonzero(monkeypatch):
    def _run(args, **kw):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr("daily_driver.integrations.clipboard.subprocess.run", _run)

    with pytest.raises(subprocess.CalledProcessError):
        copy("text that triggers error")
