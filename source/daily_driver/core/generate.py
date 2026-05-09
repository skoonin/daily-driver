"""Copy package-bundled commands, agents, and settings into the workspace .claude/ tree.

Generation is triggered on version drift or via ignore_drift=True. The version stamp
is written last so a crash mid-run leaves the stamp stale, ensuring the next invocation
retries — idempotent by construction.

File ownership boundaries: only .claude/commands/daily-driver/, .claude/agents/daily-driver/,
and .claude/settings.local.json are ever touched. All other user files are sacred.
User edits to package-managed .md files are detected via SHA-256 manifest and preserved
unless force_overwrite=True.
"""

from __future__ import annotations

import importlib.resources
import importlib.resources.abc
import json
import os
import shutil
from pathlib import Path

from daily_driver.core import manifest as _manifest
from daily_driver.core import version_stamp
from daily_driver.core.locking import file_lock
from daily_driver.core.logging import get_logger
from daily_driver.core.workspace import Workspace

_logger = get_logger("generate")


def _atomic_write_text(dest: Path, content: str) -> None:
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, dest)


def _copy_package_md(
    pkg_traversable: importlib.resources.abc.Traversable,
    dest_dir: Path,
    *,
    workspace_root: Path,
    state_dir: Path,
    force_overwrite: bool,
) -> tuple[int, list[Path], list[str]]:
    """Copy *.md files from pkg_traversable into dest_dir.

    Returns (count_written, written_paths, skipped_names).
    Skips files where the user has made edits (detected via SHA-256 manifest)
    unless force_overwrite=True.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    written: list[Path] = []
    skipped: list[str] = []

    for entry in pkg_traversable.iterdir():
        if entry.name in ("__init__.py", "__pycache__"):
            continue
        if not entry.name.endswith(".md"):
            continue
        # Guard against tampered package contents with traversal or hidden names.
        if "/" in entry.name or entry.name.startswith(".") or ".." in entry.name:
            _logger.warning("Skipping suspicious package-data entry: %r", entry.name)
            continue

        dest_file = dest_dir / entry.name
        rel = str(dest_file.relative_to(workspace_root))

        if not force_overwrite and _manifest.is_user_edited(
            workspace_root, state_dir, rel
        ):
            _logger.warning(
                "Preserving user-edited package-managed file: %s (use --reset to overwrite)",
                rel,
            )
            skipped.append(rel)
            continue

        content = entry.read_text(encoding="utf-8")
        _atomic_write_text(dest_file, content)
        written.append(dest_file)
        count += 1

    return count, written, skipped


def _wipe_and_recreate(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)


def _remove_stale_files(
    dest_dir: Path,
    pkg_traversable: importlib.resources.abc.Traversable,
    *,
    workspace_root: Path,
    state_dir: Path,
) -> None:
    """Remove files from dest_dir that are not present in pkg_traversable.

    Only removes files that are NOT user-edited (per the SHA-256 manifest).
    User-edited files are preserved even if they no longer exist in the package.
    """
    if not dest_dir.exists():
        return

    pkg_names = {
        entry.name
        for entry in pkg_traversable.iterdir()
        if entry.name.endswith(".md")
        and "/" not in entry.name
        and not entry.name.startswith(".")
        and ".." not in entry.name
    }

    for existing in dest_dir.iterdir():
        if not existing.name.endswith(".md"):
            continue
        if existing.name not in pkg_names:
            rel = str(existing.relative_to(workspace_root))
            if not _manifest.is_user_edited(workspace_root, state_dir, rel):
                existing.unlink()
                _logger.debug("Removed stale package file: %s", rel)


def generate(
    workspace: Workspace,
    *,
    ignore_drift: bool = False,
    force_overwrite: bool = False,
) -> None:
    """Generate package assets into the workspace .claude/ tree.

    ignore_drift: when True, skip the version-stamp fast-path and always run the
        generation body. Use this when you need to regenerate regardless
        of whether the version has changed (e.g. --reset).

    force_overwrite: when True, overwrite package-managed .md files even if the
        user has edited them (detected via SHA-256 manifest). When False (the
        default), user edits are preserved and a warning is logged for each
        skipped file.

    The fast-path skips all work when the stamped version matches the installed
    version and ignore_drift is False. Otherwise acquires an exclusive lock,
    re-checks drift inside the lock (double-checked locking), wipes the
    daily-driver-owned subdirs, copies package data, and writes the version
    stamp last so a crash mid-run leaves the stamp stale and triggers a retry.
    """
    if not ignore_drift and not version_stamp.is_drifted(
        workspace.state_dir, workspace.version
    ):
        return

    lock_path = workspace.ephemeral_dir / "generate.lock"
    with file_lock(lock_path):
        # Re-check inside the lock: another process may have generated while we waited.
        if not ignore_drift and not version_stamp.is_drifted(
            workspace.state_dir, workspace.version
        ):
            return

        _logger.info("Generating assets for version %s", workspace.version)

        claude_root = workspace.root / ".claude"

        commands_dest = claude_root / "commands" / "daily-driver"
        agents_dest = claude_root / "agents" / "daily-driver"

        # Copy *.md from package data. daily-driver subdir has a hyphen so it's not
        # a valid Python module identifier; navigate via joinpath on the parent traversable.
        commands_pkg = importlib.resources.files("daily_driver.commands").joinpath(
            "daily-driver"
        )
        agents_pkg = importlib.resources.files("daily_driver.agents").joinpath(
            "daily-driver"
        )

        # When overwriting user edits (force_overwrite), wipe the entire subdir first
        # for a clean slate. When preserving user edits, do a targeted cleanup: remove
        # only stale files that are absent from the package and not user-edited.
        if force_overwrite:
            _wipe_and_recreate(commands_dest)
            _wipe_and_recreate(agents_dest)
        else:
            commands_dest.mkdir(parents=True, exist_ok=True)
            agents_dest.mkdir(parents=True, exist_ok=True)
            _remove_stale_files(
                commands_dest,
                commands_pkg,
                workspace_root=workspace.root,
                state_dir=workspace.state_dir,
            )
            _remove_stale_files(
                agents_dest,
                agents_pkg,
                workspace_root=workspace.root,
                state_dir=workspace.state_dir,
            )

        n_commands, cmd_paths, cmd_skipped = _copy_package_md(
            commands_pkg,
            commands_dest,
            workspace_root=workspace.root,
            state_dir=workspace.state_dir,
            force_overwrite=force_overwrite,
        )
        n_agents, agent_paths, agent_skipped = _copy_package_md(
            agents_pkg,
            agents_dest,
            workspace_root=workspace.root,
            state_dir=workspace.state_dir,
            force_overwrite=force_overwrite,
        )
        _logger.info("Copied %d command(s), %d agent(s)", n_commands, n_agents)

        all_written = cmd_paths + agent_paths
        if all_written:
            _manifest.record(workspace.state_dir, workspace.root, all_written)

        _render_settings(workspace)
        _copy_hooks(workspace)

        # Stamp written last — crash before here = stamp stays stale = redo on next run.
        version_stamp.write(workspace.state_dir, workspace.version)
        _logger.info("Version stamp written: %s", workspace.version)


def _merge_settings(existing_text: str, rendered_text: str) -> str:
    """Merge package-default settings with user settings, preserving user-added keys.

    Package-managed keys (those present in the fresh defaults) are always refreshed
    from the template. User-added top-level keys (those absent from the defaults)
    are preserved. This ensures version bumps and permission changes propagate while
    not clobbering user customizations outside the package namespace.
    """
    try:
        existing = json.loads(existing_text)
    except json.JSONDecodeError:
        existing = {}

    try:
        defaults = json.loads(rendered_text)
    except json.JSONDecodeError:
        return rendered_text

    if not isinstance(existing, dict) or not isinstance(defaults, dict):
        return rendered_text

    # Start from fresh package defaults, then layer in user-added keys only.
    merged = dict(defaults)
    for key, value in existing.items():
        if key not in defaults:
            merged[key] = value
    return json.dumps(merged, indent=2)


def _copy_hooks(workspace: Workspace) -> None:
    """Copy package-managed hook scripts into <workspace>/.claude/hooks/."""
    hooks_dest = workspace.root / ".claude" / "hooks"
    hooks_dest.mkdir(parents=True, exist_ok=True)
    hooks_pkg = importlib.resources.files("daily_driver.templates").joinpath("hooks")
    for entry in hooks_pkg.iterdir():
        if not entry.name.endswith(".sh"):
            continue
        dest = hooks_dest / entry.name
        dest.write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")
        dest.chmod(0o755)


def _render_settings(workspace: Workspace) -> None:
    """Render settings.local.json from template; merge with existing user keys if present."""
    try:
        tmpl_traversable = importlib.resources.files("daily_driver.templates").joinpath(
            "settings.local.json.j2"
        )
        template_text = tmpl_traversable.read_text(encoding="utf-8")
    except (FileNotFoundError, TypeError):
        _logger.warning(
            "settings.local.json.j2 not found in daily_driver.templates — skipping settings.local.json render"
        )
        return

    try:
        from jinja2 import Environment, StrictUndefined

        env = Environment(undefined=StrictUndefined)
        rendered = env.from_string(template_text).render(workspace=workspace)
    except Exception as exc:
        _logger.warning("Failed to render settings.local.json.j2: %s — skipping", exc)
        return

    settings_path = workspace.root / ".claude" / "settings.local.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if settings_path.exists():
        existing_text = settings_path.read_text(encoding="utf-8")
        rendered = _merge_settings(existing_text, rendered)

    _atomic_write_text(settings_path, rendered)
    _logger.info("settings.local.json rendered at %s", settings_path)
