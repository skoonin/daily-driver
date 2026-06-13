"""Unit tests for the AI provider dispatch layer.

`invoke_for` takes a resolved route (provider + model) from the caller plus
the core provider-connection blocks (`ai.claude` / `ai.ollama`). Callers own
route resolution: summary reads `ai.summary`, the scraper enrichers read
`plugins.job_search.enrichment`.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from daily_driver.core.config_models import AIConfig
from daily_driver.integrations import ai_provider, claude_cli, ollama_client
from daily_driver.integrations.ai_provider import AIInvocationError


def test_claude_route_dispatches_to_claude() -> None:
    with patch.object(claude_cli, "invoke", return_value="ok") as inv:
        out = ai_provider.invoke_for(
            "hi", provider="claude", model="sonnet", ai=AIConfig()
        )
    assert out == "ok"
    assert inv.call_count == 1
    kwargs = inv.call_args.kwargs
    assert kwargs["headless"] is True
    assert kwargs["session_persistence"] is False
    assert kwargs["model"] == "sonnet"


def test_claude_route_never_touches_ollama_admission_pool() -> None:
    """The claude path must not acquire an ollama permit: admission control is
    ollama-only and rate-limited claude calls should never be throttled by it."""
    with (
        patch.object(claude_cli, "invoke", return_value="ok"),
        patch.object(ollama_client._controller, "acquire") as acquire,
    ):
        ai_provider.invoke_for("hi", provider="claude", model="sonnet", ai=AIConfig())
    assert acquire.call_count == 0


def test_claude_route_default_model_none() -> None:
    with patch.object(claude_cli, "invoke", return_value="ok") as inv:
        ai_provider.invoke_for("hi", provider="claude", model=None, ai=AIConfig())
    assert inv.call_args.kwargs["model"] is None


def test_ollama_route_dispatches_to_ollama_client() -> None:
    with patch.object(ollama_client, "generate", return_value="response text") as gen:
        out = ai_provider.invoke_for(
            "hi",
            provider="ollama",
            model="qwen2.5:14b",
            ai=AIConfig(),
            timeout=42,
        )
    assert out == "response text"
    kwargs = gen.call_args.kwargs
    assert kwargs["model"] == "qwen2.5:14b"
    assert kwargs["endpoint"] == "http://localhost:11434"
    assert kwargs["timeout"] == 42
    # The prompt rides through so the client can size num_ctx.
    assert gen.call_args.args[0] == "hi" or kwargs.get("prompt") == "hi"


def test_ollama_route_default_model_when_none() -> None:
    with patch.object(ollama_client, "generate", return_value="x") as gen:
        ai_provider.invoke_for("p", provider="ollama", model=None, ai=AIConfig())
    assert gen.call_args.kwargs["model"] == "qwen2.5:14b"


def test_ollama_uses_config_timeout_when_none_passed() -> None:
    ai = AIConfig.model_validate({"ollama": {"timeout": 90}})
    with patch.object(ollama_client, "generate", return_value="x") as gen:
        ai_provider.invoke_for(
            "p", provider="ollama", model="phi4", ai=ai, timeout=None
        )
    assert gen.call_args.kwargs["timeout"] == 90


def test_ollama_uses_config_endpoint() -> None:
    """A non-default ai.ollama.endpoint reaches the ollama client call."""
    ai = AIConfig.model_validate({"ollama": {"endpoint": "http://gpu-box:11434"}})
    with patch.object(ollama_client, "generate", return_value="x") as gen:
        ai_provider.invoke_for("p", provider="ollama", model="phi4", ai=ai)
    assert gen.call_args.kwargs["endpoint"] == "http://gpu-box:11434"


def test_claude_called_process_error_maps_to_ai_invocation_error() -> None:
    err = claude_cli.ClaudeInvocationError(
        1, ["claude"], stdout="auth failed", stderr=""
    )
    with patch.object(claude_cli, "invoke", side_effect=err):
        with pytest.raises(AIInvocationError) as exc_info:
            ai_provider.invoke_for("p", provider="claude", model=None, ai=AIConfig())
    assert exc_info.value.provider == "claude"
    assert exc_info.value.stdout == "auth failed"
    assert exc_info.value.returncode == 1


def test_claude_timeout_wraps_to_ai_timeout_error() -> None:
    from daily_driver.integrations.ai_provider import AITimeoutError

    err = claude_cli.ClaudeTimeoutError(5, ["claude"])
    with patch.object(claude_cli, "invoke", side_effect=err):
        with pytest.raises(AITimeoutError) as exc_info:
            ai_provider.invoke_for(
                "p", provider="claude", model=None, ai=AIConfig(), timeout=5
            )
    assert exc_info.value.provider == "claude"
    assert exc_info.value.timeout_seconds == 5


def test_ollama_not_reachable_maps_to_ai_invocation_error() -> None:
    with patch.object(
        ollama_client,
        "generate",
        side_effect=ollama_client.OllamaNotReachableError("nope"),
    ):
        with pytest.raises(AIInvocationError) as exc_info:
            ai_provider.invoke_for("p", provider="ollama", model="m", ai=AIConfig())
    assert exc_info.value.provider == "ollama"
    assert "nope" in exc_info.value.stderr


def test_ollama_model_not_found_maps_to_ai_invocation_error() -> None:
    with patch.object(
        ollama_client,
        "generate",
        side_effect=ollama_client.OllamaModelNotFoundError("pull m"),
    ):
        with pytest.raises(AIInvocationError) as exc_info:
            ai_provider.invoke_for("p", provider="ollama", model="m", ai=AIConfig())
    assert exc_info.value.provider == "ollama"


def test_ollama_http_error_maps_to_ai_invocation_error_with_body() -> None:
    fake_resp = MagicMock()
    fake_resp.status_code = 500
    fake_resp.text = "server boom"
    http_err = requests.HTTPError("500", response=fake_resp)
    with patch.object(ollama_client, "generate", side_effect=http_err):
        with pytest.raises(AIInvocationError) as exc_info:
            ai_provider.invoke_for("p", provider="ollama", model="m", ai=AIConfig())
    assert exc_info.value.provider == "ollama"
    assert exc_info.value.stdout == "server boom"
    assert exc_info.value.returncode == 500


def test_ollama_timeout_wraps_to_ai_timeout_error() -> None:
    from daily_driver.integrations.ai_provider import AITimeoutError

    ai = AIConfig.model_validate(
        {"ollama": {"endpoint": "http://localhost:11434", "timeout": 7}}
    )
    with patch.object(ollama_client, "generate", side_effect=requests.Timeout("slow")):
        with pytest.raises(AITimeoutError) as exc_info:
            ai_provider.invoke_for("p", provider="ollama", model="m", ai=ai, timeout=7)
    assert exc_info.value.provider == "ollama"
    assert exc_info.value.timeout_seconds == 7
    assert isinstance(exc_info.value, AIInvocationError)


def test_ollama_request_exception_normalized_as_ai_invocation_error() -> None:
    leak = requests.ConnectionError("DNS lookup failed")
    with patch.object(ollama_client, "generate", side_effect=leak):
        with pytest.raises(AIInvocationError) as exc_info:
            ai_provider.invoke_for("p", provider="ollama", model="m", ai=AIConfig())
    assert exc_info.value.provider == "ollama"
    assert "DNS lookup failed" in str(exc_info.value)


def test_unknown_provider_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown AI provider"):
        ai_provider.invoke_for("p", provider="nonsense", model=None, ai=AIConfig())  # type: ignore[arg-type]
