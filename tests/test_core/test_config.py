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
        "tracker:\n  categories:\n    task: {required: [title]}\n",
        encoding="utf-8",
    )
    config = load(cfg_file)
    assert "task" in config.tracker.categories


def test_load_full_plugins(tmp_path):
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(
        """\
tracker:
  default_category: task
  categories:
    task: {required: [title]}
plugins:
  job_search:
    locations:
      home_city: "Vancouver, BC"
      remote: true
      countries: {CA: [], US: []}
""",
        encoding="utf-8",
    )
    config = load(cfg_file)
    assert config.plugins.job_search is not None
    assert config.plugins.job_search.locations.remote is True


def test_load_empty_file_returns_defaults(tmp_path):
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text("", encoding="utf-8")
    config = load(cfg_file)
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


def test_read_settings_keeps_the_yaml_error_as_the_cause(tmp_path):
    import yaml

    from daily_driver.core.config import read_settings

    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text("schedule: [\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        read_settings(cfg_file)
    assert isinstance(exc.value.__cause__, yaml.YAMLError)


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r", "\x85", "\u2028", "\u2029"])
def test_read_settings_places_a_forbidden_character_on_its_line(tmp_path, newline):
    """YAML line breaks include a lone CR, NEL, and the Unicode line and
    paragraph separators, not only LF."""
    from daily_driver.core.config import read_settings

    cfg_file = tmp_path / ".dd-config.yaml"
    text = newline.join(["a: 1", "b: 2", "note: a\x00b", ""])
    cfg_file.write_text(text, encoding="utf-8", newline="")
    with pytest.raises(ConfigError, match="at line 3, column 8: special characters"):
        read_settings(cfg_file)


@pytest.mark.parametrize(
    ("text", "where"),
    [
        ("\x00a: 1\n", "line 1, column 1"),
        ("a: 1\n\x00b: 2\n", "line 2, column 1"),
        ("\ufeffa: \x00\n", "line 1, column 4"),
    ],
)
def test_read_settings_places_a_forbidden_character_at_its_column(
    tmp_path, text, where
):
    """Column 1 is the start of a line, and a byte order mark takes no column,
    as in PyYAML's own error marks."""
    from daily_driver.core.config import read_settings

    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=f"at {where}: special characters"):
        read_settings(cfg_file)


def test_read_settings_keeps_the_read_error_as_the_cause(tmp_path):
    from daily_driver.core.config import read_settings

    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.mkdir()
    with pytest.raises(ConfigError) as exc:
        read_settings(cfg_file)
    assert isinstance(exc.value.__cause__, OSError)
    assert str(exc.value).startswith(f"{cfg_file} cannot be read")


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


# ---------------------------------------------------------------------------
# Removed settings
# ---------------------------------------------------------------------------

_TRACKER = "tracker:\n  categories:\n    task: {required: [title]}\n"


@pytest.mark.parametrize(
    ("block", "dotted", "fix"),
    [
        ("user_profile:\n  name: A\n", "user_profile", "context.md"),
        (
            "recurring_tasks:\n  - {name: x, cadence: daily}\n",
            "recurring_tasks",
            "## Recurring Tasks",
        ),
        (
            "claude:\n  resume_check_in: true\n",
            "claude",
            "ai.interactive.resume_check_in",
        ),
        ("daily_driver:\n  output_dir: .\n", "daily_driver", "workspace folder"),
        (
            "plugins:\n  job_search:\n    persona: SRE\n",
            "plugins.job_search.persona",
            "context.md",
        ),
        ("ai:\n  summary:\n    provider: ollama\n", "ai.summary", "ai.providers"),
        (
            "scheduler:\n  jobs:\n    time: '07:00'\n" "plugins:\n  job_search: {}\n",
            "scheduler.jobs",
            "plugins.job_search.scheduler.run",
        ),
        (
            "scheduler:\n  discovery:\n    time: '23:59'\n"
            "plugins:\n  job_search: {}\n",
            "scheduler.discovery",
            "plugins.job_search.scheduler.discovery",
        ),
        ("ai:\n  ollama:\n    timeout: 60\n", "ai.ollama", "ai.providers.ollama.model"),
    ],
)
def test_load_warns_about_a_removed_setting_and_ignores_it(
    tmp_path, capsys, block, dotted, fix
):
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(_TRACKER + block, encoding="utf-8")
    config = load(cfg_file)
    assert config.tracker.default_category == "task"
    err = " ".join(capsys.readouterr().err.split())
    assert dotted in err
    assert fix in err
    assert "indented lines under it" in err


