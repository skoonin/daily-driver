"""Unit tests for the company product/purpose line cleaner.

The LLM sometimes echoes the prompt's own scaffolding ("Line 1: ...", a leading
bullet, or a "Product:"/"Purpose:" label) into its answer. Those prefixes must be
stripped before the text lands in a CSV cell. Observed live: a Product/Purpose
cell beginning ``Line 1: Builds ...``.
"""

from __future__ import annotations

import pytest

from daily_driver.plugins.job_search.scraper.enrichment.llm import _clean_product_line


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Line 1: Builds developer tooling", "Builds developer tooling"),
        ("line 1: Builds developer tooling", "Builds developer tooling"),
        ("Line 2: Builds developer tooling", "Builds developer tooling"),
        ("1. Builds developer tooling", "Builds developer tooling"),
        ("1) Builds developer tooling", "Builds developer tooling"),
        ("- Builds developer tooling", "Builds developer tooling"),
        ("* Builds developer tooling", "Builds developer tooling"),
        ("Product: Builds developer tooling", "Builds developer tooling"),
        ("Purpose: Builds developer tooling", "Builds developer tooling"),
        ("  Builds developer tooling  ", "Builds developer tooling"),
        # Already clean: untouched.
        ("Builds developer tooling", "Builds developer tooling"),
        # Empty stays empty.
        ("", ""),
    ],
)
def test_clean_product_line(raw: str, expected: str) -> None:
    assert _clean_product_line(raw) == expected
