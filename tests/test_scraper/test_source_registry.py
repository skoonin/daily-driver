"""Tests for the explicit SOURCE_REGISTRY (K9, Q16)."""

from __future__ import annotations

from typing import Any

from daily_driver.scraper.models import RawScrapedJob, Source
from daily_driver.scraper.sources import SCRAPERS, SOURCE_REGISTRY


def test_registry_keys_match_legacy_scrapers() -> None:
    """SOURCE_REGISTRY must list the same source ids as legacy SCRAPERS."""
    assert set(SOURCE_REGISTRY.keys()) == set(SCRAPERS.keys())


def test_registry_entries_satisfy_source_protocol() -> None:
    """Every wrapped scraper must be a Source-protocol callable (runtime check)."""
    for sid, fn in SOURCE_REGISTRY.items():
        assert isinstance(fn, Source), sid


def test_typed_wrapper_validates_rows(monkeypatch: Any) -> None:
    """A wrapped scraper returns list[RawScrapedJob] when the underlying scraper
    yields well-formed dict rows."""
    from daily_driver.scraper import sources

    fake_rows = [
        {
            "company": "Acme",
            "role": "SRE",
            "url": "https://example.com/a",
            "source": "remoteok",
            "location": "Remote",
            "comp": "$150K-$200K",
            "date_found": "2026-05-08",
        },
        {
            "company": "Beta",
            "role": "Platform Eng",
            "url": "https://example.com/b",
            "source": "remoteok",
        },
    ]
    monkeypatch.setattr(sources, "scrape_remoteok", lambda _cfg: fake_rows)

    # Re-build registry with the patched scraper.
    fn = sources._typed_source(sources.scrape_remoteok)
    out = fn({})
    assert len(out) == 2
    assert all(isinstance(r, RawScrapedJob) for r in out)
    assert out[0].company == "Acme"
    assert out[1].role == "Platform Eng"


def test_typed_wrapper_drops_unparseable_rows() -> None:
    """Malformed rows are skipped, not raised."""
    from daily_driver.scraper import sources

    def fake_scraper(_cfg: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"company": "OK", "role": "SRE", "url": "u", "source": "s"},
            {"company": "BAD", "role": "", "url": "u2", "source": ""},  # role empty
        ]

    fn = sources._typed_source(fake_scraper)
    out = fn({})
    # First row passes; second is rejected silently.
    assert len(out) == 1
    assert out[0].company == "OK"


def test_registry_is_explicit_not_dynamic() -> None:
    """Q16: explicit dict, no pkgutil/iter_modules introspection."""
    expected_sources = {
        "remoteok",
        "weworkremotely",
        "hn_who_is_hiring",
        "hn_jobs",
        "greenhouse",
        "jobspy_linkedin",
        "jobspy_indeed",
        "jobspy_google",
        "apple",
    }
    assert set(SOURCE_REGISTRY.keys()) == expected_sources
