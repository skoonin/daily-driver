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
