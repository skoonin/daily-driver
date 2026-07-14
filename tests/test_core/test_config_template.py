from __future__ import annotations

import re
from typing import Any, get_args, get_origin

import pytest
import yaml
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from daily_driver.core.config_models import Config
from daily_driver.core.config_template import (
    _extra,
    _unwrap_model,
    render_config_template,
)
from daily_driver.plugins.job_search.config import _SOURCE_TOGGLE_TYPES

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
    assert "Warn (stderr, one-time) when --status" in text
    assert "the single source of truth for" in text


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


# --- Completeness: every model field must render into the template ---------
#
# The freshness hook (`make check-config-template`) proves the checked-in
# template matches the generator. This complement proves the GENERATOR walks
# every field of the pydantic model tree — a field the generator silently
# skips would be undocumented forever and the freshness hook would never
# notice (the generator and the file would agree on the same omission).
#
# Deliberately untemplated fields go in _UNTEMPLATED below WITH a reason.
# A field missing WITHOUT a defensible reason is a generator/model bug: fix
# the model's json_schema_extra (or the generator) so it renders, then
# regenerate the template — do not paper over it here.
_UNTEMPLATED: frozenset[str] = frozenset(
    {
        # Overridden per scrape phase by the orchestrator (_ctx_with_headless);
        # a user value has no effect, so it carries template_skip in the model
        # and is deliberately kept out of the generated template.
        "plugins.job_search.scraper.headless",
    }
)


def _yaml_key(name: str, info: FieldInfo) -> str:
    """The YAML key a field renders under: its alias if set, else its name."""
    return info.alias or name


def _model_of_container(annotation: Any) -> type[BaseModel] | None:
    """Return the BaseModel element type of a list/dict-of-model annotation."""
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is list and args:
        return _unwrap_model(args[0])
    if origin is dict and len(args) == 2:
        return _unwrap_model(args[1])
    return None


def _walk_fields(
    model_cls: type[BaseModel], prefix: str
) -> list[tuple[str, str, bool]]:
    """Collect (dotted_path, yaml_key, template_skip) for every field below model_cls.

    Recurses through nested models, Optional[Model], list[Model], and
    dict[str, Model]. For dict[str, SourceToggle] the registered toggle
    subclasses (_SOURCE_TOGGLE_TYPES) carry per-source knobs beyond the base
    SourceToggle, so each subclass is walked too — the source-key dict is the
    closest thing this schema has to a discriminated union of models.
    """
    collected: list[tuple[str, str, bool]] = []
    for name, info in model_cls.model_fields.items():
        key = _yaml_key(name, info)
        dotted = f"{prefix}.{key}" if prefix else key
        skip = bool(_extra(info).get("template_skip"))
        collected.append((dotted, key, skip))

        sub = _unwrap_model(info.annotation)
        if sub is not None:
            collected.extend(_walk_fields(sub, dotted))
            continue

        element = _model_of_container(info.annotation)
        if element is None:
            continue
        collected.extend(_walk_fields(element, dotted))
        # dict[str, SourceToggle]: also walk the per-source subclasses whose
        # extra knobs (wwr_categories, greenhouse_boards, country, ...) only
        # reach the template via the source example data. The source-key dict
        # is the closest this schema has to a discriminated union of models.
        if any(
            issubclass(toggle_cls, element)
            for toggle_cls in _SOURCE_TOGGLE_TYPES.values()
        ):
            for toggle_cls in _SOURCE_TOGGLE_TYPES.values():
                collected.extend(_walk_fields(toggle_cls, dotted))
    return collected


def _key_appears(yaml_key: str, text: str) -> bool:
    """True if yaml_key appears as a YAML mapping key somewhere in the template.

    Matches `key:` as a key token (optionally indented, optionally inside a
    commented line, optionally after a `- ` list marker), not as a substring
    of a longer identifier. Leaf-key matching is intentional: a key shared by
    two blocks (e.g. `max_parallel` under ai.claude and ai.ollama) need only
    appear once. The freshness hook + per-block validate tests guard the
    deeper "rendered in the right place with valid data" property.
    """
    pattern = re.compile(
        rf"^[ #\t]*(?:- )?{re.escape(yaml_key)}:(?:\s|$)", re.MULTILINE
    )
    return bool(pattern.search(text))


def test_template_renders_every_config_field():
    """Every field in the Config model tree renders into the template.

    RED contract: introduce a model field (or a nested-model field) the
    generator's walk does not reach and this fails, naming the dotted path —
    before the omission can ship as a silently undocumented setting.
    """
    text = render_config_template()
    fields = _walk_fields(Config, "")

    # _UNTEMPLATED is the single explicit ledger of omissions. A field whose
    # key is absent from the template — whether via `template_skip` or any
    # other reason — must be listed there with a justifying comment, so a
    # contributor cannot bury a setting by skipping it without a paper trail.
    missing = sorted(
        dotted
        for dotted, key, _skip in fields
        if dotted not in _UNTEMPLATED and not _key_appears(key, text)
    )
    assert not missing, "config fields absent from generated template: " + ", ".join(
        missing
    )


def test_untemplated_exclusions_are_actually_skipped():
    """Each _UNTEMPLATED entry must be a real field the generator omits.

    Keeps the exclusion list honest: a path that no longer exists, or one the
    generator now renders, should be removed rather than masking coverage.
    """
    fields = _walk_fields(Config, "")
    by_path = {dotted: key for dotted, key, _skip in fields}
    text = render_config_template()
    for excluded in _UNTEMPLATED:
        assert (
            excluded in by_path
        ), f"_UNTEMPLATED path {excluded!r} is not a real field"
        assert not _key_appears(
            by_path[excluded], text
        ), f"_UNTEMPLATED path {excluded!r} now renders; drop it from the exclusion list"
