"""Version stamp for drift detection between installed package and generated workspace files.

The stamp file lives at {state_dir}/version and contains the value of expected():
the installed __version__, plus the names of the plugins switched on, since those
decide which files and prompt sections generate() writes.
Writing it last in the generation sequence means a crash mid-generate leaves the
stamp stale, so the next invocation sees drift and redoes the work — idempotent.
"""

from __future__ import annotations

import os
from pathlib import Path

STAMP_FILENAME = "version"


def expected(version: str, config: object) -> str:
    """The stamp a workspace carries once this install generates it for config.

    `1.3.0` with no plugins on, `1.3.0+job_search` with job search on.
    """
    from daily_driver.plugins import enabled_plugins

    names = sorted(plugin.name for plugin in enabled_plugins(config))
    return "+".join([version, *names])


def split(stamp: str) -> tuple[str, list[str]]:
    """Split a stamp from expected() into (version, plugin names).

    Relies on `__version__` never carrying a `+` (a PEP 440 local version such
    as `1.3.0+g1234` would read as a plugin name). Plugin names are
    identifiers, so they never do.
    """
    version, *plugins = stamp.split("+")
    return version, plugins


def read(state_dir: Path) -> str | None:
    """Return the stamp (see expected()), or None if the stamp file is absent."""
    stamp = state_dir / STAMP_FILENAME
    try:
        return stamp.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None


def write(state_dir: Path, version: str) -> None:
    """Atomically write version to {state_dir}/version."""
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp = state_dir / (STAMP_FILENAME + ".tmp")
    tmp.write_text(version, encoding="utf-8")
    os.replace(tmp, state_dir / STAMP_FILENAME)


def is_drifted(state_dir: Path, current: str) -> bool:
    """Return True if the stamped version is missing or does not match current."""
    stamped = read(state_dir)
    return stamped is None or stamped != current
