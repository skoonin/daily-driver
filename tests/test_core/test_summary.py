"""Unit tests for daily_driver.core.summary payload assembly.

Exercises the pure prompt-render and JSON-bundle functions directly; range
parsing is covered by test_dates (summary.parse_range only delegates).
"""

from __future__ import annotations

from datetime import date

from daily_driver.core import summary

_START = date(2026, 5, 1)
_END = date(2026, 5, 24)


def test_render_prompt_includes_range_dates() -> None:
    out = summary.render_prompt(range_start=_START, range_end=_END)
    assert "2026-05-01" in out
    assert "2026-05-24" in out


def test_render_prompt_defaults_to_med_detail() -> None:
    out = summary.render_prompt(range_start=_START, range_end=_END)
    # The med branch is the template's else clause: "balanced summary".
    assert "balanced summary" in out
    assert "brief bullet-point" not in out
    assert "comprehensive narrative" not in out


def test_render_prompt_low_detail_selects_brief_branch() -> None:
    out = summary.render_prompt(range_start=_START, range_end=_END, detail="low")
    assert "brief bullet-point" in out
    assert "balanced summary" not in out


def test_render_prompt_high_detail_selects_narrative_branch() -> None:
    out = summary.render_prompt(range_start=_START, range_end=_END, detail="high")
    assert "comprehensive narrative" in out
    assert "balanced summary" not in out


def test_render_prompt_match_terms_joined() -> None:
    out = summary.render_prompt(
        range_start=_START, range_end=_END, match=["infra", "oncall"]
    )
    assert "infra, oncall" in out


def test_render_prompt_no_match_omits_focus_clause() -> None:
    out = summary.render_prompt(range_start=_START, range_end=_END)
    assert "Focus on items matching" not in out


def test_render_prompt_none_match_treated_as_empty() -> None:
    out = summary.render_prompt(range_start=_START, range_end=_END, match=None)
    assert "Focus on items matching" not in out


def test_build_json_bundle_schema_and_range() -> None:
    bundle = summary.build_json_bundle(range_start=_START, range_end=_END)
    assert bundle["schema"] == 1
    assert bundle["data"]["range"] == {"start": "2026-05-01", "end": "2026-05-24"}
    assert bundle["data"]["detail"] == "med"
    assert bundle["data"]["match"] == []


def test_build_json_bundle_carries_detail_and_match() -> None:
    bundle = summary.build_json_bundle(
        range_start=_START,
        range_end=_END,
        detail="high",
        match=["infra"],
    )
    assert bundle["data"]["detail"] == "high"
    assert bundle["data"]["match"] == ["infra"]


def test_build_json_bundle_none_match_serializes_as_empty_list() -> None:
    bundle = summary.build_json_bundle(range_start=_START, range_end=_END, match=None)
    assert bundle["data"]["match"] == []
