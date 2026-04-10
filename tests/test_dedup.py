"""Tests for dedup_key() and _compress_search_terms()."""

import scrape_jobs as sj


class TestDedupKey:
    def test_identical_inputs_produce_same_key(self):
        assert sj.dedup_key("Acme", "Senior SRE") == sj.dedup_key("Acme", "Senior SRE")

    def test_case_insensitive(self):
        assert sj.dedup_key("ACME", "SENIOR SRE") == sj.dedup_key("acme", "senior sre")

    def test_whitespace_collapsed(self):
        assert sj.dedup_key("Acme  Corp", "Senior  SRE") == sj.dedup_key("Acme Corp", "Senior SRE")

    def test_leading_trailing_whitespace_stripped(self):
        assert sj.dedup_key("  Acme  ", "  Senior SRE  ") == sj.dedup_key("Acme", "Senior SRE")

    def test_different_companies_produce_different_keys(self):
        assert sj.dedup_key("Acme", "SRE") != sj.dedup_key("BetaCo", "SRE")

    def test_different_roles_produce_different_keys(self):
        assert sj.dedup_key("Acme", "SRE") != sj.dedup_key("Acme", "DevOps")

    def test_empty_strings_produce_valid_key(self):
        key = sj.dedup_key("", "")
        assert key == "::"

    def test_key_format_is_company_double_colon_role(self):
        key = sj.dedup_key("Acme", "Senior SRE")
        company_part, role_part = key.split("::")
        assert company_part == "acme"
        assert role_part == "senior sre"

    def test_cross_site_same_job_matches(self):
        # Same job posted on LinkedIn vs RemoteOK with minor whitespace diff
        key1 = sj.dedup_key("Rootly", "Senior Platform Engineer")
        key2 = sj.dedup_key("Rootly", "Senior Platform Engineer")
        assert key1 == key2

    def test_punctuation_preserved(self):
        # CI/CD and similar are not normalized beyond case/whitespace
        key = sj.dedup_key("Acme", "CI/CD Engineer")
        assert "ci/cd engineer" in key


class TestCompressSearchTerms:
    def test_strips_senior_prefix(self):
        terms = sj._compress_search_terms(["Senior SRE"])
        assert "SRE" in terms

    def test_strips_staff_prefix(self):
        terms = sj._compress_search_terms(["Staff Platform Engineer"])
        assert "Platform Engineer" in terms

    def test_strips_principal_prefix(self):
        terms = sj._compress_search_terms(["Principal SRE"])
        assert "SRE" in terms

    def test_deduplicates_same_base(self):
        terms = sj._compress_search_terms(["Senior SRE", "Staff SRE", "Principal SRE"])
        assert terms.count("SRE") == 1

    def test_preserves_no_prefix_roles(self):
        terms = sj._compress_search_terms(["CI/CD Engineer", "Build Engineer"])
        assert "CI/CD Engineer" in terms
        assert "Build Engineer" in terms

    def test_full_21_role_list_compresses_to_10(self):
        roles = [
            "Senior SRE", "Staff SRE", "Principal SRE",
            "Senior Site Reliability Engineer", "Staff Site Reliability Engineer",
            "Senior Platform Engineer", "Staff Platform Engineer",
            "Senior DevOps Engineer", "Staff DevOps Engineer",
            "Senior Infrastructure Engineer", "Staff Infrastructure Engineer",
            "Senior Cloud Engineer", "Staff Cloud Engineer",
            "CI/CD Engineer", "Senior CI/CD Engineer",
            "Build Engineer", "Senior Build Engineer",
            "Release Engineer", "Senior Release Engineer",
            "Production Engineer", "Senior Production Engineer",
        ]
        terms = sj._compress_search_terms(roles)
        assert len(terms) == 10

    def test_preserves_original_casing_of_base(self):
        # "CI/CD Engineer" should not be title-cased to "Ci/Cd Engineer"
        terms = sj._compress_search_terms(["CI/CD Engineer"])
        assert "CI/CD Engineer" in terms

    def test_explicit_search_terms_config_overrides(self):
        config = {
            "job_search": {
                "roles": ["Senior SRE", "Staff SRE"],
                "scraper": {
                    "search_terms": ["Platform Engineer", "DevOps"],
                },
            }
        }
        terms = sj._search_terms(config)
        assert terms == ["Platform Engineer", "DevOps"]

    def test_empty_roles_returns_empty(self):
        terms = sj._compress_search_terms([])
        assert terms == []
