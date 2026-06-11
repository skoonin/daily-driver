"""HTTP wrapper around the Ollama local server (`/api/generate`, `/api/tags`).

Mirrors the shape of `claude_cli.invoke()` enough that the provider-dispatch
layer can route headless prompts to either backend. No retry logic — local
Ollama either works or it doesn't; surfacing the failure beats hiding it.
"""

from __future__ import annotations

from typing import Any

import requests


class OllamaNotReachableError(RuntimeError):
    """Raised when the Ollama HTTP endpoint cannot be contacted."""


class OllamaModelNotFoundError(RuntimeError):
    """Raised when the requested model is not pulled on the server."""


class OllamaResponseError(RuntimeError):
    """Raised when Ollama returns 200 but the JSON body signals an error.

    Some failure modes (model crash mid-generation, runner OOM, certain
    schema errors with non-streaming requests) come back as 200 OK with
    an `error` field or an empty `response` field. Treating those as
    silent successes caches empty strings and never retries — surface
    them as real errors instead.
    """


# Ollama's effective context default (OLLAMA_CONTEXT_LENGTH) varies by machine
# (commonly 4k, scaling up with VRAM) and silently truncates prompts past it,
# so we size num_ctx per request from the prompt. ~4 chars/token matches the
# llm.py estimate; +1024 leaves output headroom; floor keeps a sane minimum and
# the cap bounds the per-request RAM allocation (RAM scales with context).
_NUM_CTX_FLOOR = 4096
_NUM_CTX_CAP = 16384
_OUTPUT_HEADROOM_TOKENS = 1024


def _estimate_num_ctx(prompt: str) -> int:
    """Size options.num_ctx for a prompt: est. tokens + headroom, floored/capped."""
    prompt_tokens = len(prompt) // 4  # ~4 chars/token, consistent with llm.py
    sized = prompt_tokens + _OUTPUT_HEADROOM_TOKENS
    return max(_NUM_CTX_FLOOR, min(sized, _NUM_CTX_CAP))


def generate(
    prompt: str,
    *,
    model: str,
    endpoint: str,
    timeout: int,
    format_json: bool = False,
) -> str:
    """POST /api/generate (non-streaming) and return the `response` field.

    Raises:
        OllamaNotReachableError: connection refused / DNS / network down.
        OllamaModelNotFoundError: server responded 404 (model not pulled).
        requests.HTTPError: any other non-2xx response.
        requests.Timeout: request exceeded `timeout` seconds.
    """
    body: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_ctx": _estimate_num_ctx(prompt)},
    }
    if format_json:
        body["format"] = "json"
    url = f"{endpoint.rstrip('/')}/api/generate"
    try:
        resp = requests.post(url, json=body, timeout=timeout)
    except requests.ConnectionError as exc:
        raise OllamaNotReachableError(
            f"Ollama not reachable at {endpoint}: {exc}. "
            "Check that the server is running (`ollama serve`) and the "
            "endpoint URL is correct."
        ) from exc
    if resp.status_code == 404:
        raise OllamaModelNotFoundError(
            f"model {model!r} not pulled. Run: ollama pull {model}"
        )
    resp.raise_for_status()
    data = resp.json() or {}
    err = data.get("error")
    if err:
        raise OllamaResponseError(f"ollama returned error: {err}")
    response_text = data.get("response", "")
    if not response_text:
        raise OllamaResponseError(f"ollama returned no response field (body: {data!r})")
    return str(response_text)


def list_models(endpoint: str, timeout: int = 5) -> list[str]:
    """GET /api/tags and return the list of pulled model names.

    Raises OllamaNotReachableError on connection failure; other HTTP errors
    propagate as `requests.HTTPError`.
    """
    url = f"{endpoint.rstrip('/')}/api/tags"
    try:
        resp = requests.get(url, timeout=timeout)
    except requests.ConnectionError as exc:
        raise OllamaNotReachableError(
            f"Ollama not reachable at {endpoint}: {exc}. "
            "Check that the server is running (`ollama serve`) and the "
            "endpoint URL is correct."
        ) from exc
    resp.raise_for_status()
    data = resp.json() or {}
    return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
