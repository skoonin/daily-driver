"""Tests for the `voice-update` subcommand.

Covers: file collection from paths/directories, --append (default),
--replace (creates .bak), --dry-run, --no-clipboard, claude headless
invocation, and error paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_workspace(tmp_path: Path) -> Path:
    from daily_driver.core.workspace import Workspace

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    Workspace.init(ws)
    return ws


def _make_sample(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# core/voice.py unit tests
# ---------------------------------------------------------------------------


class TestCollectFiles:
    """collect_source_files resolves paths and recurses directories."""

    def setup_method(self):
        from daily_driver.core.voice import collect_source_files

        self.collect = collect_source_files

    def test_single_md_file(self, tmp_path: Path) -> None:
        f = _make_sample(tmp_path, "sample.md", "hello")
        result = self.collect([f])
        assert result == [f]

    def test_single_txt_file(self, tmp_path: Path) -> None:
        f = _make_sample(tmp_path, "sample.txt", "hello")
        result = self.collect([f])
        assert result == [f]

    def test_directory_recurses_md_and_txt(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        md = _make_sample(sub, "a.md", "a")
        txt = _make_sample(sub, "b.txt", "b")
        _make_sample(sub, "c.csv", "c")  # should be excluded
        result = self.collect([sub])
        assert set(result) == {md, txt}

    def test_nested_directory_recurses(self, tmp_path: Path) -> None:
        d = tmp_path / "d1" / "d2"
        d.mkdir(parents=True)
        md = _make_sample(d, "deep.md", "deep")
        result = self.collect([tmp_path / "d1"])
        assert md in result

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        from daily_driver.core.voice import VoiceUpdateError

        with pytest.raises(VoiceUpdateError, match="not found"):
            self.collect([tmp_path / "nonexistent.md"])

    def test_empty_directory_returns_empty(self, tmp_path: Path) -> None:
        d = tmp_path / "empty"
        d.mkdir()
        result = self.collect([d])
        assert result == []

    def test_deduplicates_same_path(self, tmp_path: Path) -> None:
        f = _make_sample(tmp_path, "sample.md", "text")
        result = self.collect([f, f])
        assert len(result) == 1


class TestBuildPrompt:
    """build_prompt assembles the text bundle passed to claude."""

    def setup_method(self):
        from daily_driver.core.voice import build_prompt

        self.build_prompt = build_prompt

    def test_includes_file_content(self, tmp_path: Path) -> None:
        f = _make_sample(tmp_path, "letter.md", "Unique marker text 9182")
        prompt = self.build_prompt(
            [f], current_profile="existing profile", mode="append"
        )
        assert "Unique marker text 9182" in prompt

    def test_includes_current_profile(self, tmp_path: Path) -> None:
        f = _make_sample(tmp_path, "x.md", "x")
        prompt = self.build_prompt(
            [f], current_profile="MY PROFILE CONTENT", mode="append"
        )
        assert "MY PROFILE CONTENT" in prompt

    def test_mode_append_requests_json_observations(self, tmp_path: Path) -> None:
        f = _make_sample(tmp_path, "x.md", "x")
        prompt = self.build_prompt([f], current_profile="", mode="append")
        # Append mode asks for a JSON array of {section, bullet} observations,
        # not a full rewritten document.
        assert "JSON array" in prompt
        assert "section" in prompt and "bullet" in prompt

    def test_mode_replace_returns_full_document(self, tmp_path: Path) -> None:
        f = _make_sample(tmp_path, "x.md", "x")
        prompt = self.build_prompt([f], current_profile="", mode="replace")
        assert "complete updated voice-profile.md content" in prompt

    def test_multiple_files_all_included(self, tmp_path: Path) -> None:
        f1 = _make_sample(tmp_path, "a.md", "CONTENT_A_XYZ")
        f2 = _make_sample(tmp_path, "b.md", "CONTENT_B_ABC")
        prompt = self.build_prompt([f1, f2], current_profile="", mode="append")
        assert "CONTENT_A_XYZ" in prompt
        assert "CONTENT_B_ABC" in prompt


class TestApplyUpdate:
    """apply_update writes the new profile, handles backup for replace mode."""

    def setup_method(self):
        from daily_driver.core.voice import apply_update

        self.apply_update = apply_update

    def test_append_writes_profile(self, tmp_path: Path) -> None:
        profile_path = tmp_path / "voice-profile.md"
        profile_path.write_text("# Old profile\n", encoding="utf-8")
        self.apply_update(profile_path, new_content="# New content\n", mode="append")
        assert profile_path.read_text(encoding="utf-8") == "# New content\n"

    def test_replace_creates_backup(self, tmp_path: Path) -> None:
        profile_path = tmp_path / "voice-profile.md"
        profile_path.write_text("# Original\n", encoding="utf-8")
        self.apply_update(profile_path, new_content="# Replaced\n", mode="replace")
        bak = tmp_path / "voice-profile.md.bak"
        assert bak.exists()
        assert bak.read_text(encoding="utf-8") == "# Original\n"
        assert profile_path.read_text(encoding="utf-8") == "# Replaced\n"

    def test_replace_no_existing_file_no_backup(self, tmp_path: Path) -> None:
        profile_path = tmp_path / "voice-profile.md"
        self.apply_update(profile_path, new_content="# New\n", mode="replace")
        assert profile_path.read_text(encoding="utf-8") == "# New\n"
        assert not (tmp_path / "voice-profile.md.bak").exists()

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        profile_path = tmp_path / "voice-profile.md"
        profile_path.write_text("# Original\n", encoding="utf-8")
        self.apply_update(
            profile_path, new_content="# Changed\n", mode="append", dry_run=True
        )
        assert profile_path.read_text(encoding="utf-8") == "# Original\n"

    def test_empty_content_preserves_original_and_raises(self, tmp_path: Path) -> None:
        from daily_driver.core.voice import VoiceUpdateError

        profile_path = tmp_path / "voice-profile.md"
        profile_path.write_text("# Original\n", encoding="utf-8")
        for empty in ("", "\n", "   \n\n  ", "\t"):
            with pytest.raises(VoiceUpdateError, match="empty"):
                self.apply_update(profile_path, new_content=empty, mode="replace")
            # Profile must survive verbatim.
            assert profile_path.read_text(encoding="utf-8") == "# Original\n"

    def test_atomic_write_failure_preserves_original(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        profile_path = tmp_path / "voice-profile.md"
        profile_path.write_text("# Original\n", encoding="utf-8")

        real_replace = os.replace

        def boom(src, dst):  # type: ignore[no-untyped-def]
            raise OSError("simulated mid-write crash")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError, match="simulated"):
            self.apply_update(profile_path, new_content="# Replaced\n", mode="replace")
        # Profile content must be untouched.
        assert profile_path.read_text(encoding="utf-8") == "# Original\n"
        monkeypatch.setattr(os, "replace", real_replace)


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


def test_voice_update_registered_in_cli() -> None:
    from daily_driver.cli.cli import _COMMANDS

    names = [name for name, _ in _COMMANDS]
    assert "voice-update" in names


def test_voice_update_help_exits_0(tmp_path: Path) -> None:
    from daily_driver.cli.cli import app

    with pytest.raises(SystemExit) as exc_info:
        app(["voice-update", "--help"])
    assert exc_info.value.code == 0


def test_voice_update_no_from_exits_2(tmp_path: Path) -> None:
    """--from is required; missing it should exit 2."""
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    with pytest.raises(SystemExit) as exc_info:
        app(["--workspace", str(ws), "voice-update"])
    assert exc_info.value.code == 2


def test_voice_update_append_calls_claude_headless(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli, clipboard

    ws = _init_workspace(tmp_path)
    sample = _make_sample(tmp_path, "letter.md", "Sample writing text")
    # voice-profile.md in output_dir
    profile_path = ws / "voice-profile.md"
    profile_path.write_text("## Tone\n\n- Warm.\n", encoding="utf-8")

    monkeypatch.setattr(claude_cli, "available", lambda: True)
    # Append mode: the model returns JSON observations, merged into the profile.
    monkeypatch.setattr(
        claude_cli,
        "invoke",
        lambda **kw: '[{"section": "Tone", "bullet": "Direct and concise."}]',
    )
    monkeypatch.setattr(clipboard, "available", lambda: False)

    rc = app(["--workspace", str(ws), "voice-update", "--from", str(sample)])

    assert rc == 0
    updated = profile_path.read_text(encoding="utf-8")
    # Existing content preserved; the new bullet merged under the Tone section.
    assert "- Warm." in updated
    assert "- Direct and concise." in updated


def test_voice_update_dry_run_does_not_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli, clipboard

    ws = _init_workspace(tmp_path)
    sample = _make_sample(tmp_path, "letter.md", "Sample text")
    profile_path = ws / "voice-profile.md"
    profile_path.write_text("# Preserved\n", encoding="utf-8")

    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "invoke", lambda **kw: "# Would be new\n")
    monkeypatch.setattr(clipboard, "available", lambda: False)

    rc = app(
        ["--workspace", str(ws), "voice-update", "--from", str(sample), "--dry-run"]
    )

    assert rc == 0
    assert profile_path.read_text(encoding="utf-8") == "# Preserved\n"
    err = capsys.readouterr().err
    assert "dry-run" in err.lower() or "dry run" in err.lower()


def test_voice_update_dry_run_does_not_invoke_claude(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--dry-run must short-circuit before claude (review §9)."""
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli, clipboard

    ws = _init_workspace(tmp_path)
    sample = _make_sample(tmp_path, "letter.md", "Sample text")

    invoked = {"count": 0}

    def _boom(**kw):
        invoked["count"] += 1
        raise AssertionError("claude must not be invoked under --dry-run")

    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "invoke", _boom)
    monkeypatch.setattr(clipboard, "available", lambda: False)

    rc = app(
        ["--workspace", str(ws), "voice-update", "--from", str(sample), "--dry-run"]
    )
    assert rc == 0
    assert invoked["count"] == 0


