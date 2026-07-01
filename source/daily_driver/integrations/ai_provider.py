"""Provider-dispatch layer for headless AI calls.

`invoke_for(prompt, *, provider, model, ai, ...)` takes a route the caller has
already resolved — `provider` and `model` — plus the core provider-connection
blocks (`ai.claude` / `ai.ollama`) for tuning and the ollama endpoint/timeout.
The caller owns route resolution: core summary reads `ai.summary`, the
job_search scraper enrichers read `plugins.job_search.enrichment`. Dispatch
routes to `claude_cli.invoke` or `ollama_client.generate`. All backend failures
normalize to `AIInvocationError`, which carries provider, stdout, and stderr so
callers can surface the real upstream error message — matching the diagnostic
warning shape introduced in PR #29 (`stdout=...; stderr=...`).
"""

from __future__ import annotations

from typing import Literal, cast

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


def resolve_route(
    ai: AIConfig,
    *,
    task: str,
    domain_cfg: object | None = None,
    cli_provider: str | None = None,
    cli_model: str | None = None,
) -> tuple[Literal["claude", "ollama"], str | None]:
    """Resolve the (provider, model) route for a routable AI task.

    Provider and model are walked INDEPENDENTLY, most-specific to most-general:

        task/phase override -> domain default -> global ai default -> claude

    - Core tasks pass `task` naming a sub-block on `ai` (e.g. "summary",
      "voice_update") and leave `domain_cfg` None.
    - Plugin domains (enrichment) pass `domain_cfg` (the domain config, e.g.
      `EnrichmentConfig`) and `task` naming a per-phase block on it
      ("fit_notes"); `domain_cfg` itself supplies the domain default.

    `cli_provider` / `cli_model` outrank all config. Provider's terminal
    fallback is the literal "claude"; model has no hardcoded default and may
    resolve to None (the backend then picks its own default).
    """
    if domain_cfg is not None:
        override = getattr(domain_cfg, task, None)
    else:
        override = getattr(ai, task, None)

    def _walk(attr: str) -> str | None:
        for source in (override, domain_cfg, ai):
            if source is None:
                continue
            value = getattr(source, attr, None)
            if value is not None:
                return cast("str", value)
        return None

    provider = cli_provider or _walk("provider") or "claude"
    model = cli_model or _walk("model")
    return cast('Literal["claude", "ollama"]', provider), model


def invoke_for(
    prompt: str,
    *,
    provider: Literal["claude", "ollama"],
    model: str | None,
    ai: AIConfig,
    timeout: int | None = None,
    system: str | None = None,
    safe_mode: bool = False,
) -> str:
    """Dispatch a headless prompt to the resolved `provider` / `model` route.

    The caller resolves the route (core summary from `ai.summary`, the scraper
    enrichers from `plugins.job_search.enrichment`); `ai` supplies the shared
    provider-connection blocks (`ai.claude` tuning, `ai.ollama` endpoint /
    timeout).

    ``system`` is a static system prompt. Routed to claude's ``--system-prompt``
    (which Claude Code prompt-caches server-side, so a stable value across a
    batch is read from cache rather than reprocessed) and to ollama's native
    ``system`` field. Keep it byte-identical across a batch for cache reuse.

    ``safe_mode`` routes to claude's ``--safe-mode`` (no local customizations
    loaded; auth still works) and is inert for ollama. Set it for narrow
    enrichment calls that must not pick up workspace CLAUDE.md / settings / MCP.

    Failure normalization:
        - All provider errors (auth, rate-limit, HTTP error, model-not-found,
          connection refused, response-error) raise `AIInvocationError`.
        - All timeouts raise `AITimeoutError` (subclass of AIInvocationError)
          so callers can match on a single type across providers. The message
          names the provider (e.g. "ollama timed out after 60s").

    Catching `AIInvocationError` alone suffices for the diagnostic-warning
    path (PR #29). Catch `AITimeoutError` first when timeouts deserve a
    distinct message.
    """
    ai_cfg = ai

    if provider == "claude":
        try:
            return claude_cli.invoke(
                prompt,
                headless=True,
                session_persistence=False,
                model=model,
                timeout=timeout,
                system_prompt=system,
                safe_mode=safe_mode,
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

    if provider != "ollama":
        raise ValueError(f"unknown AI provider: {provider!r}")

    effective_timeout = timeout if timeout is not None else ai_cfg.ollama.timeout
    model = model or ollama_client.DEFAULT_MODEL
    try:
        return ollama_client.generate(
            prompt,
            model=model,
            endpoint=ai_cfg.ollama.endpoint,
            timeout=effective_timeout,
            system=system,
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
