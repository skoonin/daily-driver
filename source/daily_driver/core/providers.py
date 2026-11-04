"""AI providers: the built-in set, the user's entries, and task routing.

A provider is data, either command templates for a program (claude, opencode)
or an OpenAI-compatible HTTP endpoint (LM Studio, ollama). The built-in
providers ship in ``resources/templates/providers.default.yaml``; a user entry
with a built-in's name merges over it field by field, and any other entry can
start from one with ``extends``. No code here branches on a provider's name.

Core tasks are claimed through ``ai.providers.<name>.tasks``. A plugin routes
its own tasks with ``plugins.<name>.provider`` and ``plugins.<name>.tasks``.
Anything left unrouted goes to ``DEFAULT_PROVIDER``.
"""

from __future__ import annotations

import functools
import importlib.resources
import string
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from daily_driver.plugins import enabled_plugins
from daily_driver.plugins._base import PluginConfigBase
from daily_driver.tasks import CORE_TASKS, INTERACTIVE, Task

if TYPE_CHECKING:
    from daily_driver.core.config_models import Config

DEFAULT_PROVIDER = "claude"

# A template element is a literal argument or a group: a nested list dropped
# whole when any placeholder in it has no value.
Template = list[str | list[str]]

_LAUNCH = frozenset(
    {
        "workspace",
        "command",
        "prompt_file",
        "session_id",
        "session_name",
        "model",
        "agent",
    }
)
_HEADLESS = frozenset({"workspace", "model", "system", "session_id"})
# The values each template is rendered with. A text task runs in an empty
# directory, so `text` never gets `{workspace}`; `cleanup` runs after an agent
# or a text call, with that call's values.
PLACEHOLDERS: Mapping[str, frozenset[str]] = {
    "run": _LAUNCH,
    "resume": _LAUNCH,
    "agent": _HEADLESS,
    "text": _HEADLESS - {"workspace"},
    "cleanup": _HEADLESS,
}
# Why a placeholder can have no value, so `_check_shape` can require a group.
# `{model}` counts only when the provider sets no model, and `{workspace}` only
# in a cleanup that can follow a text call.
_EMPTY_BECAUSE: Mapping[str, str] = {
    "model": "the provider sets no model",
    "system": "some calls pass no system text",
    "command": "daily-driver resume passes none",
    "prompt_file": "daily-driver resume passes none",
    "workspace": "a text task has no workspace",
}
_CAN_BE_EMPTY: Mapping[str, frozenset[str]] = {
    "run": frozenset(),
    "resume": frozenset({"command", "prompt_file"}),
    "agent": frozenset({"system"}),
    "text": frozenset({"system"}),
    "cleanup": frozenset({"system"}),
}
_TEMPLATE_FIELDS = tuple(PLACEHOLDERS)
_CORE_TASK_NAMES = tuple(task.name for task in CORE_TASKS)
_BRACES_HINT = "write {{ and }} for a literal brace"


def placeholder_names(text: str) -> list[str]:
    """Placeholder names in `text`, in order; `{}` yields an empty name.

    Raises ValueError for a stray brace, and for a format spec or conversion,
    which `str.format_map` would apply with names the checks never see.
    """
    try:
        fields = list(string.Formatter().parse(text))
    except ValueError as exc:
        raise ValueError(f"{exc}; {_BRACES_HINT}") from exc
    names: list[str] = []
    for _, field, spec, conversion in fields:
        if field is None:
            continue
        if spec or conversion:
            raise ValueError(
                f"{{{field}}} in {text!r} takes no format spec or conversion; "
                f"{_BRACES_HINT}"
            )
        names.append(field)
    return names


def _template_texts(template: Template) -> list[str]:
    return [
        text
        for element in template
        for text in ([element] if isinstance(element, str) else element)
    ]


