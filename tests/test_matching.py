"""Tests for matches_roles() — pure function, no I/O."""

import pytest
import scrape_jobs as sj

ROLES = ["SRE", "Platform Engineer", "DevOps Engineer", "Infrastructure Engineer"]


class TestTier1ExactSubstring:
    """Configured role appears verbatim in the title."""

    @pytest.mark.parametrize("title", [
        "Senior SRE at BigCo",
        "Staff Platform Engineer",
        "DevOps Engineer Lead",
        "Infrastructure Engineer II",
    ])
    def test_matches_configured_role(self, title):
        assert sj.matches_roles(title, ROLES)

    def test_case_insensitive_match(self):
        assert sj.matches_roles("senior sre at bigco", ROLES)

    def test_no_match_when_role_absent(self):
        # "Database Administrator" contains no configured role, no domain keyword,
        # and no seniority keyword — exercises Tier 1 rejection cleanly.
        assert not sj.matches_roles("Database Administrator", ROLES)


class TestTier2DomainPlusSeniority:
    """Domain keyword + seniority keyword both present, no roles list needed."""

    @pytest.mark.parametrize("title", [
        "Senior Site Reliability Engineer",
        "Staff Cloud Engineer",
        "Principal Infrastructure Architect",
        "Lead Platform Engineering Manager",
        "Sr. DevOps Specialist",
    ])
    def test_matches_domain_and_seniority(self, title):
        assert sj.matches_roles(title, [])

    def test_domain_without_seniority_does_not_match(self):
        # "cloud engineer" is a domain keyword but no seniority here
        assert not sj.matches_roles("Cloud Engineer", [])

    def test_seniority_without_domain_does_not_match(self):
        assert not sj.matches_roles("Senior Frontend Developer", [])


class TestTier2bStandalone:
    """Certain keywords match without seniority and without configured roles."""

    def test_standalone_sre(self):
        assert sj.matches_roles("SRE", [])

    def test_standalone_platform_engineer(self):
        assert sj.matches_roles("Platform Engineer at Stripe", [])

    def test_sre_embedded_in_title(self):
        assert sj.matches_roles("Junior SRE (entry level)", [])


class TestNonMatching:
    def test_empty_title(self):
        assert not sj.matches_roles("", ROLES)

    def test_unrelated_title(self):
        assert not sj.matches_roles("Marketing Manager", ROLES)

    def test_empty_roles_and_no_keywords(self):
        assert not sj.matches_roles("Data Analyst", [])


class TestTier1Wildcard:
    def test_star_matches_any_middle(self):
        assert sj.matches_roles("Senior Cloud Engineer", ["Senior * Engineer"])
        assert sj.matches_roles("Senior Platform Engineer", ["Senior * Engineer"])

    def test_star_at_end(self):
        assert sj.matches_roles("Senior SRE at BigCo", ["Senior *"])

    def test_star_at_start(self):
        assert sj.matches_roles("Principal Engineering Manager", ["* Manager"])

    def test_wildcard_case_insensitive(self):
        assert sj.matches_roles("senior cloud engineer", ["Senior * Engineer"])

    def test_wildcard_does_not_match_wrong_shape(self):
        # "Database Administrator" trips no tier: no wildcard match, no
        # domain keyword, no seniority keyword. Clean rejection.
        assert not sj.matches_roles("Database Administrator", ["Senior * Engineer"])

    def test_literals_still_work_when_mixed_with_wildcards(self):
        roles = ["SRE", "Senior * Engineer"]
        assert sj.matches_roles("Staff SRE", roles)
        assert sj.matches_roles("Senior Build Engineer", roles)

    def test_special_chars_in_role_are_escaped(self):
        assert sj.matches_roles("Senior CI/CD Engineer at X", ["CI/CD Engineer"])
        assert sj.matches_roles("C++ Developer", ["C++ *"])


class TestNegation:
    def test_exclusion_rejects_literal(self):
        assert not sj.matches_roles("Senior SRE Manager", ["SRE", "!Manager"])

    def test_exclusion_rejects_wildcard(self):
        assert not sj.matches_roles(
            "Engineering Bootcamp Instructor",
            ["SRE", "!*Bootcamp*"],
        )

    def test_exclusion_vetoes_tier2b_standalone_sre(self):
        """'SRE Manager' passes Tier 2b today; exclusion must override."""
        assert not sj.matches_roles("Senior SRE Manager", ["!*Manager*"])

    def test_exclusion_vetoes_tier2_domain_plus_seniority(self):
        assert not sj.matches_roles(
            "Senior Infrastructure Manager",
            ["!*Manager*"],
        )

    def test_exclusion_does_not_affect_clean_title(self):
        assert sj.matches_roles("Senior SRE", ["SRE", "!Manager"])

    def test_exclusion_case_insensitive(self):
        assert not sj.matches_roles("Senior sre MANAGER", ["SRE", "!manager"])

    def test_multiple_exclusions(self):
        roles = ["SRE", "!Manager", "!Director", "!*Bootcamp*"]
        assert not sj.matches_roles("SRE Manager", roles)
        assert not sj.matches_roles("Director of SRE", roles)
        assert not sj.matches_roles("SRE Bootcamp Lead", roles)
        assert sj.matches_roles("Senior SRE", roles)

    def test_only_exclusions_no_includes_still_uses_safety_nets(self):
        """With only exclusions configured, Tier 2/2b still fire for
        non-excluded titles."""
        assert sj.matches_roles("Senior SRE", ["!Manager"])
        assert not sj.matches_roles("Senior SRE Manager", ["!Manager"])


def test_compress_skips_wildcards_and_exclusions():
    terms = sj._compress_search_terms([
        "SRE",
        "Senior * Engineer",
        "!Manager",
        "!*Bootcamp*",
        "Platform Engineer",
    ])
    assert "SRE" in terms
    assert "Platform Engineer" in terms
    assert not any("*" in t for t in terms)
    assert not any(t.startswith("!") for t in terms)
    assert not any("bootcamp" in t.lower() for t in terms)
