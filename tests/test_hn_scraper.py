"""Tests for scrape_hn_who_is_hiring()."""

from datetime import date
from unittest.mock import MagicMock, patch

import scrape_jobs as sj

from fixtures import SAMPLE_CONFIG


def _make_html_response(text: str) -> MagicMock:
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.text = text
    return mock


def _make_algolia_response(hits: list[dict]) -> MagicMock:
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.json.return_value = {"hits": hits, "nbHits": len(hits)}
    return mock


def _index_html(month_str: str) -> str:
    return f"""<html><body>
<a href="item?id=42000001">Ask HN: Who is hiring? ({month_str})</a>
</body></html>"""


def _hit(object_id: str, comment_text: str) -> dict:
    return {"objectID": object_id, "comment_text": comment_text}


_DEFAULT_HITS = [
    _hit("99000001", "Acme Corp | Senior SRE | Remote | Full-time<p>More details.</p>"),
    _hit("99000002", "BetaCo | Junior Frontend Dev | New York"),
    _hit("99000004", "GammaCo | Staff Platform Engineer | San Francisco | Contract"),
]


class TestScrapeHNWhoIsHiring:
    def _month_str(self) -> str:
        return date.today().strftime("%B %Y")

    def _two_responses(self, hits: list[dict] | None = None):
        if hits is None:
            hits = _DEFAULT_HITS
        return [
            _make_html_response(_index_html(self._month_str())),
            _make_algolia_response(hits),
        ]

    def test_returns_matching_jobs(self):
        with patch("scrape_jobs._api_get", side_effect=self._two_responses()):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        roles = [j["role"] for j in jobs]
        assert "Senior SRE" in roles

    def test_skips_non_matching_roles(self):
        with patch("scrape_jobs._api_get", side_effect=self._two_responses()):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        companies = [j["company"] for j in jobs]
        assert "BetaCo" not in companies

    def test_parses_company_from_first_part(self):
        with patch("scrape_jobs._api_get", side_effect=self._two_responses()):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert jobs[0]["company"] == "Acme Corp"

    def test_parses_role_from_second_part(self):
        with patch("scrape_jobs._api_get", side_effect=self._two_responses()):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert jobs[0]["role"] == "Senior SRE"

    def test_detects_remote_location(self):
        with patch("scrape_jobs._api_get", side_effect=self._two_responses()):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert jobs[0]["location"] == "Remote"

    def test_uses_city_location_when_not_remote(self):
        with patch("scrape_jobs._api_get", side_effect=self._two_responses()):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        gamma = next(j for j in jobs if j["company"] == "GammaCo")
        assert gamma["location"] == "San Francisco"

    def test_source_label(self):
        with patch("scrape_jobs._api_get", side_effect=self._two_responses()):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert all(j["source"] == "HN Who's Hiring" for j in jobs)

    def test_url_contains_comment_id(self):
        with patch("scrape_jobs._api_get", side_effect=self._two_responses()):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert "99000001" in jobs[0]["url"]

    def test_returns_empty_when_thread_not_found(self):
        stale_index = _make_html_response(
            "<html><body><a href='item?id=1'>Old thread</a></body></html>"
        )
        with patch("scrape_jobs._api_get", return_value=stale_index):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert jobs == []

    def test_skips_rows_without_enough_pipe_parts(self):
        hits = [_hit("88000001", "Just a comment with no pipe separators")]
        with patch("scrape_jobs._api_get", side_effect=self._two_responses(hits)):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert jobs == []

    def test_respects_max_posts_limit(self):
        cfg = {
            "job_search": {
                "roles": ["SRE", "Platform Engineer"],
                "scraper": {
                    "enabled": True,
                    "timeout": 5,
                    "hn_max_posts": 1,
                },
            },
        }
        with patch("scrape_jobs._api_get", side_effect=self._two_responses()):
            jobs = sj.scrape_hn_who_is_hiring(cfg)
        assert len(jobs) == 1

    def test_handles_absolute_url_in_index_href(self):
        index = f"""<html><body>
<a href="https://news.ycombinator.com/item?id=42000001">Ask HN: Who is hiring? ({self._month_str()})</a>
</body></html>"""
        responses = [_make_html_response(index), _make_algolia_response(_DEFAULT_HITS)]
        with patch("scrape_jobs._api_get", side_effect=responses):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert len(jobs) >= 1

    def test_skips_hit_without_comment_text(self):
        hits = [{"objectID": "99000001", "comment_text": ""}]
        with patch("scrape_jobs._api_get", side_effect=self._two_responses(hits)):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert jobs == []

    def test_falls_back_to_thread_url_when_no_object_id(self):
        hits = [{"objectID": "", "comment_text": "Acme Corp | Senior SRE | Remote"}]
        with patch("scrape_jobs._api_get", side_effect=self._two_responses(hits)):
            jobs = sj.scrape_hn_who_is_hiring(SAMPLE_CONFIG)
        assert len(jobs) == 1
        assert "item?id=42000001" in jobs[0]["url"]
