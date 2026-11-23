"""Tests for the `daily-driver init` subcommand."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from daily_driver.cli.commands.init import run
from daily_driver.core.config import load


def _args(
    path: str, *, force: bool = False, job_search: bool = False
) -> argparse.Namespace:
    return argparse.Namespace(
        path=path,
        force=force,
        plugin_job_search=job_search,
        verbose=False,
        quiet=False,
        no_color=False,
        workspace=None,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_init_scaffolds_a_complete_workspace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import daily_driver
    from tests.test_core.test_generate import JOB_TEXT

    target = tmp_path / "new-workspace"
    assert not target.exists()

    assert run(_args(str(target))) == 0

    for name in (".dd-config.yaml", "context.md", "voice-profile.md", "README.md"):
        assert (target / name).exists(), f"{name} must be written"
    for directory in (
        ".daily-driver",
        ".claude/commands/user",
        ".claude/agents/user",
    ):
        assert (target / directory).is_dir(), f"{directory} must be created"

    for name in ("context.md", "voice-profile.md", "README.md"):
        match = JOB_TEXT.search((target / name).read_text(encoding="utf-8"))
        assert match is None, f"{name} mentions {match.group(0)!r}"

    assert "task" in load(target / ".dd-config.yaml").tracker.categories

    gitignore = (target / ".gitignore").read_text(encoding="utf-8")
    for entry in (
        ".claude/commands/daily-driver/",
        ".claude/agents/daily-driver/",
        ".claude/settings.local.json",
    ):
        assert entry in gitignore

    settings = json.loads(
        (target / ".claude" / "settings.local.json").read_text(encoding="utf-8")
    )
    assert settings["metadata"]["daily_driver_version"] == daily_driver.__version__
    hook = target / ".claude" / "hooks" / "hook-current-time.sh"
    assert "additionalContext" in hook.read_text(encoding="utf-8")
    user_prompt_hooks = settings.get("hooks", {}).get("UserPromptSubmit", [])
    assert user_prompt_hooks, "UserPromptSubmit hook must be wired"
    command = user_prompt_hooks[0]["hooks"][0]["command"]
    assert "hook-current-time.sh" in command
    # Workspace path is quoted so paths with spaces (e.g. iCloud Drive) work.
    assert command.count('"') >= 2

    summary = capsys.readouterr().err
    assert "Created" in summary
    assert ".dd-config.yaml" in summary


def test_init_job_search_flag_switches_job_search_on(tmp_path: Path) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.plugins import enabled_plugins

    assert app(["init", str(tmp_path), "--job-search"]) == 0

    config = load(tmp_path / ".dd-config.yaml")
    assert [plugin.name for plugin in enabled_plugins(config)] == ["job_search"]
    assert "## Location preferences" in (tmp_path / "context.md").read_text()
    assert "daily-driver jobs run" in (tmp_path / "README.md").read_text()
    day_start = tmp_path / ".claude" / "commands" / "daily-driver" / "day-start.md"
    assert "applications by stage" in day_start.read_text()


def test_init_has_no_short_flag_for_job_search(tmp_path: Path) -> None:
    """`-j` means `--json` elsewhere in the CLI, so init does not reuse it."""
    from daily_driver.cli.cli import app

    with pytest.raises(SystemExit) as exc:
        app(["init", str(tmp_path), "-j"])

    assert exc.value.code == 2


def test_init_job_search_on_existing_config_warns_and_keeps_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(_args(str(tmp_path)))
    before = (tmp_path / ".dd-config.yaml").read_text()
    capsys.readouterr()

    assert run(_args(str(tmp_path), job_search=True)) == 0

    assert (tmp_path / ".dd-config.yaml").read_text() == before
    err = " ".join(capsys.readouterr().err.split())
    assert "--job-search did not change it" in err
    assert "`plugins: {job_search: {}}`" in err


def test_init_job_search_with_enabled_false_says_to_remove_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(_args(str(tmp_path)))
    config = tmp_path / ".dd-config.yaml"
    config.write_text(
        config.read_text() + "\nplugins:\n  job_search: {enabled: false}\n"
    )
    capsys.readouterr()

    run(_args(str(tmp_path), job_search=True))

    err = " ".join(capsys.readouterr().err.split())
    assert "remove `enabled: false`" in err
    assert "plugins: {job_search: {}}" not in err


def test_init_summary_says_job_search_is_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(_args(str(tmp_path), job_search=True))

    assert "Job search: on" in capsys.readouterr().err


def test_force_says_when_it_switched_job_search_off(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(_args(str(tmp_path), job_search=True))
    capsys.readouterr()

    assert run(_args(str(tmp_path), force=True)) == 0

    err = " ".join(capsys.readouterr().err.split())
    assert "Job search: off" in err
    # Running --force again would reset every setting; adding the block does not.
    assert "add `plugins: {job_search: {}}`" in err
    assert "doctor --fix" in err
    assert "--force" not in err


def test_a_plugin_flag_never_replaces_an_init_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plugin named like an `init` argument must not overwrite it."""
    from daily_driver.cli import commands
    from daily_driver.cli.cli import app
    from daily_driver.plugins._base import Plugin

    clash = Plugin(
        name="path",
        command_name="clash",
        command_module="daily_driver.cli.commands.status",
        command_help="x",
        init_flag_help="x",
    )
    init_module = commands.init
    monkeypatch.setattr(init_module, "PLUGINS", (*init_module.PLUGINS, clash))

    target = tmp_path / "ws"
    assert app(["init", str(target), "--path"]) == 0
    assert (target / ".dd-config.yaml").exists()


