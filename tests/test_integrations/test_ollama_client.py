"""Unit tests for the Ollama HTTP wrapper.

All HTTP traffic is mocked — no real network. Covers URL/body shape,
success path, model-not-found, generic HTTP error, connection failure,
timeout, and the `format: json` flag plumbing.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from daily_driver.integrations import ollama_client
from daily_driver.integrations.ollama_client import (
    OllamaModelNotFoundError,
    OllamaNotReachableError,
)


@pytest.fixture(autouse=True)
def _reset_admission() -> None:
    """Re-arm the process-wide admission pool to its unbounded default per test."""
    ollama_client._controller = ollama_client._AdmissionController()


def _fake_response(status: int, json_body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body or {}
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(
            f"{status} error", response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def test_generate_success_returns_response_field() -> None:
    with patch.object(ollama_client.requests, "post") as post:
        post.return_value = _fake_response(200, {"response": "hello world"})
        out = ollama_client.generate(
            "say hi",
            model="qwen2.5:14b",
            endpoint="http://localhost:11434",
            timeout=30,
        )
    assert out == "hello world"
    args, kwargs = post.call_args
    assert args[0] == "http://localhost:11434/api/generate"
    assert kwargs["json"]["model"] == "qwen2.5:14b"
    assert kwargs["json"]["prompt"] == "say hi"
    assert kwargs["json"]["stream"] is False
    assert "format" not in kwargs["json"]
    assert kwargs["timeout"] == 30


def test_generate_format_json_flag_plumbed() -> None:
    with patch.object(ollama_client.requests, "post") as post:
        post.return_value = _fake_response(200, {"response": "{}"})
        ollama_client.generate(
            "p",
            model="m",
            endpoint="http://localhost:11434",
            timeout=10,
            format_json=True,
        )
    assert post.call_args.kwargs["json"]["format"] == "json"


def test_generate_404_raises_model_not_found() -> None:
    with patch.object(ollama_client.requests, "post") as post:
        post.return_value = _fake_response(404)
        with pytest.raises(OllamaModelNotFoundError, match="not pulled"):
            ollama_client.generate(
                "p",
                model="missing:tag",
                endpoint="http://localhost:11434",
                timeout=10,
            )


def test_generate_500_raises_http_error() -> None:
    with patch.object(ollama_client.requests, "post") as post:
        post.return_value = _fake_response(500)
        with pytest.raises(requests.HTTPError):
            ollama_client.generate(
                "p", model="m", endpoint="http://localhost:11434", timeout=10
            )


def test_generate_connection_error_raises_not_reachable() -> None:
    with patch.object(ollama_client.requests, "post") as post:
        post.side_effect = requests.ConnectionError("refused")
        with pytest.raises(OllamaNotReachableError, match="not reachable"):
            ollama_client.generate(
                "p", model="m", endpoint="http://localhost:11434", timeout=10
            )


def test_generate_connection_error_message_includes_underlying_cause() -> None:
    """Per I2: the message must include the underlying error so DNS / typo
    failures aren't misdiagnosed as 'ollama not running'."""
    with patch.object(ollama_client.requests, "post") as post:
        post.side_effect = requests.ConnectionError(
            "Failed to resolve 'wrong-host.local'"
        )
        with pytest.raises(OllamaNotReachableError) as exc_info:
            ollama_client.generate(
                "p", model="m", endpoint="http://wrong-host.local:11434", timeout=10
            )
        msg = str(exc_info.value)
        assert "Failed to resolve" in msg
        assert "endpoint URL is correct" in msg


def test_generate_timeout_propagates() -> None:
    with patch.object(ollama_client.requests, "post") as post:
        post.side_effect = requests.Timeout("slow")
        with pytest.raises(requests.Timeout):
            ollama_client.generate(
                "p", model="m", endpoint="http://localhost:11434", timeout=1
            )


def test_endpoint_trailing_slash_normalized() -> None:
    with patch.object(ollama_client.requests, "post") as post:
        post.return_value = _fake_response(200, {"response": "x"})
        ollama_client.generate(
            "p", model="m", endpoint="http://localhost:11434/", timeout=10
        )
    assert post.call_args.args[0] == "http://localhost:11434/api/generate"


