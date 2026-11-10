"""init subcommand: scaffold a new daily-driver workspace.

Idempotent: re-running on an existing workspace fills in any missing
artifacts and reports a Created / Skipped summary. `--force` only changes
the behavior of `.dd-config.yaml` (overwrite + .bak) — every other static
file is preserved across runs to avoid clobbering user edits.
"""

from __future__ import annotations

import argparse
import importlib.resources
import sys
import traceback
from pathlib import Path

from daily_driver.cli._common import add_global_flags
from daily_driver.core.console import Console
from daily_driver.core.workspace import Workspace, WorkspaceError
from daily_driver.plugins import PLUGINS, switch_on_hint
from daily_driver.plugins._base import Plugin


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "init",
        parents=parents,
        help="Create a new daily-driver workspace",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Target directory (default: current directory).",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        default=False,
        help="Overwrite existing .dd-config.yaml.",
    )
    for plugin in PLUGINS:
        if plugin.init_flag_help:
            parser.add_argument(
                _init_flag(plugin),
                dest=_flag_dest(plugin),
                action="store_true",
                default=False,
                help=plugin.init_flag_help,
            )
    add_global_flags(parser)
    parser.set_defaults(func=run)
    return parser


def _init_flag(plugin: Plugin) -> str:
    return f"--{plugin.name.replace('_', '-')}"


def _flag_dest(plugin: Plugin) -> str:
    # Prefixed, so a plugin named like an `init` argument cannot replace it.
    return f"plugin_{plugin.name}"


def _title(plugin: Plugin) -> str:
    return plugin.name.replace("_", " ").capitalize()


def _plugins_on_in(config_path: Path) -> tuple[Plugin, ...]:
    """The plugins on in an existing config; none if it does not load."""
    from daily_driver.core.config import read_settings
    from daily_driver.plugins import enabled_plugins

    try:
        config, _ = read_settings(config_path)
    except Exception:  # noqa: BLE001
        # `--force` is how a user replaces a config that does not load.
        return ()
    return enabled_plugins(config)


def _seed_template(name: str, dest: Path, config: object) -> bool:
    """Render a seed template to dest if absent. Returns True if created.

    Seeded files are the user's from then on, so they are rendered once with the
    plugins on at init time and never regenerated.
    """
    if dest.exists():
        return False
    # deferred: core.templating pulls in Jinja2.
    from daily_driver.core.templating import render_text

    template_text = (
        importlib.resources.files("daily_driver.resources.templates")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )
    dest.write_text(render_text(template_text, config), encoding="utf-8")
    return True


def _render_template(
    name: str, dest: Path, *, force: bool = False, **ctx: object
) -> bool:
    """Render a Jinja2 template to dest. Returns True if (re-)written.

    Skips if dest exists and force is False.
    """
    if dest.exists() and not force:
        return False
    template_text = (
        importlib.resources.files("daily_driver.resources.templates")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )
    from jinja2 import Environment, StrictUndefined

    env = Environment(undefined=StrictUndefined)
    rendered = env.from_string(template_text).render(**ctx)
    dest.write_text(rendered, encoding="utf-8")
    return True


def _scaffold_user_dirs(claude_root: Path) -> tuple[bool, bool]:
    """Create user-territory subdirs if absent. Returns (commands_created, agents_created)."""
    cmd_dir = claude_root / "commands" / "user"
    agent_dir = claude_root / "agents" / "user"
    cmd_created = not cmd_dir.exists()
    agent_created = not agent_dir.exists()
    cmd_dir.mkdir(parents=True, exist_ok=True)
    agent_dir.mkdir(parents=True, exist_ok=True)
    return cmd_created, agent_created


def _scaffold_gitignore(root: Path, *, force: bool = False) -> bool:
    """Write workspace-root .gitignore from gitignore.j2 if absent (or force).

    Returns True when the file was (re-)written.
    """
    dest = root / ".gitignore"
    return _render_template("gitignore.j2", dest, force=force)


