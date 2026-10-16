"""Tests for core/templating.py — rendering shipped text templates."""

from __future__ import annotations

import jinja2
import pytest

from daily_driver.core.templating import render_for_plugins


def test_undefined_name_fails_loudly() -> None:
    """A typo in a shipped template must fail, not render as empty text."""
    with pytest.raises(jinja2.UndefinedError):
        render_for_plugins("{{ plugin }}", set())


def test_block_tag_on_its_own_line_leaves_no_blank_line() -> None:
    text = 'a\n{% if "job_search" in plugins %}\nb\n{% endif %}\nc\n'
    assert render_for_plugins(text, set()) == "a\nc\n"
    assert render_for_plugins(text, {"job_search"}) == "a\nb\nc\n"
