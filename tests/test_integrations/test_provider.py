"""The provider layer: template rendering and headless dispatch.

Command providers run a small real program written into tmp_path, so argv,
stdin, working directory and exit codes are the real subprocess behaviour.
HTTP traffic is mocked.
"""

from __future__ import annotations

import json
import stat
import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from daily_driver.core.providers import Provider, ProviderConfig, builtin_providers
from daily_driver.integrations import provider as provider_mod
from daily_driver.integrations.provider import (
    AIInvocationError,
    AITimeoutError,
    available,
    invoke,
    list_models,
    program,
    render,
)
from daily_driver.tasks import SUMMARY, VOICE_UPDATE

# --- render ----------------------------------------------------------------


def test_render_fills_placeholders_and_drops_empty_groups() -> None:
    argv = render(
        ["tool", ["--model", "{model}"], ["--agent", "{agent}"], "{workspace}"],
        {"model": "opus", "agent": None, "workspace": "/w"},
    )
    assert argv == ["tool", "--model", "opus", "/w"]


def test_render_drops_group_with_empty_string_value() -> None:
    assert render(["tool", ["{command}"]], {"command": ""}) == ["tool"]


def test_render_substitutes_inside_longer_strings() -> None:
    argv = render(["--prompt", "Read {prompt_file} now."], {"prompt_file": "/p.md"})
    assert argv == ["--prompt", "Read /p.md now."]


def test_render_drops_a_group_when_any_of_its_placeholders_is_empty() -> None:
    template: list[str | list[str]] = ["tool", ["--pair", "{model}", "{system}"]]
    assert render(template, {"model": "m", "system": None}) == ["tool"]
    assert render(template, {"model": "m", "system": "s"}) == [
        "tool",
        "--pair",
        "m",
        "s",
    ]


def test_render_ungrouped_missing_value_is_an_error() -> None:
    with pytest.raises(AIInvocationError, match="system"):
        render(["tool", "{system}"], {"system": None})


def test_claude_builtin_text_template_argv() -> None:
    text = builtin_providers()["claude"].text
    assert text is not None
    argv = render(text, {"model": "haiku", "system": "be brief"})
    assert argv == [
        "claude",
        "-p",
        "--no-session-persistence",
        "--safe-mode",
        "--model",
        "haiku",
        "--system-prompt",
        "be brief",
    ]


# --- command dispatch ------------------------------------------------------

# Echoes a JSON record of how it was called, then exits with $FAKE_RC.
_FAKE = """\
import json, os, sys
record = {"argv": sys.argv[1:], "cwd": os.getcwd(), "stdin": sys.stdin.read()}
log = os.environ.get("FAKE_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(json.dumps(record) + "\\n")
print(json.dumps(record))
rc = int(os.environ.get("FAKE_RC", "0"))
if rc:
    print("boom on stderr", file=sys.stderr)
sys.exit(rc)
"""


@pytest.fixture
def fake_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "fake_tool.py"
    script.write_text(_FAKE, encoding="utf-8")
    tool = tmp_path / "fake-tool"
    tool.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.jsonl"))
    return tool


