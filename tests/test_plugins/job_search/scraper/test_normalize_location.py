"""Country-first geography normalization for the Location column (task #6).

Location becomes geography-only: a canonical full country name first, then the
city/region remainder in original order. Remote tokens are stripped (remote-ness
moves to the Remote column). When the text names no country, an origin-country
hint (the per-search ISO code a scrape source knows) supplies it; with neither,
the result is blank — never the word "Remote".
"""

from __future__ import annotations

import logging

import pytest

from daily_driver.plugins.job_search.scraper.normalize_location import (
    detect_remote,
    normalize_location,
)


class TestNormalizeLocation:
    def test_country_in_text_moves_to_front(self) -> None:
        # "Amsterdam, North Holland, Netherlands" -> country first, remainder after.
        assert (
            normalize_location("Amsterdam, North Holland, Netherlands")
            == "Netherlands, Amsterdam, North Holland"
        )

    def test_country_only_text_canonicalized(self) -> None:
        assert normalize_location("Canada") == "Canada"

    def test_abbreviation_canonicalized_to_full_name(self) -> None:
        # "usa" / "uk" are matching aliases; the canonical (longest) full name wins.
        assert normalize_location("Denver, USA") == "United States, Denver"
        assert normalize_location("London, UK") == "United Kingdom, London"

    def test_hint_fallback_when_no_country_in_text(self) -> None:
        # jobspy/apple know the per-search ISO code; "Seattle" with a US search
        # resolves to the United States.
        assert normalize_location("Seattle", origin_country="US") == (
            "United States, Seattle"
        )

    def test_hint_ignored_when_text_names_a_country(self) -> None:
        # An explicit in-text country always wins over the scrape-context hint.
        assert (
            normalize_location("Toronto, Canada", origin_country="US")
            == "Canada, Toronto"
        )

    def test_remote_tokens_stripped_with_hint(self) -> None:
        # "Remote, US" from a US search -> United States (remote word removed).
        assert normalize_location("Remote, US", origin_country="US") == (
            "United States"
        )

    def test_remote_ok_token_stripped(self) -> None:
        assert normalize_location("Remote OK, Berlin, Germany") == "Germany, Berlin"

    def test_bare_remote_becomes_blank_not_the_word_remote(self) -> None:
        # The single most important contract: a remote-only location with no
        # country and no hint is blank, never the literal "Remote".
        assert normalize_location("Remote") == ""
        assert normalize_location("Anywhere") == ""
        assert normalize_location("") == ""

    def test_unrecognized_text_passes_through_verbatim(self) -> None:
        # No country alias, no hint: keep the original text (cleaned), don't drop.
        assert normalize_location("Mars Base One") == "Mars Base One"

    def test_dangling_separators_cleaned(self) -> None:
        # Stripping the country/remote token must not leave ", ," or trailing commas.
        assert (
            normalize_location("Amsterdam, , Netherlands") == "Netherlands, Amsterdam"
        )
        assert normalize_location("Remote - Germany") == "Germany"

    def test_hint_unknown_code_no_crash(self) -> None:
        # An ISO code JobSpy's enum lacks resolves to no name; fall back to text.
        assert normalize_location("Springfield", origin_country="XX") == ("Springfield")


class TestRealWorldStrings:
    """The hunter's five corrupted real-world strings (review fix 2)."""

    def test_remote_parens_country_unwrapped(self) -> None:
        # "Remote (US)": the remote token's remainder must be re-examined for
        # geography, parentheses unwrapped — not swallowed whole.
        assert normalize_location("Remote (US)") == "United States"

    def test_remote_parens_still_triggers_remote_heuristic(self) -> None:
        # The Remote column heuristic is independent and must still fire.
        assert detect_remote("Remote (US)", "SRE") == "remote"

    def test_two_countries_or_connective_first_wins(self) -> None:
        # "US or Canada": pick the FIRST country in text order; drop connective +
        # loser. No duplication, no fake city.
        assert normalize_location("US or Canada") == "United States"

    def test_two_countries_slash_connective_first_wins(self) -> None:
        assert normalize_location("USA / Canada") == "United States"

    def test_city_or_remote(self) -> None:
        # "San Francisco or Remote": " or " is a separator, bare remote stripped.
        assert normalize_location("San Francisco or Remote") == "San Francisco"

    def test_city_country_or_remote(self) -> None:
        # "Berlin, Germany or Remote" -> "Germany, Berlin" (alias removed by
        # whole-word substitution within the token, no duplication).
        assert normalize_location("Berlin, Germany or Remote") == "Germany, Berlin"

    def test_real_world_outputs_are_idempotent(self) -> None:
        for raw in (
            "Remote (US)",
            "US or Canada",
            "USA / Canada",
            "San Francisco or Remote",
            "Berlin, Germany or Remote",
        ):
            once = normalize_location(raw)
            assert normalize_location(once) == once, raw


class TestNormalizationLogging:
    def test_logs_when_nonempty_input_yields_empty(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="daily_driver"):
            assert normalize_location("Remote") == ""
        assert any(
            "Remote" in r.getMessage() and "empty" in r.getMessage().lower()
            for r in caplog.records
        ), caplog.records

    def test_logs_when_multiple_countries_present(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="daily_driver"):
            normalize_location("US or Canada")
        assert any(
            "countr" in r.getMessage().lower() for r in caplog.records
        ), caplog.records


class TestDetectRemote:
    def test_remote_token_in_location(self) -> None:
        assert detect_remote("Remote, US", "SRE") == "remote"
        assert detect_remote("Anywhere", "SRE") == "remote"

    def test_remote_token_in_title(self) -> None:
        assert detect_remote("Berlin, Germany", "SRE (Remote)") == "remote"

    def test_no_remote_token_returns_blank(self) -> None:
        assert detect_remote("Berlin, Germany", "SRE") == ""
        assert detect_remote("", "SRE") == ""
