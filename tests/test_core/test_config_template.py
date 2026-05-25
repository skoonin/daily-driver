from __future__ import annotations

import re

import pytest
import yaml

from daily_driver.core.config_models import Config
from daily_driver.core.config_template import render_config_template

_TOP_KEY_RE = re.compile(r"^# ([a-z_]+):\s*$")


def _uncommented(text: str) -> str:
    """Strip comments and blank lines so the result parses as plain YAML."""
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        # Drop trailing inline comments.
        if " #" in line:
            line = line.split(" #", 1)[0].rstrip()
        out.append(line)
    return "\n".join(out)


def _commented_top_level_blocks(text: str) -> dict[str, str]:
    """Extract `# key:` ... commented blocks keyed by top-level field name.

    Returns the block body with leading `# `/`#` stripped from every line, so
    the returned YAML is what the user would have after uncommenting.
    """
    blocks: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        m = _TOP_KEY_RE.match(line)
        if m:
            current = [f"{m.group(1)}:"]
            blocks[m.group(1)] = current
            continue
        if current is None:
            continue
        if not line.startswith("#"):
            current = None
            continue
        # Strip leading "# " (or "#" on bare-comment separator lines like "#").
        if line.startswith("# "):
            current.append(line[2:])
        elif line == "#":
            current.append("")
        else:
            current = None
    return {k: "\n".join(v) for k, v in blocks.items()}


def test_render_produces_valid_yaml():
    text = render_config_template()
    parsed = yaml.safe_load(_uncommented(text))
    assert isinstance(parsed, dict)


def test_render_validates_against_config_model():
    text = render_config_template()
    parsed = yaml.safe_load(_uncommented(text))
    Config.model_validate(parsed)


def test_render_includes_field_descriptions():
    text = render_config_template()
    assert "Where command output" in text
    assert "Print a stderr nudge" in text
    assert "is also read by `is_late_day` evaluation" in text


def test_render_marks_optional_blocks_commented():
    text = render_config_template()
    assert "# user_profile:" in text
    assert "# plugins:" in text
    assert "# focus:" in text


def test_render_preserves_preamble():
    text = render_config_template()
    assert text.startswith("# daily-driver workspace configuration")
    assert 'extra="forbid"' in text


_COMMENTED_BLOCKS = [
    "user_profile",
    "recurring_tasks",
    "scheduler",
    "schedule",
    "claude",
    "ai",
    "focus",
    "gather",
    "plugins",
]


@pytest.mark.parametrize("block", _COMMENTED_BLOCKS)
def test_each_commented_block_validates_when_uncommented(block):
    """Every `template_commented` block must parse + validate on opt-in.

    A future contributor adding a typo to `template_example` payloads would
    slip past `_uncommented` (which strips comments wholesale). Walk each
    commented block, uncomment it, merge with the required minimum, and run
    it through `Config.model_validate` — this catches example-data drift the
    moment someone uncomments a block to use it.
    """
    text = render_config_template()
    blocks = _commented_top_level_blocks(text)
    assert block in blocks, f"no commented block found for {block!r}"
    active = yaml.safe_load(_uncommented(text)) or {}
    payload = yaml.safe_load(blocks[block])
    assert (
        isinstance(payload, dict) and block in payload
    ), f"{block!r} did not round-trip cleanly through uncomment+parse"
    merged = {**active, **payload}
    Config.model_validate(merged)
