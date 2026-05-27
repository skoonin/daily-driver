"""Tests for scraper pure-function filters (no network, no HTML).

Covers the filter path that decides which scraped jobs survive to the CSV:
- `comp_meets_threshold_typed`: comp-floor gating (fails open on unknown comp).
- `location_matches`: remote + cities + countries allow-list.
- `matches_roles`: include/exclude wildcards, tier-1/2/2b logic.
- `dedup_key`: cross-site duplicate key stability.
"""

from __future__ import annotations

from daily_driver.plugins.job_search.scraper.comp import comp_meets_threshold_typed
from daily_driver.plugins.job_search.scraper.models import (
    EnrichedJob,
    NormalizedJob,
    RawScrapedJob,
)
from daily_driver.plugins.job_search.scraper.runner import (
    _known_urls_from_config,
    dedup_key,
    location_matches,
    matches_roles,
)
from daily_driver.plugins.job_search.scraper.sources._http import (
    country_names,
    jobspy_country,
)


def _enriched_with_comp(comp_display: str) -> EnrichedJob:
    """Build an EnrichedJob whose typed Comp comes from a display string."""
    raw = RawScrapedJob(
        company="Acme",
        role="SRE",
        url="https://example.com/j",
        source="remoteok",
        location="Remote",
        comp_display=comp_display,
    )
    return EnrichedJob.from_normalized(NormalizedJob.from_raw(raw))


# ---------------------------------------------------------------------------
# comp_meets_threshold_typed
# ---------------------------------------------------------------------------


class TestCompThreshold:
    def test_accepts_when_cmax_above_threshold(self) -> None:
        ok, reason = comp_meets_threshold_typed(
            _enriched_with_comp("$220,000"),
            {"job_search": {"min_comp_usd": 180_000}},
        )
        assert ok is True
        assert reason == ""

    def test_accepts_when_cmax_equals_threshold(self) -> None:
        ok, _ = comp_meets_threshold_typed(
            _enriched_with_comp("$180,000"),
            {"job_search": {"min_comp_usd": 180_000}},
        )
        assert ok is True

    def test_rejects_when_cmax_below_threshold(self) -> None:
        ok, reason = comp_meets_threshold_typed(
            _enriched_with_comp("$120,000"),
            {"job_search": {"min_comp_usd": 180_000}},
        )
        assert ok is False
        assert "below comp threshold" in reason
        assert "120,000" in reason

    def test_fails_open_when_comp_unknown(self) -> None:
        """Jobs without listed comp must reach CSV for manual review."""
        ok, _ = comp_meets_threshold_typed(
            _enriched_with_comp("competitive"),
            {"job_search": {"min_comp_usd": 180_000}},
        )
        assert ok is True

    def test_default_threshold_is_180k(self) -> None:
        """Missing config falls back to a 180k default."""
        ok, _ = comp_meets_threshold_typed(_enriched_with_comp("$179,999"), {})
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

    def test_country_beyond_original_six_matches(self) -> None:
        # Countries are derived from JobSpy's enum, so NL (and ~70 others) work
        # without a hand-maintained entry.
        cfg = {
            "job_search": {
                "locations": {"remote": False, "cities": [], "countries": ["NL"]}
            }
        }
        assert location_matches({"location": "Amsterdam, Netherlands"}, cfg) is True

    def test_bare_iso_code_not_false_matched(self) -> None:
        # "US" matches via "usa"/"united states", never the 2-char "us" — which
        # would spuriously hit "Austin".
        cfg = {
            "job_search": {
                "locations": {"remote": False, "cities": [], "countries": ["US"]}
            }
        }
        assert location_matches({"location": "Austin, TX"}, cfg) is False
        assert location_matches({"location": "Denver, USA"}, cfg) is True

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
