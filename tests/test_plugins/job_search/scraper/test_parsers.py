"""Tests for scraper HTML and JSON-LD parsers.

Fixtures are small, realistic inline snippets that represent the shapes each
parser is documented to handle. Not exhaustive — catches regressions on the
documented invariants (selector classes, salary prefix patterns, JSON-LD shape).
"""

from __future__ import annotations

import pytest

from daily_driver.plugins.job_search.scraper.parsing import (
    _parse_detail_page,
    comp_from_text,
    parse_jsonld_jobposting,
    parse_linkedin_salary_card,
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
        # datePosted is deliberately ignored: nothing downstream reads it.
        assert "posted_date" not in result

    def test_no_jsonld_block_is_inconclusive(self) -> None:
        """A page with no JobPosting block proves nothing about the job (bot
        challenge, redesign, removed posting): raising makes the enricher
        treat it as a failed fetch instead of concluding pay is absent."""
        html = "<html><body><p>No structured data.</p></body></html>"
        with pytest.raises(ValueError):
            parse_jsonld_jobposting(html)

    def test_malformed_jsonld_is_inconclusive(self) -> None:
        """Truncated / invalid JSON yields no posting, so it raises too."""
        html = """
        <html><head>
        <script type="application/ld+json">{"@type": "JobPost</script>
        </head></html>
        """
        with pytest.raises(ValueError):
            parse_jsonld_jobposting(html)

    def test_posting_without_salary_is_conclusive(self) -> None:
        """A real JobPosting block without baseSalary means the poster listed
        no pay: {} (no raise), which permits the Not-listed mark."""
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@type": "JobPosting", "title": "SRE"}
        </script>
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
            {"@type": "JobPosting", "title": "SRE", "description": "On-call SRE role"}
          ]
        }
        </script>
        </head></html>
        """
        result = parse_jsonld_jobposting(html)
        assert result.get("description_text") == "On-call SRE role"

    def test_empty_string_is_inconclusive(self) -> None:
        with pytest.raises(ValueError):
            parse_jsonld_jobposting("")


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

    def test_markdown_escaped_range_with_decimals(self) -> None:
        """JobSpy/markdownify backslash-escapes LinkedIn descriptions; the
        escaped separator and decimals broke the range down to its low figure
        (observed live: Activision posting, 2026-07-24)."""
        text = (
            "The standard base pay range for this role is "
            "$100,220\\.00 \\- $197,758\\.00 CAD. These values reflect the "
            "expected annualized base pay range of new hires."
        )
        assert comp_from_text(text) == "$100,220\u2013$197,758/yr CAD"

    def test_markdown_escaped_range_without_decimals(self) -> None:
        # Observed live in cached LinkedIn descriptions: "$138,400\-$173,000".
        assert (
            comp_from_text("Base salary range: $138,400\\-$173,000")
            == "$138,400\u2013$173,000/yr"
        )

    def test_plain_decimal_range(self) -> None:
        assert (
            comp_from_text("Annual salary: $100,220.00 - $197,758.00 CAD")
            == "$100,220\u2013$197,758/yr CAD"
        )

    def test_decimal_k_shorthand(self) -> None:
        # "$150.5K" used to silently truncate to $150 (below floor, dropped).
        assert comp_from_text("Base salary: $150.5K") == "$150,500/yr"

    def test_prose_backslash_does_not_corrupt_a_clean_match(self) -> None:
        """Unescaping targets markdown punctuation only; an unrelated backslash
        elsewhere in the text must leave a clean range untouched."""
        assert (
            comp_from_text(
                "Path C:\\Users noted. The salary range is $120,000 - $150,000 USD."
            )
            == "$120,000\u2013$150,000/yr USD"
        )

    def test_empty_and_plain_text(self) -> None:
        assert comp_from_text("") == ""
        assert comp_from_text("A great role on a great team.") == ""


# ---------------------------------------------------------------------------
# parse_linkedin_salary_card
# ---------------------------------------------------------------------------


class TestLinkedInSalaryCard:
    # Structure observed live on a guest job page (Mojio posting, 2026-07-24):
    # an outer range div holding a heading and the inner salary value node.
    _CARD = (
        '<div class="compensation__salary-range"><h3>Base pay range</h3>'
        '<div class="salary compensation__salary">'
        "$100,000.00/yr - $110,000.00/yr</div></div>"
    )

    def test_extracts_range_from_card(self) -> None:
        html = f"<html><body>{self._CARD}</body></html>"
        assert parse_linkedin_salary_card(html) == {"comp": "$100,000–$110,000/yr"}

    def test_bare_inner_value_node_still_parses(self) -> None:
        # No wrapper heading: the parser supplies the anchor itself.
        html = (
            '<div class="salary compensation__salary">'
            "CA$133,000.00/yr - CA$151,000.00/yr</div>"
        )
        assert parse_linkedin_salary_card(html) == {"comp": "CA$133,000–CA$151,000/yr"}

    def test_similar_jobs_rail_cards_ignored(self) -> None:
        """Rail cards use different class names and must not leak into the
        viewed job's comp."""
        html = (
            '<div class="top-card-layout__title">SRE</div>'
            '<div class="main-job-card__salary-info">$120,000 - $130,000</div>'
            '<div class="aside-job-card__salary-info">$150,000 - $195,000</div>'
        )
        assert parse_linkedin_salary_card(html) == {}

    def test_hourly_card_stays_blank(self) -> None:
        # Non-annual card values keep their unit, fail the annual floor, blank.
        html = (
            '<div class="compensation__salary-range"><h3>Base pay range</h3>'
            '<div class="salary compensation__salary">$45.00/hr - $55.00/hr'
            "</div></div>"
        )
        assert parse_linkedin_salary_card(html) == {}

    def test_job_page_without_card_means_no_pay(self) -> None:
        """A page carrying real job-page markers but no salary card is
        conclusive: the poster listed no structured pay."""
        html = '<div class="show-more-less-html__markup">About the job ...</div>'
        assert parse_linkedin_salary_card(html) == {}

    def test_authwall_and_empty_html_are_inconclusive(self) -> None:
        """No job-page markers at all (auth wall / challenge served with HTTP
        200, or an empty body) proves nothing — raising degrades to a failed
        fetch so the row is never falsely marked 'Not listed'."""
        with pytest.raises(ValueError):
            parse_linkedin_salary_card("<html><body>Sign in to view</body></html>")
        with pytest.raises(ValueError):
            parse_linkedin_salary_card("")

    def test_detail_page_dispatch_routes_linkedin(self) -> None:
        html = f"<html><body>{self._CARD}</body></html>"
        assert _parse_detail_page(
            html, "https://www.linkedin.com/jobs/view/4439447388"
        ) == {"comp": "$100,000–$110,000/yr"}
        # Non-LinkedIn URLs keep the generic JSON-LD path; a page with no
        # JobPosting block is inconclusive there.
        with pytest.raises(ValueError):
            _parse_detail_page(html, "https://apply.workable.com/x/j/1")
