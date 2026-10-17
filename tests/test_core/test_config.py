from __future__ import annotations

import pytest
from pydantic import ValidationError

from daily_driver.core.config import ConfigError, load

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


# ---------------------------------------------------------------------------
# Removed settings
# ---------------------------------------------------------------------------

_TRACKER = "tracker:\n  categories:\n    task: {required: [title]}\n"


@pytest.mark.parametrize(
    ("block", "fix"),
    [
        ("user_profile:\n  name: A\n", "context.md"),
        ("recurring_tasks:\n  - {name: x, cadence: daily}\n", "## Recurring Tasks"),
    ],
)
def test_load_names_removed_setting_and_its_fix(tmp_path, block, fix):
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(_TRACKER + block, encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load(cfg_file)
    message = str(excinfo.value)
    assert block.split(":")[0] in message
    assert fix in message
    assert str(cfg_file) in message


def test_load_reports_every_removed_setting_at_once(tmp_path):
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(
        _TRACKER + "user_profile: {}\nrecurring_tasks: []\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError) as excinfo:
        load(cfg_file)
    assert "user_profile" in str(excinfo.value)
    assert "recurring_tasks" in str(excinfo.value)


def test_load_reports_removed_and_other_problems_together(tmp_path):
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(
        _TRACKER + "user_profile: {}\nfocus: {bogus: 1}\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError) as excinfo:
        load(cfg_file)
    assert "user_profile" in str(excinfo.value)
    assert "focus.bogus" in str(excinfo.value)


def test_cli_shows_removed_setting_fix(tmp_path, capsys):
    from daily_driver.cli.cli import app
    from daily_driver.core.workspace import Workspace

    Workspace.init(tmp_path)
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(
        cfg_file.read_text(encoding="utf-8") + "user_profile:\n  name: A\n",
        encoding="utf-8",
    )
    rc = app(["--workspace", str(tmp_path), "tracker", "list"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "user_profile" in err
    assert "context.md" in err
    assert "Extra inputs are not permitted" not in err
    assert "ConfigError" not in err
