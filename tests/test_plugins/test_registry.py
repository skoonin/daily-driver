"""Plugin registry self-validation (plugins._validate_registry)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from daily_driver.core.config_models import Config
from daily_driver.plugins import _validate_registry, enabled_plugins
from daily_driver.plugins._base import Plugin


def _plugin(**overrides) -> Plugin:
    base = dict(
        name="sample",
        command_name="sample",
        command_module="daily_driver.plugins.job_search.cli",
        command_help="help",
    )
    base.update(overrides)
    return Plugin(**base)


def test_valid_registry_passes() -> None:
    _validate_registry((_plugin(),))


def test_non_identifier_name_rejected() -> None:
    with pytest.raises(ValueError, match="not a valid identifier"):
        _validate_registry((_plugin(name="job search"),))


def test_duplicate_command_name_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate plugin command_name"):
        _validate_registry((_plugin(name="a"), _plugin(name="b")))


def test_unknown_hook_module_rejected() -> None:
    with pytest.raises(ValueError, match="unknown module"):
        _validate_registry(
            (_plugin(doctor_checks="daily_driver.plugins.does_not_exist.run_checks"),)
        )


def _config(plugins: dict | None = None) -> Config:
    raw: dict = {"tracker": {"categories": {"task": {"required": ["title"]}}}}
    if plugins is not None:
        raw["plugins"] = plugins
    return Config.model_validate(raw)


def test_enabled_plugins_empty_without_block() -> None:
    assert enabled_plugins(_config()) == ()


def test_enabled_plugins_block_without_key_is_on() -> None:
    names = [p.name for p in enabled_plugins(_config({"job_search": {}}))]
    assert names == ["job_search"]


def test_bare_block_with_options_commented_out_is_on() -> None:
    """`job_search:` with every option commented out loads as null."""
    names = [p.name for p in enabled_plugins(_config({"job_search": None}))]
    assert names == ["job_search"]


def test_plugins_section_with_every_block_commented_out_loads_off() -> None:
    """`plugins:` with its only child commented out loads as null."""
    raw: dict = {
        "tracker": {"categories": {"task": {"required": ["title"]}}},
        "plugins": None,
    }
    assert enabled_plugins(Config.model_validate(raw)) == ()


def test_enabled_plugins_honors_enabled_false() -> None:
    assert enabled_plugins(_config({"job_search": {"enabled": False}})) == ()


def test_enabled_plugins_tolerates_missing_config() -> None:
    assert enabled_plugins(None) == ()


def test_plugin_without_config_model_is_always_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no config namespace there is no way to opt in, so it is on."""
    import daily_driver.plugins as plugins_pkg

    sample = _plugin()
    monkeypatch.setattr(plugins_pkg, "PLUGINS", (sample,))

    assert enabled_plugins(None) == (sample,)


def test_config_model_without_enabled_field_is_on_when_block_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import daily_driver.plugins as plugins_pkg

    class SampleConfig(BaseModel):
        note: str = ""

    sample = _plugin(config_model=SampleConfig)
    monkeypatch.setattr(plugins_pkg, "PLUGINS", (sample,))
    on = SimpleNamespace(plugins=SimpleNamespace(sample=SampleConfig()))
    off = SimpleNamespace(plugins=SimpleNamespace(sample=None))

    assert enabled_plugins(on) == (sample,)
    assert enabled_plugins(off) == ()
