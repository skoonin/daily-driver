"""Tests for scraper HTML and JSON-LD parsers.

Fixtures are small, realistic inline snippets that represent the shapes each
parser is documented to handle. Not exhaustive — catches regressions on the
documented invariants (selector classes, salary prefix patterns, JSON-LD shape).
"""

from __future__ import annotations

from daily_driver.scraper._impl import (
    parse_greenhouse_html,
    parse_jsonld_jobposting,
    parse_linkedin_html,
)

# ---------------------------------------------------------------------------
# parse_linkedin_html
# ---------------------------------------------------------------------------


class TestLinkedInParser:
    def test_extracts_comp_from_compensation_salary_div(self) -> None:
        html = """
        <html><body>
        <div class="compensation__salary">$150,000.00/yr - $200,000.00/yr</div>
        </body></html>
        """
        result = parse_linkedin_html(html)
        assert "comp" in result
        assert "$150" in result["comp"]

    def test_extracts_description_text(self) -> None:
        html = """
        <html><body>
        <div class="show-more-less-html__markup">We are hiring an SRE.</div>
        </body></html>
        """
        result = parse_linkedin_html(html)
        assert result["description_text"] == "We are hiring an SRE."

    def test_returns_empty_when_no_compensation_div(self) -> None:
        html = "<html><body><p>No comp here.</p></body></html>"
        result = parse_linkedin_html(html)
        assert "comp" not in result

    def test_empty_string_returns_empty_dict(self) -> None:
        assert parse_linkedin_html("") == {}

    def test_ignores_similar_jobs_salary_info_class(self) -> None:
        """`.salary-info` is a sidebar class; only `.compensation__salary` counts."""
        html = """
        <html><body>
        <div class="salary-info">$50k</div>
        </body></html>
        """
        result = parse_linkedin_html(html)
        assert "comp" not in result


# ---------------------------------------------------------------------------
# parse_greenhouse_html
# ---------------------------------------------------------------------------


class TestGreenhouseParser:
    def test_extracts_annual_salary_prefix(self) -> None:
        html = """
        <html><body>
        <div id="content">
        <p>Annual Salary: $150,000 - $200,000 USD</p>
        </div>
        </body></html>
        """
        result = parse_greenhouse_html(html)
        assert "comp" in result
        assert "150,000" in result["comp"]
        assert "200,000" in result["comp"]

    def test_extracts_k_shorthand(self) -> None:
        """Anthropic's board posts '$150K-$200K' shorthand."""
        html = """
        <html><body>
        <div id="content">
        <p>Annual Salary: $150K - $200K USD</p>
        </div>
        </body></html>
        """
        result = parse_greenhouse_html(html)
        assert "comp" in result
        assert "150,000" in result["comp"] or "150" in result["comp"]

    def test_captures_description_text(self) -> None:
        html = """
        <html><body>
        <div id="content">Role description here.</div>
        </body></html>
        """
        result = parse_greenhouse_html(html)
        assert "description_text" in result
        assert "Role description" in result["description_text"]

    def test_ignores_unrelated_dollar_amounts(self) -> None:
        """Without the 'Annual Salary:' prefix, dollar amounts elsewhere are ignored."""
        html = """
        <html><body>
        <div id="content"><p>We have a $50M funding round.</p></div>
        </body></html>
        """
        result = parse_greenhouse_html(html)
        assert "comp" not in result

    def test_empty_string_returns_empty_dict(self) -> None:
        assert parse_greenhouse_html("") == {}


# ---------------------------------------------------------------------------
# parse_jsonld_jobposting
# ---------------------------------------------------------------------------


class TestJsonLdParser:
    def test_extracts_salary_from_jobposting(self) -> None:
        html = """
        <html><head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org/",
          "@type": "JobPosting",
          "title": "SRE",
          "datePosted": "2026-04-10",
          "employmentType": "FULL_TIME",
          "baseSalary": {
            "@type": "MonetaryAmount",
            "currency": "USD",
            "value": {
              "@type": "QuantitativeValue",
              "minValue": 150000,
              "maxValue": 200000,
              "unitText": "YEAR"
            }
          }
        }
        </script>
        </head><body></body></html>
        """
        result = parse_jsonld_jobposting(html)
        assert "comp" in result
        assert result.get("posted_date") == "2026-04-10"
        assert result.get("employment_type") == "FULL_TIME"

    def test_returns_empty_when_no_jsonld_block(self) -> None:
        html = "<html><body><p>No structured data.</p></body></html>"
        assert parse_jsonld_jobposting(html) == {}

    def test_tolerates_malformed_jsonld(self) -> None:
        """Truncated / invalid JSON is logged but doesn't raise."""
        html = """
        <html><head>
        <script type="application/ld+json">{"@type": "JobPost</script>
        </head></html>
        """
        assert parse_jsonld_jobposting(html) == {}

    def test_finds_jobposting_nested_in_graph(self) -> None:
        """JSON-LD may be wrapped in `@graph`."""
        html = """
        <html><head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org/",
          "@graph": [
            {"@type": "Organization", "name": "Acme"},
            {"@type": "JobPosting", "title": "SRE", "datePosted": "2026-04-10"}
          ]
        }
        </script>
        </head></html>
        """
        result = parse_jsonld_jobposting(html)
        assert result.get("posted_date") == "2026-04-10"

    def test_empty_string_returns_empty_dict(self) -> None:
        assert parse_jsonld_jobposting("") == {}