def test_list_models_returns_names() -> None:
    body = {"models": [{"name": "qwen2.5:14b"}, {"name": "phi4:latest"}]}
    with patch.object(ollama_client.requests, "get") as get:
        get.return_value = _fake_response(200, body)
        names = ollama_client.list_models("http://localhost:11434")
    assert names == ["qwen2.5:14b", "phi4:latest"]
    assert get.call_args.args[0] == "http://localhost:11434/api/tags"


def test_list_models_connection_error_raises_not_reachable() -> None:
    with patch.object(ollama_client.requests, "get") as get:
        get.side_effect = requests.ConnectionError("refused")
        with pytest.raises(OllamaNotReachableError):
            ollama_client.list_models("http://localhost:11434")


def test_generate_raises_on_200_with_error_field() -> None:
    """Ollama can return 200 OK with `error` in body on mid-stream failures."""
    from daily_driver.integrations.ollama_client import OllamaResponseError

    with patch.object(ollama_client.requests, "post") as post:
        post.return_value = _fake_response(
            200, {"error": "model 'foo' not found, try pulling it first"}
        )
        with pytest.raises(OllamaResponseError) as exc_info:
            ollama_client.generate(
                "hi", model="foo", endpoint="http://localhost:11434", timeout=5
            )
        assert "model 'foo' not found" in str(exc_info.value)


def test_generate_raises_on_200_with_empty_response_field() -> None:
    """Empty `response` is a silent failure mode; must surface explicitly."""
    from daily_driver.integrations.ollama_client import OllamaResponseError

    with patch.object(ollama_client.requests, "post") as post:
        post.return_value = _fake_response(200, {"response": ""})
        with pytest.raises(OllamaResponseError):
            ollama_client.generate(
                "hi", model="qwen2.5:14b", endpoint="http://localhost:11434", timeout=5
            )


# ---------------------------------------------------------------------------
# num_ctx sizing: ollama's server default context (4096) silently truncates
# long enrichment prompts, so generate() sizes options.num_ctx per request.
# ---------------------------------------------------------------------------


def test_generate_sends_num_ctx_floor_for_short_prompt() -> None:
    """A short prompt still requests at least the 4096 floor."""
    with patch.object(ollama_client.requests, "post") as post:
        post.return_value = _fake_response(200, {"response": "x"})
        ollama_client.generate(
            "hi", model="m", endpoint="http://localhost:11434", timeout=10
        )
    opts = post.call_args.kwargs["json"]["options"]
    assert opts["num_ctx"] == 4096


def test_generate_num_ctx_scales_with_prompt_length() -> None:
    """A long prompt requests more than the floor: ~tokens (len//4) + headroom."""
    prompt = "x" * 40000  # ~10000 tokens by the len//4 heuristic
    with patch.object(ollama_client.requests, "post") as post:
        post.return_value = _fake_response(200, {"response": "x"})
        ollama_client.generate(
            prompt, model="m", endpoint="http://localhost:11434", timeout=10
        )
    num_ctx = post.call_args.kwargs["json"]["options"]["num_ctx"]
    # 10000 prompt tokens + 1024 output headroom -> >= 11024, above the floor.
    assert num_ctx >= 11024
    assert num_ctx <= 16384


def test_generate_num_ctx_capped() -> None:
    """An enormous prompt is capped at the 16384 ceiling, not unbounded."""
    prompt = "x" * 400000  # ~100k tokens
    with patch.object(ollama_client.requests, "post") as post:
        post.return_value = _fake_response(200, {"response": "x"})
        ollama_client.generate(
            prompt, model="m", endpoint="http://localhost:11434", timeout=10
        )
    assert post.call_args.kwargs["json"]["options"]["num_ctx"] == 16384


def test_generate_warns_when_prompt_exceeds_cap(caplog) -> None:
    """A prompt past the num_ctx cap warns once that the tail truncates."""
    import logging

    prompt = "x" * 400000  # ~100k tokens, well past the 16384 cap
    with patch.object(ollama_client.requests, "post") as post:
        post.return_value = _fake_response(200, {"response": "x"})
        with caplog.at_level(logging.WARNING, logger="daily_driver"):
            ollama_client.generate(
                prompt, model="m", endpoint="http://localhost:11434", timeout=10
            )
    warnings = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and "truncat" in r.getMessage().lower()
    ]
    assert warnings, f"expected a truncation warning, got: {caplog.records}"
    # The message names both the estimated and capped token counts.
    assert "16384" in warnings[0]


