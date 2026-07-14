"""Tests for scraper pure-function filters (no network, no HTML).

Covers the filter path that decides which scraped jobs survive to the CSV:
- `location_matches`: remote + countries allow-list.
- `matches_roles`: include/exclude wildcards, tier-1/2 logic.
- `dedup_key`: cross-site duplicate key stability.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.roles import matches_roles
from daily_driver.plugins.job_search.scraper.runner import (
    dedup_key,
    location_matches,
)


def _plugin(**kwargs: object) -> JobSearchPlugin:
    """Build a JobSearchPlugin for the location/role filters under test."""
    return JobSearchPlugin.model_validate(kwargs)


# ---------------------------------------------------------------------------
# location_matches
# ---------------------------------------------------------------------------


class TestLocationMatches:
    def test_accepts_remote_when_remote_enabled(self) -> None:
        plugin = _plugin(locations={"remote": True, "countries": {}})
        assert location_matches({"location": "Remote, Worldwide"}, plugin) is True

    def test_empty_location_accepted_when_remote_enabled(self) -> None:
        plugin = _plugin(locations={"remote": True})
        assert location_matches({"location": ""}, plugin) is True
        assert location_matches({}, plugin) is True

    def test_empty_location_rejected_when_remote_disabled(self) -> None:
        plugin = _plugin(locations={"remote": False, "countries": {}})
        assert location_matches({"location": ""}, plugin) is False

    def test_country_match(self) -> None:
        plugin = _plugin(locations={"remote": False, "countries": {"CA": []}})
        assert location_matches({"location": "Toronto, Canada"}, plugin) is True
        assert location_matches({"location": "New York, NY"}, plugin) is False

    def test_country_beyond_original_six_matches(self) -> None:
        # Countries are derived from JobSpy's enum, so NL (and ~70 others) work
        # without a hand-maintained entry.
        plugin = _plugin(locations={"remote": False, "countries": {"NL": []}})
        assert location_matches({"location": "Amsterdam, Netherlands"}, plugin) is True

    def test_bare_iso_code_not_false_matched(self) -> None:
        # "US" matches via "usa"/"united states", never the 2-char "us" — which
        # would spuriously hit "Austin".
        plugin = _plugin(locations={"remote": False, "countries": {"US": []}})
        assert location_matches({"location": "Austin, TX"}, plugin) is False
        assert location_matches({"location": "Denver, USA"}, plugin) is True

    def test_no_locations_block_accepts_everything(self) -> None:
        assert location_matches({"location": "Mars"}, _plugin()) is True

    def test_listed_city_matches_without_country_name(self) -> None:
        # Real ATS strings usually name the city, never the country
        # ("Movable Ink - Toronto"); a listed city must stand alone.
        plugin = _plugin(
            locations={"remote": False, "countries": {"CA": ["Vancouver"]}}
        )
        assert location_matches({"location": "Movable Ink - Vancouver"}, plugin) is True
        assert (
            location_matches({"location": "Vancouver, British Columbia"}, plugin)
            is True
        )

    def test_unlisted_city_rejected_for_city_narrowed_country(self) -> None:
        plugin = _plugin(
            locations={"remote": False, "countries": {"CA": ["Vancouver"]}}
        )
        assert location_matches({"location": "Movable Ink - Toronto"}, plugin) is False

    def test_country_name_alone_rejected_when_cities_listed(self) -> None:
        # Naming cities means "only these cities (or remote)": a bare
        # country-wide location no longer passes.
        plugin = _plugin(
            locations={"remote": False, "countries": {"CA": ["Vancouver"]}}
        )
        assert location_matches({"location": "Canada"}, plugin) is False

    def test_remote_passes_regardless_of_city_narrowing(self) -> None:
        plugin = _plugin(locations={"remote": True, "countries": {"CA": ["Vancouver"]}})
        assert location_matches({"location": "Toronto (Remote)"}, plugin) is True

    def test_city_match_is_whole_word(self) -> None:
        plugin = _plugin(
            locations={"remote": False, "countries": {"CA": ["Vancouver"]}}
        )
        assert (
            location_matches({"location": "Vancouverish, Elsewhere"}, plugin) is False
        )

    def test_blank_city_entry_rejected_at_config_load(self) -> None:
        # A blank city would compile to a zero-width regex that accepts nearly
        # everything — the opposite of narrowing. The model rejects it loudly.
        with pytest.raises(ValidationError, match="blank city"):
            _plugin(locations={"remote": False, "countries": {"CA": ["  "]}})

    def test_city_narrowing_does_not_leak_across_countries(self) -> None:
        # DK is whole-country, CA is city-narrowed: Copenhagen passes via the
        # DK country name; a non-listed CA city still fails.
        plugin = _plugin(
            locations={
                "remote": False,
                "countries": {"CA": ["Vancouver"], "DK": []},
            }
        )
        assert location_matches({"location": "Copenhagen, Denmark"}, plugin) is True
        assert location_matches({"location": "Ottawa"}, plugin) is False

    def test_remote_unlisted_country_dropped_by_default(self) -> None:
        # Default (remote_unlisted_countries=False): a remote role naming a
        # country not in `countries` is dropped even with remote enabled.
        plugin = _plugin(locations={"remote": True, "countries": {"US": ["charlotte"]}})
        assert location_matches({"location": "Canada (Remote)"}, plugin) is False
        assert location_matches({"location": "London, United Kingdom"}, plugin) is False

    def test_remote_configured_country_kept(self) -> None:
        # A remote role naming a configured country passes, even though US is
        # city-narrowed for onsite roles (remote keys off the country code set).
        plugin = _plugin(locations={"remote": True, "countries": {"US": ["charlotte"]}})
        assert location_matches({"location": "United States (Remote)"}, plugin) is True

    def test_remote_naming_no_country_accepted(self) -> None:
        # Ambiguous remote locations (no country named) are accepted by design.
        plugin = _plugin(locations={"remote": True, "countries": {"US": ["charlotte"]}})
        assert location_matches({"location": "Remote"}, plugin) is True
        assert location_matches({"location": "Charlotte (Remote)"}, plugin) is True

    def test_remote_unlisted_countries_flag_restores_anywhere(self) -> None:
        # Opt-in flag brings back country-blind remote acceptance.
        plugin = _plugin(
            locations={
                "remote": True,
                "remote_unlisted_countries": True,
                "countries": {"US": ["charlotte"]},
            }
        )
        assert location_matches({"location": "Canada (Remote)"}, plugin) is True

    def test_remote_multi_country_accepts_when_any_configured(self) -> None:
        # A remote role naming both a configured and an unlisted country is
        # accepted on the configured one — matching the onsite branch's
        # "any configured country present" rule (not a single-alias tiebreak).
        plugin = _plugin(locations={"remote": True, "countries": {"NL": []}})
        assert (
            location_matches(
                {"location": "Remote - United States or Netherlands"}, plugin
            )
            is True
        )
        # Naming only unlisted countries still drops.
        assert location_matches({"location": "Remote - United States"}, plugin) is False

    def test_remote_empty_countries_accepts_anywhere(self) -> None:
        # An empty countries map imposes no restriction: remote is unscoped, so
        # a country-named remote role passes (mirrors the enrichment prompt).
        plugin = _plugin(locations={"remote": True, "countries": {}})
        assert location_matches({"location": "Remote - Germany"}, plugin) is True
        assert location_matches({"location": "Remote"}, plugin) is True

    def test_country_alias_matches_whole_word(self) -> None:
        # Country aliases match whole-word, not substring: the 2-char GB alias
        # "uk" must not hit a US city like "Milwaukee". With GB configured, a
        # remote role naming the (unlisted) US is dropped, not leaked via the
        # "uk" inside "Milwaukee".
        plugin = _plugin(locations={"remote": True, "countries": {"GB": []}})
        assert (
            location_matches({"location": "Remote - Milwaukee, United States"}, plugin)
            is False
        )
        # A real UK remote role still passes (whole-word alias hit).
        assert location_matches({"location": "Remote - Manchester, UK"}, plugin) is True

    def test_shadowing_subnation_resolves_to_owner_country(self) -> None:
        # "New South Wales" is Australia: it must not satisfy a GB config via
        # the contained "wales" (drops as naming unlisted AU), and it must
        # satisfy an AU config directly.
        gb = _plugin(locations={"remote": True, "countries": {"GB": []}})
        au = _plugin(locations={"remote": True, "countries": {"AU": []}})
        loc = {"location": "Sydney, New South Wales (Remote)"}
        assert location_matches(loc, gb) is False
        assert location_matches(loc, au) is True
        # A genuine Welsh location still passes the GB config.
        assert location_matches({"location": "Cardiff, Wales (Remote)"}, gb) is True


# ---------------------------------------------------------------------------
# matches_roles
# ---------------------------------------------------------------------------


def _roles(*roles: str) -> JobSearchPlugin:
    """A plugin carrying the given role list for matches_roles(title, plugin)."""
    return JobSearchPlugin.model_validate({"roles": list(roles)})


class TestMatchesRoles:
    def test_literal_role_substring_match(self) -> None:
        assert matches_roles("Senior SRE", _roles("SRE")) is True
        assert matches_roles("Senior SRE", _roles("senior sre")) is True

    def test_wildcard_role_match(self) -> None:
        """Wildcard pattern matches when prefix aligns."""
        assert matches_roles("Staff Backend Engineer", _roles("Staff *")) is True
        # "Junior Backend Engineer" doesn't match the wildcard AND has no domain
        # keyword (backend isn't in _DEFAULT_DOMAIN_KEYWORDS), so it falls
        # through all tiers to return False.
        assert matches_roles("Junior Backend Engineer", _roles("Staff *")) is False

    def test_exclusion_short_circuits(self) -> None:
        """Exclusion wins over any include match."""
        assert matches_roles("Manager of SRE", _roles("SRE", "!*Manager*")) is False

    def test_tier2_domain_plus_seniority(self) -> None:
        """DevOps (domain) + Senior (seniority) both present → match."""
        assert matches_roles("Senior DevOps Engineer", _roles()) is True

    def test_tier2_domain_without_seniority_rejected(self) -> None:
        assert matches_roles("DevOps Engineer", _roles()) is False

    def test_standalone_sre_requires_explicit_role(self) -> None:
        """SRE / Platform Engineer are matched only when named in `roles`.

        Role matching is fully config-driven: there is no built-in fallback that
        keeps technical titles a workspace never asked for. Absent an explicit
        role entry (and with no seniority co-occurrence for tier 2), these fall
        through to False; listing them as roles matches them via tier 1.
        """
        assert matches_roles("Site Reliability Engineer", _roles()) is False
        assert matches_roles("Platform Engineer", _roles()) is False
        assert matches_roles("SRE III", _roles()) is False

        sre = _roles("sre", "platform engineer", "site reliability engineer")
        assert matches_roles("Site Reliability Engineer", sre) is True
        assert matches_roles("Platform Engineer", sre) is True
        assert matches_roles("SRE III", sre) is True

    def test_no_match_for_unrelated_title(self) -> None:
        assert matches_roles("Marketing Intern", _roles("SRE")) is False

    def test_special_chars_in_role_not_regex_interpreted(self) -> None:
        """Entries like 'CI/CD Engineer' must match literally — no regex surprise."""
        assert matches_roles("CI/CD Engineer", _roles("CI/CD Engineer")) is True

    def test_custom_domain_and_seniority_keywords_drive_tier2(self) -> None:
        """domain_keywords + seniority_keywords REPLACE the built-in tier-2 sets.

        A title built from words outside the defaults only matches once both
        custom keyword lists name them; with the default sets the same title
        falls through every tier to False.
        """
        title = "Wizard Robotics Engineer"
        custom = JobSearchPlugin.model_validate(
            {"domain_keywords": ["robotics"], "seniority_keywords": ["wizard"]}
        )
        assert matches_roles(title, custom) is True
        # Default keyword sets: "robotics"/"wizard" are not present, so no match.
        assert matches_roles(title, _plugin()) is False

    def test_custom_domain_without_matching_seniority_rejected(self) -> None:
        """Custom domain keyword alone is not enough — tier-2 needs both."""
        custom = JobSearchPlugin.model_validate(
            {"domain_keywords": ["robotics"], "seniority_keywords": ["wizard"]}
        )
        # Domain present, seniority absent => tier-2 fails.
        assert matches_roles("Robotics Engineer", custom) is False


# ---------------------------------------------------------------------------
# dedup_key
# ---------------------------------------------------------------------------


class TestDedupKey:
    def test_case_and_whitespace_normalized(self) -> None:
        assert dedup_key("ACME Corp", "Senior  SRE") == dedup_key(
            "acme corp", "senior sre"
        )

    def test_different_role_different_key(self) -> None:
        assert dedup_key("Acme", "SRE") != dedup_key("Acme", "Platform Engineer")

    def test_different_company_different_key(self) -> None:
        assert dedup_key("Acme", "SRE") != dedup_key("Globex", "SRE")
