"""Tests for JSON-LD job-detail parser and enrich_job_details network pass."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest
import requests

import scrape_jobs as sj


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _html_with_jsonld(jsonld_obj) -> str:
    """Wrap a JSON-LD object (or list) in a minimal HTML page."""
    body = json.dumps(jsonld_obj)
    return (
        "<html><head><title>x</title>"
        f'<script type="application/ld+json">{body}</script>'
        "</head><body></body></html>"
    )


FABLE_JOBPOSTING = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "title": "Senior Site Reliability Engineer (SRE)",
    "datePosted": "2026-04-10T08:00:00+00:00",
    "employmentType": "FULL_TIME",
    "hiringOrganization": {"@type": "Organization", "name": "Fable"},
    "baseSalary": {
        "@type": "MonetaryAmount",
        "currency": "CAD",
        "value": {
            "@type": "QuantitativeValue",
            "minValue": 130000,
            "maxValue": 150000,
            "unitText": "YEAR",
        },
    },
}


# ── parse_jsonld_jobposting ──────────────────────────────────────────────────


class TestParseJsonldJobposting:
    def test_parses_salary_range_cad(self):
        html = _html_with_jsonld(FABLE_JOBPOSTING)
        result = sj.parse_jsonld_jobposting(html)
        assert result["comp"] == "CA$130,000\u2013150,000/yr"
        assert result["posted_date"] == "2026-04-10"
        assert result["employment_type"] == "FULL_TIME"

    def test_parses_single_value_usd(self):
        obj = {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "baseSalary": {
                "@type": "MonetaryAmount",
                "currency": "USD",
                "value": {
                    "@type": "QuantitativeValue",
                    "value": 140000,
                    "unitText": "YEAR",
                },
            },
        }
        result = sj.parse_jsonld_jobposting(_html_with_jsonld(obj))
        assert result["comp"] == "$140,000/yr"

    def test_currency_other_uses_code_prefix(self):
        # AUD has no entry in _COMP_CURRENCY_PREFIX → falls through to "AUD ".
        obj = {
            "@type": "JobPosting",
            "baseSalary": {
                "currency": "AUD",
                "value": {"minValue": 90000, "maxValue": 110000, "unitText": "YEAR"},
            },
        }
        result = sj.parse_jsonld_jobposting(_html_with_jsonld(obj))
        assert result["comp"] == "AUD 90,000\u2013110,000/yr"

    def test_hourly_unit_text(self):
        obj = {
            "@type": "JobPosting",
            "baseSalary": {
                "currency": "USD",
                "value": {"value": 75, "unitText": "HOUR"},
            },
        }
        result = sj.parse_jsonld_jobposting(_html_with_jsonld(obj))
        assert result["comp"] == "$75/hr"

    def test_missing_basesalary_omits_comp_key(self):
        obj = {
            "@type": "JobPosting",
            "datePosted": "2026-04-09T10:00:00Z",
        }
        result = sj.parse_jsonld_jobposting(_html_with_jsonld(obj))
        # Caller uses .get("comp") truthy-check; an absent key and "" both
        # behave the same downstream, but we prefer absence so enrich_job_details
        # doesn't accidentally overwrite a pre-existing comp with "".
        assert result.get("comp", "") == ""
        assert result["posted_date"] == "2026-04-09"

    def test_unparseable_basesalary_omits_comp_key(self):
        # A bare string baseSalary ("Competitive") should not yield a comp value.
        # We specifically do NOT want to write "" into the output and have it
        # silently clobber a value the caller already had.
        obj = {
            "@type": "JobPosting",
            "baseSalary": "Competitive",
        }
        result = sj.parse_jsonld_jobposting(_html_with_jsonld(obj))
        assert "comp" not in result

    def test_no_jsonld_script_returns_empty_dict(self):
        result = sj.parse_jsonld_jobposting("<html><body>nothing here</body></html>")
        assert result == {}

    def test_malformed_jsonld_returns_empty_dict(self):
        html = (
            "<html><head>"
            '<script type="application/ld+json">{not valid json</script>'
            "</head></html>"
        )
        result = sj.parse_jsonld_jobposting(html)
        assert result == {}

    def test_jsonld_in_array_finds_jobposting(self):
        # schema.org allows multiple JSON-LD blocks; the JobPosting may be in a list.
        arr = [
            {"@type": "BreadcrumbList", "itemListElement": []},
            FABLE_JOBPOSTING,
        ]
        html = _html_with_jsonld(arr)
        result = sj.parse_jsonld_jobposting(html)
        assert result["comp"] == "CA$130,000\u2013150,000/yr"

    def test_multiple_script_blocks_picks_jobposting(self):
        # Two separate <script> tags, only the second is JobPosting.
        body = (
            '<script type="application/ld+json">'
            '{"@type": "Organization", "name": "x"}</script>'
            '<script type="application/ld+json">'
            f"{json.dumps(FABLE_JOBPOSTING)}</script>"
        )
        html = f"<html><head>{body}</head></html>"
        result = sj.parse_jsonld_jobposting(html)
        assert result["comp"] == "CA$130,000\u2013150,000/yr"

    def test_no_jobposting_type_returns_empty_dict(self):
        obj = {"@type": "Article", "headline": "Not a job"}
        result = sj.parse_jsonld_jobposting(_html_with_jsonld(obj))
        assert result == {}


# ── enrich_job_details ───────────────────────────────────────────────────────


def _mock_response(text: str, status: int = 200) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.text = text
    resp.status_code = status
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status} error")
    return resp


@pytest.fixture
def fable_html() -> str:
    return _html_with_jsonld(FABLE_JOBPOSTING)


class TestEnrichJobDetails:
    def test_populates_comp_for_each_job(self, fable_html, config):
        jobs = [
            {"company": "Fable", "role": "SRE", "url": "https://x.com/a"},
            {"company": "Fable", "role": "DevOps", "url": "https://x.com/b"},
        ]
        with patch.object(sj.requests, "get", return_value=_mock_response(fable_html)) as mock_get:
            sj.enrich_job_details(jobs, config)
        assert jobs[0]["comp"] == "CA$130,000\u2013150,000/yr"
        assert jobs[1]["comp"] == "CA$130,000\u2013150,000/yr"
        assert mock_get.call_count == 2

    def test_skips_jobs_without_url(self, config):
        jobs = [{"company": "X", "role": "SRE", "url": ""}]
        with patch.object(sj.requests, "get") as mock_get:
            sj.enrich_job_details(jobs, config)
        mock_get.assert_not_called()
        # No url → no comp key written (don't fabricate empty data)
        assert "comp" not in jobs[0]

    def test_does_not_overwrite_existing_comp(self, fable_html, config):
        jobs = [{"company": "X", "role": "SRE", "url": "https://x.com/a", "comp": "preset"}]
        with patch.object(sj.requests, "get", return_value=_mock_response(fable_html)) as mock_get:
            sj.enrich_job_details(jobs, config)
        assert jobs[0]["comp"] == "preset"
        mock_get.assert_not_called()

    def test_handles_request_timeout_gracefully(self, config):
        jobs = [{"company": "X", "role": "SRE", "url": "https://x.com/a"}]
        with patch.object(sj.requests, "get", side_effect=requests.Timeout("slow")):
            sj.enrich_job_details(jobs, config)
        # Failure → comp stays absent or empty, never raises
        assert jobs[0].get("comp", "") == ""

    def test_handles_http_error_gracefully(self, config):
        jobs = [{"company": "X", "role": "SRE", "url": "https://x.com/a"}]
        with patch.object(sj.requests, "get", return_value=_mock_response("forbidden", status=403)):
            sj.enrich_job_details(jobs, config)
        assert jobs[0].get("comp", "") == ""

    def test_caches_by_url(self, fable_html, config):
        url = "https://x.com/same"
        jobs = [
            {"company": "A", "role": "SRE", "url": url},
            {"company": "B", "role": "DevOps", "url": url},
        ]
        with patch.object(sj.requests, "get", return_value=_mock_response(fable_html)) as mock_get:
            sj.enrich_job_details(jobs, config)
        # Same URL should only be fetched once even though two jobs reference it
        assert mock_get.call_count == 1
        assert jobs[0]["comp"] == jobs[1]["comp"] == "CA$130,000\u2013150,000/yr"

    def test_zero_delay_skips_sleep(self, fable_html, config):
        cfg = {"job_search": {"scraper": {"detail_delay_seconds": 0.0}}}
        jobs = [
            {"company": "A", "role": "SRE", "url": "https://x.com/a"},
            {"company": "B", "role": "SRE", "url": "https://x.com/b"},
        ]
        with patch.object(sj.requests, "get", return_value=_mock_response(fable_html)):
            with patch.object(sj.time, "sleep") as mock_sleep:
                sj.enrich_job_details(jobs, cfg)
        # Delay 0 → sleep should never be called
        mock_sleep.assert_not_called()

    def test_positive_delay_sleeps_between_fetches(self, fable_html):
        # Regression guard: assert sleep is actually called with the configured
        # delay between the 1st and 2nd fetch. Without this, the delay logic
        # could be deleted entirely and the zero-delay test would still pass.
        cfg = {"job_search": {"scraper": {"detail_delay_seconds": 0.25}}}
        jobs = [
            {"company": "A", "role": "SRE", "url": "https://x.com/a"},
            {"company": "B", "role": "SRE", "url": "https://x.com/b"},
            {"company": "C", "role": "SRE", "url": "https://x.com/c"},
        ]
        with patch.object(sj.requests, "get", return_value=_mock_response(fable_html)):
            with patch.object(sj.time, "sleep") as mock_sleep:
                sj.enrich_job_details(jobs, cfg)
        # 3 fetches → 2 inter-fetch delays (not before the first)
        assert mock_sleep.call_count == 2
        for call in mock_sleep.call_args_list:
            assert call.args == (0.25,)


# ── parse_linkedin_html ──────────────────────────────────────────────────────


# Minimal fragment mirroring LinkedIn's real public markup as of 2026-04-10.
# The full /jobs/view/ page is ~300KB; we only capture the salary div + a
# non-salary decoy to guard against the parser matching on the wrong class.
LINKEDIN_FABLE_FRAGMENT = """
<html><body>
  <section class="top-card-layout">
    <h1>Senior Site Reliability Engineer (SRE)</h1>
    <div class="salary compensation__salary">
      CA$130,000.00/yr - CA$150,000.00/yr
    </div>
  </section>
  <aside class="similar-jobs">
    <div class="salary-info block my-0.5 mx-0">CA$90,000.00 - CA$135,000.00</div>
  </aside>