def _calls(tmp_path: Path) -> list[dict[str, Any]]:
    lines = (tmp_path / "calls.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


def _provider(**fields: Any) -> Provider:
    return Provider("fake", ProviderConfig.model_validate(fields))


def test_text_call_sends_prompt_on_stdin_and_returns_stdout(fake_tool: Path) -> None:
    out = invoke(_provider(text=[str(fake_tool), "run"]), VOICE_UPDATE, "hello")
    assert json.loads(out)["stdin"] == "hello"
    assert json.loads(out)["argv"] == ["run"]


def test_text_call_runs_in_a_fresh_temp_dir(fake_tool: Path, tmp_path: Path) -> None:
    invoke(_provider(text=[str(fake_tool)]), VOICE_UPDATE, "x", workspace=tmp_path)
    cwd = Path(_calls(tmp_path)[0]["cwd"])
    assert cwd != tmp_path
    assert not cwd.exists()


def test_agent_call_runs_in_the_workspace(fake_tool: Path, tmp_path: Path) -> None:
    invoke(_provider(agent=[str(fake_tool)]), SUMMARY, "x", workspace=tmp_path)
    assert Path(_calls(tmp_path)[0]["cwd"]).resolve() == tmp_path.resolve()


def test_system_goes_to_its_placeholder(fake_tool: Path) -> None:
    provider = _provider(text=[str(fake_tool), ["--system", "{system}"]])
    out = json.loads(invoke(provider, VOICE_UPDATE, "body", system="rules"))
    assert out["argv"] == ["--system", "rules"]
    assert out["stdin"] == "body"


def test_system_is_prepended_without_a_placeholder(fake_tool: Path) -> None:
    out = json.loads(
        invoke(_provider(text=[str(fake_tool)]), VOICE_UPDATE, "body", system="rules")
    )
    assert out["stdin"] == "rules\n\nbody"


def test_model_argument_beats_provider_model(fake_tool: Path) -> None:
    provider = _provider(text=[str(fake_tool), ["-m", "{model}"]], model="small")
    assert json.loads(invoke(provider, VOICE_UPDATE, "x"))["argv"] == ["-m", "small"]
    out = invoke(provider, VOICE_UPDATE, "x", model="big")
    assert json.loads(out)["argv"] == ["-m", "big"]


def test_nonzero_exit_raises_with_output(
    fake_tool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_RC", "3")
    with pytest.raises(AIInvocationError) as info:
        invoke(_provider(text=[str(fake_tool)]), VOICE_UPDATE, "x")
    assert info.value.provider == "fake"
    assert info.value.returncode == 3
    assert "boom on stderr" in info.value.stderr


def test_missing_program_raises_invocation_error(tmp_path: Path) -> None:
    with pytest.raises(AIInvocationError, match="not found"):
        invoke(_provider(text=[str(tmp_path / "nope")]), VOICE_UPDATE, "x")


def test_timeout_raises_timeout_error(tmp_path: Path) -> None:
    provider = _provider(text=[sys.executable, "-c", "import time; time.sleep(5)"])
    with pytest.raises(AITimeoutError) as info:
        invoke(provider, VOICE_UPDATE, "x", timeout=1)
    assert info.value.timeout_seconds == 1
    assert "fake" in str(info.value)


def test_provider_timeout_used_when_call_gives_none(
    fake_tool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[float | None] = []
    real = provider_mod._communicate

    def spy(proc: Any, prompt: str, timeout: float | None) -> tuple[str, str]:
        seen.append(timeout)
        return real(proc, prompt, timeout)

    monkeypatch.setattr(provider_mod, "_communicate", spy)
    invoke(_provider(text=[str(fake_tool)], timeout=42), VOICE_UPDATE, "x")
    invoke(_provider(text=[str(fake_tool)]), VOICE_UPDATE, "x")
    invoke(_provider(agent=[str(fake_tool)]), SUMMARY, "x")
    assert seen == [42, 180, 900]


def test_call_timeout_beats_provider_timeout(
    fake_tool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[float | None] = []
    real = provider_mod._communicate

    def spy(proc: Any, prompt: str, timeout: float | None) -> tuple[str, str]:
        seen.append(timeout)
        return real(proc, prompt, timeout)

    monkeypatch.setattr(provider_mod, "_communicate", spy)
    invoke(_provider(text=[str(fake_tool)], timeout=42), VOICE_UPDATE, "x", timeout=7)
    assert seen == [7]


def test_blank_output_is_no_answer() -> None:
    blank = _provider(text=[sys.executable, "-c", "print('  ')"])
    with pytest.raises(AIInvocationError, match="returned no answer"):
        invoke(blank, VOICE_UPDATE, "x")


def test_missing_workspace_is_reported_as_a_directory(
    fake_tool: Path, tmp_path: Path
) -> None:
    gone = tmp_path / "gone"
    with pytest.raises(AIInvocationError, match="working directory .*gone"):
        invoke(_provider(agent=[str(fake_tool)]), SUMMARY, "x", workspace=gone)


def test_unenterable_workspace_is_reported_as_a_directory(
    fake_tool: Path, tmp_path: Path
) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        with pytest.raises(AIInvocationError, match="working directory .*locked"):
            invoke(_provider(agent=[str(fake_tool)]), SUMMARY, "x", workspace=locked)
    finally:
        locked.chmod(stat.S_IRWXU)


def test_workspace_that_is_a_file_is_reported_as_a_directory(
    fake_tool: Path, tmp_path: Path
) -> None:
    a_file = tmp_path / "a-file"
    a_file.write_text("")
    with pytest.raises(AIInvocationError, match="working directory .*a-file"):
        invoke(_provider(agent=[str(fake_tool)]), SUMMARY, "x", workspace=a_file)


@pytest.mark.parametrize(
    ("content", "mode", "message"),
    [
        (b"#!/bin/sh\n", 0o644, "is not executable"),
        (b"\x00\x01 not an executable format", 0o755, "could not start"),
    ],
)
def test_spawn_of_a_program_that_cannot_start_says_why(
    tmp_path: Path, content: bytes, mode: int, message: str
) -> None:
    tool = tmp_path / "tool"
    tool.write_bytes(content)
    tool.chmod(mode)
    with pytest.raises(AIInvocationError, match=message):
        provider_mod.spawn(_provider(run=[str(tool)]), [str(tool)])


def test_program_that_cannot_start_raises_invocation_error(tmp_path: Path) -> None:
    not_a_program = tmp_path / "not-a-program"
    not_a_program.write_bytes(b"\x00\x01 not an executable format")
    not_a_program.chmod(not_a_program.stat().st_mode | stat.S_IEXEC)
    with pytest.raises(AIInvocationError, match="could not start"):
        invoke(_provider(text=[str(not_a_program)]), VOICE_UPDATE, "x")


def _session_provider(tool: Path) -> Provider:
    return _provider(
        session_id="ses_{uuid}",
        text=[str(tool), "run", ["-s", "{session_id}"]],
        cleanup=[str(tool), "delete", "{session_id}"],
    )


def test_session_minted_per_call_and_cleaned_up(
    fake_tool: Path, tmp_path: Path
) -> None:
    invoke(_session_provider(fake_tool), VOICE_UPDATE, "x")
    run, cleanup = _calls(tmp_path)
    session = run["argv"][2]
    assert session.startswith("ses_")
    uuid.UUID(session.removeprefix("ses_"))
    assert cleanup["argv"] == ["delete", session]


def test_cleanup_runs_after_a_failed_call(
    fake_tool: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_RC", "1")
    with pytest.raises(AIInvocationError):
        invoke(_session_provider(fake_tool), VOICE_UPDATE, "x")
    assert [call["argv"][0] for call in _calls(tmp_path)] == ["run", "delete"]


def test_cleanup_runs_after_a_timeout(tmp_path: Path, fake_tool: Path) -> None:
    provider = _provider(
        session_id="{uuid}",
        text=[sys.executable, "-c", "import time; time.sleep(5)", "{session_id}"],
        cleanup=[str(fake_tool), "delete", "{session_id}"],
    )
    with pytest.raises(AITimeoutError):
        invoke(provider, VOICE_UPDATE, "x", timeout=1)
    assert _calls(tmp_path)[0]["argv"][0] == "delete"


def test_no_cleanup_when_the_program_never_started(
    fake_tool: Path, tmp_path: Path
) -> None:
    provider = _provider(
        session_id="{uuid}",
        text=[str(tmp_path / "nope"), "{session_id}"],
        cleanup=[str(fake_tool), "delete", "{session_id}"],
    )
    with pytest.raises(AIInvocationError, match="not found"):
        invoke(provider, VOICE_UPDATE, "x")
    assert not (tmp_path / "calls.jsonl").exists()


def test_failed_cleanup_warns_and_keeps_the_result(
    fake_tool: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    provider = _provider(
        session_id="{uuid}",
        text=[str(fake_tool)],
        cleanup=[str(tmp_path / "missing-cleanup")],
    )
    out = invoke(provider, VOICE_UPDATE, "hello")
    assert json.loads(out)["stdin"] == "hello"
    assert "cleanup" in caplog.text


# --- HTTP dispatch ---------------------------------------------------------


def _http() -> Provider:
    return _provider(base_url="http://localhost:1234/v1/", model="qwen3-coder")


def _response(status: int, body: Any) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    resp.text = json.dumps(body)
    resp.ok = status < 400
    return resp


def test_http_posts_chat_completion_and_returns_content() -> None:
    body = {"choices": [{"message": {"content": "an answer"}}]}
    with patch.object(provider_mod.requests, "post") as post:
        post.return_value = _response(200, body)
        out = invoke(_http(), VOICE_UPDATE, "question", system="rules", timeout=7)
    assert out == "an answer"
    url = post.call_args.args[0]
    sent = post.call_args.kwargs["json"]
    assert url == "http://localhost:1234/v1/chat/completions"
    assert sent["model"] == "qwen3-coder"
    assert sent["messages"] == [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "question"},
    ]
    assert post.call_args.kwargs["timeout"] == 7


def test_http_without_system_sends_only_the_user_message() -> None:
    body = {"choices": [{"message": {"content": "ok"}}]}
    with patch.object(provider_mod.requests, "post") as post:
        post.return_value = _response(200, body)
        invoke(_http(), VOICE_UPDATE, "q")
    assert post.call_args.kwargs["json"]["messages"] == [
        {"role": "user", "content": "q"}
    ]


def test_http_error_status_raises_with_body() -> None:
    with patch.object(provider_mod.requests, "post") as post:
        post.return_value = _response(404, {"error": "model not loaded"})
        with pytest.raises(AIInvocationError) as info:
            invoke(_http(), VOICE_UPDATE, "q")
    assert info.value.returncode == 404
    assert "model not loaded" in info.value.stdout


def test_http_unreachable_names_the_url() -> None:
    with patch.object(provider_mod.requests, "post") as post:
        post.side_effect = requests.ConnectionError("refused")
        with pytest.raises(AIInvocationError, match="localhost:1234"):
            invoke(_http(), VOICE_UPDATE, "q")


def test_http_timeout_raises_timeout_error() -> None:
    with patch.object(provider_mod.requests, "post") as post:
        post.side_effect = requests.Timeout()
        with pytest.raises(AITimeoutError):
            invoke(_http(), VOICE_UPDATE, "q", timeout=3)


@pytest.mark.parametrize("content", ["", "  \n", None])
def test_http_empty_content_raises(content: str | None) -> None:
    body = {"choices": [{"message": {"content": content}}]}
    with patch.object(provider_mod.requests, "post") as post:
        post.return_value = _response(200, body)
        with pytest.raises(AIInvocationError, match="no answer"):
            invoke(_http(), VOICE_UPDATE, "q")


def test_http_no_answer_names_the_finish_reason() -> None:
    body = {
        "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 9000},
    }
    with patch.object(provider_mod.requests, "post") as post:
        post.return_value = _response(200, body)
        with pytest.raises(AIInvocationError) as info:
            invoke(_http(), VOICE_UPDATE, "q")
    assert str(info.value) == "fake returned no answer (finish_reason: length)"
    assert info.value.stdout == ""


def test_http_no_answer_names_the_refusal() -> None:
    body = {
        "choices": [
            {
                "message": {"content": None, "refusal": "I cannot help with that"},
                "finish_reason": "stop",
            }
        ]
    }
    with patch.object(provider_mod.requests, "post") as post:
        post.return_value = _response(200, body)
        with pytest.raises(AIInvocationError) as info:
            invoke(_http(), VOICE_UPDATE, "q")
    assert str(info.value) == (
        "fake returned no answer (finish_reason: stop; refusal: I cannot help "
        "with that)"
    )


@pytest.mark.parametrize("body", [{"error": "model not loaded"}, {"choices": []}, []])
def test_http_reply_without_a_choice_is_unreadable(body: Any) -> None:
    with patch.object(provider_mod.requests, "post") as post:
        post.return_value = _response(200, body)
        with pytest.raises(AIInvocationError, match="unreadable reply") as info:
            invoke(_http(), VOICE_UPDATE, "q")
    assert info.value.stdout == json.dumps(body)


def test_http_non_json_reply_is_unreadable() -> None:
    resp = _response(200, None)
    resp.json.side_effect = ValueError("not json")
    resp.text = "<html>proxy page</html>"
    with patch.object(provider_mod.requests, "post") as post:
        post.return_value = resp
        with pytest.raises(AIInvocationError, match="unreadable reply"):
            invoke(_http(), VOICE_UPDATE, "q")


def test_http_content_parts_are_joined() -> None:
    parts = [
        {"type": "text", "text": "hello "},
        {"type": "image_url", "image_url": {}},
        {"type": "refusal", "text": "not this"},
        {"type": "text", "text": "world"},
    ]
    body = {"choices": [{"message": {"content": parts}}]}
    with patch.object(provider_mod.requests, "post") as post:
        post.return_value = _response(200, body)
        assert invoke(_http(), VOICE_UPDATE, "q") == "hello world"


# --- list_models -----------------------------------------------------------


def test_list_models_returns_the_ids() -> None:
    body = {"data": [{"id": "qwen3-coder"}, {"id": "llama"}]}
    with patch.object(provider_mod.requests, "get") as get:
        get.return_value = _response(200, body)
        assert list_models(_http(), timeout=5) == ["qwen3-coder", "llama"]


@pytest.mark.parametrize("body", [["not", "a", "dict"], {"models": []}, {"data": 3}])
def test_list_models_unreadable_body_raises(body: Any) -> None:
    with patch.object(provider_mod.requests, "get") as get:
        get.return_value = _response(200, body)
        with pytest.raises(AIInvocationError, match="unreadable model list"):
            list_models(_http(), timeout=5)


def test_list_models_non_json_body_raises() -> None:
    resp = _response(200, None)
    resp.json.side_effect = ValueError("not json")
    with patch.object(provider_mod.requests, "get") as get:
        get.return_value = resp
        with pytest.raises(AIInvocationError, match="unreadable model list"):
            list_models(_http(), timeout=5)


# --- available -------------------------------------------------------------


def test_available_checks_the_program_on_path(fake_tool: Path) -> None:
    assert available(_provider(text=[str(fake_tool)]))
    assert not available(_provider(text=["no-such-program-dd-test"]))


def test_http_provider_counts_as_available() -> None:
    assert available(_http())


def test_program_is_none_only_for_http() -> None:
    assert program(_http().settings) is None
    assert program(_provider(text=["mytool", ["--model", "{model}"]]).settings) == (
        "mytool"
    )