def test_voice_update_replace_creates_bak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli, clipboard

    ws = _init_workspace(tmp_path)
    sample = _make_sample(tmp_path, "letter.md", "Sample text")
    profile_path = ws / "voice-profile.md"
    profile_path.write_text("# Original\n", encoding="utf-8")

    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "invoke", lambda **kw: "# Replaced\n")
    monkeypatch.setattr(clipboard, "available", lambda: False)

    rc = app(
        ["--workspace", str(ws), "voice-update", "--from", str(sample), "--replace"]
    )

    assert rc == 0
    bak = ws / "voice-profile.md.bak"
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == "# Original\n"
    assert profile_path.read_text(encoding="utf-8") == "# Replaced\n"


def test_voice_update_no_clipboard_suppresses_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli, clipboard

    ws = _init_workspace(tmp_path)
    sample = _make_sample(tmp_path, "x.md", "text")
    (ws / "voice-profile.md").write_text("## Tone\n\n- p\n", encoding="utf-8")

    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(
        claude_cli, "invoke", lambda **kw: '[{"section": "Tone", "bullet": "new"}]'
    )
    monkeypatch.setattr(clipboard, "available", lambda: True)

    copies: list[str] = []
    monkeypatch.setattr(clipboard, "copy", lambda t: copies.append(t))

    rc = app(
        [
            "--workspace",
            str(ws),
            "voice-update",
            "--from",
            str(sample),
            "--no-clipboard",
        ]
    )

    assert rc == 0
    assert copies == []


