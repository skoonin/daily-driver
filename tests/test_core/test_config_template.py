"""Checks on the shipped `.dd-config.yaml.j2` that `init` renders into a workspace.

The template is hand-written for people editing it. Each live value is the
default, and a setting that cannot be live is a commented sample. These tests
keep it honest against the pydantic models: it parses, each live value equals
the default, each sample validates when turned on, every setting in the model
tree appears at its own path, and job search settings and text appear only when
job search is on.
"""

from __future__ import annotations

import importlib.resources
import re
from typing import Any, cast, get_args, get_origin

import pytest
import yaml
from pydantic import BaseModel

from daily_driver.core.config_models import Config, SchedulerConfig
from daily_driver.core.providers import (
    builtin_providers,
    provider_for,
    resolved_provider,
)
from daily_driver.core.templating import render_for_plugins
from daily_driver.plugins import enabled_plugins
from daily_driver.plugins.job_search.config import _SOURCE_TOGGLE_TYPES
from daily_driver.plugins.job_search.scraper.sources import SCRAPERS
from daily_driver.tasks import INTERACTIVE, SUMMARY, VOICE_UPDATE, Task
from tests.test_core.test_generate import JOB_TEXT


def _shipped_template() -> str:
    shipped = importlib.resources.files("daily_driver.resources.templates").joinpath(
        ".dd-config.yaml.j2"
    )
    assert (
        shipped.is_file()
    ), f"{shipped} is missing; check the package-data patterns in pyproject.toml"
    return shipped.read_text(encoding="utf-8")


def _workspace_config(*, job_search: bool) -> str:
    """The .dd-config.yaml `init` writes, with or without --job-search."""
    return render_for_plugins(
        _shipped_template(), {"job_search"} if job_search else set()
    )


def _config(*, job_search: bool) -> Config:
    return Config.model_validate(
        yaml.safe_load(_workspace_config(job_search=job_search))
    )


def _no_settings() -> Config:
    """The config a workspace has when it sets nothing beyond the requirements."""
    return Config.model_validate({"tracker": {}})


# A line whose content, once every leading "# " is removed, is a YAML key:
# `key:`, `- key:`, or a two-letter country code such as `CA:`.
_SETTING_RE = re.compile(r"^ *(?:- )?(?:[a-z_][a-z0-9_-]*|[A-Z]{2}):(?:\s|$)")
_COMMENT_MARKER_RE = re.compile(r"^( *)# ?(.*)$")


def _every_setting_on(text: str) -> str:
    """Turn on every commented sample, however deeply commented.

    Notes stay comments; only lines that are settings once their `# ` markers
    are removed get uncommented. A trailing note is dropped, as YAML would.
    """
    out = []
    for line in text.splitlines():
        candidate = line
        while match := _COMMENT_MARKER_RE.match(candidate):
            candidate = match.group(1) + match.group(2)
        if _SETTING_RE.match(candidate):
            out.append(candidate.split(" #", 1)[0].rstrip())
        else:
            out.append(line)
    return "\n".join(out)


def _every_setting_config(*, job_search: bool) -> Config:
    """The rendered config with every commented sample turned on."""
    text = _every_setting_on(_workspace_config(job_search=job_search))
    return Config.model_validate(yaml.safe_load(text))


@pytest.mark.parametrize("job_search", [False, True])
def test_render_validates_against_config_model(job_search: bool) -> None:
    config = _config(job_search=job_search)
    assert bool(enabled_plugins(config)) is job_search
    assert config.tracker.categories["task"].required == ["title"]


def _shipped_scheduler() -> SchedulerConfig:
    return SchedulerConfig.model_validate(
        yaml.safe_load(
            importlib.resources.files("daily_driver.resources.templates")
            .joinpath("scheduler.default.yaml")
            .read_text(encoding="utf-8")
        )
    )


# The template writes ai.providers.claude to show which provider runs each
# task; test_template_ai_routing_is_the_default checks it changes nothing.
_SHOWN_NOT_DEFAULT: frozenset[str] = frozenset({"ai.providers"})

# Blocks whose field default is null that the template writes anyway: the root,
# the required tracker, and job_search, whose presence is what switches it on.
_NULL_DEFAULT_BLOCKS_SHOWN: frozenset[str] = frozenset(
    {"", "tracker", "plugins.job_search"}
)


