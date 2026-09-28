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


_TEMPLATES_DIR = (
    Path(__file__).resolve().parents[2]
    / "source"
    / "daily_driver"
    / "resources"
    / "templates"
)
_RENDERED_FILES = [
    *_FILES,
    _TEMPLATES_DIR / "workspace-readme.md.j2",
    _TEMPLATES_DIR / "context.md",
]


@pytest.mark.parametrize("path", _RENDERED_FILES, ids=lambda p: p.name)
def test_inline_block_tag_never_ends_a_line(path: Path) -> None:
    """trim_blocks eats the newline after a block tag.

    That is right for a tag alone on its line, but a tag ending a line of
    text would join it to the next line. End such a line with `+%}` instead.
    """
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped.endswith("%}") or stripped.endswith("+%}"):
            continue
        assert (
            stripped.startswith("{%") and stripped.count("{%") == 1
        ), f"{path.name}:{number} ends with an inline block tag; use `+%}}`"


@pytest.mark.parametrize(("path", "token"), _PARAMS, ids=lambda v: str(v))
def test_shipped_prompt_has_no_banned_token(path: Path, token: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert token not in text, (
        f"banned token {token!r} found in {path.name}; "
        "rename/remove the reference to match current CLI surface"
    )
