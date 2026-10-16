"""Helpers for tests that drive a real, `init`-scaffolded workspace."""

from __future__ import annotations

from pathlib import Path


def enable_job_search(ws_root: Path) -> None:
    """Switch job search on in an init-scaffolded workspace config.

    A plain `init` leaves the plugins section out, so appending a top-level
    `plugins:` block cannot collide with an existing key.
    """
    config = ws_root / ".dd-config.yaml"
    with config.open("a", encoding="utf-8") as fh:
        fh.write(_ON_BLOCK)


def disable_job_search(ws_root: Path) -> None:
    """Switch job search off (keeping the block) after enable_job_search."""
    config = ws_root / ".dd-config.yaml"
    text = config.read_text(encoding="utf-8")
    if _ON_BLOCK not in text:
        raise AssertionError("enable_job_search was not called on this workspace")
    config.write_text(
        text.replace(_ON_BLOCK, "\nplugins:\n  job_search: {enabled: false}\n"),
        encoding="utf-8",
    )


_ON_BLOCK = "\nplugins:\n  job_search: {}\n"
