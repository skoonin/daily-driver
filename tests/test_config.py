"""Tests for config loader and helper functions."""

import pytest
import scrape_jobs as sj


class TestLoadConfig:
    def test_loads_valid_yaml(self, config_file):
        cfg = sj.load_config(config_file)
        assert "output_dir" in cfg
        assert "job_search" in cfg

    def test_raises_on_missing_file(self, tmp_path):
        with pytest.raises((FileNotFoundError, OSError)):
            sj.load_config(tmp_path / "nonexistent.yaml")



class TestResolveOutputDir:
    def test_expands_tilde(self, config):
        result = sj.resolve_output_dir(config)
        assert not str(result).startswith("~")
        assert result.is_absolute()

    def test_raises_when_key_missing(self):
        with pytest.raises(SystemExit, match="output_dir not set"):
            sj.resolve_output_dir({})

    def test_raises_when_value_empty(self):
        with pytest.raises(SystemExit, match="output_dir not set"):
            sj.resolve_output_dir({"output_dir": ""})


class TestScraperCfg:
    def test_returns_scraper_section(self, config):
        cfg = sj.scraper_cfg(config)
        assert cfg["enabled"] is True
        assert cfg["timeout"] == 5

    def test_returns_empty_dict_when_missing(self):
        assert sj.scraper_cfg({}) == {}

    def test_returns_empty_dict_for_missing_job_search(self):
        assert sj.scraper_cfg({"job_search": {}}) == {}


class TestRolesList:
    def test_returns_configured_roles(self, config):
        roles = sj.roles_list(config)
        assert "SRE" in roles
        assert "Platform Engineer" in roles

    def test_returns_empty_list_when_missing(self):
        assert sj.roles_list({}) == []


class TestUserAgent:
    def test_returns_configured_agent(self, config):
        assert sj.user_agent(config) == "TestAgent/1.0"

    def test_returns_default_mozilla_string_when_missing(self):
        assert "Mozilla" in sj.user_agent({})


class TestTimeoutSeconds:
    def test_returns_configured_timeout(self, config):
        assert sj.timeout_seconds(config) == 5

    def test_returns_default_30_when_missing(self):
        assert sj.timeout_seconds({}) == 30

    def test_coerces_string_to_int(self):
        cfg = {"job_search": {"scraper": {"timeout": "30"}}}
        assert sj.timeout_seconds(cfg) == 30
