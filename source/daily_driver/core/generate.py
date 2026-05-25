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
from dataclasses import dataclass
from pathlib import Path

from daily_driver.core import manifest as _manifest
from daily_driver.core import version_stamp
from daily_driver.core.contract import ENTRIES
from daily_driver.core.locking import file_lock
from daily_driver.core.logging import get_logger
from daily_driver.core.workspace import Workspace
from daily_driver.plugins._base import PackageDataDir

_logger = get_logger("generate")

# Core's own package-data: the daily-driver workflow commands + agents. Each
# plugin appends its own dirs (see _package_data_dirs); the daily-driver subdir
# has a hyphen so it's reached via joinpath rather than a module identifier.
_CORE_PACKAGE_DATA: tuple[PackageDataDir, ...] = (
    PackageDataDir("daily_driver.commands", "commands/daily-driver"),
    PackageDataDir("daily_driver.agents", "agents/daily-driver"),
)


def _package_data_dirs() -> list[PackageDataDir]:
    """Core baseline package-data dirs plus every registered plugin's.

    PLUGINS is imported lazily here so importing core.generate does not pull in
    plugin implementation modules at import time.
    """
    from daily_driver.plugins import PLUGINS

    dirs = list(_CORE_PACKAGE_DATA)
    for plugin in PLUGINS:
        dirs.extend(plugin.package_data_dirs)
    return dirs


@dataclass(frozen=True)
class GenerationResult:
    """Counts surfaced after a successful generate() run.

    n_written counts all package-managed files written this run (commands,
    agents, and contract-entry templates such as README.md). n_preserved counts
    files skipped because the SHA-256 manifest detected user edits.
    """

    n_written: int
    n_preserved: int


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


def _package_md_names(
    pkg_traversable: importlib.resources.abc.Traversable,
) -> set[str]:
    """Names of the safe .md files a package-data source contributes."""
    return {
        entry.name
        for entry in pkg_traversable.iterdir()
        if entry.name.endswith(".md")
        and "/" not in entry.name
        and not entry.name.startswith(".")
        and ".." not in entry.name
    }


def _remove_stale_files(
    dest_dir: Path,
    pkg_names: set[str],
    *,
    workspace_root: Path,
    state_dir: Path,
) -> None:
    """Remove files from dest_dir whose names are not in pkg_names.

    pkg_names is the union of every package-data source mapped to this dest, so
    co-located sources do not delete each other's files. Only files that are NOT
    user-edited (per the SHA-256 manifest) are removed; user-edited files are
    preserved even when absent from the package.
    """
    if not dest_dir.exists():
        return

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
) -> GenerationResult | None:
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
        return None

    lock_path = workspace.ephemeral_dir / "generate.lock"
    with file_lock(lock_path):
        # Re-check inside the lock: another process may have generated while we waited.
        if not ignore_drift and not version_stamp.is_drifted(
            workspace.state_dir, workspace.version
        ):
            return None

        _logger.info("Generating assets for version %s", workspace.version)

        claude_root = workspace.root / ".claude"

        # Resolve each package-data source to its (traversable, dest) pair. The
        # .md files live in a hyphenated leaf subdir (e.g. commands/daily-driver)
        # that is not a valid module identifier, so reach it via joinpath on the
        # source package's traversable using the dest's leaf name. Two sources may
        # map to the same dest; the per-dest passes below dedupe so a later source
        # does not erase files an earlier one wrote.
        resolved: list[tuple[importlib.resources.abc.Traversable, Path]] = []
        for data_dir in _package_data_dirs():
            dest_path = Path(data_dir.dest)
            pkg_traversable = importlib.resources.files(
                data_dir.source_package
            ).joinpath(dest_path.name)
            resolved.append((pkg_traversable, claude_root / dest_path))

        # Union of valid .md names per dest, so a dest fed by two sources keeps
        # files from both during stale removal rather than treating the other
        # source's files as stale.
        names_by_dest: dict[Path, set[str]] = {}
        for pkg_traversable, dest_dir in resolved:
            names_by_dest.setdefault(dest_dir, set()).update(
                _package_md_names(pkg_traversable)
            )

        # When overwriting user edits (force_overwrite), wipe each dest subdir once
        # for a clean slate. When preserving user edits, do a targeted cleanup:
        # remove only stale files absent from the package and not user-edited.
        # Both passes run once per unique dest so co-located sources coexist.
        for dest_dir, pkg_names in names_by_dest.items():
            if force_overwrite:
                _wipe_and_recreate(dest_dir)
            else:
                dest_dir.mkdir(parents=True, exist_ok=True)
                _remove_stale_files(
                    dest_dir,
                    pkg_names,
                    workspace_root=workspace.root,
                    state_dir=workspace.state_dir,
                )

        n_copied = 0
        n_preserved = 0
        all_written: list[Path] = []
        for pkg_traversable, dest_dir in resolved:
            count, paths, skipped = _copy_package_md(
                pkg_traversable,
                dest_dir,
                workspace_root=workspace.root,
                state_dir=workspace.state_dir,
                force_overwrite=force_overwrite,
            )
            n_copied += count
            n_preserved += len(skipped)
            all_written.extend(paths)
        _logger.info("Copied %d package-managed file(s)", n_copied)

        if all_written:
            _manifest.record(workspace.state_dir, workspace.root, all_written)

        entry_paths = _render_contract_entries(
            workspace, force_overwrite=force_overwrite
        )
        if entry_paths:
            _manifest.record(workspace.state_dir, workspace.root, entry_paths)

        _render_settings(workspace)
        _copy_hooks(workspace)

        # Stamp written last — crash before here = stamp stays stale = redo on next run.
        version_stamp.write(workspace.state_dir, workspace.version)
        _logger.info("Version stamp written: %s", workspace.version)

        return GenerationResult(
            n_written=n_copied + len(entry_paths),
            n_preserved=n_preserved,
        )


