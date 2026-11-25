"""Tests for ``daily_driver.integrations.opencode``."""

from __future__ import annotations

import logging
import subprocess

import pytest

from daily_driver.integrations import opencode

# Imported before the autouse conftest guard replaces the module attribute, so
# these tests exercise the real function against a stubbed subprocess.run.
from daily_driver.integrations.opencode import list_sessions


def _stub_run(monkeypatch, *, stdout="", stderr="", rc=0, calls=None):
    def _run(args, **kwargs):
        if calls is not None:
            calls.append(args)
        return subprocess.CompletedProcess(
            args=args, returncode=rc, stdout=stdout, stderr=stderr
        )

    monkeypatch.setattr(opencode.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(opencode.subprocess, "run", _run)


def test_list_sessions_returns_stdout(monkeypatch):
    calls: list[list[str]] = []
    _stub_run(monkeypatch, stdout='{"data": []}', calls=calls)

    assert list_sessions("parentID=null") == '{"data": []}'
    assert calls == [["opencode", "api", "GET", "/api/session?parentID=null"]]


def test_list_sessions_none_when_opencode_missing(monkeypatch, caplog):
    monkeypatch.setattr(opencode.shutil, "which", lambda name: None)

    def _fail(*args, **kwargs):
        raise AssertionError("must not run a missing program")

    monkeypatch.setattr(opencode.subprocess, "run", _fail)

    with caplog.at_level(logging.DEBUG):
        assert list_sessions("parentID=null") is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_list_sessions_none_and_warns_on_failure(monkeypatch, caplog):
    _stub_run(monkeypatch, rc=1, stderr="Could not reach server\nmore detail")

    assert list_sessions("parentID=null") is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings
    assert "Could not reach server" in warnings[0].getMessage()


def test_list_sessions_none_and_warns_on_timeout(monkeypatch, caplog):
    monkeypatch.setattr(opencode.shutil, "which", lambda name: f"/bin/{name}")

    def _timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs.get("timeout", 0))

    monkeypatch.setattr(opencode.subprocess, "run", _timeout)

    assert list_sessions("parentID=null") is None
    assert [r for r in caplog.records if r.levelno == logging.WARNING]


def test_list_sessions_none_when_program_cannot_start(monkeypatch, caplog):
    monkeypatch.setattr(opencode.shutil, "which", lambda name: f"/bin/{name}")

    def _oserror(args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(opencode.subprocess, "run", _oserror)

    assert list_sessions("parentID=null") is None
    assert [r for r in caplog.records if r.levelno == logging.WARNING]


def test_conftest_guard_refuses_unstubbed_call():
    with pytest.raises(AssertionError, match="list_sessions"):
        opencode.list_sessions("parentID=null")
