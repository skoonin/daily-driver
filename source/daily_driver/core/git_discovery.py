"""Discover git repositories under a list of search roots.

Used by `gather git` when no explicit `--repo` is given and one or more
``gather.git.search_paths`` are configured. A path is considered a repo
if it contains a ``.git`` directory (worktrees with a ``.git`` *file*
that points elsewhere are intentionally not followed; treat each clone
as its own repo).

Walk depth is capped at ``max_depth=4`` -- measured at ~145ms for ~13
repos under ``~/git``, well below the 500ms threshold that would justify
a TTL cache. Users with deeper layouts should configure explicit roots
rather than scanning a deeply-nested home directory.
"""

from __future__ import annotations

from pathlib import Path

from daily_driver.core.logging import get_logger

log = get_logger(__name__)

DEFAULT_MAX_DEPTH = 4


def _walk(root: Path, max_depth: int, found: set[Path]) -> None:
    if max_depth < 0:
        return
    git_dir = root / ".git"
    if git_dir.is_dir():
        # Stop descent at the repo root -- nested .git inside a repo is noise.
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


def discover_repos(roots: list[Path], max_depth: int = DEFAULT_MAX_DEPTH) -> list[Path]:
    """Return resolved repo roots discovered under any of ``roots``.

    Each result is the directory that contains the ``.git`` directory.
    Output is de-duplicated by resolved path and sorted for deterministic
    iteration. Non-existent or non-directory roots are silently skipped
    (logged at DEBUG).
    """
    found: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            log.debug("git_discovery: skipping missing root %s", root)
            continue
        _walk(root, max_depth, found)
    return sorted(found)
