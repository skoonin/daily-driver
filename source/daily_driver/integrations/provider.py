"""Run AI tasks on a resolved provider: one path for every program and server.

A command provider is launched from its template: placeholders filled, groups
with an empty placeholder dropped, every other argument passed through
unchanged. The prompt goes in on stdin and stdout is the answer. An HTTP
provider gets an OpenAI-compatible chat completion request.

All failures normalize to `AIInvocationError`, and timeouts to its subclass
`AITimeoutError`, so callers catch one type whatever the provider.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import requests

from daily_driver.core.logging import get_logger
from daily_driver.core.providers import (
    Provider,
    ProviderConfig,
    Template,
    placeholder_names,
    template_placeholders,
)
from daily_driver.tasks import Mode, Task

log = get_logger(__name__)

# Agent tasks read workspace files and reason over them; text tasks answer one
# self-contained prompt.
MODE_TIMEOUTS: Mapping[Mode, int] = {"text": 180, "agent": 900}
_CLEANUP_TIMEOUT = 30


class AIInvocationError(RuntimeError):
    """A headless AI call failed.

    `stdout`, `stderr` and `returncode` mirror `subprocess.CalledProcessError`
    so diagnostic warnings can log both output tails for every provider.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        stdout: str = "",
        stderr: str = "",
        returncode: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class AITimeoutError(AIInvocationError):
    """A headless AI call ran past its time limit."""

    def __init__(
        self, message: str, *, provider: str, timeout_seconds: int | None
    ) -> None:
        super().__init__(message, provider=provider, stderr=message)
        self.timeout_seconds = timeout_seconds


def render(
    template: Template, values: Mapping[str, str | None], *, provider: str = ""
) -> list[str]:
    """Fill a template into an argv list.

    A group (nested list) is dropped whole when any placeholder in it has no
    value. A placeholder outside a group with no value is an error.
    """
    argv: list[str] = []
    for element in template:
        if isinstance(element, str):
            missing = [
                name for name in placeholder_names(element) if not values.get(name)
            ]
            if missing:
                raise AIInvocationError(
                    f"{provider or 'provider'}: {element!r} needs {{{missing[0]}}}, "
                    "which has no value for this call; put it in a group (a "
                    "nested list) so it is dropped when empty",
                    provider=provider,
                )
            argv.append(element.format_map(values))
        elif all(
            values.get(name) for text in element for name in placeholder_names(text)
        ):
            argv.extend(text.format_map(values) for text in element)
    return argv


def uses(template: Template, name: str) -> bool:
    """Whether any element of `template` has the placeholder `name`."""
    return name in template_placeholders(template)


def mint_session_id(settings: ProviderConfig) -> str | None:
    if settings.session_id is None:
        return None
    return settings.session_id.format(uuid=uuid.uuid4())


def program(settings: ProviderConfig) -> str | None:
    """The program a command provider launches, or None for an HTTP provider.

    Config validation guarantees every template starts with a plain program
    name, a command provider has a run, agent or text template, and an HTTP
    provider has none.
    """
    for template in (settings.run, settings.agent, settings.text):
        if template:
            return str(template[0])
    return None


def available(provider: Provider) -> bool:
    """True when a command provider's program is on PATH; HTTP is always True.

    Whether an HTTP server is up is doctor's question, not a launch check.
    """
    name = program(provider.settings)
    return name is None or shutil.which(name) is not None


def spawn(provider: Provider, argv: list[str]) -> int:
    """Hand the terminal to an interactive program; return its exit code."""
    try:
        return subprocess.run(argv, check=False).returncode
    except (FileNotFoundError, PermissionError) as exc:
        raise AIInvocationError(
            f"{argv[0]} not found on PATH (provider {provider.name})",
            provider=provider.name,
        ) from exc


