from __future__ import annotations

import yaml

from daily_driver.core.config_models import Config
from daily_driver.core.config_template import render_config_template


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
    assert 'extra="allow"' in text
