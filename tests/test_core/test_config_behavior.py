"""Behavior-coverage guard for every config knob.

`test_config_template.py` proves every pydantic field RENDERS in the generated
config template. This complement proves every *leaf* knob's BEHAVIOR is
exercised by — or deliberately excluded from — the test suite, so a knob can
never ship silently untested.

Mechanism (kept deliberately simple and fast): walk the same model tree the
template walker walks, reduce it to leaf knobs (fields with no sub-fields),
then do a string-level scan of every file under ``tests/`` for each leaf's
YAML key. A leaf whose key appears in some test module counts as referenced;
a leaf that is genuinely untestable in unit scope is listed in
``_BEHAVIOR_UNTESTED`` WITH a one-line reason. A leaf that is neither
referenced nor listed fails this test — the same red-contract discipline as
the template walker's ``_UNTEMPLATED`` ledger.

This is a coarse name-level scan, not a semantic check: it guarantees a knob
is *named* by the suite, and the focused behavior tests (referenced in the
ledger reasons below) carry the actual assert-the-effect coverage. Its job is
to keep FUTURE knobs honest — add a field without a test or a ledger entry and
this turns red, naming the path.
"""

from __future__ import annotations

import pathlib
import re

from daily_driver.core.config_models import Config

# Reuse the template walker's traversal so this guard and the template
# completeness guard see the exact same field tree.
from tests.test_core.test_config_template import _walk_fields

# --- The behavior-exclusion ledger -----------------------------------------
#
# Each entry is a leaf knob dotted path that has NO unit-testable behavior:
# the program never branches on it; it is surfaced verbatim into a Claude
# prompt via the rendered config file. Asserting "the value reached the prompt"
# would only re-test the config loader / template renderer (already covered by
# test_config_template.py + the freshness hook), not any behavior of this knob.
#
# A knob with real behavior does NOT belong here — write a focused test
# instead. Keep each reason to one line.
_BEHAVIOR_UNTESTED: dict[str, str] = {
    # user_profile.* is pure prompt context: no core/cli/plugin code branches
    # on it; it is rendered into the config and surfaced to Claude as-is.
    "user_profile.name": "prompt-only context; no Python consumer",
    "user_profile.citizenship": "prompt-only context; no Python consumer",
    "user_profile.work_auth": "prompt-only context; no Python consumer",
    "user_profile.timezone": "prompt-only context; no Python consumer",
    "user_profile.seeking_since": "prompt-only context; no Python consumer",
    # recurring_tasks rows are surfaced into day-start planning prompts; the
    # cadence/day fields carry a cross-field validator (covered in
    # test_config_models.py), but name/estimated_minutes are pure prompt data.
    "recurring_tasks.name": "prompt-only planning context; no Python consumer",
    "recurring_tasks.estimated_minutes": (
        "prompt-only planning context; no Python consumer"
    ),
    # Orchestrator-overridden per scrape phase (_ctx_with_headless); a user
    # value has no effect, so it carries template_skip and is untestable as a
    # user knob. Mirrors test_config_template._UNTEMPLATED.
    "plugins.job_search.scraper.headless": "orchestrator-overridden; no user effect",
}

# Budget caps (max_enrich_companies / max_enrich_fit) are NOT in the ledger:
# their behavior is covered in test_typed_enrichers.py (config-default vs
# 0=no-calls), and the in-flight fix/enrich-budget-boundary branch owns the
# budget-param boundary semantics. They pass this guard via name reference; no
# duplicate cap tests are added here.


def _leaf_knobs() -> list[tuple[str, str]]:
    """Return (dotted_path, yaml_key) for every leaf field in the model tree.

    A leaf is a field no other field nests under (i.e. not a model container).
    Duplicate yaml keys from the source-toggle subclasses collapse — the scan
    only needs the key to appear once anywhere in tests/.
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


def _tests_text() -> str:
    root = pathlib.Path(__file__).resolve().parents[1]
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in root.rglob("*.py")
        if p.name != pathlib.Path(__file__).name
    )


def _key_referenced(yaml_key: str, text: str) -> bool:
    return bool(re.search(rf"\b{re.escape(yaml_key)}\b", text))


def test_every_config_knob_is_tested_or_ledgered() -> None:
    """Every leaf knob is name-referenced by some test, or in the ledger.

    RED contract: add a config field without a behavior test (or a justified
    ledger entry) and this fails, naming the dotted path.
    """
    text = _tests_text()
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