class ModelFile(BaseModel):
    """A JSON file in the workspace that sets the model of interactive sessions.

    For a program whose interactive mode takes no model flag but reads one from
    its own config, such as opencode's `opencode.json`.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    key: str

    @field_validator("path")
    @classmethod
    def _inside_workspace(cls, value: str) -> str:
        parts = PurePosixPath(value).parts
        if (
            not value.strip()
            or not parts
            or value.startswith(("/", "~"))
            or ".." in parts
        ):
            raise ValueError("must be a file path relative to the workspace, inside it")
        return value

    @field_validator("key")
    @classmethod
    def _key_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("cannot be blank")
        return value


class ProviderConfig(BaseModel):
    """One `ai.providers.<name>` entry; the map key is the provider's name."""

    model_config = ConfigDict(extra="forbid")

    extends: str | None = None
    tasks: list[str] = []
    run: Template | None = None
    resume: Template | None = None
    agent: Template | None = None
    text: Template | None = None
    cleanup: Template | None = None
    session_id: str | None = None
    base_url: str | None = None
    model: str | None = None
    model_file: ModelFile | None = None
    timeout: int | None = Field(default=None, ge=1)
    max_parallel: int = Field(default=4, ge=1)

    @field_validator("tasks")
    @classmethod
    def _known_tasks(cls, value: list[str]) -> list[str]:
        for name in value:
            if name not in _CORE_TASK_NAMES:
                raise ValueError(
                    f"unknown task {name!r}; core tasks are "
                    f"{', '.join(_CORE_TASK_NAMES)}"
                )
        if len(set(value)) != len(value):
            raise ValueError("lists the same task more than once")
        return value

    @field_validator(*_TEMPLATE_FIELDS)
    @classmethod
    def _known_placeholders(
        cls, value: Template | None, info: ValidationInfo
    ) -> Template | None:
        if not value:
            return value
        first = value[0]
        if not isinstance(first, str) or not first.strip() or set("{}") & set(first):
            raise ValueError(
                "must start with the program to run, as plain text with no "
                "braces, not a group or a placeholder"
            )
        known = PLACEHOLDERS[str(info.field_name)]
        for text in _template_texts(value):
            for name in placeholder_names(text):
                if name not in known:
                    raise ValueError(
                        f"unknown placeholder {{{name}}}; {info.field_name} "
                        f"templates can use {', '.join(sorted(known))}; "
                        f"{_BRACES_HINT}"
                    )
        return value

    @field_validator("model")
    @classmethod
    def _not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            # `null`, not "leave it out": an entry with `extends` inherits a
            # model it leaves out.
            raise ValueError("cannot be blank; to set no model, use null")
        return value

    @field_validator("session_id")
    @classmethod
    def _uuid_only(cls, value: str | None) -> str | None:
        if value is not None and placeholder_names(value) != ["uuid"]:
            raise ValueError('must contain {uuid} once, for example "ses_{uuid}"')
        return value


@dataclass(frozen=True)
class Provider:
    """A provider after merging, as a task resolves to it."""

    name: str
    settings: ProviderConfig


@functools.cache
def builtin_providers_text() -> str:
    return (
        importlib.resources.files("daily_driver.resources.templates")
        .joinpath("providers.default.yaml")
        .read_text(encoding="utf-8")
    )


@functools.cache
def _builtin_providers() -> Mapping[str, ProviderConfig]:
    raw = yaml.safe_load(builtin_providers_text())
    return {name: ProviderConfig.model_validate(entry) for name, entry in raw.items()}


def builtin_providers() -> dict[str, ProviderConfig]:
    return dict(_builtin_providers())


def default_provider() -> Provider:
    """The provider for anything unrouted, before any user entries apply."""
    return Provider(DEFAULT_PROVIDER, _builtin_providers()[DEFAULT_PROVIDER])


def _merge(base: ProviderConfig, override: ProviderConfig) -> ProviderConfig:
    # Tasks never carry over: an entry that extends `claude` must not also
    # claim every task `claude` claims.
    data: dict[str, Any] = base.model_dump(exclude_unset=True, exclude={"tasks"})
    data.update(override.model_dump(exclude_unset=True, exclude={"extends"}))
    return ProviderConfig.model_validate(data)


