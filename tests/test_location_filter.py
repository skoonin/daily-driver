"""Tests for location_matches() filtering."""

import scrape_jobs as sj


def _cfg(remote=True, countries=None, cities=None):
    loc = {"remote": remote}
    if countries is not None:
        loc["countries"] = countries
    if cities is not None:
        loc["cities"] = cities
    return {"job_search": {"locations": loc}}


class TestLocationMatches:
    def test_remote_accepted_when_enabled(self):
        assert sj.location_matches({"location": "Remote"}, _cfg(remote=True))

    def test_remote_rejected_when_disabled(self):
        assert not sj.location_matches({"location": "Remote"}, _cfg(remote=False))

    def test_remote_case_insensitive(self):
        assert sj.location_matches({"location": "REMOTE - US"}, _cfg(remote=True))

    def test_city_match(self):
        cfg = _cfg(cities=["Vancouver, BC"])
        assert sj.location_matches({"location": "Vancouver, BC, Canada"}, cfg)

    def test_city_match_case_insensitive(self):
        cfg = _cfg(cities=["Vancouver, BC"])
        assert sj.location_matches({"location": "vancouver, bc"}, cfg)

    def test_city_no_match(self):
        cfg = _cfg(remote=False, cities=["Vancouver, BC"])
        assert not sj.location_matches({"location": "Toronto, ON"}, cfg)

    def test_country_match_by_name(self):
        cfg = _cfg(remote=False, countries=["CA"])
        assert sj.location_matches({"location": "Toronto, Canada"}, cfg)

    def test_country_match_by_alias_uk(self):
        cfg = _cfg(remote=False, countries=["GB"])
        assert sj.location_matches({"location": "London, UK"}, cfg)

    def test_country_match_by_alias_usa(self):
        cfg = _cfg(remote=False, countries=["US"])
        assert sj.location_matches({"location": "New York, USA"}, cfg)

    def test_country_no_match(self):
        cfg = _cfg(remote=False, countries=["CA"])
        assert not sj.location_matches({"location": "New York, United States"}, cfg)

    def test_empty_location_accepted_when_remote_enabled(self):
        assert sj.location_matches({"location": ""}, _cfg(remote=True))

    def test_empty_location_rejected_when_remote_disabled(self):
        assert not sj.location_matches({"location": ""}, _cfg(remote=False))

    def test_missing_location_accepted_when_remote_enabled(self):
        assert sj.location_matches({}, _cfg(remote=True))

    def test_missing_location_rejected_when_remote_disabled(self):
        assert not sj.location_matches({}, _cfg(remote=False))

    def test_no_locations_config_accepts_all(self):
        cfg = {"job_search": {}}
        assert sj.location_matches({"location": "Anywhere"}, cfg)

    def test_multiple_cities(self):
        cfg = _cfg(remote=False, cities=["London, UK", "Dublin, IE"])
        assert sj.location_matches({"location": "Dublin, IE"}, cfg)
        assert not sj.location_matches({"location": "Berlin, DE"}, cfg)

    def test_country_match_australia(self):
        cfg = _cfg(remote=False, countries=["AU"])
        assert sj.location_matches({"location": "Sydney, Australia"}, cfg)

    def test_combined_remote_and_city(self):
        cfg = _cfg(remote=True, cities=["Vancouver, BC"])
        assert sj.location_matches({"location": "Remote"}, cfg)
        assert sj.location_matches({"location": "Vancouver, BC"}, cfg)
        assert not sj.location_matches({"location": "Tokyo, Japan"}, cfg)

    def test_all_closed_rejects_everything(self):
        cfg = _cfg(remote=False, countries=[], cities=[])
        assert not sj.location_matches({"location": "Remote"}, cfg)
        assert not sj.location_matches({"location": "Vancouver, BC"}, cfg)

    def test_iso_code_does_not_match_state_abbreviation(self):
        cfg = _cfg(remote=False, countries=["CA"])
        assert not sj.location_matches({"location": "San Francisco, CA"}, cfg)

    def test_gb_matches_england(self):
        cfg = _cfg(remote=False, countries=["GB"])
        assert sj.location_matches({"location": "Manchester, England"}, cfg)


class TestLocationSummary:
    def test_builds_from_config(self):
        cfg = _cfg(remote=True, countries=["CA", "GB"], cities=["Vancouver, BC"])
        summary = sj._location_summary(cfg)
        assert "Vancouver, BC" in summary
        assert "Canada" in summary
        assert "Remote: yes" in summary
        assert "Based in:" in summary

    def test_empty_config_returns_default(self):
        summary = sj._location_summary({"job_search": {}})
        assert "Vancouver" in summary
