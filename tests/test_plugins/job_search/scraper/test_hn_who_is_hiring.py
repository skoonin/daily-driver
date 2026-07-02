"""Tests for HN 'Who is hiring?' scraper.

Validates the Algolia-based thread discovery (replaces previous HN HTML
scrape that hit 429 rate-limits on launchd-driven runs).
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.runner import ScrapeContext
from daily_driver.plugins.job_search.scraper.sources import (
    hn_who_is_hiring as hn_module,
)


def _stories_response(hits: list[dict[str, Any]]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"hits": hits}
    return resp


def _comments_response(hits: list[dict[str, Any]]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"hits": hits, "nbHits": len(hits)}
    return resp


def _config(roles: list[str] | None = None) -> ScrapeContext:
    return ScrapeContext(
        plugin=JobSearchPlugin.model_validate(
            {
                "roles": roles or ["SRE"],
                "scraper": {
                    "enabled": True,
                    "timeout": 1,
                    "max_retries": 0,
                },
                "sources": {"hn_who_is_hiring": {"hn_max_posts": 100}},
            }
        )
    )


def test_thread_id_resolved_from_algolia_stories(monkeypatch: Any) -> None:
    """Thread ID must come from Algolia's author_whoishiring story search,
    not from HN's HTML index page (which 429s)."""
    captured_urls: list[str] = []

    def fake_api_get(session: Any, url: str, config: Any, **kwargs: Any) -> MagicMock:
        captured_urls.append(url)
        if "author_whoishiring" in url:
            return _stories_response(
                [
                    {
                        "title": "Ask HN: Who wants to be hired? (May 2026)",
                        "objectID": "111",
                    },
                    {
                        "title": "Ask HN: Who is hiring? (May 2026)",
                        "objectID": "222",
                    },
                    {
                        "title": "Ask HN: Who is hiring? (April 2026)",
                        "objectID": "333",
                    },
                ]
            )
        if "story_222" in url:
            return _comments_response(
                [
                    {
                        "objectID": "c1",
                        "comment_text": "Acme | SRE | Remote\nDetails here",
                    }
                ]
            )
        return None

    monkeypatch.setattr(hn_module, "_api_get", fake_api_get)
    monkeypatch.setattr(hn_module, "_http_session", lambda cfg: MagicMock())
    monkeypatch.setattr(hn_module, "today", lambda: date(2026, 5, 9))

    jobs = hn_module.scrape_hn_who_is_hiring(_config())

    assert any(
        "author_whoishiring" in u for u in captured_urls
    ), "must query Algolia for whoishiring stories"
    assert not any(
        "submitted?id=whoishiring" in u for u in captured_urls
    ), "must NOT hit HN's rate-limited HTML index"
    assert any(
        "story_222" in u for u in captured_urls
    ), "must use thread ID 222 (May 2026 'Who is hiring?' match)"
    assert len(jobs) == 1
    assert jobs[0]["company"] == "Acme"


def test_falls_back_to_most_recent_when_no_exact_match(monkeypatch: Any) -> None:
    """When no story matches the current month exactly, fall back to the
    most-recent 'who is hiring?' story (Algolia returns hits date-descending)."""
    captured_urls: list[str] = []

    def fake_api_get(session: Any, url: str, config: Any, **kwargs: Any) -> MagicMock:
        captured_urls.append(url)
        if "author_whoishiring" in url:
            return _stories_response(
                [
                    {
                        "title": "Ask HN: Who wants to be hired? (April 2026)",
                        "objectID": "111",
                    },
                    {
                        "title": "Ask HN: Who is hiring? (April 2026)",
                        "objectID": "444",
                    },
                    {
                        "title": "Ask HN: Who is hiring? (March 2026)",
                        "objectID": "555",
                    },
                ]
            )
        if "story_444" in url:
            return _comments_response(
                [
                    {
                        "objectID": "c1",
                        "comment_text": "Acme | SRE | Remote\nDetails here",
                    }
                ]
            )
        return None

    monkeypatch.setattr(hn_module, "_api_get", fake_api_get)
    monkeypatch.setattr(hn_module, "_http_session", lambda cfg: MagicMock())
    monkeypatch.setattr(hn_module, "today", lambda: date(2026, 5, 9))

    jobs = hn_module.scrape_hn_who_is_hiring(_config())

    assert any(
        "story_444" in u for u in captured_urls
    ), "must fall back to the most-recent 'who is hiring?' story (April, id 444)"
    assert len(jobs) == 1
    assert jobs[0]["company"] == "Acme"


def test_full_comment_body_captured_as_description(monkeypatch: Any) -> None:
    """Only the headline is parsed for company/role/location; the rest of the
    comment body is carried through as ``description_text`` (HTML stripped,
    entities decoded), not discarded."""

    def fake_api_get(session: Any, url: str, config: Any, **kwargs: Any) -> MagicMock:
        if "author_whoishiring" in url:
            return _stories_response(
                [{"title": "Ask HN: Who is hiring? (May 2026)", "objectID": "222"}]
            )
        if "story_222" in url:
            return _comments_response(
                [
                    {
                        "objectID": "c1",
                        "comment_text": (
                            "Acme | SRE | Remote<p>We run Python &amp; "
                            "Kubernetes.</p>"
                        ),
                    }
                ]
            )
        return None

    monkeypatch.setattr(hn_module, "_api_get", fake_api_get)
    monkeypatch.setattr(hn_module, "_http_session", lambda cfg: MagicMock())
    monkeypatch.setattr(hn_module, "today", lambda: date(2026, 5, 9))

    jobs = hn_module.scrape_hn_who_is_hiring(_config())

    assert len(jobs) == 1
    description = jobs[0]["description_text"]
    assert "Acme | SRE | Remote" in description
    assert "We run Python & Kubernetes." in description
    assert "<p>" not in description


def test_returns_empty_when_no_whoishiring_story(monkeypatch: Any) -> None:
    """If Algolia returns no 'who is hiring?' stories at all, return []."""

    def fake_api_get(session: Any, url: str, config: Any, **kwargs: Any) -> MagicMock:
        if "author_whoishiring" in url:
            return _stories_response(
                [
                    {
                        "title": "Ask HN: Who wants to be hired? (April 2026)",
                        "objectID": "111",
                    }
                ]
            )
        return None

    monkeypatch.setattr(hn_module, "_api_get", fake_api_get)
    monkeypatch.setattr(hn_module, "_http_session", lambda cfg: MagicMock())
    monkeypatch.setattr(hn_module, "today", lambda: date(2026, 5, 9))

    jobs = hn_module.scrape_hn_who_is_hiring(_config())
    assert jobs == []


def test_returns_empty_when_stories_fetch_fails(monkeypatch: Any) -> None:
    """If the Algolia stories fetch returns None (terminal error), return []."""
    monkeypatch.setattr(hn_module, "_api_get", lambda *a, **k: None)
    monkeypatch.setattr(hn_module, "_http_session", lambda cfg: MagicMock())
    monkeypatch.setattr(hn_module, "today", lambda: date(2026, 5, 9))

    jobs = hn_module.scrape_hn_who_is_hiring(_config())
    assert jobs == []
