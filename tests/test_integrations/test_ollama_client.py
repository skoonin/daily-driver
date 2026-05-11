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


def test_reachable_true_when_tags_ok() -> None:
    with patch.object(ollama_client.requests, "get") as get:
        get.return_value = _fake_response(200, {"models": []})
        assert ollama_client.reachable("http://localhost:11434") is True


def test_reachable_false_when_connection_error() -> None:
    with patch.object(ollama_client.requests, "get") as get:
        get.side_effect = requests.ConnectionError("refused")
        assert ollama_client.reachable("http://localhost:11434") is False


def test_reachable_false_on_http_error() -> None:
    with patch.object(ollama_client.requests, "get") as get:
        get.return_value = _fake_response(500)
        assert ollama_client.reachable("http://localhost:11434") is False


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
