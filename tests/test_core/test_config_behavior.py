"""Behavior-coverage guard for every config knob.

`test_config_template.py` proves every pydantic field RENDERS in the generated
config template. This complement proves every *leaf* knob's BEHAVIOR is
exercised by — or deliberately excluded from — the test suite, so a knob can
never ship silently untested.

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
validates / renders, not that its behavior is exercised. A knob whose only
coverage is a model-level validator in those modules belongs in the ledger.

A leaf that is genuinely untestable in unit scope (or validator-only) is listed
in ``_BEHAVIOR_UNTESTED`` WITH a one-line reason. A leaf that is neither
referenced nor listed fails this test — the same red-contract discipline as the
template walker's ``_UNTEMPLATED`` ledger. Its job is to keep FUTURE knobs
honest: add a field without a behavior test or a ledger entry and this turns
red, naming the dotted path.
"""

from __future__ import annotations

import pathlib
import re
from collections import Counter

from daily_driver.core.config_models import Config

# Reuse the template walker's traversal so this guard and the template
# completeness guard see the exact same field tree.
from tests.test_core.test_config_template import _walk_fields

# Schema/template test modules prove a field VALIDATES or RENDERS, not that its
# behavior is exercised; excluded from the behavior scan so a config round-trip
# never counts as behavior coverage. (This module is excluded too — it names
# every ledger key in prose.)
_SCHEMA_TEST_MODULES = frozenset(
    {
        "test_config_models.py",
        "test_config_template.py",
        "test_config_behavior.py",
    }
)

# --- The behavior-exclusion ledger -----------------------------------------
#
# Each entry is a leaf knob dotted path with NO unit-testable behavior in a
# functional (non-schema) test module:
#
#   * prompt-only context — surfaced verbatim into a Claude prompt via the
#     rendered config; the program never branches on it. Asserting "the value
#     reached the prompt" would only re-test the config loader / template
#     renderer (already covered by test_config_template.py + the freshness
#     hook), not any behavior of this knob.
#   * validator-only — the knob's only behavior is a pydantic cross-field
#     validator, which lives in (the excluded) test_config_models.py.
#
# A knob with real, functional behavior does NOT belong here — write a focused
# test instead. Keep each reason to one line.
_BEHAVIOR_UNTESTED: dict[str, str] = {
    # user_profile.* is pure prompt context: no core/cli/plugin code branches
    # on it; it is rendered into the config and surfaced to Claude as-is.
    "user_profile.name": "prompt-only context; no Python consumer",
    "user_profile.citizenship": "prompt-only context; no Python consumer",
    "user_profile.work_auth": "prompt-only context; no Python consumer",
    "user_profile.timezone": "prompt-only context; no Python consumer",
    "user_profile.seeking_since": "prompt-only context; no Python consumer",
    # recurring_tasks rows are surfaced into day-start planning prompts.
    # name / estimated_minutes are pure prompt data; cadence / day carry only a
    # cross-field validator (covered in the excluded test_config_models.py).
    "recurring_tasks.name": "prompt-only planning context; no Python consumer",
    "recurring_tasks.estimated_minutes": (
        "prompt-only planning context; no Python consumer"
    ),
    "recurring_tasks.cadence": "validator-only; covered in test_config_models.py",
    "recurring_tasks.day": "validator-only; covered in test_config_models.py",
}

# The budget cap (max_enrich_fit) is NOT in the ledger: its behavior is covered
# in test_typed_enrichers.py (config-default vs 0=no-calls), and the
# fit-enrich-budget-boundary work owns the budget-param boundary semantics. It
# passes this guard via name reference; no duplicate cap tests are added here.
#
# scraper.headless is NOT in the ledger either: although orchestrator-overridden
# in production, test_playwright_browser.py asserts the flag reaches the launch
# call, so it is genuinely behavior-referenced.

# Keys that string-attribution cannot pin to one knob: model duplicates (e.g.
# `model`, `timeout`, `enabled`) plus common Python identifiers (`day`, `name`,
# `timezone`) that appear in unrelated code (datetime.day, dt.timezone.utc,
# response["name"], ...). For these the ledger-honesty check below can't assert
# "not referenced" — it would flag a false positive — so those ledger entries
# lean on test_behavior_ledger_paths_are_real_leaf_knobs instead.
_AMBIGUOUS_EXTRA_KEYS = frozenset({"day", "timezone"})


def _leaf_knobs() -> list[tuple[str, str]]:
    """Return (dotted_path, yaml_key) for every leaf field in the model tree.

    A leaf is a field no other field nests under (i.e. not a model container).
    """
    fields = _walk_fields(Config, "")
    all_paths = {dotted for dotted, _k, _s in fields}
    leaves: list[tuple[str, str]] = []
    seen_paths: set[str] = set()
    for dotted, key, _skip in fields:
        if dotted in seen_paths:
            continue
        seen_paths.add(dotted)
        is_container = any(
            other != dotted and other.startswith(dotted + ".") for other in all_paths
        )
        if not is_container:
            leaves.append((dotted, key))
    return leaves


def _shared_keys() -> frozenset[str]:
    """Leaf yaml keys carried by more than one knob (string-unattributable)."""
    counts = Counter(key for _dotted, key in _leaf_knobs())
    return frozenset(k for k, n in counts.items() if n > 1)


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


def test_every_config_knob_is_tested_or_ledgered() -> None:
    """Every leaf knob is behavior-referenced by some test, or in the ledger.

    RED contract: add a config field without a behavior test (or a justified
    ledger entry) and this fails, naming the dotted path.
    """
    text = _behavior_text()
    uncovered = sorted(
        dotted
        for dotted, key in _leaf_knobs()
        if dotted not in _BEHAVIOR_UNTESTED and not _key_referenced(key, text)
    )
    assert (
        not uncovered
    ), "config knobs neither behavior-tested nor in _BEHAVIOR_UNTESTED: " + ", ".join(
        uncovered
    )


def test_behavior_ledger_paths_are_real_leaf_knobs() -> None:
    """Each _BEHAVIOR_UNTESTED entry must name a real leaf field.

    Keeps the ledger honest: a path that no longer exists (or is a container,
    not a leaf) should be removed rather than masking coverage.
    """
    leaf_paths = {dotted for dotted, _key in _leaf_knobs()}
    for excluded in _BEHAVIOR_UNTESTED:
        assert (
            excluded in leaf_paths
        ), f"_BEHAVIOR_UNTESTED path {excluded!r} is not a real leaf knob"


def test_behavior_ledger_entries_are_not_behavior_referenced() -> None:
    """A ledgered knob must NOT be behavior-referenced by the scan.

    If someone later writes a real behavior test for a ledgered knob (so its key
    now appears in a construction form), this turns red — forcing the stale
    "untestable" entry out of the ledger instead of silently masking the new
    coverage. Keys that string-attribution cannot pin to one knob (model
    duplicates + common identifiers) are skipped: a false "referenced" there
    would fail spuriously, so those entries rely on the real-leaf check above.
    """
    text = _behavior_text()
    unattributable = _shared_keys() | _AMBIGUOUS_EXTRA_KEYS
    leftover = sorted(
        dotted
        for dotted, key in _leaf_knobs()
        if dotted in _BEHAVIOR_UNTESTED
        and key not in unattributable
        and _key_referenced(key, text)
    )
    assert not leftover, (
        "ledgered knobs are now behavior-referenced; remove them from "
        "_BEHAVIOR_UNTESTED: " + ", ".join(leftover)
    )