def _check_shape(name: str, entry: ProviderConfig) -> None:
    launched = [field for field in _TEMPLATE_FIELDS if getattr(entry, field)]
    if entry.base_url is not None and (launched or entry.session_id is not None):
        raise ValueError(
            f"ai.providers.{name}: base_url cannot be combined with "
            f"{', '.join(launched or ['session_id'])}; a provider is either a "
            "program (templates) or an HTTP server (base_url)"
        )
    if entry.base_url is None and not (entry.run or entry.agent or entry.text):
        raise ValueError(
            f"ai.providers.{name}: needs a run, agent or text template, or a base_url"
        )
    if entry.model_file is not None and not entry.run:
        raise ValueError(
            f"ai.providers.{name}.model_file needs a run template; it sets the "
            "model of interactive sessions"
        )
    if entry.session_id is None:
        for field in launched:
            if "session_id" in template_placeholders(getattr(entry, field)):
                raise ValueError(
                    f"ai.providers.{name}.{field} uses {{session_id}}, but the "
                    'provider has no session_id format; add one, such as "{uuid}"'
                )
    for field in launched:
        can_be_empty = (
            _CAN_BE_EMPTY[field]
            | ({"model"} if entry.model is None else set())
            | ({"workspace"} if field == "cleanup" and entry.text else set())
        )
        ungrouped = next(
            (
                placeholder
                for element in getattr(entry, field)
                if isinstance(element, str)
                for placeholder in placeholder_names(element)
                if placeholder in can_be_empty
            ),
            None,
        )
        if ungrouped is not None:
            raise ValueError(
                f"ai.providers.{name}.{field} uses {{{ungrouped}}} outside a group, "
                f"but {_EMPTY_BECAUSE[ungrouped]}; put it in a group"
            )


def template_placeholders(template: Template) -> set[str]:
    """Every placeholder name used anywhere in `template`, groups included."""
    return {
        name for text in _template_texts(template) for name in placeholder_names(text)
    }


def resolve_providers(user: Mapping[str, ProviderConfig]) -> dict[str, ProviderConfig]:
    """Every provider by name: the built-ins with the user's entries applied."""
    builtins = _builtin_providers()
    resolved: dict[str, ProviderConfig] = {}
    for name, base in builtins.items():
        entry = user.get(name)
        if entry is not None and entry.extends is not None:
            raise ValueError(
                f"ai.providers.{name}: an entry named after a built-in provider "
                f"merges over the built-in {name} and cannot use extends"
            )
        resolved[name] = base if entry is None else _merge(base, entry)
    for name, entry in user.items():
        if name in builtins:
            continue
        target = entry.extends
        if target is None:
            resolved[name] = entry
            continue
        if target not in builtins and target not in user:
            raise ValueError(
                f"ai.providers.{name}: extends {target!r}, which is not a provider"
            )
        if target not in builtins and user[target].extends is not None:
            raise ValueError(
                f"ai.providers.{name}: extends {target!r}, which itself uses "
                "extends; extends goes one level only"
            )
        base = resolved[target] if target in builtins else user[target]
        resolved[name] = _merge(base, entry)
    for name, entry in resolved.items():
        _check_shape(name, entry)
    return resolved


def _core_claims(providers: Mapping[str, ProviderConfig]) -> dict[str, str]:
    claims: dict[str, list[str]] = {}
    for name, entry in providers.items():
        for task_name in entry.tasks:
            claims.setdefault(task_name, []).append(name)
    for task_name, names in claims.items():
        if len(names) > 1:
            raise ValueError(
                f"task {task_name!r} is claimed by more than one provider: "
                f"{', '.join(names)}"
            )
    return {task_name: names[0] for task_name, names in claims.items()}


def _route(
    config: Config,
    providers: Mapping[str, ProviderConfig],
    task: Task,
    plugin: str | None,
) -> str:
    if plugin is None:
        return _core_claims(providers).get(task.name, DEFAULT_PROVIDER)
    block: PluginConfigBase | None = getattr(config.plugins, plugin)
    if block is None:
        return DEFAULT_PROVIDER
    return block.tasks.get(task.name) or block.provider or DEFAULT_PROVIDER


