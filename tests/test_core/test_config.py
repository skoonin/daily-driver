from __future__ import annotations

import pytest
from pydantic import ValidationError

from daily_driver.core.config import load

# ---------------------------------------------------------------------------
# Valid round-trips
# ---------------------------------------------------------------------------


def test_load_minimal_valid(tmp_path):
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(
        "daily_driver:\n  output_dir: ./notes\ntracker:\n  categories:\n    task: {required: [title]}\n",
        encoding="utf-8",
    )
    config = load(cfg_file)
    assert config.daily_driver.output_dir == "./notes"
    assert "task" in config.tracker.categories


def test_load_full_plugins(tmp_path):
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(
        """\
daily_driver:
  output_dir: .
tracker:
  default_category: task
  categories:
    task: {required: [title]}
plugins:
  job_search:
    persona: "Senior SRE"
    locations:
      home_city: "Vancouver, BC"
      remote: true
      countries: {CA: [], US: []}
""",
        encoding="utf-8",
    )
    config = load(cfg_file)
    assert config.plugins.job_search is not None
    assert config.plugins.job_search.persona == "Senior SRE"
    assert config.plugins.job_search.locations.remote is True


def test_load_empty_file_returns_defaults(tmp_path):
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text("", encoding="utf-8")
    config = load(cfg_file)
    assert config.daily_driver.output_dir == "."
    assert "task" in config.tracker.categories


# ---------------------------------------------------------------------------
# safe_load security: YAML deserialization must not execute arbitrary code
# ---------------------------------------------------------------------------


def test_load_rejects_yaml_python_object(tmp_path):
    """yaml.safe_load must raise on !!python/object tags, not execute them."""
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(
        '!!python/object/apply:os.system ["echo pwned"]\n',
        encoding="utf-8",
    )
    with pytest.raises(Exception):
        load(cfg_file)


# ---------------------------------------------------------------------------
# Invalid YAML → ValidationError
# ---------------------------------------------------------------------------


def test_load_bad_tracker_raises(tmp_path):
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(
        # default_category 'job' is not in categories
        "tracker:\n  default_category: job\n  categories:\n    task: {required: [title]}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load(cfg_file)


def test_load_rejects_unknown_top_level_key(tmp_path):
    """Root config is extra='forbid' so a typo'd top-level key fails loudly."""
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(
        "tracker:\n  categories:\n    task: {required: [title]}\ntracer: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as excinfo:
        load(cfg_file)
    assert "tracer" in str(excinfo.value)
