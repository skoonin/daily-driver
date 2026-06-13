"""HTTP wrapper around the Ollama local server (`/api/generate`, `/api/tags`).

Mirrors the shape of `claude_cli.invoke()` enough that the provider-dispatch
layer can route headless prompts to either backend. No retry logic — local
Ollama either works or it doesn't; surfacing the failure beats hiding it.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import requests

from daily_driver.core.logging import get_logger

log = get_logger(__name__)

# Fallback model when a route resolves to ollama with no explicit model. Single
# source of truth so the dispatch layer and both doctor checks can never name
# different defaults (a mismatch would have doctor green a model the run won't
# actually use).
DEFAULT_MODEL = "qwen2.5:14b"


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
    """Size options.num_ctx for a prompt: est. tokens + headroom, floored/capped.

    Warns once when the estimate exceeds the cap: the server allocates only the
    capped window, so the prompt tail past it is truncated silently server-side.
    Surfacing it here turns a silent quality loss into a visible signal.
    """
    prompt_tokens = len(prompt) // 4  # ~4 chars/token, consistent with llm.py
    sized = prompt_tokens + _OUTPUT_HEADROOM_TOKENS
    if sized > _NUM_CTX_CAP:
        log.warning(
            "prompt needs ~%d context tokens but num_ctx is capped at %d; "
            "prompt tail will be truncated server-side",
            sized,
            _NUM_CTX_CAP,
        )
    return max(_NUM_CTX_FLOOR, min(sized, _NUM_CTX_CAP))


# Adaptive admission control for ollama calls.
#
# OLLAMA_NUM_PARALLEL is not detectable via the API, so a client that fans out
# wider than the server's real parallelism just makes requests queue server-side
# — and queue wait counts against the per-call timeout, turning a wide fan-out
# into guaranteed timeouts. The non-streaming /api/generate final JSON carries
# `total_duration` (nanoseconds the server spent on THIS request, excluding queue
# wait), so per call: queue_wait ~= wall_elapsed - total_duration/1e9. We use that
# measurement to converge a client-side permit pool to the server's true
# concurrency. The pool only shrinks within a run (never grows back) for stable,
# predictable behavior; raising OLLAMA_NUM_PARALLEL server-side is still the way
# to get more real parallel throughput.

# Poll interval for a blocked permit acquire. Bounded so acquisition never blocks
# forever: a worker waiting here is not running a request (not spending the
# enrich_timeout budget) and the coordinator's pool.shutdown(wait=False) drains
# in-flight work, so a short re-check loop is enough to stay interrupt-friendly.
_ACQUIRE_POLL_SECONDS = 0.5

# Queue-wait threshold above which we shrink the pool, derived from the per-call
# timeout (no new config knob): a fixed floor plus a fraction of the timeout.
_QUEUE_WAIT_FLOOR_SECONDS = 5.0
_QUEUE_WAIT_TIMEOUT_FRACTION = 0.2


def _queue_wait_threshold(timeout: int) -> float:
    return max(_QUEUE_WAIT_FLOOR_SECONDS, _QUEUE_WAIT_TIMEOUT_FRACTION * timeout)


class _AdmissionController:
    """Permit pool that converges to the ollama server's real parallelism.

    One instance per process, shared across every ollama enrichment path (the
    overlapped product+fit coordinator and the serial path both funnel through
    `generate`). Until :func:`reset_admission_control` configures a run, the pool
    is effectively unbounded so non-run callers (e.g. headless `summary`, which
    makes a single call) are never throttled.
    """

    # A large sentinel standing in for "unbounded" before a run configures the
    # pool — bigger than any sane max_parallel, so single-call paths never block.
    _UNBOUNDED = 1024

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._not_full = threading.Condition(self._lock)
        self._capacity = self._UNBOUNDED
        self._in_use = 0
        self._missing_duration_warned = False

    def reset(self, initial_size: int) -> None:
        """Re-arm the pool for a new run at `initial_size` permits (floor 1)."""
        with self._lock:
            self._capacity = max(1, initial_size)
            self._in_use = 0
            self._missing_duration_warned = False
            self._not_full.notify_all()

    def note_missing_duration(self) -> None:
        """Warn ONCE per run when a response carried no usable `total_duration`.

        Without that field the queue-wait measurement can't run, so the pool
        never adapts to a too-wide fan-out — the timeout path is the only
        backstop left. An older ollama (or a proxy that strips the field) hits
        this on every call; one signal turns a silent blind spot into a fixable
        one.
        """
        with self._lock:
            if self._missing_duration_warned:
                return
            self._missing_duration_warned = True
        log.warning(
            "ollama response carried no usable total_duration; queue-wait "
            "concurrency adaptation is disabled (timeout-based shrink still "
            "applies). Upgrade ollama or set ai.ollama.max_parallel to match "
            "OLLAMA_NUM_PARALLEL."
        )

    def acquire(self) -> None:
        """Block (with a bounded poll) until a permit is free, then take it."""
        with self._not_full:
            while self._in_use >= self._capacity:
                self._not_full.wait(timeout=_ACQUIRE_POLL_SECONDS)
            self._in_use += 1

    def release(self) -> None:
        with self._not_full:
            if self._in_use > 0:
                self._in_use -= 1
            self._not_full.notify()

    @property
    def capacity(self) -> int:
        with self._lock:
            return self._capacity

    def observe_queue_wait(self, queue_wait: float, timeout: int) -> None:
        """After a successful call, shrink by 1 if measured queue wait is high.

        Logs ONE warning per shrink. Never grows back within a run.
        """
        if queue_wait <= _queue_wait_threshold(timeout):
            return
        with self._not_full:
            if self._capacity <= 1:
                return
            old = self._capacity
            self._capacity = old - 1
            new = self._capacity
        log.warning(
            "ollama is serializing requests (queue wait ~%.0fs); reducing client "
            "concurrency %d->%d — raise OLLAMA_NUM_PARALLEL server-side to use more",
            queue_wait,
            old,
            new,
        )

    def shrink_on_timeout(self) -> None:
        """Halve the pool (floor 1) after a timeout. Logs one warning per shrink."""
        with self._not_full:
            if self._capacity <= 1:
                return
            old = self._capacity
            self._capacity = max(1, old // 2)
            new = self._capacity
        log.warning(
            "ollama call timed out; halving client concurrency %d->%d (likely "
            "self-induced queue wait) and retrying once",
            old,
            new,
        )


_controller = _AdmissionController()


def reset_admission_control(initial_size: int) -> None:
    """Re-arm the per-process ollama permit pool for a new enrichment run.

    Called from the enrichment coordinator's run-scoped reset points with
    `ai.ollama.max_parallel`. Resetting per run keeps shrink decisions from one
    run from leaking into the next.
    """
    _controller.reset(initial_size)


def _parse_total_duration_seconds(data: dict[str, Any]) -> float | None:
    """Extract `total_duration` (ns) from a generate response as seconds.

    Defensive: absent / non-numeric / non-positive -> None, so a malformed or
    missing field skips the queue-wait measurement rather than crashing or
    triggering a spurious shrink on bad data.
    """
    raw = data.get("total_duration")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return None
    if raw <= 0:
        return None
    return float(raw) / 1e9


def _post_generate(
    prompt: str,
    *,
    model: str,
    endpoint: str,
    timeout: int,
    format_json: bool,
) -> str:
    """One POST /api/generate under a permit; measures queue wait on success.

    Holds an admission-control permit for the duration of the request so the
    in-flight ollama call count never exceeds the (adaptive) pool size. On a
    successful response, derives the server-side queue wait from the wall clock
    and the response's `total_duration` and feeds it to the controller.
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
    _controller.acquire()
    started = time.monotonic()
    try:
        resp = requests.post(url, json=body, timeout=timeout)
    except requests.ConnectionError as exc:
        raise OllamaNotReachableError(
            f"Ollama not reachable at {endpoint}: {exc}. "
            "Check that the server is running (`ollama serve`) and the "
            "endpoint URL is correct."
        ) from exc
    finally:
        _controller.release()
    wall_elapsed = time.monotonic() - started
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
    server_seconds = _parse_total_duration_seconds(data)
    if server_seconds is not None:
        queue_wait = wall_elapsed - server_seconds
        _controller.observe_queue_wait(queue_wait, timeout)
    else:
        _controller.note_missing_duration()
    return str(response_text)


