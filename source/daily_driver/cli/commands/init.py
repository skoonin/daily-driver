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
from daily_driver.core.generate import generate
from daily_driver.core.workspace import Workspace, WorkspaceError


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
    add_global_flags(parser)
    parser.set_defaults(func=run)
    return parser


def _copy_template(name: str, dest: Path) -> bool:
    """Write a static template file to dest if absent. Returns True if created."""
    if dest.exists():
        return False
    content = (
        importlib.resources.files("daily_driver.templates")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )
    dest.write_text(content, encoding="utf-8")
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
        importlib.resources.files("daily_driver.templates")
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
    root: Path, created: list[str], skipped: list[str], generated: int
) -> str:
    """Render the post-init summary block. Created/Skipped omitted if empty."""
    lines = [f"Initialized workspace at {root}"]
    if created:
        lines.append(f"  Created: {', '.join(created)}")
    if skipped:
        lines.append(f"  Skipped: {', '.join(skipped)} (already present)")
    lines.append(
        f"  Generated: .claude/*/daily-driver/* ({generated} file"
        f"{'s' if generated != 1 else ''})"
    )
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    root.mkdir(parents=True, exist_ok=True)

    marker = root / ".dd-config.yaml"
    marker_existed = marker.exists()

    created: list[str] = []
    skipped: list[str] = []

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
            workspace = Workspace.init(root)
            created.append(".dd-config.yaml")
    except WorkspaceError as exc:
        _restore_backup()
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Static files: only write when absent, even under --force. Tracking the
    # write/skip outcome lets us surface "what changed" in the summary.
    for name in ("context.md", "voice-profile.md"):
        if _copy_template(name, root / name):
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

    try:
        result = generate(workspace, ignore_drift=True, force_overwrite=True)
    except Exception as exc:  # noqa: BLE001
        _restore_backup()
        print(f"error during workspace generation: {exc}", file=sys.stderr)
        if getattr(args, "verbose", False):
            traceback.print_exc(file=sys.stderr)
        return 1

    if backup is not None and backup.exists():
        backup.unlink()

    generated_count = getattr(result, "n_written", 0) + getattr(
        result, "n_preserved", 0
    )

    print(_format_summary(root, created, skipped, generated_count), file=sys.stderr)
    return 0