def test_force_with_job_search_keeps_it_on_and_says_nothing_about_off(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(_args(str(tmp_path), job_search=True))
    capsys.readouterr()

    assert run(_args(str(tmp_path), force=True, job_search=True)) == 0

    err = capsys.readouterr().err
    assert "Job search: on" in err
    assert "Job search: off" not in err


def test_force_replaces_a_config_that_does_not_load(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(_args(str(tmp_path), job_search=True))
    config = tmp_path / ".dd-config.yaml"
    config.write_text("schedule: [\n", encoding="utf-8")
    capsys.readouterr()

    assert run(_args(str(tmp_path), force=True)) == 0

    assert "schedule: [" not in config.read_text(encoding="utf-8")
    assert "Job search" not in capsys.readouterr().err


def test_init_summary_says_nothing_about_job_search_when_off(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(_args(str(tmp_path)))

    assert "job" not in capsys.readouterr().err.lower()


def test_plain_reinit_does_not_warn_about_job_search(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(_args(str(tmp_path)))
    capsys.readouterr()

    run(_args(str(tmp_path)))

    assert "did not change it" not in capsys.readouterr().err


def test_init_job_search_on_workspace_already_on_does_not_warn(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(_args(str(tmp_path), job_search=True))
    capsys.readouterr()

    assert run(_args(str(tmp_path), job_search=True)) == 0

    captured = capsys.readouterr()
    assert "did not change it" not in captured.out + captured.err


def test_init_force_with_job_search_rewrites_config_on(tmp_path: Path) -> None:
    from daily_driver.plugins import enabled_plugins

    run(_args(str(tmp_path)))

    assert run(_args(str(tmp_path), force=True, job_search=True)) == 0

    assert enabled_plugins(load(tmp_path / ".dd-config.yaml"))


# ---------------------------------------------------------------------------
# Double-init guards
# ---------------------------------------------------------------------------


def test_second_init_is_idempotent_and_exits_zero(tmp_path: Path) -> None:
    """Second init on the same path must succeed (exit 0), not error out."""
    run(_args(str(tmp_path)))
    result = run(_args(str(tmp_path)))
    assert result == 0


def test_force_keeps_user_edited_files(tmp_path: Path) -> None:
    run(_args(str(tmp_path)))
    custom = {
        "context.md": "my custom context",
        "voice-profile.md": "my custom voice profile",
        ".gitignore": "# my custom gitignore\n*.pyc\n",
    }
    for name, text in custom.items():
        (tmp_path / name).write_text(text, encoding="utf-8")

    assert run(_args(str(tmp_path), force=True)) == 0

    for name, text in custom.items():
        assert (tmp_path / name).read_text(encoding="utf-8") == text, name


def test_second_init_summary_lists_skipped_artifacts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Second-run summary must enumerate skipped artifacts so users see
    the system isn't silently no-op'ing."""
    run(_args(str(tmp_path)))
    capsys.readouterr()  # clear first-run output
    rc = run(_args(str(tmp_path)))
    assert rc == 0
    out = capsys.readouterr().err  # init prints summary to stderr
    # Summary terminology: Created / Skipped sections must both appear
    # so the user can audit what changed.
    assert "Skipped" in out
    assert ".dd-config.yaml" in out


def test_init_summary_survives_quiet_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The scaffold summary is a write-confirmation: -q must NOT silence it.

    Console.info/success both suppress under quiet, so init writes the summary
    straight to the stderr log console to keep the confirmation visible.
    """
    from daily_driver.core.console import Console

    Console.setup_for_user(quiet=True, verbose=False, no_color=True)
    try:
        rc = run(_args(str(tmp_path)))
    finally:
        Console.setup_for_user(quiet=False, verbose=False, no_color=False)

    assert rc == 0
    out = capsys.readouterr().err
    assert "Initialized workspace at" in out
    assert ".dd-config.yaml" in out


def test_partial_reinit_creates_missing_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """If a non-config artifact is removed, re-init must restore it."""
    run(_args(str(tmp_path)))
    (tmp_path / "context.md").unlink()
    capsys.readouterr()
    rc = run(_args(str(tmp_path)))
    assert rc == 0
    assert (tmp_path / "context.md").exists()
    out = capsys.readouterr().err
    # Summary should now show context.md as Created (this run) while
    # .dd-config.yaml shows as Skipped.
    assert "Created" in out and "context.md" in out


def test_init_skips_inner_generate_on_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Idempotent re-init at the same version must NOT call generate()'s
    work path. The version-stamp fast-path should return None, suppressing
    the misleading 'Copied N command(s)' / 'Version stamp written' INFO
    logs that previously fired on every init invocation.
    """
    # First init — full work expected.
    run(_args(str(tmp_path)))

    # Second init — count calls to the inner generate wrapper. init imports
    # generate lazily, so patch the source module it resolves at call time.
    from daily_driver.core import generate as generate_module

    call_count = {"n": 0, "args_seen": []}
    real_generate = generate_module.generate

    def counting_generate(workspace, **kwargs):
        call_count["n"] += 1
        call_count["args_seen"].append(kwargs)
        return real_generate(workspace, **kwargs)

    monkeypatch.setattr(generate_module, "generate", counting_generate)
    run(_args(str(tmp_path)))

    assert call_count["n"] == 1, "generate should be called exactly once"
    seen = call_count["args_seen"][0]
    assert (
        seen.get("ignore_drift") is False
    ), "second init (no --force) must enable the version-stamp fast-path"
    assert (
        seen.get("force_overwrite") is False
    ), "second init (no --force) must not wipe managed files"


def test_init_force_still_regenerates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--force must keep the legacy ignore_drift=True / force_overwrite=True path."""
    run(_args(str(tmp_path)))

    from daily_driver.core import generate as generate_module

    args_seen = []
    real_generate = generate_module.generate

    def capturing_generate(workspace, **kwargs):
        args_seen.append(kwargs)
        return real_generate(workspace, **kwargs)

    monkeypatch.setattr(generate_module, "generate", capturing_generate)
    run(_args(str(tmp_path), force=True))

    assert args_seen[0].get("ignore_drift") is True
    assert args_seen[0].get("force_overwrite") is True
