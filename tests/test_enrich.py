"""Tests for JSON-LD job-detail parser and enrich_job_details network pass."""

import json
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
        sj.append_jobs(empty_csv, jobs, CSV_HEADER, next_num=1)
        with open(empty_csv) as f:
            rows = list(_csv.reader(f))
        comp_idx = CSV_HEADER.index("Comp")
        assert rows[1][comp_idx] == "CA$130,000\u2013150,000/yr"

    def test_missing_comp_writes_blank(self, empty_csv):
        from fixtures import CSV_HEADER
        import csv as _csv

        jobs = [{"company": "X", "role": "SRE", "url": "https://x.com/a", "source": "HN"}]
        sj.append_jobs(empty_csv, jobs, CSV_HEADER, next_num=1)
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
