"""Tests for scraper pure-function filters (no network, no HTML).

Covers the filter path that decides which scraped jobs survive to the CSV:
- `comp_meets_threshold`: comp-floor gating (fails open on unknown comp).
- `location_matches`: remote + cities + countries allow-list.
- `matches_roles`: include/exclude wildcards, tier-1/2/2b logic.
- `dedup_key`: cross-site duplicate key stability.
- `normalize_job`: canonical remote/role/source/comp massaging.
"""

from __future__ import annotations

import pytest

from daily_driver.plugins.job_search.scraper.comp import (
    comp_meets_threshold,
    currency_matches_primary,
)
from daily_driver.plugins.job_search.scraper.runner import (
    _known_urls_from_config,
    dedup_key,
    location_matches,
    matches_roles,
    normalize_job,
)

# ---------------------------------------------------------------------------
# comp_meets_threshold
# ---------------------------------------------------------------------------


class TestCompThreshold:
    def test_accepts_when_cmax_above_threshold(self) -> None:
        ok, reason = comp_meets_threshold(
            {"comp_max_usd": 220_000}, {"job_search": {"min_comp_usd": 180_000}}
        )
        assert ok is True
        assert reason == ""

    def test_accepts_when_cmax_equals_threshold(self) -> None:
        ok, _ = comp_meets_threshold(
            {"comp_max_usd": 180_000}, {"job_search": {"min_comp_usd": 180_000}}
        )
        assert ok is True

    def test_rejects_when_cmax_below_threshold(self) -> None:
        ok, reason = comp_meets_threshold(
            {"comp_max_usd": 120_000}, {"job_search": {"min_comp_usd": 180_000}}
        )
        assert ok is False
        assert "below comp threshold" in reason
        assert "$120,000" in reason

    def test_fails_open_when_comp_unknown(self) -> None:
        """Jobs without listed comp must reach CSV for manual review."""
        ok, _ = comp_meets_threshold({}, {"job_search": {"min_comp_usd": 180_000}})
        assert ok is True

    def test_fails_open_when_cmax_zero(self) -> None:
        ok, _ = comp_meets_threshold(
            {"comp_max_usd": 0}, {"job_search": {"min_comp_usd": 180_000}}
        )
        assert ok is True

    def test_default_threshold_is_180k(self) -> None:
        """Missing config falls back to a 180k default."""
        ok, _ = comp_meets_threshold({"comp_max_usd": 179_999}, {})
        assert ok is False


# ---------------------------------------------------------------------------
# location_matches
# ---------------------------------------------------------------------------


class TestLocationMatches:
    def test_accepts_remote_when_remote_enabled(self) -> None:
        cfg = {
            "job_search": {"locations": {"remote": True, "cities": [], "countries": []}}
        }
        assert location_matches({"location": "Remote, Worldwide"}, cfg) is True

    def test_empty_location_accepted_when_remote_enabled(self) -> None:
        cfg = {"job_search": {"locations": {"remote": True}}}
        assert location_matches({"location": ""}, cfg) is True
        assert location_matches({}, cfg) is True

    def test_empty_location_rejected_when_remote_disabled(self) -> None:
        cfg = {
            "job_search": {
                "locations": {"remote": False, "cities": ["Vancouver"], "countries": []}
            }
        }
        assert location_matches({"location": ""}, cfg) is False

    def test_city_match_case_insensitive(self) -> None:
        cfg = {
            "job_search": {
                "locations": {"remote": False, "cities": ["Vancouver"], "countries": []}
            }
        }
        assert location_matches({"location": "vancouver, BC"}, cfg) is True
        assert location_matches({"location": "Toronto, ON"}, cfg) is False

    def test_country_match(self) -> None:
        cfg = {
            "job_search": {
                "locations": {"remote": False, "cities": [], "countries": ["CA"]}
            }
        }
        assert location_matches({"location": "Toronto, Canada"}, cfg) is True
        assert location_matches({"location": "New York, NY"}, cfg) is False

    def test_no_locations_block_accepts_everything(self) -> None:
        assert location_matches({"location": "Mars"}, {}) is True


# ---------------------------------------------------------------------------
# matches_roles
# ---------------------------------------------------------------------------


class TestMatchesRoles:
    def test_literal_role_substring_match(self) -> None:
        assert matches_roles("Senior SRE", ["SRE"]) is True
        assert matches_roles("Senior SRE", ["senior sre"]) is True

    def test_wildcard_role_match(self) -> None:
        """Wildcard pattern matches when prefix aligns."""
        assert matches_roles("Staff Backend Engineer", ["Staff *"]) is True
        # "Junior Backend Engineer" doesn't match the wildcard AND has no domain
        # keyword (backend isn't in _DEFAULT_DOMAIN_KEYWORDS), so it falls
        # through all tiers to return False.
        assert matches_roles("Junior Backend Engineer", ["Staff *"]) is False

    def test_exclusion_short_circuits(self) -> None:
        """Exclusion wins over any include match."""
        assert matches_roles("Manager of SRE", ["SRE", "!*Manager*"]) is False

    def test_tier2_domain_plus_seniority(self) -> None:
        """DevOps (domain) + Senior (seniority) both present → match."""
        assert matches_roles("Senior DevOps Engineer", []) is True

    def test_tier2_domain_without_seniority_rejected(self) -> None:
        assert matches_roles("DevOps Engineer", []) is False

    def test_tier2b_sre_matches_alone(self) -> None:
        """SRE / Platform Engineer pass without a seniority qualifier."""
        assert matches_roles("Site Reliability Engineer", []) is True
        assert matches_roles("Platform Engineer", []) is True
        assert matches_roles("SRE III", []) is True

    def test_no_match_for_unrelated_title(self) -> None:
        assert matches_roles("Marketing Intern", ["SRE"]) is False

    def test_special_chars_in_role_not_regex_interpreted(self) -> None:
        """Entries like 'CI/CD Engineer' must match literally — no regex surprise."""
        assert matches_roles("CI/CD Engineer", ["CI/CD Engineer"]) is True


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


