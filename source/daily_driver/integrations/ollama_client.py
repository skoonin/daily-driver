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
    body: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False}
    if format_json:
        body["format"] = "json"
    url = f"{endpoint.rstrip('/')}/api/generate"
    try:
        resp = requests.post(url, json=body, timeout=timeout)
    except requests.ConnectionError as exc:
        raise OllamaNotReachableError(
            f"Ollama not reachable at {endpoint}. Is `ollama serve` running?"
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
            f"Ollama not reachable at {endpoint}. Is `ollama serve` running?"
        ) from exc
    resp.raise_for_status()
    data = resp.json() or {}
    return [m.get("name", "") for m in data.get("models", []) if m.get("name")]


def reachable(endpoint: str, timeout: int = 5) -> bool:
    """True if `GET /api/tags` returns 2xx; False on any connection failure."""
    try:
        list_models(endpoint, timeout=timeout)
    except (OllamaNotReachableError, requests.RequestException):
        return False
    return True