def test_voice_update_copies_to_clipboard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli, clipboard

    ws = _init_workspace(tmp_path)
    sample = _make_sample(tmp_path, "x.md", "text")
    (ws / "voice-profile.md").write_text("## Tone\n\n- old\n", encoding="utf-8")

    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(
        claude_cli, "invoke", lambda **kw: '[{"section": "Tone", "bullet": "fresh"}]'
    )
    monkeypatch.setattr(clipboard, "available", lambda: True)

    copies: list[str] = []
    monkeypatch.setattr(clipboard, "copy", lambda t: copies.append(t))

    rc = app(["--workspace", str(ws), "voice-update", "--from", str(sample)])

    assert rc == 0
    # The clipboard receives the full merged profile, not the raw model output.
    assert len(copies) == 1
    assert "- old" in copies[0] and "- fresh" in copies[0]


def test_voice_update_directory_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli, clipboard

    ws = _init_workspace(tmp_path)
    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    _make_sample(samples_dir, "a.md", "Writing A")
    _make_sample(samples_dir, "b.txt", "Writing B")
    (ws / "voice-profile.md").write_text("# p\n", encoding="utf-8")

    captured_prompts: list[str] = []

    def fake_invoke(**kw):
        captured_prompts.append(kw.get("prompt", ""))
        return '[{"section": "Tone", "bullet": "updated"}]'

    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "invoke", fake_invoke)
    monkeypatch.setattr(clipboard, "available", lambda: False)

    rc = app(["--workspace", str(ws), "voice-update", "--from", str(samples_dir)])

    assert rc == 0
    assert len(captured_prompts) == 1
    # Both file contents should appear in the prompt
    assert "Writing A" in captured_prompts[0]
    assert "Writing B" in captured_prompts[0]


def test_voice_update_claude_not_found_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli

    ws = _init_workspace(tmp_path)
    sample = _make_sample(tmp_path, "x.md", "text")

    monkeypatch.setattr(claude_cli, "available", lambda: False)

    rc = app(["--workspace", str(ws), "voice-update", "--from", str(sample)])

    captured = capsys.readouterr()
    assert rc == 1
    assert "claude" in captured.err.lower()