def _non_default_paths(value: Any, default: Any, path: str) -> list[str]:
    """Dotted paths under `value` that differ from `default`."""
    if path in _SHOWN_NOT_DEFAULT:
        return []
    if path == "scheduler":
        # Its defaults live in scheduler.default.yaml, not on the field.
        default = _shipped_scheduler()
    if isinstance(value, BaseModel):
        if default is None and path not in _NULL_DEFAULT_BLOCKS_SHOWN:
            # A block whose default is null is not the same as null: an empty
            # `locations` block, for one, drops every job with a location.
            return [path]
        # model_construct fills field defaults without demanding required ones.
        base = (
            default
            if isinstance(default, type(value))
            else type(value).model_construct()
        )
        return [
            found
            for name in value.model_fields_set
            for found in _non_default_paths(
                getattr(value, name),
                getattr(base, name, None),
                f"{path}.{name}" if path else name,
            )
        ]
    if isinstance(value, dict) and any(
        isinstance(item, BaseModel) for item in value.values()
    ):
        defaults = default or {}
        return [
            found
            for key, item in value.items()
            for found in (
                _non_default_paths(item, defaults[key], f"{path}.{key}")
                if key in defaults
                else [f"{path}.{key}"]
            )
        ]
    return [] if value == default else [path]


@pytest.mark.parametrize("job_search", [False, True])
def test_every_template_value_is_the_default(job_search: bool) -> None:
    """The header promises that every value in the file is the default."""
    differ = sorted(_non_default_paths(_config(job_search=job_search), None, ""))
    assert not differ, "template values that are not the default: " + ", ".join(differ)


@pytest.mark.parametrize("job_search", [False, True])
def test_template_ai_routing_is_the_default(job_search: bool) -> None:
    """The claude entry the template shows routes every task as no entry does."""
    config, plain = _config(job_search=job_search), _no_settings()
    for task in (INTERACTIVE, SUMMARY, VOICE_UPDATE):
        assert provider_for(config, task).name == provider_for(plain, task).name
    shown = resolved_provider(config, "claude").settings
    assert shown.model_copy(update={"tasks": []}) == builtin_providers()["claude"]
    if job_search:
        fit_notes = Task("fit_notes", "text")
        assert provider_for(config, fit_notes, plugin="job_search").name == "claude"


@pytest.mark.parametrize("job_search", [False, True])
def test_every_sample_validates_when_turned_on(job_search: bool) -> None:
    config = _every_setting_config(job_search=job_search)
    assert config.ai.providers["claude-sonnet"].parent_provider == "claude"
    assert config.ai.providers["my-server"].base_url
    if job_search:
        job_search_config = config.plugins.job_search  # type: ignore[attr-defined]
        assert job_search_config.locations is not None
        assert job_search_config.enrichment.criteria


def test_template_claude_samples_are_the_shipped_commands() -> None:
    """The commented claude commands, turned on, are the built-in claude.

    `model` and `timeout` are samples of a change, so they are left out.
    """
    shown = resolved_provider(_every_setting_config(job_search=True), "claude")
    samples = {"tasks": [], "model": None, "timeout": None}
    assert shown.settings.model_copy(update=samples) == builtin_providers()["claude"]


def test_config_without_job_search_has_no_job_text() -> None:
    text = _workspace_config(job_search=False)
    match = JOB_TEXT.search(text)
    assert match is None, f"config mentions {match.group(0)!r}"


@pytest.mark.parametrize("job_search", [False, True])
def test_workspace_config_has_no_stray_blank_lines(job_search: bool) -> None:
    assert "\n\n\n" not in _workspace_config(job_search=job_search)


def test_config_without_job_search_has_no_plugins_section() -> None:
    assert "plugins:" not in _workspace_config(job_search=False)


# --- Completeness: every model field appears in the template ----------------
#
# A setting missing from the template is invisible to anyone editing their
# config. Each field must appear at its own dotted path once every commented
# sample is turned on. A field left out on purpose goes in _UNTEMPLATED WITH a
# reason; anything else missing means the template needs a line for it.
_UNTEMPLATED: frozenset[str] = frozenset(
    {
        # Overridden per scrape phase by the orchestrator (_ctx_with_headless);
        # a user value has no effect.
        "plugins.job_search.scraper.headless",
    }
)

# Core settings that only matter to job search, so the template writes them
# only when job search is on.
_JOB_SEARCH_ONLY: frozenset[str] = frozenset(
    {
        "ai.providers.max_parallel",
    }
)


