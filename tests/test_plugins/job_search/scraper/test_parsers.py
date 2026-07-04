"""Tests for scraper HTML and JSON-LD parsers.

Fixtures are small, realistic inline snippets that represent the shapes each
parser is documented to handle. Not exhaustive — catches regressions on the
documented invariants (selector classes, salary prefix patterns, JSON-LD shape).
"""

from __future__ import annotations

from daily_driver.plugins.job_search.scraper.parsing import (
    comp_from_text,
    parse_jsonld_jobposting,
)

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


# ---------------------------------------------------------------------------
# comp_from_text
# ---------------------------------------------------------------------------


class TestCompFromText:
    def test_anchored_range_with_qualifiers(self) -> None:
        """Real Greenhouse pay-transparency prose (trueanomalyinc, 2026-07-04):
        the anchor and the amount are separated by a level qualifier."""
        text = (
            "What you bring... California Base Salary: Senior: "
            "$150,000\u2013$205,000, Staff: $170,000 to $245,000. Equity too."
        )
        assert comp_from_text(text) == "$150,000\u2013$205,000/yr"

    def test_to_separated_range(self) -> None:
        text = "The annual salary range for this role is $170,000 to $245,000."
        assert comp_from_text(text) == "$170,000\u2013$245,000/yr"

    def test_k_shorthand_and_single_value(self) -> None:
        assert comp_from_text("Annual Salary: $150K") == "$150,000/yr"

    def test_non_usd_symbols_and_currency_code(self) -> None:
        assert (
            comp_from_text("Base pay range: CA$140,000 - CA$180,000 CAD")
            == "CA$140,000\u2013CA$180,000/yr CAD"
        )
        assert comp_from_text("Salary: \u00a370,000-\u00a385,000") == (
            "\u00a370,000\u2013\u00a385,000/yr"
        )

    def test_unanchored_amount_rejected(self) -> None:
        """A dollar figure with no salary word nearby must not become comp --
        revenue and funding figures are the classic false positive."""
        assert comp_from_text("We just raised $150,000,000 in Series C.") == ""

    def test_small_figures_rejected_even_when_anchored(self) -> None:
        """Bonuses, stipends, and hourly rates are below the annual floor;
        wrong comp is worse than blank."""
        assert comp_from_text("Compensation includes a $5,000 signing bonus.") == ""
        assert comp_from_text("Pay: $55 - $65 per hour") == ""

    def test_anchor_too_far_away_rejected(self) -> None:
        filler = "x" * 120
        assert comp_from_text(f"salary {filler} $150,000") == ""

    def test_inverted_range_rejected(self) -> None:
        assert comp_from_text("Salary: $205,000 - $150,000") == ""

    def test_entity_encoded_pay_range_block(self) -> None:
        """Greenhouse ships entity-encoded HTML; the cached description keeps
        literal tags and an &mdash; divider between the range's two spans. The
        extractor must read the full range, not truncate to the low figure
        (observed live on 836 cached descriptions, 2026-07-04)."""
        text = (
            '<div class="title">Base Pay Range</div><div class="pay-range">'
            '<span>$137,275</span><span class="divider">&mdash;</span>'
            "<span>$190,000 USD</span></div>"
        )
        assert comp_from_text(text) == "$137,275\u2013$190,000/yr USD"

    def test_business_figures_rejected(self) -> None:
        """'paying customers... $100M ARR' must not anchor: 'pay' is word-
        bounded, and figures above the annual ceiling are business numbers
        (both observed live in a cached Ashby description, 2026-07-04)."""
        assert comp_from_text("45% are paying customers. We hit $100M ARR.") == ""
        assert comp_from_text("Our compensation fund totals $50,000,000.") == ""

    def test_degenerate_equal_range_collapses(self) -> None:
        # Anthropic posts "$320,000 — $320,000"; one figure reads better.
        assert comp_from_text("Annual Salary: $320,000 — $320,000 USD") == (
            "$320,000/yr USD"
        )

    def test_non_annual_period_drops_the_yr_suffix(self) -> None:
        """A lump-sum/monthly figure keeps its faithful amount but must not be
        annualized (observed live: a 2-week program's lump sum)."""
        text = "Compensation: $11,500 - $15,500 (Estimated lump sum payment)"
        assert comp_from_text(text) == "$11,500\u2013$15,500"

    def test_one_time_payment_words_disqualify_the_match(self) -> None:
        """The wrong-figure class the anchors alone cannot catch: a one-time
        amount inside an anchored window. The disqualifier skips it, and a
        later clean salary range still wins."""
        assert (
            comp_from_text("Total compensation includes equity valued at $500,000.")
            == ""
        )
        assert comp_from_text("Compensation perks: 401(k) match up to $15,000.") == ""
        assert (
            comp_from_text(
                "Relocation salary support: we reimburse up to $20,000. "
                "The base pay range for this role is $150,000-$180,000."
            )
            == "$150,000\u2013$180,000/yr"
        )

    def test_bare_pay_no_longer_anchors(self) -> None:
        """'we will pay up to $20,000' must not read as annual comp; only the
        stronger salary/compensation/base-pay/pay-range anchors count."""
        assert comp_from_text("We will pay up to $20,000 for the project.") == ""

    def test_frequency_adverbs_do_not_strip_the_annual_suffix(self) -> None:
        # "weekly standups" after the figure is not a pay period.
        assert (
            comp_from_text(
                "Salary: $150,000. We hold weekly standups and monthly reviews."
            )
            == "$150,000/yr"
        )

    def test_stray_angle_bracket_does_not_swallow_the_salary(self) -> None:
        """A bare '<' in prose must not be treated as an open tag that eats
        everything up to a later real closing tag (salary included)."""
        assert (
            comp_from_text(
                "For roles < director level the salary is $150,000 - $200,000. "
                "<a>apply</a>"
            )
            == "$150,000\u2013$200,000/yr"
        )

    def test_empty_and_plain_text(self) -> None:
        assert comp_from_text("") == ""
        assert comp_from_text("A great role on a great team.") == ""