</body></html>
"""


class TestParseLinkedinHtml:
    def test_extracts_range_and_normalizes(self):
        result = sj.parse_linkedin_html(LINKEDIN_FABLE_FRAGMENT)
        assert result["comp"] == "CA$130,000\u2013150,000/yr"

    def test_extracts_single_value(self):
        html = (
            '<div class="salary compensation__salary">'
            "CA$140,000.00/yr"
            "</div>"
        )
        result = sj.parse_linkedin_html(html)
        assert result["comp"] == "CA$140,000/yr"

    def test_extracts_usd(self):
        html = (
            '<div class="salary compensation__salary">'
            "$180,000.00/yr - $220,000.00/yr"
            "</div>"
        )
        result = sj.parse_linkedin_html(html)
        assert result["comp"] == "$180,000\u2013220,000/yr"

    def test_hourly_rate(self):
        html = (
            '<div class="compensation__salary">$75.00/hr - $95.00/hr</div>'
        )
        result = sj.parse_linkedin_html(html)
        assert result["comp"] == "$75\u201395/hr"

    def test_no_salary_div_returns_empty(self):
        html = "<html><body><p>No comp disclosed here.</p></body></html>"
        result = sj.parse_linkedin_html(html)
        assert result == {}

    def test_ignores_similar_jobs_sidebar(self):
        # Only the compensation__salary div should be read, not similar-jobs
        # .salary-info sidebars that list other postings' ranges.
        result = sj.parse_linkedin_html(LINKEDIN_FABLE_FRAGMENT)
        assert "90,000" not in result["comp"]
        assert "135,000" not in result["comp"]


# ── enrich_job_details hostname dispatch ─────────────────────────────────────


class TestEnrichHostnameDispatch:
    def test_linkedin_url_uses_linkedin_parser(self, config):
        jobs = [{"company": "Fable", "role": "SRE",
                 "url": "https://ca.linkedin.com/jobs/view/123"}]
        with patch.object(
            sj.requests, "get",
            return_value=_mock_response(LINKEDIN_FABLE_FRAGMENT),
        ):
            sj.enrich_job_details(jobs, config)
        assert jobs[0]["comp"] == "CA$130,000\u2013150,000/yr"

    def test_non_linkedin_url_uses_jsonld_parser(self, fable_html, config):
        jobs = [{"company": "Acme", "role": "SRE",
                 "url": "https://boards.greenhouse.io/acme/jobs/123"}]
        with patch.object(
            sj.requests, "get", return_value=_mock_response(fable_html),
        ):
            sj.enrich_job_details(jobs, config)
        # fable_html contains JSON-LD → JSON-LD parser works
        assert jobs[0]["comp"] == "CA$130,000\u2013150,000/yr"

    def test_linkedin_parser_does_not_run_jsonld_path(self, config):
        # Give LinkedIn-shaped HTML that also accidentally contains a JSON-LD
        # block with different comp — the LinkedIn parser should win.
        mixed = LINKEDIN_FABLE_FRAGMENT + _html_with_jsonld({
            "@type": "JobPosting",
            "baseSalary": {
                "currency": "USD",
                "value": {"value": 999999, "unitText": "YEAR"},
            },
        })
        jobs = [{"company": "Fable", "role": "SRE",
                 "url": "https://www.linkedin.com/jobs/view/123"}]
        with patch.object(
            sj.requests, "get", return_value=_mock_response(mixed),
        ):
            sj.enrich_job_details(jobs, config)
        assert "130,000" in jobs[0]["comp"]
        assert "999,999" not in jobs[0]["comp"]


# ── parse_greenhouse_html ────────────────────────────────────────────────────


class TestParseGreenhouseHtml:
    def test_greenhouse_anthropic_labeled_salary(self):
        html = """
        <html><body>
        <p>Annual Salary: $350,000 - $500,000 USD</p>
        </body></html>
        """
        out = sj.parse_greenhouse_html(html)
        assert "350,000" in out["comp"]
        assert "500,000" in out["comp"]
        assert "USD" in out["comp"]

    def test_greenhouse_dispatch_falls_through_when_no_jsonld(self):
        html = "<p>Annual Salary: $350,000 - $500,000 USD</p>"
        out = sj._parse_detail_page(html, "https://job-boards.greenhouse.io/anthropic/jobs/1")
        assert "350,000" in out["comp"]

    def test_greenhouse_dispatch_prefers_jsonld_when_present(self):
        html = """
        <script type="application/ld+json">
        {"@type": "JobPosting", "baseSalary": {
            "currency": "USD",
            "value": {"minValue": 400000, "maxValue": 600000, "unitText": "YEAR"}
        }}
        </script>
        <p>Annual Salary: $100 - $200 USD</p>
        """
        out = sj._parse_detail_page(html, "https://job-boards.greenhouse.io/x/jobs/1")
        assert "400,000" in out["comp"]
        assert "100" not in out["comp"]

    def test_greenhouse_returns_empty_when_no_salary_text(self):
        html = "<p>No salary disclosed.</p>"
        assert sj.parse_greenhouse_html(html) == {}


# ── _clean_linkedin_comp loose regex ────────────────────────────────────────


@pytest.mark.parametrize("raw,expected_substring", [
    ("$144,000\u2014$200,000 CAD", "144,000"),
    ("$144,000\u2014$200,000 CAD", "200,000"),
    ("$144,000\u2014$200,000 CAD", "CAD"),
    ("$144,000\u2014$200,000", "144,000"),
    ("$144,000\u2013$200,000 USD", "USD"),
    ("$100,000 - $150,000", "100,000"),
])
def test_linkedin_loose_range_formats(raw, expected_substring):
    assert expected_substring in sj._clean_linkedin_comp(raw)


def test_linkedin_strict_format_still_wins():
    out = sj._clean_linkedin_comp("CA$130,000.00/yr - CA$150,000.00/yr")
    assert out == "CA$130,000\u2013150,000/yr"


def test_linkedin_em_dash_strict_format():
    # Strict regex now accepts em-dash separator
    out = sj._clean_linkedin_comp("CA$130,000.00/yr\u2014CA$150,000.00/yr")
    assert "130,000" in out and "150,000" in out


def test_linkedin_unparseable_returns_empty():
    assert sj._clean_linkedin_comp("Competitive salary") == ""


# ── append_jobs CSV mapping ──────────────────────────────────────────────────


class TestAppendJobsCompColumn:
    def test_comp_field_writes_to_comp_column(self, empty_csv):
        from fixtures import CSV_HEADER
        import csv as _csv

        jobs = [
            {
                "company": "Fable",
                "role": "Senior SRE",
                "location": "Vancouver",
                "url": "https://x.com/job",
                "source": "LinkedIn",
                "comp": "CA$130,000\u2013150,000/yr",
            }
        ]
        sj.append_jobs(empty_csv, jobs, CSV_HEADER)
        with open(empty_csv) as f:
            rows = list(_csv.reader(f))
        comp_idx = CSV_HEADER.index("Comp")
        assert rows[1][comp_idx] == "CA$130,000\u2013150,000/yr"

    def test_missing_comp_writes_blank(self, empty_csv):
        from fixtures import CSV_HEADER
        import csv as _csv

        jobs = [{"company": "X", "role": "SRE", "url": "https://x.com/a", "source": "HN"}]
        sj.append_jobs(empty_csv, jobs, CSV_HEADER)
        with open(empty_csv) as f:
            rows = list(_csv.reader(f))
        comp_idx = CSV_HEADER.index("Comp")
        assert rows[1][comp_idx] == ""


# ── enrich_company_descriptions budget ──────────────────────────────────────


class TestEnrichCompanyDescriptionsBudget:
    def test_budget_limits_claude_calls(self):
        # 5 companies, budget=2 — only 2 claude subprocess calls should be made.
        jobs = [
            {"company": f"Co{i}", "role": "SRE", "url": f"https://x.com/{i}"}
            for i in range(5)
        ]
        config = {"job_search": {"scraper": {"max_enrich_companies": 2}}}

        def _fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "Makes software products."
            return result

        with patch("scrape_jobs.shutil.which", return_value="/usr/bin/claude"):
            with patch("scrape_jobs.subprocess.run", side_effect=_fake_run) as mock_run:
                sj.enrich_company_descriptions(jobs, config)

        assert mock_run.call_count == 2

    def test_populates_gd_rating_when_enabled(self):
        jobs = [{"company": "Stripe", "role": "SRE", "url": "https://x.com/1"}]
        config = {"job_search": {"scraper": {"enrich_gd_rating": True}}}

        def _fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "Online payment processing infrastructure.\n4.2"
            return result

        with patch("scrape_jobs.shutil.which", return_value="/usr/bin/claude"):
            with patch("scrape_jobs.subprocess.run", side_effect=_fake_run):
                sj.enrich_company_descriptions(jobs, config)

        assert jobs[0]["product"] == "Online payment processing infrastructure."
        assert jobs[0]["gd_rating"] == "4.2"

    def test_gd_rating_unknown_accepted(self):
        jobs = [{"company": "Stealth", "role": "SRE", "url": "https://x.com/1"}]
        config = {"job_search": {"scraper": {}}}

        def _fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "Pre-launch startup.\nunknown"
            return result

        with patch("scrape_jobs.shutil.which", return_value="/usr/bin/claude"):
            with patch("scrape_jobs.subprocess.run", side_effect=_fake_run):
                sj.enrich_company_descriptions(jobs, config)

        assert jobs[0]["gd_rating"] == "unknown"

    def test_gd_rating_skipped_when_disabled(self):
        jobs = [{"company": "Acme", "role": "SRE", "url": "https://x.com/1"}]
        config = {"job_search": {"scraper": {"enrich_gd_rating": False}}}

        def _fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "Makes widgets."
            return result

        with patch("scrape_jobs.shutil.which", return_value="/usr/bin/claude"):
            with patch("scrape_jobs.subprocess.run", side_effect=_fake_run):
                sj.enrich_company_descriptions(jobs, config)

        assert jobs[0]["product"] == "Makes widgets."
        assert not jobs[0].get("gd_rating")


# ── enrich_fit ────────────────────────────────────────────────────────────────


class TestEnrichFit:
    def _run_with_mock(self, jobs, config=None, stdout="7"):
        if config is None:
            config = {"job_search": {"scraper": {}}, "output_dir": "/tmp"}

        def _fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = stdout
            return result

        with patch("scrape_jobs.shutil.which", return_value="/usr/bin/claude"):
            with patch("scrape_jobs.subprocess.run", side_effect=_fake_run) as mock_run:
                with patch("scrape_jobs.resolve_output_dir", return_value=MagicMock(**{"__truediv__": lambda self, x: MagicMock(**{"read_text.return_value": "| 1 - Ideal | Vancouver, BC |"})})):
                    sj.enrich_fit(jobs, config)
        return mock_run

    def test_populates_fit_for_job(self):
        jobs = [{"company": "Acme", "role": "SRE", "location": "Vancouver", "url": "https://x.com/1"}]
        self._run_with_mock(jobs)
        assert jobs[0]["fit"] == "7/10"

    def test_parses_score_with_slash(self):
        jobs = [{"company": "Acme", "role": "SRE", "location": "Remote", "url": "https://x.com/1"}]
        self._run_with_mock(jobs, stdout="8/10")
        assert jobs[0]["fit"] == "8/10"

    def test_budget_limits_calls(self):
        jobs = [
            {"company": f"Co{i}", "role": "SRE", "location": "Remote", "url": f"https://x.com/{i}"}
            for i in range(5)
        ]
        config = {"job_search": {"scraper": {"max_enrich_fit": 2}}, "output_dir": "/tmp"}
        mock_run = self._run_with_mock(jobs, config)
        assert mock_run.call_count == 2

    def test_skips_jobs_with_existing_fit(self):
        jobs = [{"company": "Acme", "role": "SRE", "location": "Remote", "url": "https://x.com/1", "fit": "9/10"}]
        mock_run = self._run_with_mock(jobs)
        assert mock_run.call_count == 0

    def test_disabled_via_config(self):
        jobs = [{"company": "Acme", "role": "SRE", "location": "Remote", "url": "https://x.com/1"}]
        config = {"job_search": {"scraper": {"enrich_fit": False}}, "output_dir": "/tmp"}

        with patch("scrape_jobs.shutil.which", return_value="/usr/bin/claude"):
            with patch("scrape_jobs.subprocess.run") as mock_run:
                sj.enrich_fit(jobs, config)
        assert mock_run.call_count == 0

    def test_claude_not_on_path(self):
        jobs = [{"company": "Acme", "role": "SRE", "location": "Remote", "url": "https://x.com/1"}]
        with patch("scrape_jobs.shutil.which", return_value=None):
            with patch("scrape_jobs.subprocess.run") as mock_run:
                sj.enrich_fit(jobs, {"job_search": {"scraper": {}}, "output_dir": "/tmp"})
        assert mock_run.call_count == 0
        assert not jobs[0].get("fit")

    def test_timeout_leaves_fit_absent(self):
        import subprocess
        jobs = [{"company": "Acme", "role": "SRE", "location": "Remote", "url": "https://x.com/1"}]
        config = {"job_search": {"scraper": {}}, "output_dir": "/tmp"}

        with patch("scrape_jobs.shutil.which", return_value="/usr/bin/claude"):
            with patch("scrape_jobs.subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 15)):
                with patch("scrape_jobs.resolve_output_dir", return_value=MagicMock(**{"__truediv__": lambda self, x: MagicMock(**{"read_text.return_value": ""})})):
                    sj.enrich_fit(jobs, config)
        assert not jobs[0].get("fit")


# ── Description text extraction ─────────────────────────────────────────────


class TestDescriptionTextExtraction:
    def test_jsonld_extracts_description_text(self):
        posting = {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "SRE",
            "description": "<p>Build <b>distributed</b> systems.</p>",
        }
        result = sj.parse_jsonld_jobposting(_html_with_jsonld(posting))
        assert result["description_text"] == "Build distributed systems."

    def test_linkedin_html_extracts_description_text(self):
        html = (
            "<html><body>"
            '<div class="show-more-less-html__markup">Kubernetes, Go, remote US-only</div>'
            "</body></html>"
        )
        result = sj.parse_linkedin_html(html)
        assert result["description_text"] == "Kubernetes, Go, remote US-only"

    def test_linkedin_html_fallback_description_div(self):
        html = (
            "<html><body>"
            '<div class="description__text">Python, AWS, hybrid</div>'
            "</body></html>"
        )
        result = sj.parse_linkedin_html(html)
        assert result["description_text"] == "Python, AWS, hybrid"

    def test_greenhouse_html_extracts_description_text(self):
        html = (
            "<html><body>"
            '<div id="content">Terraform, remote Canada OK, Series B</div>'
            "</body></html>"
        )
        result = sj.parse_greenhouse_html(html)
        assert result["description_text"] == "Terraform, remote Canada OK, Series B"

    def test_greenhouse_html_fallback_job_description(self):
        html = (
            "<html><body>"
            '<div class="job-description">GCP, on-site SF</div>'
            "</body></html>"
        )
        result = sj.parse_greenhouse_html(html)
        assert result["description_text"] == "GCP, on-site SF"

    def test_description_text_stored_on_job(self):
        posting = {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "SRE",
            "description": "<p>Stack: K8s, Go</p>",
        }
        html = _html_with_jsonld(posting)
        resp = MagicMock(spec=requests.Response)
        resp.text = html
        resp.status_code = 200
        resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=resp):
            jobs = [{"company": "X", "role": "SRE", "url": "https://example.com/1"}]
            sj.enrich_job_details(jobs, {})
        assert jobs[0]["description_text"] == "Stack: K8s, Go"


# ── enrich_notes ────────────────────────────────────────────────────────────


class TestEnrichNotes:
    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch("subprocess.run")
    def test_populates_notes_with_description(self, mock_run, _which):
        mock_run.return_value = MagicMock(returncode=0, stdout="K8s, Go, remote US-only, no red flags\n")
        jobs = [{"company": "Acme", "role": "SRE", "location": "Remote",
                 "description_text": "Build K8s platform using Go"}]
        sj.enrich_notes(jobs, None)
        assert jobs[0]["notes"] == "K8s, Go, remote US-only, no red flags"
        # Prompt should include description text
        prompt_arg = mock_run.call_args[0][0][2]
        assert "Build K8s platform using Go" in prompt_arg

    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch("subprocess.run")
    def test_populates_notes_without_description(self, mock_run, _which):
        mock_run.return_value = MagicMock(returncode=0, stdout="Infra platform, likely K8s/AWS\n")
        jobs = [{"company": "Acme", "role": "SRE", "location": "Remote",
                 "product": "cloud IDE"}]
        sj.enrich_notes(jobs, None)
        assert jobs[0]["notes"] == "Infra platform, likely K8s/AWS"
        prompt_arg = mock_run.call_args[0][0][2]
        assert "(cloud IDE)" in prompt_arg

    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch("subprocess.run")
    def test_skips_existing_notes(self, mock_run, _which):
        jobs = [{"company": "Acme", "role": "SRE", "location": "Remote",
                 "notes": "already filled"}]
        sj.enrich_notes(jobs, None)
        mock_run.assert_not_called()

    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch("subprocess.run")
    def test_budget_cap(self, mock_run, _which):
        mock_run.return_value = MagicMock(returncode=0, stdout="summary\n")
        jobs = [{"company": f"Co{i}", "role": "SRE", "location": "Remote"} for i in range(5)]
        sj.enrich_notes(jobs, {"job_search": {"scraper": {"max_enrich_notes": 2}}})
        assert mock_run.call_count == 2

    @patch("shutil.which", return_value=None)
    def test_skips_when_no_claude(self, _which):
        jobs = [{"company": "Acme", "role": "SRE", "location": "Remote"}]
        sj.enrich_notes(jobs, None)
        assert "notes" not in jobs[0]

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_disabled_via_config(self, _which):
        jobs = [{"company": "Acme", "role": "SRE", "location": "Remote"}]
        sj.enrich_notes(jobs, {"job_search": {"scraper": {"enrich_notes": False}}})
        assert "notes" not in jobs[0]

    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=15))
    def test_timeout_handled(self, _run, _which):
        jobs = [{"company": "Acme", "role": "SRE", "location": "Remote"}]
        sj.enrich_notes(jobs, None)
        assert "notes" not in jobs[0]


class TestAppendJobsNotes:
    def test_notes_written_to_csv(self, empty_csv):
        from fixtures import CSV_HEADER
        import csv as _csv

        jobs = [{
            "company": "Acme", "role": "SRE", "url": "https://x.com/1",
            "source": "HN", "notes": "K8s, Go, remote US-only",
        }]
        sj.append_jobs(empty_csv, jobs, CSV_HEADER)
        with open(empty_csv) as f:
            rows = list(_csv.reader(f))
        notes_idx = CSV_HEADER.index("Notes")
        assert rows[1][notes_idx] == "K8s, Go, remote US-only"


class TestAppendJobsFitGDRating:
    def test_fit_and_gd_rating_write_to_csv(self, empty_csv):
        from fixtures import CSV_HEADER
        import csv as _csv

        jobs = [{
            "company": "Acme", "role": "SRE", "url": "https://x.com/1",
            "source": "HN", "fit": "8/10", "gd_rating": "4.1",
        }]
        sj.append_jobs(empty_csv, jobs, CSV_HEADER)
        with open(empty_csv) as f:
            rows = list(_csv.reader(f))
        fit_idx = CSV_HEADER.index("Fit")
        gd_idx = CSV_HEADER.index("GD Rating")
        assert rows[1][fit_idx] == "8/10"
        assert rows[1][gd_idx] == "4.1"