@pytest.mark.parametrize(
    ("block", "dotted"),
    [
        ("calender:\n  sync_enabled: true\n", "calender"),
        ("calendar:\n  sync_enabeld: true\n", "calendar.sync_enabeld"),
        ("plugins:\n  job_search:\n    rolez: []\n", "plugins.job_search.rolez"),
        ("plugins:\n  nope: {}\n", "plugins.nope"),
        (
            "plugins:\n  job_search:\n    sources:\n      linkedin:\n"
            "        enabld: true\n",
            "plugins.job_search.sources.linkedin.enabld",
        ),
        (
            "plugins:\n  job_search:\n    sources:\n      workday:\n"
            "        workday_boards:\n          - {tenant: a, host: wd5, site: s,"
            " bogus: 1}\n",
            "plugins.job_search.sources.workday.workday_boards.0.bogus",
        ),
        (
            "ai:\n  providers:\n    claude:\n      modle: sonnet\n",
            "ai.providers.claude.modle",
        ),
    ],
)
def test_load_warns_about_an_unknown_setting_and_ignores_it(
    tmp_path, capsys, block, dotted
):
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(_TRACKER + block, encoding="utf-8")
    load(cfg_file)
    err = " ".join(capsys.readouterr().err.split())
    assert f"ignoring {dotted}:" in err
    assert "correct the spelling" in err


@pytest.mark.parametrize("old_key", ["jobs", "discovery"])
def test_moved_schedule_warning_says_how_to_keep_the_run_off(tmp_path, capsys, old_key):
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(
        _TRACKER + f"scheduler:\n  {old_key}:\n    time: null\n", encoding="utf-8"
    )
    load(cfg_file)
    err = " ".join(capsys.readouterr().err.split())
    assert f"ignoring scheduler.{old_key}: delete it" in err
    # Job search is off here: the warning names no key that would switch it on.
    assert "plugins.job_search" not in err


@pytest.mark.parametrize("old_key", ["jobs", "discovery"])
def test_moved_schedule_warning_names_the_new_key_when_job_search_is_on(
    tmp_path, capsys, old_key
):
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(
        _TRACKER
        + f"scheduler:\n  {old_key}:\n    time: null\n"
        + "plugins:\n  job_search: {}\n",
        encoding="utf-8",
    )
    load(cfg_file)
    err = " ".join(capsys.readouterr().err.split())
    assert "plugins.job_search.scheduler." in err
    assert "enabled: false" in err


def test_a_custom_error_inside_a_source_keeps_its_type_and_path():
    from pydantic import BaseModel, field_validator
    from pydantic_core import PydanticCustomError

    from daily_driver.plugins.job_search.config import SourceToggle, _validate_source

    class Strict(SourceToggle):
        flavour: str = ""

        @field_validator("flavour")
        @classmethod
        def _check(cls, value: str) -> str:
            raise PydanticCustomError("my_code", "no {what}", {"what": "flavour"})

    assert issubclass(Strict, BaseModel)
    with pytest.raises(ValidationError) as excinfo:
        _validate_source(Strict, "mysite", {"flavour": "x"})
    (error,) = excinfo.value.errors()
    assert error["type"] == "my_code"
    assert error["loc"] == ("mysite", "flavour")
    assert error["msg"] == "no flavour"


def test_load_still_stops_on_a_wrong_value_inside_a_source(tmp_path):
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(
        _TRACKER
        + "plugins:\n  job_search:\n    sources:\n      linkedin:\n"
        + "        hours_old: soon\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as excinfo:
        load(cfg_file)
    locs = [error["loc"] for error in excinfo.value.errors()]
    assert locs == [("plugins", "job_search", "sources", "linkedin", "hours_old")]


def test_load_keeps_the_valid_settings_beside_an_unknown_one(tmp_path):
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(
        _TRACKER + "calendar:\n  sync_enabled: true\n  nope: 1\n", encoding="utf-8"
    )
    assert load(cfg_file).calendar.sync_enabled is True


def test_load_warns_once_per_setting(tmp_path, capsys):
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(_TRACKER + "nope: 1\n", encoding="utf-8")
    load(cfg_file)
    load(cfg_file)
    assert capsys.readouterr().err.count("ignoring nope") == 1


def test_load_still_stops_on_a_wrong_value(tmp_path):
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(
        _TRACKER + "nope: 1\ncalendar:\n  sync_enabled: [not, a, bool]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as excinfo:
        load(cfg_file)
    assert "sync_enabled" in str(excinfo.value)
    assert "nope" not in str(excinfo.value)


@pytest.mark.parametrize("output_dir", ["notes", "~/plans"])
def test_load_stops_when_data_files_were_kept_in_another_folder(tmp_path, output_dir):
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(
        _TRACKER + f"daily_driver:\n  output_dir: {output_dir}\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError) as excinfo:
        load(cfg_file)
    message = str(excinfo.value)
    assert str(cfg_file) in message
    assert "Move the files" in message


def test_load_names_a_removed_nested_setting_by_its_path(tmp_path, capsys):
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(
        _TRACKER
        + "ai:\n  summary: {provider: ollama}\n  interactive: {model: opus}\n"
        + "plugins:\n  job_search:\n    enrichment:\n      provider: claude\n",
        encoding="utf-8",
    )
    load(cfg_file)
    err = " ".join(capsys.readouterr().err.split())
    for dotted in (
        "ai.summary",
        "ai.interactive.model",
        "plugins.job_search.enrichment.provider",
    ):
        assert f"ignoring {dotted}:" in err