# ---------------------------------------------------------------------------
# normalize_job
# ---------------------------------------------------------------------------


class TestNormalizeJob:
    def test_remote_alias_collapsed(self) -> None:
        job = normalize_job({"location": "Anywhere", "role": "SRE"}, "RemoteOK")
        assert job["location"] == "Remote"

    def test_remote_suffix_stripped_from_role(self) -> None:
        job = normalize_job(
            {"role": "Senior SRE (Remote)", "location": "Remote"}, "RemoteOK"
        )
        assert job["role"] == "Senior SRE"

    def test_greenhouse_board_split_into_canonical_fields(self) -> None:
        job = normalize_job(
            {"role": "SRE", "location": "Remote"}, "Greenhouse (acme-corp)"
        )
        assert job["source_canonical"] == "greenhouse"
        assert job["source_board"] == "acme-corp"

    def test_non_greenhouse_source_canonical_lowercased(self) -> None:
        job = normalize_job({"role": "SRE", "location": "Remote"}, "RemoteOK")
        assert job["source_canonical"] == "remoteok"
        assert job["source_board"] == ""

    def test_comp_string_parsed_into_structured_fields(self) -> None:
        job = normalize_job(
            {"role": "SRE", "location": "Remote", "comp": "$150,000-$200,000"},
            "RemoteOK",
        )
        assert job["comp_min_native"] == 150_000
        assert job["comp_max_native"] == 200_000
        assert job["comp_currency"] == "USD"

    def test_does_not_mutate_input(self) -> None:
        raw = {"location": "Anywhere", "role": "SRE"}
        normalize_job(raw, "RemoteOK")
        assert raw["location"] == "Anywhere"  # unchanged


# ---------------------------------------------------------------------------
# _parse_comp (via normalize_job) — currency + period precedence
# ---------------------------------------------------------------------------


class TestCompParsing:
    def _parsed(self, comp_str: str) -> dict:
        return normalize_job({"comp": comp_str, "location": "Remote"}, "Test")

    def test_bare_dollar_defaults_to_usd(self) -> None:
        r = self._parsed("$150,000-$200,000")
        assert r["comp_currency"] == "USD"

    def test_explicit_cad_iso_code(self) -> None:
        r = self._parsed("CAD 120,000-160,000/yr")
        assert r["comp_currency"] == "CAD"
        assert r["comp_min_native"] == 120_000
        assert r["comp_period"] == "year"

    def test_iso_code_precedence_over_symbol(self) -> None:
        """Explicit ISO code wins even when $ is also present."""
        r = self._parsed("USD $180,000")
        assert r["comp_currency"] == "USD"

    def test_unparseable_string_returns_empty(self) -> None:
        r = self._parsed("unpublished")
        assert r["comp_min_native"] is None
        assert r["comp_max_native"] is None

    def test_k_salary_shorthand_expanded(self) -> None:
        """'150k-200k' → 150000-200000."""
        r = self._parsed("$150k-$200k")
        assert r["comp_min_native"] == 150_000
        assert r["comp_max_native"] == 200_000

    def test_range_swapped_when_min_exceeds_max(self) -> None:
        r = self._parsed("$200,000-$150,000")
        assert r["comp_min_native"] == 150_000
        assert r["comp_max_native"] == 200_000

    @pytest.mark.parametrize(
        "comp_str, expected_period",
        [
            ("$180,000/yr", "year"),
            ("$50/hr", "hour"),
            ("$15,000/mo", "month"),
        ],
    )
    def test_period_extracted(self, comp_str: str, expected_period: str) -> None:
        r = self._parsed(comp_str)
        assert r["comp_period"] == expected_period


class TestKnownUrlsFromConfig:
    """Adapters read pruned/dedup URLs from the config dict's _known_urls key.

    The orchestrator stuffs the union of jobs.csv + jobs.archive.csv URLs in
    here so Playwright adapters (Apple) can short-circuit during pagination
    without re-deriving the dedup state.
    """

    def test_returns_empty_set_when_key_absent(self) -> None:
        assert _known_urls_from_config({}) == set()

    def test_returns_set_when_key_present(self) -> None:
        urls = {
            "https://jobs.apple.com/x/details/1",
            "https://remoteok.com/remote-jobs/2",
        }
        assert _known_urls_from_config({"_known_urls": urls}) == urls


class TestCurrencyMatchesPrimary:
    """Currency primary-mode filter (#48).

    When ``plugins.job_search.primary_currency`` is set, drop scraped rows
    whose Comp parses to a different currency. Rows with empty/unknown
    currency (unparseable comp) pass through — currency=None is the sentinel
    for "couldn't read", not "doesn't match".
    """

    def _config(self, primary: str | None) -> dict:
        return {"job_search": {"primary_currency": primary}}

    def test_no_primary_keeps_all(self) -> None:
        assert currency_matches_primary({"comp_currency": "EUR"}, self._config(None))

    def test_matching_currency_kept(self) -> None:
        assert currency_matches_primary({"comp_currency": "USD"}, self._config("USD"))

    def test_mismatched_currency_dropped(self) -> None:
        assert not currency_matches_primary(
            {"comp_currency": "EUR"}, self._config("USD")
        )

    def test_unparseable_currency_kept(self) -> None:
        assert currency_matches_primary({"comp_currency": ""}, self._config("USD"))
        assert currency_matches_primary({}, self._config("USD"))
