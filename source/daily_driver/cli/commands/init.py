"""init subcommand: scaffold a new daily-driver workspace."""

from __future__ import annotations

import argparse
import importlib.resources
import sys
import traceback
from pathlib import Path

from daily_driver.core.materialize import materialize
from daily_driver.core.workspace import Workspace, WorkspaceError


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    parents: list[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "init",
        parents=parents,
        help="Scaffold a new daily-driver workspace",
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
    parser.set_defaults(func=run)
    return parser


def _copy_template(name: str, dest: Path) -> None:
    """Write a static template file to dest if it does not already exist."""
    if dest.exists():
        return
    content = (
        importlib.resources.files("daily_driver.templates")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )
    dest.write_text(content, encoding="utf-8")


def _render_template(
    name: str, dest: Path, *, force: bool = False, **ctx: object
) -> None:
    """Render a Jinja2 template to dest. Skips if dest exists and force is False."""
    if dest.exists() and not force:
        return
    template_text = (
        importlib.resources.files("daily_driver.templates")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )
    from jinja2 import Environment, StrictUndefined

    env = Environment(undefined=StrictUndefined)
    rendered = env.from_string(template_text).render(**ctx)
    dest.write_text(rendered, encoding="utf-8")


def _scaffold_user_dirs(claude_root: Path) -> None:
    """Create user-territory Claude command and agent subdirectories if absent."""
    (claude_root / "commands" / "user").mkdir(parents=True, exist_ok=True)
    (claude_root / "agents" / "user").mkdir(parents=True, exist_ok=True)


def _scaffold_gitignore(root: Path, *, force: bool = False) -> None:
    """Write workspace-root .gitignore from gitignore.j2 template if absent (or force)."""
    dest = root / ".gitignore"
    _render_template("gitignore.j2", dest, force=force)


def run(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    root.mkdir(parents=True, exist_ok=True)

    marker = root / ".dd-config.yaml"

    if marker.exists() and not args.force:
        print(
            f"workspace already initialized at {root} (use --force to overwrite)",
            file=sys.stderr,
        )
        return 1

    backup: Path | None = None
    if marker.exists() and args.force:
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
        workspace = Workspace.init(root)
    except WorkspaceError as exc:
        _restore_backup()
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Static files: only write when absent, even under --force.
    _copy_template("context.md", root / "context.md")
    _copy_template("voice-profile.md", root / "voice-profile.md")

    # Ensure .claude/ exists and scaffold user-territory subdirs.
    claude_root = root / ".claude"
    claude_root.mkdir(exist_ok=True)
    _scaffold_user_dirs(claude_root)

    # .gitignore: write if absent; --force does not overwrite (user owns it after first write).
    _scaffold_gitignore(root, force=False)

    try:
        materialize(workspace, ignore_drift=True, force_overwrite=True)
    except Exception as exc:  # noqa: BLE001
        _restore_backup()
        print(f"error during materialize: {exc}", file=sys.stderr)
        if getattr(args, "verbose", False):
            traceback.print_exc(file=sys.stderr)
        return 1

    if backup is not None and backup.exists():
        backup.unlink()

    print(
        f"Initialized workspace at {root}\n"
        f"  Edit {root / '.dd-config.yaml'} to configure\n"
        f"  Run `daily-driver doctor` to verify installation",
        file=sys.stderr,
    )
    return 0
