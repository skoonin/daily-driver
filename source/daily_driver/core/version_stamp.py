"""Version stamp for drift detection between installed package and generated workspace files.

The stamp file lives at {state_dir}/version and contains the installed __version__ string.
Writing it last in the generation sequence means a crash mid-generate leaves the
stamp stale, so the next invocation sees drift and redoes the work — idempotent.
"""

from __future__ import annotations

import os
from pathlib import Path

STAMP_FILENAME = "version"


def read(state_dir: Path) -> str | None:
    """Return the stamped version string, or None if the stamp file is absent."""
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
