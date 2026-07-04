"""The dict->EnrichedJob lift applies geography normalization + remote heuristic.

`_enriched_from_scraped` is where untyped scrape dicts cross into the typed
pipeline. It must:
  - normalize Location to country-first geography (using an ``origin_country``
    hint the dict may carry from the scrape source),
  - set ``remote`` from the free heuristic tier (remote tokens in raw
    location/title),
without disturbing ``location_matches``, which runs earlier on the RAW dict.
"""

from __future__ import annotations

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.runner import (
    _enriched_from_scraped,
    location_matches,
)


def _job(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "company": "Acme",
        "role": "SRE",
        "url": "https://example.com/job/1",
        "source": "linkedin",
        "location": "Seattle, USA",
        "comp": "",
        "date_found": "2026-06-10",
    }
    base.update(overrides)
    return base


class TestLiftNormalization:
    def test_location_normalized_country_first(self) -> None:
        job = _enriched_from_scraped(_job(location="Amsterdam, Netherlands"))
        assert job.location == "Netherlands, Amsterdam"

    def test_origin_country_hint_used_when_text_lacks_country(self) -> None:
        job = _enriched_from_scraped(_job(location="Seattle", origin_country="US"))
        assert job.location == "United States, Seattle"

    def test_remote_location_becomes_blank_and_sets_remote(self) -> None:
        job = _enriched_from_scraped(_job(location="Remote"))
        assert job.location == ""
        assert job.remote == "remote"

    def test_remote_heuristic_from_title(self) -> None:
        job = _enriched_from_scraped(
            _job(location="Berlin, Germany", role="SRE (Remote)")
        )
        assert job.remote == "remote"
        assert job.location == "Germany, Berlin"

    def test_non_remote_job_has_blank_remote(self) -> None:
        job = _enriched_from_scraped(_job(location="Berlin, Germany"))
        assert job.remote == ""


class TestLocationMatchesUnaffected:
    """location_matches must keep operating on RAW scraped text, unchanged."""

    def test_remote_only_job_still_matches_on_raw_text(self) -> None:
        # The filter sees the raw "Remote" location and accepts (remote enabled),
        # exactly as before this change — even though the lifted Location is blank.
        plugin = JobSearchPlugin.model_validate(
            {"locations": {"remote": True, "countries": {"US": []}}}
        )
        raw = {"location": "Remote"}
        assert location_matches(raw, plugin) is True

    def test_country_only_in_hint_does_not_help_the_filter(self) -> None:
        # The filter reads raw text only; an origin_country hint on the dict is
        # invisible to it. A bare "Seattle" (no country alias in text) fails the
        # country filter exactly as it does today.
        plugin = JobSearchPlugin.model_validate(
            {"locations": {"remote": False, "countries": {"US": []}}}
        )
        assert (
            location_matches({"location": "Seattle", "origin_country": "US"}, plugin)
            is False
        )
        # And a country named in the text still matches.
        assert location_matches({"location": "Seattle, USA"}, plugin) is True
