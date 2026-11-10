"""`daily-driver config defaults` prints what `init` writes."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from daily_driver.cli.cli import app
from daily_driver.core.config_models import Config
from daily_driver.core.workspace import Workspace


def _defaults(capsys: pytest.CaptureFixture[str], *argv: str) -> str:
    assert app(["config", "defaults", *argv]) == 0
    return capsys.readouterr().out


def test_outside_a_workspace_prints_the_plain_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    text = _defaults(capsys)
    assert text.startswith("# daily-driver settings")
    assert "job_search" not in text
    Config.model_validate(yaml.safe_load(text))


def test_plugin_flag_adds_that_plugins_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    text = _defaults(capsys, "--plugin", "job_search")
    assert Config.model_validate(yaml.safe_load(text)).plugins.job_search is not None


def test_unknown_plugin_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        app(["config", "defaults", "--plugin", "nope"])
    assert "nope" in capsys.readouterr().err


@pytest.mark.parametrize("plugins", [(), ("job_search",)])
def test_in_a_workspace_prints_what_init_wrote_for_its_plugins(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], plugins: tuple[str, ...]
) -> None:
    Workspace.init(tmp_path, plugins)
    written = (tmp_path / ".dd-config.yaml").read_text(encoding="utf-8")
    assert _defaults(capsys, "--workspace", str(tmp_path)) == written


def test_a_config_that_cannot_load_still_prints_the_defaults(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    Workspace.init(tmp_path)
    cfg = tmp_path / ".dd-config.yaml"
    cfg.write_text("focus: {default_duration: [1]}\n", encoding="utf-8")
    assert app(["config", "defaults", "--workspace", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("# daily-driver settings")
    assert "doctor" in captured.err


def test_a_workspace_flag_that_names_no_workspace_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing"
    assert app(["config", "defaults", "--workspace", str(missing)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "missing" in captured.err


def test_bare_config_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert app(["config"]) == 2
    assert "defaults" in capsys.readouterr().err
