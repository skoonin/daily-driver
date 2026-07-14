"""Tests for country lookups + Apple postLocation resolution (no network)."""

from __future__ import annotations

from typing import Any

from daily_driver.plugins.job_search.scraper.countries import (
    apple_postlocation_code,
    canonical_country_name,
    country_named_in,
    country_names,
    detect_country,
    jobspy_country,
)


class TestCountryDerivation:
    """country_names / jobspy_country are derived from JobSpy's Country enum."""

    def test_bare_iso_code_excluded_from_match_aliases(self) -> None:
        # "us" (2 chars) is dropped so it can't substring-match "Austin".
        names = country_names("US")
        assert "us" not in names
        assert "united states" in names

    def test_gb_keeps_constituent_nations(self) -> None:
        assert {"england", "scotland", "wales", "uk"} <= set(country_names("GB"))

    def test_country_beyond_original_six_supported(self) -> None:
        assert "netherlands" in country_names("NL")

    def test_jobspy_country_name_round_trips(self) -> None:
        # Names must be accepted by JobSpy's Country.from_string.
        from jobspy.model import Country

        for code in ("US", "GB", "DE", "NL", "ZA"):
            assert Country.from_string(jobspy_country(code, "usa"))

    def test_unsupported_code_falls_back(self) -> None:
        assert country_names("XX") == []
        assert jobspy_country("XX", "usa") == "usa"


class TestSubnationShadowing:
    """Sub-national names containing another country's alias resolve to their
    owner instead of false-hitting the contained alias ("New South Wales" is
    Australia, not the "wales" of a GB config)."""

    def test_detect_country_resolves_shadowing_subnation_to_owner(self) -> None:
        assert detect_country("Sydney, New South Wales") == (
            "new south wales",
            "Australia",
        )
        assert detect_country("Boston, New England") == (
            "new england",
            "United States",
        )

    def test_detect_country_still_finds_real_uk_nations(self) -> None:
        assert detect_country("Cardiff, Wales") == ("wales", "United Kingdom")
        assert detect_country("Manchester, England") == (
            "england",
            "United Kingdom",
        )

    def test_country_named_in_ignores_foreign_subnation(self) -> None:
        loc = "sydney, new south wales (remote)"
        assert country_named_in(loc, "GB") is False
        assert country_named_in(loc, "AU") is True

    def test_canonical_names_not_polluted_by_shadow_phrases(self) -> None:
        # Shadow phrases stay out of country_names, so the longest-alias
        # canonical derivation keeps the spelled-out country name.
        assert canonical_country_name("AU") == "Australia"
        assert canonical_country_name("US") == "United States"
        assert "new south wales" not in country_names("AU")


class TestApplePostLocationCode:
    """apple_postlocation_code resolves Apple's internal codes via refData."""

    def test_resolves_level1_exact_match(self) -> None:
        def fetch_json(url: str) -> dict[str, Any]:
            return {"res": [{"code": "CHEC", "name": "Switzerland", "level": 1}]}

        assert apple_postlocation_code(["Switzerland"], fetch_json) == "CHEC"

    def test_disambiguates_united_states_by_exact_name(self) -> None:
        def fetch_json(url: str) -> dict[str, Any]:
            return {
                "res": [
                    {"code": "USA", "name": "United States", "level": 1},
                    {
                        "code": "UMI",
                        "name": "United States Minor Outlying Islands",
                        "level": 1,
                    },
                ]
            }

        assert apple_postlocation_code(["United States"], fetch_json) == "USA"

    def test_tries_aliases_until_one_resolves(self) -> None:
        # JobSpy's primary name for the US is "usa", which Apple's API does not
        # recognise; the spelled-out alias resolves. Guards the regression where
        # feeding only the primary abbreviation silently skipped US/GB.
        def fetch_json(url: str) -> dict[str, Any]:
            if "input=usa" in url:
                return {"res": []}
            return {"res": [{"code": "USA", "name": "United States", "level": 1}]}

        assert apple_postlocation_code(["usa", "united states"], fetch_json) == "USA"

    def test_rejects_near_miss_alias(self) -> None:
        # "uk" resolves to a level-1 entry named "Ukraine"; the exact-name match
        # must reject it rather than mis-scope GB jobs to Ukraine.
        def fetch_json(url: str) -> dict[str, Any]:
            return {"res": [{"code": "UKR", "name": "Ukraine", "level": 1}]}

        assert apple_postlocation_code(["uk"], fetch_json) is None

    def test_empty_results_returns_none(self) -> None:
        def fetch_json(url: str) -> dict[str, Any]:
            return {"res": []}

        assert apple_postlocation_code(["Switzerland"], fetch_json) is None

    def test_missing_res_key_returns_none(self) -> None:
        # {} is the failure sentinel _fetch_json returns when the refData request
        # errors; it must resolve to a clean non-resolution (skip), not raise.
        def fetch_json(url: str) -> dict[str, Any]:
            return {}

        assert apple_postlocation_code(["Switzerland"], fetch_json) is None

    def test_non_level1_results_returns_none(self) -> None:
        def fetch_json(url: str) -> dict[str, Any]:
            return {"res": [{"code": "X", "name": "Switzerland", "level": 2}]}

        assert apple_postlocation_code(["Switzerland"], fetch_json) is None

    def test_passes_encoded_country_name_in_url(self) -> None:
        seen: list[str] = []

        def fetch_json(url: str) -> dict[str, Any]:
            seen.append(url)
            return {"res": [{"code": "USA", "name": "United States", "level": 1}]}

        apple_postlocation_code(["United States"], fetch_json)
        assert seen and "input=United%20States" in seen[0]
