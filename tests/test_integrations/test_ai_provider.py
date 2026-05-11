"""Unit tests for the AI provider dispatch layer."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest
import requests

from daily_driver.integrations import ai_provider, claude_cli, ollama_client
from daily_driver.integrations.ai_provider import AIInvocationError


def test_default_config_routes_to_claude() -> None:
    with patch.object(claude_cli, "invoke", return_value="ok") as inv:
        out = ai_provider.invoke_for("enrichment", "hi", config={})
    assert out == "ok"
    assert inv.call_count == 1
    kwargs = inv.call_args.kwargs
    assert kwargs["headless"] is True
    assert kwargs["session_persistence"] is False


def test_none_config_routes_to_claude() -> None:
    with patch.object(claude_cli, "invoke", return_value="ok"):
        out = ai_provider.invoke_for("summary", "hi", config=None)
    assert out == "ok"


def test_ollama_provider_routes_to_ollama_client() -> None:
    cfg = {"ai": {"enrichment": {"provider": "ollama", "model": "qwen2.5:14b"}}}
    with patch.object(ollama_client, "generate", return_value="response text") as gen:
        out = ai_provider.invoke_for(
            "enrichment", "hi", config=cfg, timeout=42, format_json=True
        )
    assert out == "response text"
    kwargs = gen.call_args.kwargs
    assert kwargs["model"] == "qwen2.5:14b"
    assert kwargs["endpoint"] == "http://localhost:11434"
    assert kwargs["timeout"] == 42
    assert kwargs["format_json"] is True


def test_ollama_uses_config_timeout_when_none_passed() -> None:
    cfg = {
        "ai": {
            "enrichment": {"provider": "ollama", "model": "phi4"},
            "ollama": {"timeout": 90},
        }
    }
    with patch.object(ollama_client, "generate", return_value="x") as gen:
        ai_provider.invoke_for("enrichment", "p", config=cfg, timeout=None)
    assert gen.call_args.kwargs["timeout"] == 90


def test_claude_called_process_error_maps_to_ai_invocation_error() -> None:
    err = subprocess.CalledProcessError(
        returncode=1, cmd=["claude"], output="auth failed", stderr=""
    )
    with patch.object(claude_cli, "invoke", side_effect=err):
        with pytest.raises(AIInvocationError) as exc_info:
            ai_provider.invoke_for("enrichment", "p", config={})
    assert exc_info.value.provider == "claude"
    assert exc_info.value.stdout == "auth failed"
    assert exc_info.value.returncode == 1


def test_claude_timeout_propagates_unchanged() -> None:
    with patch.object(
        claude_cli,
        "invoke",
        side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=5),
    ):
        with pytest.raises(subprocess.TimeoutExpired):
            ai_provider.invoke_for("enrichment", "p", config={}, timeout=5)


def test_ollama_not_reachable_maps_to_ai_invocation_error() -> None:
    cfg = {"ai": {"enrichment": {"provider": "ollama", "model": "m"}}}
    with patch.object(
        ollama_client,
        "generate",
        side_effect=ollama_client.OllamaNotReachableError("nope"),
    ):
        with pytest.raises(AIInvocationError) as exc_info:
            ai_provider.invoke_for("enrichment", "p", config=cfg)
    assert exc_info.value.provider == "ollama"
    assert "nope" in exc_info.value.stderr


def test_ollama_model_not_found_maps_to_ai_invocation_error() -> None:
    cfg = {"ai": {"enrichment": {"provider": "ollama", "model": "m"}}}
    with patch.object(
        ollama_client,
        "generate",
        side_effect=ollama_client.OllamaModelNotFoundError("pull m"),
    ):
        with pytest.raises(AIInvocationError) as exc_info:
            ai_provider.invoke_for("enrichment", "p", config=cfg)
    assert exc_info.value.provider == "ollama"


def test_ollama_http_error_maps_to_ai_invocation_error_with_body() -> None:
    cfg = {"ai": {"enrichment": {"provider": "ollama", "model": "m"}}}
    fake_resp = MagicMock()
    fake_resp.status_code = 500
    fake_resp.text = "server boom"
    http_err = requests.HTTPError("500", response=fake_resp)
    with patch.object(ollama_client, "generate", side_effect=http_err):
        with pytest.raises(AIInvocationError) as exc_info:
            ai_provider.invoke_for("enrichment", "p", config=cfg)
    assert exc_info.value.provider == "ollama"
    assert exc_info.value.stdout == "server boom"
    assert exc_info.value.returncode == 500


def test_ollama_timeout_propagates_as_requests_timeout() -> None:
    cfg = {"ai": {"enrichment": {"provider": "ollama", "model": "m"}}}
    with patch.object(ollama_client, "generate", side_effect=requests.Timeout("slow")):
        with pytest.raises(requests.Timeout):
            ai_provider.invoke_for("enrichment", "p", config=cfg)


def test_unknown_task_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown AI task"):
        ai_provider.invoke_for("nonsense", "p", config={})


def test_summary_task_independent_model_from_enrichment() -> None:
    cfg = {
        "ai": {
            "enrichment": {"provider": "ollama", "model": "qwen2.5:14b"},
            "summary": {"provider": "claude", "model": "sonnet"},
        }
    }
    with patch.object(claude_cli, "invoke", return_value="s") as inv:
        ai_provider.invoke_for("summary", "p", config=cfg)
    assert inv.call_args.kwargs["model"] == "sonnet"
