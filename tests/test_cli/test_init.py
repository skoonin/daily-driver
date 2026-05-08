"""Tests for the `daily-driver init` subcommand."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from daily_driver.cli.commands.init import run
from daily_driver.core.config import load


def _args(path: str, *, force: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        path=path,
        force=force,
        verbose=False,
        quiet=False,
        no_color=False,
        workspace=None,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_init_creates_expected_artifacts(tmp_path: Path) -> None:
    result = run(_args(str(tmp_path)))

    assert result == 0
    assert (tmp_path / ".dd-config.yaml").exists(), ".dd-config.yaml must be written"
    assert (tmp_path / ".daily-driver").is_dir(), ".daily-driver state dir must exist"
    assert (tmp_path / ".claude").is_dir(), ".claude dir must be created"
    assert (tmp_path / "context.md").exists(), "context.md must be seeded"
    assert (tmp_path / "voice-profile.md").exists(), "voice-profile.md must be seeded"


def test_init_creates_nonexistent_target(tmp_path: Path) -> None:
    target = tmp_path / "new-workspace"
    assert not target.exists()
    result = run(_args(str(target)))
    assert result == 0
    assert target.is_dir()
    assert (target / ".dd-config.yaml").exists()


def test_init_settings_json_is_valid_json(tmp_path: Path) -> None:
    run(_args(str(tmp_path)))
    settings = tmp_path / ".claude" / "settings.local.json"
    assert settings.exists(), "settings.local.json must be rendered by materialize"
    parsed = json.loads(settings.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)


def test_init_settings_json_contains_version(tmp_path: Path) -> None:
    import daily_driver

    run(_args(str(tmp_path)))
    settings = tmp_path / ".claude" / "settings.local.json"
    parsed = json.loads(settings.read_text(encoding="utf-8"))
    assert parsed["metadata"]["daily_driver_version"] == daily_driver.__version__


def test_init_scaffolds_time_hook(tmp_path: Path) -> None:
    """init must drop hook-current-time.sh into .claude/hooks/ and wire it in settings."""
    run(_args(str(tmp_path)))
    hook = tmp_path / ".claude" / "hooks" / "hook-current-time.sh"
    assert hook.exists(), "hook-current-time.sh must be materialized"
    assert "additionalContext" in hook.read_text(encoding="utf-8")

    settings = tmp_path / ".claude" / "settings.local.json"
    parsed = json.loads(settings.read_text(encoding="utf-8"))
    user_prompt_hooks = parsed.get("hooks", {}).get("UserPromptSubmit", [])
    assert user_prompt_hooks, "UserPromptSubmit hook must be wired"
    cmd = user_prompt_hooks[0]["hooks"][0]["command"]
    assert "hook-current-time.sh" in cmd


def test_init_config_parses_via_load(tmp_path: Path) -> None:
    run(_args(str(tmp_path)))
    config = load(tmp_path / ".dd-config.yaml")
    assert config.daily_driver.output_dir == "."
    assert "task" in config.tracker.categories


# ---------------------------------------------------------------------------
# Double-init guards
# ---------------------------------------------------------------------------


def test_second_init_without_force_fails(tmp_path: Path) -> None:
    run(_args(str(tmp_path)))
    result = run(_args(str(tmp_path)))
    assert result == 1


def test_second_init_with_force_succeeds(tmp_path: Path) -> None:
    run(_args(str(tmp_path)))
    result = run(_args(str(tmp_path), force=True))
    assert result == 0


# ---------------------------------------------------------------------------
# Static files not clobbered under --force
# ---------------------------------------------------------------------------


def test_force_does_not_overwrite_existing_context_md(tmp_path: Path) -> None:
    run(_args(str(tmp_path)))
    custom = "my custom context"
    (tmp_path / "context.md").write_text(custom, encoding="utf-8")

    run(_args(str(tmp_path), force=True))

    assert (tmp_path / "context.md").read_text(encoding="utf-8") == custom


def test_force_does_not_overwrite_existing_voice_profile(tmp_path: Path) -> None:
    run(_args(str(tmp_path)))
    custom = "my custom voice profile"
    (tmp_path / "voice-profile.md").write_text(custom, encoding="utf-8")

    run(_args(str(tmp_path), force=True))

    assert (tmp_path / "voice-profile.md").read_text(encoding="utf-8") == custom


# ---------------------------------------------------------------------------
# User-territory directories
# ---------------------------------------------------------------------------


def test_init_creates_user_command_dir(tmp_path: Path) -> None:
    run(_args(str(tmp_path)))
    assert (tmp_path / ".claude" / "commands" / "user").is_dir()


def test_init_creates_user_agent_dir(tmp_path: Path) -> None:
    run(_args(str(tmp_path)))
    assert (tmp_path / ".claude" / "agents" / "user").is_dir()


# ---------------------------------------------------------------------------
# .gitignore scaffolding
# ---------------------------------------------------------------------------


def test_init_creates_gitignore(tmp_path: Path) -> None:
    run(_args(str(tmp_path)))
    gitignore = tmp_path / ".gitignore"
    assert gitignore.exists(), ".gitignore must be written on init"
    content = gitignore.read_text(encoding="utf-8")
    assert ".claude/commands/daily-driver/" in content
    assert ".claude/agents/daily-driver/" in content
    assert ".claude/settings.local.json" in content


def test_init_gitignore_not_overwritten_on_force(tmp_path: Path) -> None:
    run(_args(str(tmp_path)))
    custom = "# my custom gitignore\n*.pyc\n"
    (tmp_path / ".gitignore").write_text(custom, encoding="utf-8")

    run(_args(str(tmp_path), force=True))

    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == custom
