"""Shared fixtures for scraper tests."""

from __future__ import annotations

import shutil

import pytest

_real_which = shutil.which


@pytest.fixture(autouse=True)
def _claude_binary_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the claude binary is installed.

    The enrichment entry points guard on ``shutil.which("claude")`` before any
    LLM work (llm.py). These tests stub ``invoke_for`` and never spawn the real
    binary, but on CI runners (no claude installed) the guard short-circuits
    and every claude-path test silently does nothing. Tests that need the
    missing-binary branch monkeypatch ``shutil.which`` themselves, overriding
    this fixture.
    """

    def _which(cmd: str, *args: object, **kwargs: object) -> str | None:
        if cmd == "claude":
            return "/usr/local/bin/claude"
        return _real_which(cmd, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(shutil, "which", _which)
