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
