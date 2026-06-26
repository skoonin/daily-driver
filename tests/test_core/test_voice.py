"""Unit tests for daily_driver.core.voice.

Covers file collection (suffix/size/binary filters, dedup, recursion), prompt
assembly (mode instruction + sample embedding), and the atomic profile write
(empty-content refusal, .bak for replace, dry-run no-op).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from daily_driver.core import voice
from daily_driver.core.voice import VoiceUpdateError

# ---------------------------------------------------------------------------
# collect_source_files
# ---------------------------------------------------------------------------


def test_collect_accepts_md_and_txt(tmp_path: Path) -> None:
    md = tmp_path / "a.md"
    txt = tmp_path / "b.txt"
    md.write_text("hello", encoding="utf-8")
    txt.write_text("world", encoding="utf-8")

    result = voice.collect_source_files([md, txt])

    assert set(result) == {md, txt}


def test_collect_skips_unsupported_suffix(tmp_path: Path) -> None:
    doc = tmp_path / "a.docx"
    doc.write_text("nope", encoding="utf-8")

    assert voice.collect_source_files([doc]) == []


def test_collect_skips_oversized_file(tmp_path: Path) -> None:
    big = tmp_path / "big.md"
    big.write_bytes(b"x" * (voice._MAX_FILE_BYTES + 1))

    assert voice.collect_source_files([big]) == []


def test_collect_skips_binary_file(tmp_path: Path) -> None:
    binary = tmp_path / "blob.md"
    binary.write_bytes(b"text\x00more")

    assert voice.collect_source_files([binary]) == []


def test_collect_missing_explicit_path_raises(tmp_path: Path) -> None:
    missing = tmp_path / "gone.md"

    with pytest.raises(VoiceUpdateError, match="source not found"):
        voice.collect_source_files([missing])


def test_collect_recurses_directories(tmp_path: Path) -> None:
    nested = tmp_path / "sub"
    nested.mkdir()
    leaf = nested / "deep.md"
    leaf.write_text("deep", encoding="utf-8")

    result = voice.collect_source_files([tmp_path])

    assert leaf in result


def test_collect_dedups_overlapping_inputs(tmp_path: Path) -> None:
    f = tmp_path / "dup.md"
    f.write_text("once", encoding="utf-8")

    # The file is reachable both via the directory walk and the explicit path.
    result = voice.collect_source_files([tmp_path, f])

    assert result.count(f) == 1


# ---------------------------------------------------------------------------
# build_prompt
# ---------------------------------------------------------------------------


def test_build_prompt_embeds_sample_filename_and_content(tmp_path: Path) -> None:
    sample = tmp_path / "note.md"
    sample.write_text("the quick brown fox", encoding="utf-8")

    prompt = voice.build_prompt([sample], current_profile="old", mode="append")

    assert "### note.md" in prompt
    assert "the quick brown fox" in prompt


def test_build_prompt_append_mode_instruction() -> None:
    prompt = voice.build_prompt([], current_profile="old", mode="append")

    assert "Mode: append" in prompt
    assert "Append new observations" in prompt
    assert "Preserve all existing content" in prompt


def test_build_prompt_replace_mode_instruction() -> None:
    prompt = voice.build_prompt([], current_profile="old", mode="replace")

    assert "Mode: replace" in prompt
    assert "Replace the profile content" in prompt


def test_build_prompt_empty_profile_uses_placeholder() -> None:
    prompt = voice.build_prompt([], current_profile="   ", mode="append")

    assert "create a new profile from the samples" in prompt


# ---------------------------------------------------------------------------
# apply_update
# ---------------------------------------------------------------------------


def test_apply_update_writes_content(tmp_path: Path) -> None:
    profile = tmp_path / "voice-profile.md"

    voice.apply_update(profile, new_content="fresh", mode="replace")

    assert profile.read_text(encoding="utf-8") == "fresh"


def test_apply_update_refuses_empty_content(tmp_path: Path) -> None:
    profile = tmp_path / "voice-profile.md"

    with pytest.raises(VoiceUpdateError, match="empty"):
        voice.apply_update(profile, new_content="   ", mode="replace")

    assert not profile.exists()


def test_apply_update_replace_mode_creates_backup(tmp_path: Path) -> None:
    profile = tmp_path / "voice-profile.md"
    profile.write_text("original", encoding="utf-8")

    voice.apply_update(profile, new_content="updated", mode="replace")

    bak = profile.with_suffix(profile.suffix + ".bak")
    assert bak.read_text(encoding="utf-8") == "original"
    assert profile.read_text(encoding="utf-8") == "updated"


def test_apply_update_append_mode_creates_backup(tmp_path: Path) -> None:
    profile = tmp_path / "voice-profile.md"
    profile.write_text("original", encoding="utf-8")

    # Append writes a full file (os.replace) just like replace, so it must back
    # up the original too — the asymmetry was the data-loss root cause.
    voice.apply_update(
        profile, new_content="original plus new observations", mode="append"
    )

    bak = profile.with_suffix(profile.suffix + ".bak")
    assert bak.read_text(encoding="utf-8") == "original"
    assert profile.read_text(encoding="utf-8") == "original plus new observations"


def test_apply_update_append_rejects_shrunk_output(tmp_path: Path) -> None:
    profile = tmp_path / "voice-profile.md"
    original = "x" * 200
    profile.write_text(original, encoding="utf-8")

    # A model meta-summary ("Voice profile updated…") is far shorter than the
    # profile it claims to preserve; append mode must refuse it and leave the
    # original untouched (no write, no .bak).
    with pytest.raises(VoiceUpdateError, match="smaller"):
        voice.apply_update(
            profile,
            new_content="Voice profile updated; all existing content preserved.",
            mode="append",
            current_profile=original,
        )

    assert profile.read_text(encoding="utf-8") == original
    assert not profile.with_suffix(profile.suffix + ".bak").exists()


def test_apply_update_dry_run_leaves_file_unchanged(tmp_path: Path) -> None:
    profile = tmp_path / "voice-profile.md"
    profile.write_text("original", encoding="utf-8")

    voice.apply_update(profile, new_content="updated", mode="replace", dry_run=True)

    assert profile.read_text(encoding="utf-8") == "original"


def test_apply_update_creates_parent_dirs(tmp_path: Path) -> None:
    profile = tmp_path / "nested" / "deep" / "voice-profile.md"

    voice.apply_update(profile, new_content="fresh", mode="replace")

    assert profile.read_text(encoding="utf-8") == "fresh"
