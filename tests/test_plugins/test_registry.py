"""Plugin registry self-validation (plugins._validate_registry)."""

from __future__ import annotations

import pytest

from daily_driver.plugins import _validate_registry
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


def test_label_in_both_current_and_legacy_rejected() -> None:
    with pytest.raises(ValueError, match="both"):
        _validate_registry(
            (
                _plugin(
                    launchd_labels=("com.x.job",),
                    legacy_launchd_labels=("com.x.job",),
                ),
            )
        )


def test_unknown_hook_module_rejected() -> None:
    with pytest.raises(ValueError, match="unknown module"):
        _validate_registry(
            (_plugin(doctor_checks="daily_driver.plugins.does_not_exist.run_checks"),)
        )