def _check_serves(name: str, entry: ProviderConfig, task: Task) -> None:
    if task.mode == "text" and entry.base_url is not None:
        if entry.model is None:
            raise ValueError(
                f"{name} serves {task.name} but has no model; "
                f"set ai.providers.{name}.model"
            )
        return
    if entry.base_url is not None:
        raise ValueError(
            f"task {task.name!r} is routed to {name!r}, an HTTP server; HTTP "
            "providers serve text tasks only"
        )
    needed = "run" if task.mode == "interactive" else task.mode
    if getattr(entry, needed) is None:
        raise ValueError(
            f"task {task.name!r} is routed to {name!r}, which has no "
            f"{needed} template"
        )


def provider_for(config: Config, task: Task, plugin: str | None = None) -> Provider:
    """The provider that runs `task`; `plugin` names the plugin that owns it."""
    providers = resolve_providers(_user_providers(config))
    name = _route(config, providers, task, plugin)
    return Provider(name, providers[name])


def _user_providers(config: Config) -> Mapping[str, ProviderConfig]:
    return config.ai.providers


def validate_routes(config: Config) -> None:
    """Fail on any provider entry or task route that cannot work.

    Plugins that are off are not checked, so `enabled: false` never breaks a
    config.
    """
    providers = resolve_providers(_user_providers(config))
    _core_claims(providers)
    for plugin in enabled_plugins(config):
        block: PluginConfigBase = getattr(config.plugins, plugin.name)
        declared = {task.name for task in plugin.tasks}
        for task_name, name in block.tasks.items():
            if task_name not in declared:
                raise ValueError(
                    f"plugins.{plugin.name}.tasks: unknown task {task_name!r}; "
                    f"{plugin.name} has {', '.join(sorted(declared)) or 'no tasks'}"
                )
            _require_defined(providers, name, f"plugins.{plugin.name}.tasks")
        if block.provider is not None:
            _require_defined(
                providers, block.provider, f"plugins.{plugin.name}.provider"
            )
    for task, plugin_name, name in _routes(config, providers):
        _check_serves(name, providers[name], task)
    if config.ai.interactive.resume_check_in:
        name = _route(config, providers, INTERACTIVE, None)
        if providers[name].resume is None:
            raise ValueError(
                f"ai.interactive.resume_check_in needs a resume template, and "
                f"{name!r}, which runs interactive, has none"
            )


def _routes(
    config: Config, providers: Mapping[str, ProviderConfig]
) -> list[tuple[Task, str | None, str]]:
    """(task, owning plugin or None, provider name) for every live task."""
    owned: list[tuple[Task, str | None]] = [(task, None) for task in CORE_TASKS]
    for plugin in enabled_plugins(config):
        owned.extend((task, plugin.name) for task in plugin.tasks)
    return [
        (task, plugin, _route(config, providers, task, plugin))
        for task, plugin in owned
    ]


def tasks_by_provider(config: Config) -> dict[str, list[str]]:
    """Provider name -> the tasks it runs; plugin tasks read `<plugin>.<task>`.

    Only providers that run at least one task appear.
    """
    providers = resolve_providers(_user_providers(config))
    served: dict[str, list[str]] = {}
    for task, plugin, name in _routes(config, providers):
        served.setdefault(name, []).append(
            f"{plugin}.{task.name}" if plugin else task.name
        )
    return served


def resolved_provider(config: Config, name: str) -> Provider:
    """A provider by name, with the user's entries applied."""
    return Provider(name, resolve_providers(_user_providers(config))[name])


def unused_user_providers(config: Config) -> list[str]:
    """User-defined entries that run no task and that no entry extends."""
    user = _user_providers(config)
    builtins = _builtin_providers()
    served = tasks_by_provider(config)
    bases = {entry.extends for entry in user.values() if entry.extends}
    return [
        name
        for name in user
        if name not in builtins and name not in served and name not in bases
    ]


def _require_defined(
    providers: Mapping[str, ProviderConfig], name: str, where: str
) -> None:
    if name not in providers:
        raise ValueError(
            f"{where}: names provider {name!r}, which is not defined under "
            "ai.providers"
        )
