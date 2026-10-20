"""Behavior-coverage guard for every config knob.

`test_config_template.py` proves every pydantic field APPEARS in the shipped
config template. This complement proves every *leaf* knob's BEHAVIOR is
exercised by the test suite, so a knob can never ship silently untested.

Mechanism (kept deliberately simple and fast): walk the same model tree the
template walker walks, reduce it to leaf knobs (fields with no sub-fields),
then string-scan the test suite for each leaf's YAML key in a
config-construction form — a dotted attribute (``.key``), a kwarg/assignment
(``key=``), a quoted mapping key (``"key"``), or a YAML mapping key
(``key:`` at line start or after ``{``/``,``). A bare ``\\bkey\\b`` scan was
rejected because common words (``timeout``, ``enabled``, ``model``) match
prose and unrelated code, so a future common-word knob could ship untested
while the guard stayed green. These four forms are how config is actually
*constructed* in tests, which is the signal we want.

The two pure-schema modules (``test_config_models.py``,
``test_config_template.py``) are excluded from the scan: they prove a field
validates / renders, not that its behavior is exercised.

A leaf that no functional test references fails this test, naming the dotted
path. Its job is to keep FUTURE knobs honest: add a field without a behavior
test and this turns red.
"""

from __future__ import annotations

import pathlib
import re

from daily_driver.core.config_models import Config

# Reuse the template walker's traversal so this guard and the template
# completeness guard see the exact same field tree.
from tests.test_core.test_config_template import _walk_fields

# Schema/template test modules prove a field VALIDATES or RENDERS, not that its
# behavior is exercised; excluded from the behavior scan so a config round-trip
# never counts as behavior coverage. This module is excluded too.
_SCHEMA_TEST_MODULES = frozenset(
    {
        "test_config_models.py",
        "test_config_template.py",
        "test_config_behavior.py",
    }
)


def _leaf_knobs() -> list[tuple[str, str]]:
    """Return (dotted_path, yaml_key) for every leaf field in the model tree.

    A leaf is a field no other field nests under (i.e. not a model container).
    """
    fields = _walk_fields(Config, "")
    all_paths = {dotted for dotted, _key in fields}
    leaves: list[tuple[str, str]] = []
    seen_paths: set[str] = set()
    for dotted, key in fields:
        if dotted in seen_paths:
            continue
        seen_paths.add(dotted)
        is_container = any(
            other != dotted and other.startswith(dotted + ".") for other in all_paths
        )
        if not is_container:
            leaves.append((dotted, key))
    return leaves


def _behavior_text() -> str:
    root = pathlib.Path(__file__).resolve().parents[1]
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in root.rglob("*.py")
        if p.name not in _SCHEMA_TEST_MODULES
    )


def _key_referenced(yaml_key: str, text: str) -> bool:
    """True if yaml_key appears in a config-construction form (not bare prose).

    Forms: dotted attribute `.key`, kwarg/assignment `key=`, quoted mapping key
    `"key"`/`'key'`, or YAML mapping key `key:` (line start, or after `{`/`,`
    for inline-flow maps).
    """
    k = re.escape(yaml_key)
    patterns = (
        rf"\.{k}\b",
        rf"\b{k}\s*=",
        rf"['\"]{k}['\"]",
        rf"(?m)(?:^|[ \t{{,]){k}:",
    )
    return any(re.search(p, text) for p in patterns)


def test_every_config_knob_is_behavior_tested() -> None:
    """Every leaf knob is behavior-referenced by some test.

    RED contract: add a config field without a behavior test and this fails,
    naming the dotted path.
    """
    text = _behavior_text()
    uncovered = sorted(
        dotted for dotted, key in _leaf_knobs() if not _key_referenced(key, text)
    )
    assert not uncovered, "config knobs with no behavior test: " + ", ".join(uncovered)
