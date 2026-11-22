"""Search queries: the terms from `roles`, then `scraper.search_terms`."""

from __future__ import annotations

from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.scraper.roles import _search_terms


def _terms(**settings: object) -> list[str]:
    return _search_terms(JobSearchPlugin.model_validate(settings))


def test_search_terms_add_to_the_roles():
    assert _terms(roles=["SRE"], scraper={"search_terms": ["platform engineer"]}) == [
        "SRE",
        "platform engineer",
    ]


def test_a_search_term_that_repeats_a_role_is_searched_once():
    assert _terms(roles=["SRE"], scraper={"search_terms": ["sre"]}) == ["SRE"]


def test_search_terms_alone_are_searched_without_roles():
    assert _terms(scraper={"search_terms": ["platform engineer"]}) == [
        "platform engineer"
    ]