def _is_model_type(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _unwrap_model(annotation: Any) -> type[BaseModel] | None:
    """Return the model class of a `Model` or `Model | None` annotation."""
    if _is_model_type(annotation):
        return cast("type[BaseModel]", annotation)
    if get_origin(annotation) in (None, list, dict):
        return None
    non_none = [arg for arg in get_args(annotation) if arg is not type(None)]
    if len(non_none) == 1 and _is_model_type(non_none[0]):
        return cast("type[BaseModel]", non_none[0])
    return None


def _model_of_container(annotation: Any) -> type[BaseModel] | None:
    """Return the BaseModel element type of a list/dict-of-model annotation."""
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is list and args:
        return _unwrap_model(args[0])
    if origin is dict and len(args) == 2:
        return _unwrap_model(args[1])
    return None


def _walk_fields(model_cls: type[BaseModel], prefix: str) -> list[tuple[str, str]]:
    """Collect (dotted_path, yaml_key) for every field below model_cls.

    Recurses through nested models, Optional[Model], list[Model], and
    dict[str, Model]. For dict[str, SourceToggle] the registered toggle
    subclasses (_SOURCE_TOGGLE_TYPES) carry per-source knobs beyond the base
    SourceToggle, so each subclass is walked too — the source-key dict is the
    closest thing this schema has to a discriminated union of models.
    """
    collected: list[tuple[str, str]] = []
    for name, info in model_cls.model_fields.items():
        dotted = f"{prefix}.{name}" if prefix else name
        collected.append((dotted, name))

        sub = _unwrap_model(info.annotation)
        if sub is not None:
            collected.extend(_walk_fields(sub, dotted))
            continue

        element = _model_of_container(info.annotation)
        if element is None:
            continue
        collected.extend(_walk_fields(element, dotted))
        if any(
            issubclass(toggle_cls, element)
            for toggle_cls in _SOURCE_TOGGLE_TYPES.values()
        ):
            for toggle_cls in _SOURCE_TOGGLE_TYPES.values():
                collected.extend(_walk_fields(toggle_cls, dotted))
    return collected


def _set_paths(value: Any, prefix: str) -> set[str]:
    """Dotted paths of every field set in a validated config.

    Uses the same path shape as _walk_fields: list items and dict values add no
    path segment, so `tracker.categories.<name>.required` is
    `tracker.categories.required`. A setting shared by two dict values counts
    once; test_template_sets_every_setting_of_every_job_source covers sources.
    """
    if isinstance(value, BaseModel):
        paths: set[str] = set()
        for name in value.model_fields_set:
            dotted = f"{prefix}.{name}" if prefix else name
            paths.add(dotted)
            paths |= _set_paths(getattr(value, name), dotted)
        return paths
    if isinstance(value, dict):
        return set().union(*(_set_paths(item, prefix) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_set_paths(item, prefix) for item in value))
    return set()


def _expected_paths() -> set[str]:
    return {dotted for dotted, _key in _walk_fields(Config, "")} - _UNTEMPLATED


def _is_plugin_path(dotted: str) -> bool:
    return dotted == "plugins" or dotted.startswith("plugins.")


def test_template_renders_every_config_field():
    """Every field in the Config model tree appears in the template.

    RED contract: add a model field without a template line, or delete a
    template line, and this fails, naming the dotted path.
    """
    set_paths = _set_paths(_every_setting_config(job_search=True), "")
    missing = sorted(_expected_paths() - set_paths)
    assert not missing, "config fields absent from the template: " + ", ".join(missing)


def test_job_search_settings_appear_only_with_job_search():
    """Without job search, exactly the job-only settings are left out."""
    set_paths = _set_paths(_every_setting_config(job_search=False), "")
    expected = _expected_paths()
    missing = expected - set_paths
    job_only = {path for path in expected if _is_plugin_path(path)} | _JOB_SEARCH_ONLY
    assert missing == job_only, (
        f"leaked into the plain config: {sorted(job_only - missing)}; "
        f"missing from it: {sorted(missing - job_only)}"
    )


def test_template_lists_every_job_source():
    config = _every_setting_config(job_search=True)
    sources = config.plugins.job_search.sources  # type: ignore[attr-defined]
    assert set(sources) == set(SCRAPERS)


def test_template_sets_every_setting_of_every_job_source():
    """Each source shows all of its own settings.

    _set_paths merges sources that share a setting name (linkedin and indeed
    both have hours_old), so this checks each source on its own.
    """
    config = _every_setting_config(job_search=True)
    sources = config.plugins.job_search.sources  # type: ignore[attr-defined]
    missing = sorted(
        f"{key}.{name}"
        for key, toggle in sources.items()
        for name in type(toggle).model_fields
        if name not in toggle.model_fields_set
    )
    assert not missing, "source settings absent from the template: " + ", ".join(
        missing
    )


def test_untemplated_exclusions_are_actually_skipped():
    """Each _UNTEMPLATED entry must be a real field the template omits."""
    all_paths = {dotted for dotted, _key in _walk_fields(Config, "")}
    set_paths = _set_paths(_every_setting_config(job_search=True), "")
    for excluded in _UNTEMPLATED:
        assert (
            excluded in all_paths
        ), f"_UNTEMPLATED path {excluded!r} is not a real field"
        assert (
            excluded not in set_paths
        ), f"_UNTEMPLATED path {excluded!r} now appears; drop it from the exclusion list"