def invoke(
    provider: Provider,
    task: Task,
    prompt: str,
    *,
    workspace: Path | None = None,
    system: str | None = None,
    model: str | None = None,
    timeout: int | None = None,
) -> str:
    """Run an agent or text task and return the answer text.

    `timeout` (the caller's explicit limit) wins over the provider's `timeout`,
    which wins over the mode default. `model` wins over the provider's `model`.
    """
    settings = provider.settings
    limit = timeout or settings.timeout or MODE_TIMEOUTS[task.mode]
    chosen_model = model or settings.model
    if settings.base_url is not None:
        return _post(provider, prompt, system=system, model=chosen_model, timeout=limit)

    template = settings.agent if task.mode == "agent" else settings.text
    if template is None or task.mode == "interactive":
        raise AIInvocationError(
            f"{provider.name} has no {task.mode} template for task {task.name!r}",
            provider=provider.name,
        )
    session_id = mint_session_id(settings)
    # A text task gets all its data in the prompt and runs in an empty
    # directory, so it never sees the workspace path.
    shown_workspace = workspace if task.mode == "agent" else None
    values: dict[str, str | None] = {
        "workspace": str(shown_workspace) if shown_workspace is not None else None,
        "model": chosen_model,
        "system": system,
        "session_id": session_id,
    }
    if system and not uses(template, "system"):
        prompt = f"{system}\n\n{prompt}"
    argv = render(template, values, provider=provider.name)
    cleanup = None
    if session_id is not None and settings.cleanup:
        cleanup = functools.partial(_cleanup, provider.name, settings.cleanup, values)
    if task.mode == "agent":
        return _run(
            provider.name, argv, prompt, cwd=workspace, timeout=limit, cleanup=cleanup
        )
    # A text task needs no files; an empty directory keeps the program from
    # picking up project files or settings from wherever it was started. A
    # failure to remove that directory must not replace the answer.
    with tempfile.TemporaryDirectory(
        prefix="daily-driver-", ignore_cleanup_errors=True
    ) as scratch:
        return _run(
            provider.name,
            argv,
            prompt,
            cwd=Path(scratch),
            timeout=limit,
            cleanup=cleanup,
        )


def _communicate(
    proc: subprocess.Popen[str], prompt: str, timeout: float | None
) -> tuple[str, str]:
    return proc.communicate(input=prompt, timeout=timeout)


def _run(
    name: str,
    argv: list[str],
    prompt: str,
    *,
    cwd: Path | None,
    timeout: int,
    cleanup: Callable[[], None] | None = None,
) -> str:
    """Run `argv` with `prompt` on stdin and return its stdout.

    `cleanup` runs when the call ends, whatever the outcome, and not at all
    when the program never started.
    """
    try:
        # No start_new_session: the child shares our foreground process group,
        # so a terminal Ctrl-C reaches it instead of leaving it running.
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
        )
    except FileNotFoundError as exc:
        # Popen raises the same error for a missing working directory, and
        # names that directory as the missing file.
        if cwd is not None and str(exc.filename) == str(cwd):
            message = f"{name}: working directory {cwd} does not exist"
        else:
            message = f"{argv[0]} not found on PATH (provider {name})"
        raise AIInvocationError(message, provider=name) from exc
    except PermissionError as exc:
        raise AIInvocationError(
            f"{argv[0]} is not executable (provider {name})", provider=name
        ) from exc
    except OSError as exc:
        raise AIInvocationError(
            f"{argv[0]} could not start (provider {name}): {exc.strerror or exc}",
            provider=name,
        ) from exc
    try:
        stdout, stderr = _communicate(proc, prompt, timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.wait()
        raise AITimeoutError(
            f"{name} timed out after {timeout}s", provider=name, timeout_seconds=timeout
        ) from exc
    finally:
        if cleanup is not None:
            cleanup()
    if proc.returncode != 0:
        raise AIInvocationError(
            f"{name} exited {proc.returncode}",
            provider=name,
            stdout=stdout,
            stderr=stderr,
            returncode=proc.returncode,
        )
    if not stdout.strip():
        raise AIInvocationError(
            f"{name} returned no answer", provider=name, stdout=stdout, stderr=stderr
        )
    return stdout


def _cleanup(name: str, template: Template, values: Mapping[str, str | None]) -> None:
    # Runs in a finally: it must never raise over the call's own result or error.
    try:
        argv = render(template, values, provider=name)
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_CLEANUP_TIMEOUT,
            check=False,
        )
    except (AIInvocationError, OSError, subprocess.TimeoutExpired) as exc:
        log.warning("%s session cleanup failed: %s", name, exc)
        return
    if result.returncode != 0:
        output = (result.stderr or result.stdout).strip()[-200:]
        log.warning(
            "%s session cleanup exited %d%s",
            name,
            result.returncode,
            f": {output}" if output else "",
        )


