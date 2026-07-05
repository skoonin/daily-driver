"""Source capability metadata: enumeration model + verify channel per source.

Plan phase 1: every SCRAPERS entry declares how it enumerates (full board
listing vs windowed slice) and how a known row's liveness can be verified
(board-diff / url-check / none). Keyed by source_id -- linkedin and indeed
share one scrape function but differ in verify capability. Phases 2-4
(saturation, board-diff closure, jobs verify) consume this map; phase 1 is
declaration only, no behavior change.
"""

from __future__ import annotations

from daily_driver.plugins.job_search.scraper.sources import (
    SCRAPERS,
    SOURCE_CAPABILITIES,
    SourceCapability,
)


def test_every_scraper_declares_capabilities() -> None:
    """The capability map and the scraper registry cover the same sources --
    a new adapter cannot land without declaring its capabilities."""
    assert set(SOURCE_CAPABILITIES) == set(SCRAPERS)


def test_capability_values_are_valid() -> None:
    for source_id, cap in SOURCE_CAPABILITIES.items():
        assert isinstance(cap, SourceCapability), source_id
        assert cap.enumeration in ("full", "windowed"), source_id
        assert cap.verify in ("board-diff", "url-check", "none"), source_id


def test_board_diff_requires_full_enumeration() -> None:
    """board-diff closure infers 'absent from listing = closed', which is only
    sound when the scrape returns the source's complete inventory."""
    for source_id, cap in SOURCE_CAPABILITIES.items():
        if cap.verify == "board-diff":
            assert cap.enumeration == "full", source_id


def test_capability_map_matches_plan() -> None:
    """The ratified capability map (plan-jobs-lifecycle-redesign.md):
    ATS boards are full/board-diff; apple is windowed (new-only results,
    slug-unstable URLs -- board-diff impossible); linkedin verifies by URL;
    indeed is bot-walled (unverifiable)."""
    expect = {
        "greenhouse": ("full", "board-diff"),
        "ashby": ("full", "board-diff"),
        "lever": ("full", "board-diff"),
        "workable": ("full", "board-diff"),
        "workday": ("full", "board-diff"),
        "apple": ("windowed", "url-check"),
        "linkedin": ("windowed", "url-check"),
        "indeed": ("windowed", "none"),
        "remoteok": ("windowed", "url-check"),
        "weworkremotely": ("windowed", "url-check"),
        "hn_jobs": ("windowed", "url-check"),
        "hn_who_is_hiring": ("windowed", "none"),
    }
    actual = {
        sid: (cap.enumeration, cap.verify) for sid, cap in SOURCE_CAPABILITIES.items()
    }
    assert actual == expect


def test_capabilities_are_immutable() -> None:
    import pytest

    cap = SOURCE_CAPABILITIES["greenhouse"]
    with pytest.raises(AttributeError):
        cap.verify = "none"  # type: ignore[misc]