def generate(
    prompt: str,
    *,
    model: str,
    endpoint: str,
    timeout: int,
    format_json: bool = False,
) -> str:
    """POST /api/generate (non-streaming) and return the `response` field.

    A single timeout shrinks the admission pool and retries the request once: a
    timeout is most often queue wait the client itself caused by fanning out
    wider than the server's parallelism, so a narrower pool on the retry is
    likely to succeed within the budget. A second timeout propagates as the real
    failure, so the request phase is bounded at 2 * timeout (admission wait for a
    free permit is separate and bounded by the coordinator's stop/drain).

    Raises:
        OllamaNotReachableError: connection refused / DNS / network down.
        OllamaModelNotFoundError: server responded 404 (model not pulled).
        requests.HTTPError: any other non-2xx response.
        requests.Timeout: request exceeded `timeout` seconds on both attempts.
    """
    try:
        return _post_generate(
            prompt,
            model=model,
            endpoint=endpoint,
            timeout=timeout,
            format_json=format_json,
        )
    except requests.Timeout:
        # shrink_on_timeout's warning is gated at capacity > 1; once the pool has
        # floored at 1 the retry would otherwise be silent, so the per-item time
        # doubling has no attributable signal. Log the retry here regardless.
        _controller.shrink_on_timeout()
        log.debug(
            "ollama call timed out; retrying once at capacity %d", _controller.capacity
        )
        return _post_generate(
            prompt,
            model=model,
            endpoint=endpoint,
            timeout=timeout,
            format_json=format_json,
        )


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
