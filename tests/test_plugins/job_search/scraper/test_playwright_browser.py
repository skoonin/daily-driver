"""Behavior tests for the Playwright launch seam (`_playwright_browser`).

Two scraper config knobs drive this seam and are exercised here:

- ``scraper.browser`` selects which Playwright engine is launched
  (``getattr(pw, engine).launch(...)``).
- ``scraper.user_agent`` is the UA string handed to ``new_context``.

Both are mocked at the Playwright boundary so no real browser is launched.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from daily_driver.plugins.job_search.scraper.sources import _http


def _ctx(**scraper: object) -> ScrapeContext:
    return ScrapeContext(plugin=JobSearchPlugin.model_validate({"scraper": scraper}))


def _install_fake_playwright(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Mock playwright.sync_api so launch/new_context calls are recorded.

    Returns a dict the test inspects: which engine attribute was accessed and
    what kwargs reached launch / new_context.
    """
    import playwright.sync_api as pw_api

    recorded: dict[str, Any] = {"engine_attr": None, "launch_kwargs": None}

    browser = MagicMock()
    browser_ctx = MagicMock()
    browser.new_context.side_effect = lambda **kw: (
        recorded.__setitem__("context_kwargs", kw),
        browser_ctx,
    )[1]
    browser_ctx.new_page.return_value = MagicMock()

    class _Engine:
        def launch(self, **kwargs: object) -> Any:
            recorded["launch_kwargs"] = kwargs
            return browser

    class _PW:
        def __getattr__(self, name: str) -> Any:
            recorded["engine_attr"] = name
            return _Engine()

    @contextmanager
    def fake_sync_playwright() -> Iterator[_PW]:
        yield _PW()

    monkeypatch.setattr(pw_api, "sync_playwright", fake_sync_playwright)
    monkeypatch.setattr(_http, "_has_playwright", lambda: True)
    return recorded


def test_browser_knob_selects_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-default `browser: chromium` launches the chromium engine."""
    recorded = _install_fake_playwright(monkeypatch)
    with _http._playwright_browser(_ctx(browser="chromium")):
        pass
    assert recorded["engine_attr"] == "chromium"


def test_browser_default_is_firefox(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = _install_fake_playwright(monkeypatch)
    with _http._playwright_browser(_ctx()):
        pass
    assert recorded["engine_attr"] == "firefox"


def test_user_agent_knob_reaches_browser_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scraper.user_agent is the UA string passed to new_context."""
    recorded = _install_fake_playwright(monkeypatch)
    ua = "DailyDriver-Test-UA/9.9"
    with _http._playwright_browser(_ctx(user_agent=ua)):
        pass
    assert recorded["context_kwargs"]["user_agent"] == ua


def test_headless_default_passed_to_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The headless flag (default false) reaches the launch call."""
    recorded = _install_fake_playwright(monkeypatch)
    with _http._playwright_browser(_ctx()):
        pass
    assert recorded["launch_kwargs"]["headless"] is False