def _format_summary(
    root: Path,
    created: list[str],
    skipped: list[str],
    generate_result: object,
    plugins_on: tuple[Plugin, ...],
    switched_off: tuple[Plugin, ...] = (),
) -> str:
    """Render the post-init summary block. Created/Skipped omitted if empty.

    `generate_result` is the return value of `core.generate.generate()`:
    None when the version-stamp fast-path skipped all work; a
    GenerationResult with `n_written` / `n_preserved` otherwise.
    A plugin that is off is not mentioned, unless `--force` switched it off
    (`switched_off`).
    """
    lines = [f"Initialized workspace at {root}"]
    if created:
        lines.append(f"  Created: {', '.join(created)}")
    if skipped:
        lines.append(f"  Skipped: {', '.join(skipped)} (already present)")
    if generate_result is None:
        lines.append("  Generated: unchanged (managed assets already current)")
    else:
        count = getattr(generate_result, "n_written", 0) + getattr(
            generate_result, "n_preserved", 0
        )
        lines.append(
            f"  Generated: .claude/*/daily-driver/* ({count} file"
            f"{'s' if count != 1 else ''})"
        )
    lines.extend(
        f"  {_title(plugin)}: on" for plugin in plugins_on if plugin.init_flag_help
    )
    lines.extend(
        f"  {_title(plugin)}: off (it was on; to switch it on, "
        f"{switch_on_hint(plugin.name, None)}, then run `daily-driver doctor --fix`)"
        for plugin in switched_off
        if plugin.init_flag_help
    )
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    root.mkdir(parents=True, exist_ok=True)

    marker = root / ".dd-config.yaml"
    marker_existed = marker.exists()

    created: list[str] = []
    skipped: list[str] = []
    requested = [
        plugin for plugin in PLUGINS if getattr(args, _flag_dest(plugin), False)
    ]
    plugins = {plugin.name for plugin in requested}

    # `--force` writes a new config with only the requested plugins on; the
    # summary says which plugins that switched off.
    was_on = _plugins_on_in(marker) if marker_existed and args.force else ()

    backup: Path | None = None
    if marker_existed and args.force:
        # Preserve the previous config as .bak so users can recover from a
        # botched `--force`. Removed on a successful run.
        backup = marker.with_suffix(marker.suffix + ".bak")
        if backup.exists():
            backup.unlink()
        marker.rename(backup)

    def _restore_backup() -> None:
        if backup is not None and backup.exists():
            if marker.exists():
                marker.unlink()
            backup.rename(marker)

    try:
        if marker_existed and not args.force:
            # Idempotent path: load the existing workspace rather than calling
            # Workspace.init (which raises when the marker is present).
            workspace = Workspace.discover_or_fail(override=root)
            skipped.append(".dd-config.yaml")
        else:
            workspace = Workspace.init(root, plugins=plugins)
            created.append(".dd-config.yaml")
    except WorkspaceError as exc:
        _restore_backup()
        Console.error(str(exc))
        return 1

    from daily_driver.plugins import enabled_plugins

    plugins_on = enabled_plugins(workspace.config)
    for plugin in requested:
        if marker_existed and not args.force and plugin not in plugins_on:
            hint = switch_on_hint(plugin.name, workspace.config)
            Console.warning(
                f".dd-config.yaml already exists, so {_init_flag(plugin)} did not "
                f"change it. To switch {_title(plugin).lower()} on, {hint}, then "
                "run `daily-driver doctor --fix`."
            )

    # Seed files (rendered once, then the user's): only write when absent, even
    # under --force. Tracking the write/skip outcome lets us surface "what
    # changed" in the summary.
    for name in ("context.md", "voice-profile.md"):
        if _seed_template(name, root / name, workspace.config):
            created.append(name)
        else:
            skipped.append(name)

    claude_root = root / ".claude"
    claude_dir_existed = claude_root.exists()
    claude_root.mkdir(exist_ok=True)
    if not claude_dir_existed:
        created.append(".claude/")

    cmd_created, agent_created = _scaffold_user_dirs(claude_root)
    (created if cmd_created else skipped).append(".claude/commands/user/")
    (created if agent_created else skipped).append(".claude/agents/user/")

    # .gitignore: never overwritten — user owns it after first write.
    if _scaffold_gitignore(root, force=False):
        created.append(".gitignore")
    else:
        skipped.append(".gitignore")

    # Idempotence: when re-running without --force on a workspace whose stamp
    # matches (same version, same plugins on), skip the inner generate work
    # entirely. The stamp fast-path in generate() returns None and no logs fire. With
    # --force, wipe and rewrite (legacy behavior — user explicitly asked).
    # deferred: core.generate pulls in Jinja2 + pydantic, only needed here.
    from daily_driver.core.generate import generate

    try:
        if args.force:
            result = generate(workspace, ignore_drift=True, force_overwrite=True)
        else:
            result = generate(workspace, ignore_drift=False, force_overwrite=False)
    except Exception as exc:  # noqa: BLE001
        _restore_backup()
        Console.error(f"workspace generation: {exc}")
        if (getattr(args, "verbose", 0) or 0) >= 1:
            traceback.print_exc(file=sys.stderr)
        return 1

    if backup is not None and backup.exists():
        backup.unlink()

    # The scaffold summary is a write-confirmation, not routine status: the user
    # needs to see that init actually wrote files even under -q. Console.info and
    # Console.success both suppress under quiet mode, so print straight to the
    # stderr log console (where the summary has always lived) to bypass the gate.
    Console.get_log_console().print(
        _format_summary(
            root,
            created,
            skipped,
            result,
            plugins_on,
            tuple(plugin for plugin in was_on if plugin not in plugins_on),
        ),
        style="success",
    )
    return 0
