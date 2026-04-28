"""Core logic for voice-update: file collection, prompt assembly, profile write."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_ACCEPTED_SUFFIXES = {".md", ".txt"}
_MAX_FILE_BYTES = 500_000
_BINARY_SNIFF_BYTES = 512


class VoiceUpdateError(RuntimeError):
    """Raised for user-facing errors in voice-update processing."""


def _is_binary(path: Path) -> bool:
    try:
        head = path.open("rb").read(_BINARY_SNIFF_BYTES)
    except OSError:
        return True
    return b"\x00" in head


def _acceptable(path: Path) -> bool:
    if path.suffix not in _ACCEPTED_SUFFIXES:
        return False
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return False
    except OSError:
        return False
    return not _is_binary(path)


def collect_source_files(paths: list[Path]) -> list[Path]:
    """Resolve a mixed list of files and directories to a deduplicated list of .md/.txt files.

    Raises VoiceUpdateError if any explicit file path does not exist. Files are
    skipped if they have an unsupported suffix, exceed 500KB, or sniff as binary
    (contain a NUL byte in the first 512 bytes). Directories are recursed.
    """
    seen: set[Path] = set()
    result: list[Path] = []

    for path in paths:
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and _acceptable(child):
                    resolved = child.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        result.append(child)
        elif path.exists():
            if not _acceptable(path):
                continue
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                result.append(path)
        else:
            raise VoiceUpdateError(f"source not found: {path}")

    return result


def build_prompt(
    source_files: list[Path],
    *,
    current_profile: str,
    mode: str,
) -> str:
    """Assemble the headless claude prompt for updating the voice profile.

    The prompt instructs claude to analyze the provided writing samples and
    return a complete updated voice-profile.md — either appending new
    observations or fully replacing the content based on mode.
    """
    samples_block = ""
    for f in source_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        samples_block += f"\n### {f.name}\n\n{content}\n"

    mode_instruction = (
        "Append new observations to the existing profile. "
        "Preserve all existing content exactly; only add new, non-redundant observations."
        if mode == "append"
        else "Replace the profile content with an updated version that synthesizes "
        "the existing profile with new observations from the samples."
    )

    prompt = f"""\
You are updating a writing voice profile for a professional communications system.

## Task

Analyze the writing samples below and update the voice profile accordingly.

Mode: {mode}
Instruction: {mode_instruction}

## Current Voice Profile

{current_profile if current_profile.strip() else "(empty — create a new profile from the samples)"}

## Writing Samples

{samples_block}

## Output

Return ONLY the complete updated voice-profile.md content — no preamble, no explanation.
The output will be written directly to voice-profile.md.
"""
    return prompt


def apply_update(
    profile_path: Path,
    *,
    new_content: str,
    mode: str,
    dry_run: bool = False,
) -> None:
    """Write new_content to profile_path, creating a .bak for replace mode.

    Refuses empty/whitespace-only content (raises VoiceUpdateError) so a
    failed `claude` call cannot blank the profile. The write is atomic:
    content lands in a same-directory tempfile and is moved into place via
    os.replace, so a mid-write crash leaves the original intact.

    In dry_run mode the file is not modified.
    """
    if dry_run:
        return

    if not new_content or not new_content.strip():
        raise VoiceUpdateError(
            "refusing to write empty voice profile; "
            "content must not be empty or whitespace-only"
        )

    profile_path.parent.mkdir(parents=True, exist_ok=True)

    if mode == "replace" and profile_path.exists():
        bak = profile_path.with_suffix(profile_path.suffix + ".bak")
        shutil.copy2(profile_path, bak)

    fd, tmp_name = tempfile.mkstemp(
        prefix=profile_path.name + ".",
        suffix=".tmp",
        dir=str(profile_path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, profile_path)
    except BaseException:
        # Ensure the tmp file does not linger on any failure path.
        tmp_path.unlink(missing_ok=True)
        raise
