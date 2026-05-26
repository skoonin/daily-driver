"""Provider-dispatch layer for headless AI calls.

`invoke_for(task, prompt, ...)` reads `ai.<task>` from the root config and
routes to either `claude_cli.invoke` (default) or `ollama_client.generate`.
All backend failures normalize to `AIInvocationError`, which carries
provider, stdout, and stderr so callers can surface the real upstream
error message — matching the diagnostic warning shape introduced in
PR #29 (`stdout=...; stderr=...`).
"""

from __future__ import annotations

from typing import Any

import requests

from daily_driver.core.config_models import AIConfig
from daily_driver.integrations import claude_cli, ollama_client


class AIInvocationError(RuntimeError):
    """A headless AI call failed. Carries provider + stdout/stderr context.

    The `stdout` / `stderr` attributes mirror `subprocess.CalledProcessError`
    so the existing PR #29 diagnostic warnings (which log both tails) keep
    working uniformly for claude and ollama.
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
    """A headless AI call timed out.

    Subclasses AIInvocationError so callers that already catch the base
    type don't need updating — but specific handlers can still distinguish
    timeouts from other failures by catching this type first. Carries a
    `timeout_seconds` field so the warning message can name the bound.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        timeout_seconds: int | None,
    ) -> None:
        super().__init__(message, provider=provider, stderr=message)
        self.timeout_seconds = timeout_seconds


def resolve_ai_config(config: dict[str, Any] | None) -> AIConfig:
    """Extract the `ai` block from a raw config dict, applying defaults.

    Missing or absent `ai:` key resolves to `AIConfig()` (claude defaults
    everywhere). Public so callers like `enrichment.py` can read the
    configured provider without going through `invoke_for`.
    """
    if not config:
        return AIConfig()
    raw = config.get("ai")
    if raw is None:
        return AIConfig()
    return AIConfig.model_validate(raw)


def invoke_for(
    task: str,
    prompt: str,
    *,
    config: dict[str, Any] | None,
    timeout: int | None = None,
    format_json: bool = False,
) -> str:
    """Dispatch a headless prompt to the provider configured for `task`.

    task: "enrichment" | "summary".

    Failure normalization:
        - All provider errors (auth, rate-limit, HTTP error, model-not-found,
          connection refused, response-error) raise `AIInvocationError`.
        - All timeouts raise `AITimeoutError` (subclass of AIInvocationError)
          so callers can match on a single type across providers.

    Catching `AIInvocationError` alone suffices for the diagnostic-warning
    path (PR #29). Catch `AITimeoutError` first when timeouts deserve a
    distinct message.
    """
    ai_cfg = resolve_ai_config(config)
    try:
        task_cfg = getattr(ai_cfg, task)
    except AttributeError as exc:
        raise ValueError(f"unknown AI task: {task!r}") from exc

    if task_cfg.provider == "claude":
        try:
            return claude_cli.invoke(
                prompt,
                headless=True,
                session_persistence=False,
                model=task_cfg.model,
                timeout=timeout,
            )
        except claude_cli.ClaudeInvocationError as exc:
            raise AIInvocationError(
                f"claude exited {exc.returncode}",
                provider="claude",
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                returncode=exc.returncode,
            ) from exc
        except claude_cli.ClaudeTimeoutError as exc:
            raise AITimeoutError(
                f"claude timed out after {timeout}s",
                provider="claude",
                timeout_seconds=timeout,
            ) from exc

    # Pydantic Literal["claude", "ollama"] on AITaskConfig.provider rules out
    # any other value before we reach here.
    effective_timeout = timeout if timeout is not None else ai_cfg.ollama.timeout
    model = task_cfg.model or "qwen2.5:14b"
    try:
        return ollama_client.generate(
            prompt,
            model=model,
            endpoint=ai_cfg.ollama.endpoint,
            timeout=effective_timeout,
            format_json=format_json,
        )
    except (
        ollama_client.OllamaNotReachableError,
        ollama_client.OllamaModelNotFoundError,
        ollama_client.OllamaResponseError,
    ) as exc:
        raise AIInvocationError(str(exc), provider="ollama", stderr=str(exc)) from exc
    except requests.Timeout as exc:
        raise AITimeoutError(
            f"ollama timed out after {effective_timeout}s",
            provider="ollama",
            timeout_seconds=effective_timeout,
        ) from exc
    except requests.HTTPError as exc:
        body = exc.response.text if exc.response is not None else ""
        rc = exc.response.status_code if exc.response is not None else None
        raise AIInvocationError(
            f"ollama HTTP {rc}",
            provider="ollama",
            stdout=body,
            returncode=rc,
        ) from exc
    except requests.RequestException as exc:
        # Defense in depth: ollama_client.generate wraps ConnectionError
        # into OllamaNotReachableError, but if a future code path lets any
        # other requests exception leak (DNS edge cases, SSL errors mid-
        # stream, etc.) the dispatch layer still normalizes it instead of
        # bypassing the AIInvocationError contract callers rely on.
        raise AIInvocationError(
            f"ollama request failed: {exc}",
            provider="ollama",
            stderr=str(exc),
        ) from exc
