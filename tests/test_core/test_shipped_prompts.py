"""Banned-token lint for shipped Claude commands and agent prompts.

Catches stale references that survive renames or deletions: command names
removed in W-prompt-sync, schema fields obsoleted by W9, scheduler renames
from W8, jobs rename from W2. The list grows when historical churn leaves
landmines that grep can detect mechanically.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PROMPT_ROOTS = (
    Path(__file__).resolve().parents[2]
    / "source"
    / "daily_driver"
    / "resources"
    / "slash_commands"
    / "daily-driver",
    Path(__file__).resolve().parents[2]
    / "source"
    / "daily_driver"
    / "resources"
    / "agents"
    / "daily-driver",
)

BANNED_TOKENS = (
    "app-NNN",
    "app_id",
    "install-scheduler",
    "uninstall-scheduler",
    "materialize",
    "materialize.lock",
    "scrape-jobs",
    "daily-driver read",
    "ensure-daily-dir",
    "gather sessions",
    "gather notes",
)


def _shipped_prompt_files() -> list[Path]:
    files: list[Path] = []
    for root in _PROMPT_ROOTS:
        if not root.is_dir():
            continue
        files.extend(sorted(root.glob("*.md")))
    return files


_FILES = _shipped_prompt_files()
_PARAMS = [(f, tok) for f in _FILES for tok in BANNED_TOKENS]


def test_shipped_prompt_files_discovered() -> None:
    """Guard against the test silently passing because globbing found nothing."""
    assert _FILES, f"no shipped prompt files found under {_PROMPT_ROOTS}"


@pytest.mark.parametrize(("path", "token"), _PARAMS, ids=lambda v: str(v))
def test_shipped_prompt_has_no_banned_token(path: Path, token: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert token not in text, (
        f"banned token {token!r} found in {path.name}; "
        "rename/remove the reference to match current CLI surface"
    )