def _post(
    provider: Provider,
    prompt: str,
    *,
    system: str | None,
    model: str | None,
    timeout: int,
) -> str:
    name = provider.name
    url = f"{(provider.settings.base_url or '').rstrip('/')}/chat/completions"
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        resp = requests.post(
            url, json={"model": model, "messages": messages}, timeout=timeout
        )
    except requests.Timeout as exc:
        raise AITimeoutError(
            f"{name} timed out after {timeout}s", provider=name, timeout_seconds=timeout
        ) from exc
    except requests.RequestException as exc:
        raise AIInvocationError(
            f"{name} not reachable at {url}", provider=name, stderr=str(exc)
        ) from exc
    if not resp.ok:
        raise AIInvocationError(
            f"{name} HTTP {resp.status_code}",
            provider=name,
            stdout=resp.text,
            returncode=resp.status_code,
        )
    choice = _first_choice(resp)
    if choice is None:
        raise AIInvocationError(
            f"{name} sent an unreadable reply from {url}",
            provider=name,
            stdout=resp.text,
        )
    answer = _content(choice)
    if not answer.strip():
        # The finish reason (often "length": the context ran out) and any
        # refusal say why there is no answer; the rest of the body is usage
        # counts, which would crowd them out of the error tail.
        message = choice.get("message")
        details = [
            f"{key}: {value}"
            for key, value in (
                ("finish_reason", choice.get("finish_reason")),
                (
                    "refusal",
                    message.get("refusal") if isinstance(message, dict) else None,
                ),
            )
            if value
        ]
        detail = f" ({'; '.join(details)})" if details else ""
        raise AIInvocationError(f"{name} returned no answer{detail}", provider=name)
    return answer


def list_models(provider: Provider, *, timeout: int) -> list[str]:
    """Model IDs an HTTP provider serves (`GET {base_url}/models`).

    Raises `AIInvocationError` when the server cannot be reached, answers
    with an error, or sends a body that is not a model list.
    """
    name = provider.name
    url = f"{(provider.settings.base_url or '').rstrip('/')}/models"
    try:
        resp = requests.get(url, timeout=timeout)
    except requests.RequestException as exc:
        raise AIInvocationError(
            f"{name} not reachable at {url}", provider=name, stderr=str(exc)
        ) from exc
    if not resp.ok:
        raise AIInvocationError(
            f"{name} HTTP {resp.status_code} from {url}",
            provider=name,
            stdout=resp.text,
            returncode=resp.status_code,
        )
    try:
        entries = resp.json()["data"]
    except (ValueError, KeyError, TypeError):
        entries = None
    if not isinstance(entries, list):
        raise AIInvocationError(
            f"{name} sent an unreadable model list from {url}",
            provider=name,
            stdout=resp.text,
        )
    return [
        str(entry["id"])
        for entry in entries
        if isinstance(entry, dict) and entry.get("id")
    ]


def _first_choice(resp: requests.Response) -> dict[str, Any] | None:
    try:
        choice = resp.json()["choices"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    return choice if isinstance(choice, dict) else None


def _content(choice: Mapping[str, Any]) -> str:
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    # Some servers send content as a list of parts: [{"type": "text", ...}].
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""