def test_voice_update_timeout_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli
    from daily_driver.integrations.claude_cli import ClaudeTimeoutError

    ws = _init_workspace(tmp_path)
    sample = _make_sample(tmp_path, "x.md", "text")
    (ws / "voice-profile.md").write_text("# p\n", encoding="utf-8")

    monkeypatch.setattr(claude_cli, "available", lambda: True)

    def raise_timeout(**kw):
        raise ClaudeTimeoutError(60, ["claude"])

    monkeypatch.setattr(claude_cli, "invoke", raise_timeout)

    rc = app(
        [
            "--workspace",
            str(ws),
            "voice-update",
            "--from",
            str(sample),
            "--timeout",
            "60",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert "timed out" in captured.err


def test_voice_update_missing_source_file_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli

    ws = _init_workspace(tmp_path)
    monkeypatch.setattr(claude_cli, "available", lambda: True)

    rc = app(
        [
            "--workspace",
            str(ws),
            "voice-update",
            "--from",
            str(tmp_path / "nonexistent.md"),
        ]
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in captured.err.lower() or "error" in captured.err.lower()


def test_voice_update_append_and_replace_mutually_exclusive(
    tmp_path: Path,
) -> None:
    from daily_driver.cli.cli import app

    ws = _init_workspace(tmp_path)
    sample = tmp_path / "x.md"
    sample.write_text("text", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        app(
            [
                "--workspace",
                str(ws),
                "voice-update",
                "--from",
                str(sample),
                "--append",
                "--replace",
            ]
        )
    assert exc_info.value.code == 2


def test_voice_update_missing_voice_profile_creates_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If voice-profile.md doesn't exist yet, voice-update creates it."""
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli, clipboard

    ws = _init_workspace(tmp_path)
    sample = _make_sample(tmp_path, "letter.md", "Sample")
    profile_path = ws / "voice-profile.md"
    # Ensure it doesn't exist
    assert not profile_path.exists()

    monkeypatch.setattr(claude_cli, "available", lambda: True)
    # Append to a non-existent profile: observations seed new sections.
    monkeypatch.setattr(
        claude_cli,
        "invoke",
        lambda **kw: '[{"section": "Tone", "bullet": "Warm and direct."}]',
    )
    monkeypatch.setattr(clipboard, "available", lambda: False)

    rc = app(["--workspace", str(ws), "voice-update", "--from", str(sample)])

    assert rc == 0
    assert profile_path.exists()
    created = profile_path.read_text(encoding="utf-8")
    assert "## Tone" in created and "- Warm and direct." in created


def test_voice_update_append_unparseable_output_leaves_profile_intact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """In append mode, model output that is not a JSON observations array is
    rejected (rc 1) and the existing profile is left untouched."""
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli, clipboard

    ws = _init_workspace(tmp_path)
    sample = _make_sample(tmp_path, "x.md", "text")
    profile_path = ws / "voice-profile.md"
    original = "## Tone\n\n- Warm.\n"
    profile_path.write_text(original, encoding="utf-8")

    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(
        claude_cli, "invoke", lambda **kw: "Sorry, I could not produce observations."
    )
    monkeypatch.setattr(clipboard, "available", lambda: False)

    rc = app(["--workspace", str(ws), "voice-update", "--from", str(sample)])

    captured = capsys.readouterr()
    assert rc == 1
    assert "parse" in captured.err.lower()
    assert profile_path.read_text(encoding="utf-8") == original


def test_voice_update_append_no_new_observations_is_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An empty observations array reports a no-op and leaves the profile and
    its .bak untouched (no needless rewrite)."""
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli, clipboard

    ws = _init_workspace(tmp_path)
    sample = _make_sample(tmp_path, "x.md", "text")
    profile_path = ws / "voice-profile.md"
    original = "## Tone\n\n- Warm.\n"
    profile_path.write_text(original, encoding="utf-8")

    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "invoke", lambda **kw: "[]")
    monkeypatch.setattr(clipboard, "available", lambda: False)

    rc = app(["--workspace", str(ws), "voice-update", "--from", str(sample)])

    assert rc == 0
    assert profile_path.read_text(encoding="utf-8") == original
    assert not (ws / "voice-profile.md.bak").exists()
    assert "no new observations" in capsys.readouterr().err.lower()


def test_voice_update_ollama_route_honors_config_timeout_not_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: the ollama route is bounded by ai.ollama.timeout, not --timeout.

    Regression for M2 — voice-update used to pass --timeout to invoke_for,
    shadowing ai.ollama.timeout end-to-end. With an explicit --timeout 60, the
    ollama client must still receive the configured 137s.
    """
    from daily_driver.cli.cli import app
    from daily_driver.integrations import clipboard, ollama_client

    ws = _init_workspace(tmp_path)
    cfg_path = ws / ".dd-config.yaml"
    cfg_path.write_text(
        cfg_path.read_text()
        + "\nai:\n  voice_update:\n    provider: ollama\n    model: phi4\n"
        + "  ollama:\n    timeout: 137\n",
        encoding="utf-8",
    )
    sample = _make_sample(tmp_path, "letter.md", "Sample writing text")
    (ws / "voice-profile.md").write_text("## Tone\n\n- Warm.\n", encoding="utf-8")

    seen: dict[str, object] = {}

    def fake_generate(prompt, *, model, endpoint, timeout, system=None):
        seen.update(model=model, timeout=timeout)
        return '[{"section": "Tone", "bullet": "Direct and concise."}]'

    monkeypatch.setattr(ollama_client, "generate", fake_generate)
    monkeypatch.setattr(clipboard, "available", lambda: False)

    rc = app(
        [
            "--workspace",
            str(ws),
            "voice-update",
            "--from",
            str(sample),
            "--timeout",
            "60",
        ]
    )

    assert rc == 0
    assert seen["timeout"] == 137
    assert seen["model"] == "phi4"


def test_voice_update_rereads_profile_under_lock_after_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent write during the model call must not be clobbered (L1).

    The merge base is re-read under the lock after the model returns, so a
    section another writer added while the model was running survives the merge.
    """
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli, clipboard

    ws = _init_workspace(tmp_path)
    sample = _make_sample(tmp_path, "letter.md", "Sample writing text")
    profile_path = ws / "voice-profile.md"
    profile_path.write_text("## Tone\n\n- Warm.\n", encoding="utf-8")

    def fake_invoke(**kw):
        # Simulate a concurrent writer landing during the (slow) model call.
        profile_path.write_text(
            "## Tone\n\n- Warm.\n\n## Energy\n\n- Lively.\n", encoding="utf-8"
        )
        return '[{"section": "Tone", "bullet": "Direct and concise."}]'

    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "invoke", fake_invoke)
    monkeypatch.setattr(clipboard, "available", lambda: False)

    rc = app(["--workspace", str(ws), "voice-update", "--from", str(sample)])

    assert rc == 0
    updated = profile_path.read_text(encoding="utf-8")
    # Concurrent section survives (re-read under lock); new bullet merged in.
    assert "- Lively." in updated
    assert "- Direct and concise." in updated
    assert "- Warm." in updated


def test_voice_update_lock_lives_in_ephemeral_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lock sentinel is under ephemeral_dir, not the user-visible output (L2)."""
    from daily_driver.cli.cli import app
    from daily_driver.integrations import claude_cli, clipboard

    ws = _init_workspace(tmp_path)
    sample = _make_sample(tmp_path, "x.md", "text")
    (ws / "voice-profile.md").write_text("## Tone\n\n- old\n", encoding="utf-8")

    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(
        claude_cli, "invoke", lambda **kw: '[{"section": "Tone", "bullet": "new"}]'
    )
    monkeypatch.setattr(clipboard, "available", lambda: False)

    rc = app(["--workspace", str(ws), "voice-update", "--from", str(sample)])

    assert rc == 0
    assert (ws / ".daily-driver" / "state" / "voice-profile.lock").exists()
    # The old output-dir sentinel must not be created.
    assert not (ws / "voice-profile.md.lock").exists()


def test_voice_update_write_failure_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A backup/lock/write OSError surfaces as a friendly message, not a traceback."""
    from daily_driver.cli.cli import app
    from daily_driver.cli.commands import voice_update as vu
    from daily_driver.integrations import claude_cli, clipboard

    ws = _init_workspace(tmp_path)
    sample = _make_sample(tmp_path, "x.md", "text")
    (ws / "voice-profile.md").write_text("# Original profile\n", encoding="utf-8")

    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(
        claude_cli, "invoke", lambda **kw: '[{"section": "Tone", "bullet": "x"}]'
    )
    monkeypatch.setattr(clipboard, "available", lambda: False)

    def boom(*a: object, **kw: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(vu, "apply_update", boom)

    rc = app(["--workspace", str(ws), "voice-update", "--from", str(sample)])

    captured = capsys.readouterr()
    assert rc == 1
    assert "original left unchanged" in captured.err.lower()
