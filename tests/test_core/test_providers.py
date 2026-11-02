"""Provider config: built-in providers, merging, `extends`, and task routing."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from daily_driver.core.config_models import Config
from daily_driver.core.providers import (
    DEFAULT_PROVIDER,
    ProviderConfig,
    builtin_providers,
    provider_for,
)
from daily_driver.tasks import CORE_TASKS, INTERACTIVE, SUMMARY, VOICE_UPDATE, Task

_FIT_NOTES = Task("fit_notes", "text")


def _config(
    providers: dict[str, Any] | None = None, job_search: dict[str, Any] | None = None
) -> Config:
    raw: dict[str, Any] = {"tracker": {"categories": {"task": {"required": ["title"]}}}}
    if providers is not None:
        raw["ai"] = {"providers": providers}
    if job_search is not None:
        raw["plugins"] = {"job_search": job_search}
    return Config.model_validate(raw)


# --- Built-in providers ----------------------------------------------------


def test_builtin_providers_are_the_four_shipped() -> None:
    assert set(builtin_providers()) == {"claude", "opencode", "lmstudio", "ollama"}


def test_builtin_providers_claim_no_tasks() -> None:
    assert all(not entry.tasks for entry in builtin_providers().values())


def test_http_builtins_ship_without_model() -> None:
    builtins = builtin_providers()
    assert builtins["lmstudio"].base_url == "http://localhost:1234/v1"
    assert builtins["ollama"].base_url == "http://localhost:11434/v1"
    assert builtins["lmstudio"].model is None
    assert builtins["ollama"].model is None


@pytest.mark.parametrize("name", ["claude", "opencode"])
def test_launched_builtins_cover_every_mode(name: str) -> None:
    entry = builtin_providers()[name]
    assert entry.run and entry.resume and entry.agent and entry.text


def test_opencode_builtin_never_auto_approves() -> None:
    entry = builtin_providers()["opencode"]
    templates = [entry.run, entry.resume, entry.agent, entry.text]
    assert all("--auto" not in str(template) for template in templates)


# --- Routing ---------------------------------------------------------------


@pytest.mark.parametrize("task", CORE_TASKS)
def test_unclaimed_core_task_goes_to_claude(task: Task) -> None:
    provider = provider_for(_config(), task)
    assert provider.name == DEFAULT_PROVIDER == "claude"
    assert provider.settings == builtin_providers()["claude"]


def test_tasks_line_alone_switches_interactive_to_opencode() -> None:
    config = _config({"opencode": {"tasks": ["interactive"]}})
    provider = provider_for(config, INTERACTIVE)
    assert provider.name == "opencode"
    assert provider.settings.run == builtin_providers()["opencode"].run
    assert provider_for(config, SUMMARY).name == "claude"


def test_user_entry_overrides_one_builtin_field() -> None:
    config = _config({"claude": {"model": "opus"}})
    settings = provider_for(config, SUMMARY).settings
    assert settings.model == "opus"
    assert settings.agent == builtin_providers()["claude"].agent


def test_extends_inherits_fields_but_not_tasks() -> None:
    config = _config(
        {
            "claude": {"tasks": ["interactive"]},
            "claude-opus": {"extends": "claude", "model": "opus", "tasks": ["summary"]},
        }
    )
    opus = provider_for(config, SUMMARY)
    assert opus.name == "claude-opus"
    assert opus.settings.model == "opus"
    assert opus.settings.agent == builtin_providers()["claude"].agent
    assert opus.settings.tasks == ["summary"]
    assert opus.settings.extends is None


def test_extends_sees_the_users_override_of_a_builtin() -> None:
    config = _config(
        {
            "lmstudio": {"base_url": "http://gpu-box:1234/v1"},
            "lmstudio-coder": {
                "extends": "lmstudio",
                "model": "qwen3-coder",
                "tasks": ["voice_update"],
            },
        }
    )
    settings = provider_for(config, VOICE_UPDATE).settings
    assert settings.base_url == "http://gpu-box:1234/v1"


def test_http_provider_serves_text_task() -> None:
    config = _config({"lmstudio": {"model": "qwen3-coder", "tasks": ["voice_update"]}})
    provider = provider_for(config, VOICE_UPDATE)
    assert provider.name == "lmstudio"
    assert provider.settings.model == "qwen3-coder"


def test_plugin_task_falls_back_to_claude() -> None:
    config = _config(job_search={})
    assert provider_for(config, _FIT_NOTES, plugin="job_search").name == "claude"


def test_plugin_default_provider_routes_its_tasks() -> None:
    config = _config(
        {"lmstudio": {"model": "qwen3-coder"}}, job_search={"provider": "lmstudio"}
    )
    assert provider_for(config, _FIT_NOTES, plugin="job_search").name == "lmstudio"


def test_plugin_task_override_beats_plugin_default() -> None:
    config = _config(
        {"lmstudio": {"model": "qwen3-coder"}},
        job_search={"provider": "lmstudio", "tasks": {"fit_notes": "claude"}},
    )
    assert provider_for(config, _FIT_NOTES, plugin="job_search").name == "claude"


# --- Validation ------------------------------------------------------------


def _rejects(match: str, **kwargs: Any) -> None:
    with pytest.raises(ValidationError, match=match):
        _config(**kwargs)


def test_core_task_claimed_twice_names_both() -> None:
    _rejects(
        "summary.*claude.*opencode|summary.*opencode.*claude",
        providers={
            "claude": {"tasks": ["summary"]},
            "opencode": {"tasks": ["summary"]},
        },
    )


def test_unknown_core_task_rejected() -> None:
    _rejects("unknown task 'sumary'", providers={"claude": {"tasks": ["sumary"]}})


def test_mode_template_mismatch_names_missing_field() -> None:
    _rejects(
        "interactive.*mine.*run",
        providers={"mine": {"text": ["x"], "tasks": ["interactive"]}},
    )


def test_unknown_placeholder_rejected() -> None:
    _rejects(
        "unknown placeholder {workdir}; text templates can use model, "
        "session_id, system",
        providers={"mine": {"text": ["mytool", "{workdir}"]}},
    )


def test_session_id_allows_only_uuid() -> None:
    _rejects("session_id", providers={"mine": {"text": ["x"], "session_id": "{model}"}})


def test_templates_and_base_url_together_rejected() -> None:
    _rejects(
        "mine.*base_url",
        providers={"mine": {"text": ["x"], "base_url": "http://localhost:1/v1"}},
    )


def test_entry_with_neither_templates_nor_base_url_rejected() -> None:
    _rejects("mine", providers={"mine": {"model": "x"}})


def test_http_provider_in_use_without_model_rejected() -> None:
    _rejects(
        "lmstudio serves voice_update but has no model",
        providers={"lmstudio": {"tasks": ["voice_update"]}},
    )


def test_unused_http_builtin_without_model_accepted() -> None:
    assert _config({"claude": {"model": "opus"}})


def test_plugin_default_http_provider_without_model_rejected() -> None:
    _rejects(
        "lmstudio serves fit_notes but has no model",
        job_search={"provider": "lmstudio"},
    )


def test_extends_missing_entry_rejected() -> None:
    _rejects("extends 'nope'", providers={"mine": {"extends": "nope", "model": "x"}})


def test_extends_chain_rejected() -> None:
    _rejects(
        "one level",
        providers={
            "a": {"extends": "claude", "model": "opus"},
            "b": {"extends": "a", "model": "haiku"},
        },
    )


def test_builtin_named_entry_cannot_extend() -> None:
    _rejects("claude", providers={"claude": {"extends": "opencode"}})


def test_undeclared_plugin_task_key_rejected() -> None:
    _rejects("fit_note", job_search={"tasks": {"fit_note": "claude"}})


def test_unresolved_plugin_provider_rejected() -> None:
    _rejects("nope", job_search={"provider": "nope"})


def test_disabled_plugin_is_not_checked() -> None:
    assert _config(job_search={"enabled": False, "provider": "nope"})


def test_provider_config_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        ProviderConfig.model_validate({"text": ["x"], "kind": "cli"})


def test_cleanup_template_placeholders_checked() -> None:
    _rejects(
        "unknown placeholder {id}",
        providers={"mine": {"text": ["x"], "cleanup": ["x", "delete", "{id}"]}},
    )


def test_plugin_default_checked_even_when_every_task_is_overridden() -> None:
    _rejects(
        "plugins.job_search.provider.*nope",
        job_search={"provider": "nope", "tasks": {"fit_notes": "claude"}},
    )


def test_task_listed_twice_in_one_entry_rejected() -> None:
    _rejects("more than once", providers={"claude": {"tasks": ["summary", "summary"]}})


def test_extending_a_user_entry_does_not_inherit_its_tasks() -> None:
    config = _config(
        providers={
            "a": {"text": ["tool-a"], "tasks": ["voice_update"]},
            "b": {"extends": "a", "model": "m"},
        }
    )
    assert provider_for(config, VOICE_UPDATE).name == "a"
    from daily_driver.core.providers import resolved_provider

    b = resolved_provider(config, "b").settings
    assert b.tasks == []
    assert b.text == ["tool-a"]


@pytest.mark.parametrize(
    ("field", "template", "name"),
    [
        ("text", ["tool", ["--dir", "{workspace}"]], "workspace"),
        ("agent", ["tool", "{command}"], "command"),
        ("text", ["tool", "{session_name}"], "session_name"),
        ("run", ["tool", "{system}"], "system"),
    ],
)
def test_placeholder_outside_its_template_rejected(
    field: str, template: list[Any], name: str
) -> None:
    _rejects(
        rf"unknown placeholder \{{{name}\}}; {field} templates",
        providers={"mine": {field: template}},
    )


@pytest.mark.parametrize("first", [["tool"], "{model}", "{{tool}}", "", "  "])
def test_template_must_start_with_a_plain_program(first: Any) -> None:
    _rejects(
        "must start with the program",
        providers={"mine": {"text": [first, "run"]}},
    )


@pytest.mark.parametrize("text", ["{model:>10}", "{model!r}", "{model:{system}}"])
def test_placeholder_format_spec_or_conversion_rejected(text: str) -> None:
    _rejects(
        "takes no format spec or conversion",
        providers={"mine": {"text": ["tool", text]}},
    )


def test_cleanup_workspace_outside_a_group_rejected_with_a_text_template() -> None:
    _rejects(
        "mine.cleanup uses \\{workspace\\} outside a group, but a text task has no "
        "workspace",
        providers={
            "mine": {
                "session_id": "{uuid}",
                "text": ["tool"],
                "cleanup": ["tool", "rm", "{workspace}", "{session_id}"],
            }
        },
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"text": ["tool"], "cleanup": ["tool", ["--dir", "{workspace}"]]},
        {"agent": ["tool"], "cleanup": ["tool", "--dir", "{workspace}"]},
    ],
)
def test_cleanup_workspace_accepted_when_every_call_has_one(
    fields: dict[str, Any],
) -> None:
    assert _config({"mine": {"session_id": "{uuid}", **fields}})


def test_session_id_placeholder_needs_a_session_id_format() -> None:
    _rejects(
        "mine.agent uses \\{session_id\\}, but the provider has no session_id format",
        providers={"mine": {"agent": ["tool", ["-s", "{session_id}"]]}},
    )


@pytest.mark.parametrize("text", ['{"k": 1}', "{"])
def test_literal_brace_error_suggests_doubling(text: str) -> None:
    _rejects(
        "write \\{\\{ and \\}\\} for a literal brace",
        providers={"mine": {"text": ["tool", text]}},
    )


def test_empty_placeholder_rejected() -> None:
    _rejects(r"unknown placeholder \{\}", providers={"mine": {"text": ["x", "{}"]}})


def test_http_provider_on_agent_task_says_text_only() -> None:
    _rejects(
        "text tasks only",
        providers={"lmstudio": {"model": "qwen3-coder", "tasks": ["summary"]}},
    )


def test_plugin_task_without_a_plugin_block_runs_on_the_default() -> None:
    config = _config()
    assert config.plugins.job_search is None
    assert provider_for(config, _FIT_NOTES, plugin="job_search").name == "claude"


def test_resume_check_in_needs_a_resume_template() -> None:
    with pytest.raises(ValidationError, match="resume_check_in needs a resume"):
        Config.model_validate(
            {
                "tracker": {"categories": {"task": {"required": ["title"]}}},
                "ai": {
                    "interactive": {"resume_check_in": True},
                    "providers": {"mine": {"run": ["x"], "tasks": ["interactive"]}},
                },
            }
        )
