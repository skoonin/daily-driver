"""Discover git repositories under a list of search roots.

Treats both `.git` directories (regular clones) and `.git` files (linked
worktrees created by ``git worktree add``) as repo markers, so a configured
search path that points at a worktrees parent finds them all.
"""

from __future__ import annotations

from pathlib import Path

from daily_driver.core.logging import get_logger

log = get_logger(__name__)

_MAX_DEPTH = 4


def _walk(root: Path, max_depth: int, found: set[Path]) -> None:
    if max_depth < 0:
        return
    if (root / ".git").exists():
        found.add(root.resolve())
        return
    if max_depth == 0:
        return
    try:
        children = list(root.iterdir())
    except (PermissionError, OSError) as exc:
        log.debug("git_discovery: cannot list %s: %s", root, exc)
        return
    for child in children:
        if not child.is_dir() or child.is_symlink():
            continue
        if child.name.startswith("."):
            continue
        _walk(child, max_depth - 1, found)


def discover_repos(roots: list[Path]) -> list[Path]:
    """Return resolved repo roots discovered under any of ``roots``.

    Each result is the directory that contains the ``.git`` marker. Output
    is de-duplicated and sorted. Non-existent roots are skipped silently
    (logged at DEBUG). Walk depth is capped at 4: at the measured ~145ms
    for 13 repos under ``~/git``, deeper layouts should be configured as
    explicit roots rather than scanned.
    """
    found: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            log.debug("git_discovery: skipping missing root %s", root)
            continue
        _walk(root, _MAX_DEPTH, found)
    return sorted(found)