def test_generate_no_truncation_warning_for_normal_prompt(caplog) -> None:
    """A prompt within the cap emits no truncation warning."""
    import logging

    with patch.object(ollama_client.requests, "post") as post:
        post.return_value = _fake_response(200, {"response": "x"})
        with caplog.at_level(logging.WARNING, logger="daily_driver"):
            ollama_client.generate(
                "short prompt", model="m", endpoint="http://localhost:11434", timeout=10
            )
    assert not [r for r in caplog.records if "truncat" in r.getMessage().lower()]


# ---------------------------------------------------------------------------
# Adaptive admission control: the client measures server-side queue wait
# (wall_elapsed - total_duration) and converges the permit pool to the
# server's real parallelism. Pool only shrinks within a run, floor 1.
# ---------------------------------------------------------------------------

_NS = 1_000_000_000  # nanoseconds per second


def test_high_queue_wait_shrinks_pool_by_one_and_warns_once(caplog) -> None:
    """A success whose wall time far exceeds server time (queue wait over the
    threshold) shrinks the pool by 1 and logs exactly one warning."""
    import logging

    ollama_client.reset_admission_control(4)
    # total_duration = 1s server time; force wall_elapsed = 20s so queue
    # wait ~= 19s, well above max(5s, 0.2*30) = 6s.
    times = iter([100.0, 120.0])
    with (
        patch.object(ollama_client.requests, "post") as post,
        patch.object(ollama_client.time, "monotonic", side_effect=lambda: next(times)),
    ):
        post.return_value = _fake_response(
            200, {"response": "ok", "total_duration": 1 * _NS}
        )
        with caplog.at_level(logging.WARNING, logger="daily_driver"):
            ollama_client.generate(
                "p", model="m", endpoint="http://localhost:11434", timeout=30
            )
    # Capacity dropping by exactly 1 proves a single shrink fired for this call.
    assert ollama_client._controller.capacity == 3
    serializing = {
        r.getMessage()
        for r in caplog.records
        if "serializing requests" in r.getMessage()
    }
    assert len(serializing) == 1


def test_low_queue_wait_does_not_shrink(caplog) -> None:
    """A success with negligible queue wait leaves the pool unchanged."""
    ollama_client.reset_admission_control(4)
    times = iter([100.0, 101.0])  # 1s wall, 0.9s server -> ~0.1s queue wait
    with (
        patch.object(ollama_client.requests, "post") as post,
        patch.object(ollama_client.time, "monotonic", side_effect=lambda: next(times)),
    ):
        post.return_value = _fake_response(
            200, {"response": "ok", "total_duration": int(0.9 * _NS)}
        )
        ollama_client.generate(
            "p", model="m", endpoint="http://localhost:11434", timeout=30
        )
    assert ollama_client._controller.capacity == 4


def test_bad_total_duration_skips_measurement_no_shrink() -> None:
    """Absent / zero / garbage total_duration never shrinks the pool."""
    ollama_client.reset_admission_control(4)
    for body in (
        {"response": "ok"},  # absent
        {"response": "ok", "total_duration": 0},  # zero
        {"response": "ok", "total_duration": "lots"},  # garbage
        {"response": "ok", "total_duration": True},  # bool, not a real ns count
    ):
        times = iter([100.0, 130.0])  # huge wall, but no usable server time
        with (
            patch.object(ollama_client.requests, "post") as post,
            patch.object(
                ollama_client.time, "monotonic", side_effect=lambda: next(times)
            ),
        ):
            post.return_value = _fake_response(200, body)
            ollama_client.generate(
                "p", model="m", endpoint="http://localhost:11434", timeout=30
            )
        assert ollama_client._controller.capacity == 4