def _merge_settings(
    existing_text: str, rendered_text: str, *, settings_path: Path
) -> str:
    """Merge package-default settings with user settings, preserving user-added keys.

    Package-managed keys (those present in the fresh defaults) are always refreshed
    from the template. User-added top-level keys (those absent from the defaults)
    are preserved. This ensures version bumps and permission changes propagate while
    not clobbering user customizations outside the package namespace.

    If the existing file is not valid JSON, it cannot be merged, so it is copied to
    settings.local.json.invalid before the rendered defaults replace it — discarding
    it silently would erase any recoverable user customization.
    """
    try:
        existing = json.loads(existing_text)
    except json.JSONDecodeError as exc:
        backup_path = settings_path.with_suffix(settings_path.suffix + ".invalid")
        shutil.copyfile(settings_path, backup_path)
        _logger.warning(
            "Existing %s is not valid JSON (%s); backed up to %s before applying defaults",
            settings_path,
            exc,
            backup_path,
        )
        return rendered_text

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


def _render_contract_entries(
    workspace: Workspace,
    *,
    force_overwrite: bool,
) -> list[Path]:
    """Render each ContractEntry template into the workspace root.

    Returns the list of paths that were written (skipped files are excluded).
    User-edited files are preserved unless force_overwrite=True.
    """
    import jinja2
    from jinja2 import Environment, StrictUndefined

    env = Environment(undefined=StrictUndefined)
    written: list[Path] = []
    for entry in ENTRIES:
        # Guard against traversal attacks in case ENTRIES ever gains dynamic sources.
        if "/" in entry.dst or entry.dst.startswith(".") or ".." in entry.dst:
            _logger.warning("Skipping suspicious ContractEntry dst: %r", entry.dst)
            continue
        if "/" in entry.src or entry.src.startswith(".") or ".." in entry.src:
            _logger.warning("Skipping suspicious ContractEntry src: %r", entry.src)
            continue

        dest = workspace.root / entry.dst
        rel = entry.dst

        if not force_overwrite and _manifest.is_user_edited(
            workspace.root, workspace.state_dir, rel
        ):
            _logger.warning(
                "Preserving user-edited package-managed file: %s (use --reset to overwrite)",
                rel,
            )
            continue

        try:
            tmpl_traversable = importlib.resources.files(
                "daily_driver.templates"
            ).joinpath(entry.src)
            template_text = tmpl_traversable.read_text(encoding="utf-8")
        except (FileNotFoundError, TypeError):
            _logger.warning(
                "%s not found in daily_driver.templates — skipping %s",
                entry.src,
                rel,
            )
            continue

        try:
            rendered = env.from_string(template_text).render(workspace=workspace)
        except jinja2.TemplateError as exc:
            _logger.warning("Failed to render %s: %s — skipping", entry.src, exc)
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(dest, rendered)
        written.append(dest)
        _logger.info("Rendered %s -> %s", entry.src, rel)

    return written


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

    settings_path = workspace.root / ".claude" / "settings.local.json"

    try:
        import jinja2
        from jinja2 import Environment, StrictUndefined

        env = Environment(undefined=StrictUndefined)
        rendered = env.from_string(template_text).render(workspace=workspace)
    except (jinja2.TemplateError, OSError) as exc:
        _logger.warning(
            "Failed to render settings.local.json.j2 for %s: %s — skipping",
            settings_path,
            exc,
        )
        return

    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if settings_path.exists():
        existing_text = settings_path.read_text(encoding="utf-8")
        rendered = _merge_settings(existing_text, rendered, settings_path=settings_path)

    _atomic_write_text(settings_path, rendered)
    _logger.info("settings.local.json rendered at %s", settings_path)
