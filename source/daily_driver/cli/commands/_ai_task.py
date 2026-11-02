"""Run one headless AI task for a command: status line and error messages."""

from __future__ import annotations

from pathlib import Path

from daily_driver.core.console import Console
from daily_driver.core.providers import Provider
from daily_driver.integrations import provider as ai
from daily_driver.tasks import Task


def run_ai_task(
    label: str,
    provider: Provider,
    task: Task,
    prompt: str,
    *,
    workspace: Path,
    model: str | None,
    timeout: int | None,
) -> str | None:
    """Return the answer, or print why the call failed and return None."""
    title = f"{label} via {provider.name}"
    try:
        with Console.waiting(title):
            return ai.invoke(
                provider,
                task,
                prompt,
                workspace=workspace,
                model=model,
                timeout=timeout,
            )
    except ai.AITimeoutError as exc:
        Console.error(
            f"{title} timed out after {exc.timeout_seconds}s; raise with "
            f"--timeout or ai.providers.{provider.name}.timeout"
        )
    except ai.AIInvocationError as exc:
        message = f"{title} failed: {exc}"
        tail = (exc.stderr or exc.stdout or "").strip()[-200:]
        if tail:
            message = f"{message}\n{tail}"
        Console.error(
            f"{message}\nCheck that {provider.name} runs on its own, or give "
            f"{task.name} to another provider under ai.providers"
        )
    return None