def test_missing_total_duration_warns_once_per_run(caplog) -> None:
    """A server that never returns total_duration disables queue-wait adaptation;
    warn once per run so the blind spot is visible, not silent."""
    import logging

    ollama_client.reset_admission_control(4)
    with (
        patch.object(ollama_client.requests, "post") as post,
        patch.object(ollama_client.time, "monotonic", side_effect=[0.0, 1.0, 2.0, 3.0]),
    ):
        post.return_value = _fake_response(200, {"response": "ok"})  # no total_duration
        with caplog.at_level(logging.WARNING, logger="daily_driver"):
            ollama_client.generate(
                "p", model="m", endpoint="http://localhost:11434", timeout=30
            )
            ollama_client.generate(
                "p", model="m", endpoint="http://localhost:11434", timeout=30
            )
    msgs = {
        r.getMessage() for r in caplog.records if "total_duration" in r.getMessage()
    }
    assert len(msgs) == 1  # once per run despite two calls
    # A fresh run re-arms the warning.
    ollama_client.reset_admission_control(4)
    caplog.clear()
    with (
        patch.object(ollama_client.requests, "post") as post,
        patch.object(ollama_client.time, "monotonic", side_effect=[0.0, 1.0]),
    ):
        post.return_value = _fake_response(200, {"response": "ok"})
        with caplog.at_level(logging.WARNING, logger="daily_driver"):
            ollama_client.generate(
                "p", model="m", endpoint="http://localhost:11434", timeout=30
            )
    assert any("total_duration" in r.getMessage() for r in caplog.records)


def test_timeout_halves_pool_retries_once_and_succeeds() -> None:
    """A timeout halves the pool and retries once; a success on retry returns
    normally (counted enriched by the worker, not failed)."""
    ollama_client.reset_admission_control(4)
    # A timed-out attempt consumes one value (`started`) before the Timeout
    # propagates; the success consumes two (start + wall end). Over-provisioned.
    times = iter([0.0, 1.0, 5.0, 6.0])
    with (
        patch.object(ollama_client.requests, "post") as post,
        patch.object(ollama_client.time, "monotonic", side_effect=lambda: next(times)),
    ):
        post.side_effect = [
            requests.Timeout("slow"),
            _fake_response(200, {"response": "recovered"}),
        ]
        out = ollama_client.generate(
            "p", model="m", endpoint="http://localhost:11434", timeout=30
        )
    assert out == "recovered"
    assert post.call_count == 2
    assert ollama_client._controller.capacity == 2  # 4 // 2


def test_second_timeout_propagates() -> None:
    """Two consecutive timeouts propagate requests.Timeout (the existing
    counted-failure path); the request is attempted exactly twice."""
    ollama_client.reset_admission_control(4)
    times = iter([0.0, 1.0, 5.0, 6.0])
    with (
        patch.object(ollama_client.requests, "post") as post,
        patch.object(ollama_client.time, "monotonic", side_effect=lambda: next(times)),
    ):
        post.side_effect = requests.Timeout("slow")
        with pytest.raises(requests.Timeout):
            ollama_client.generate(
                "p", model="m", endpoint="http://localhost:11434", timeout=30
            )
    assert post.call_count == 2


def test_pool_never_shrinks_below_one() -> None:
    """Repeated high-queue-wait shrinks floor at 1; timeout halving also floors."""
    ollama_client.reset_admission_control(2)
    ollama_client._controller.shrink_on_timeout()  # 2 -> 1
    assert ollama_client._controller.capacity == 1
    ollama_client._controller.shrink_on_timeout()  # stays 1
    assert ollama_client._controller.capacity == 1
    ollama_client._controller.observe_queue_wait(999.0, timeout=30)  # stays 1
    assert ollama_client._controller.capacity == 1


def test_reset_re_arms_pool_to_initial_size() -> None:
    """A new run resets the pool, discarding a prior run's shrink decisions."""
    ollama_client.reset_admission_control(4)
    ollama_client._controller.shrink_on_timeout()  # 4 -> 2
    assert ollama_client._controller.capacity == 2
    ollama_client.reset_admission_control(4)
    assert ollama_client._controller.capacity == 4


def test_reset_floors_initial_size_at_one() -> None:
    ollama_client.reset_admission_control(0)
    assert ollama_client._controller.capacity == 1
