"""Core logic for voice-update: file collection, prompt assembly, profile write."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

_ACCEPTED_SUFFIXES = {".md", ".txt"}
_MAX_FILE_BYTES = 500_000
_BINARY_SNIFF_BYTES = 512

# A markdown heading line (1-6 leading '#'), capturing the heading text.
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*$")


class _Segment(TypedDict):
    """A profile region: a heading line (None for the leading preamble), its
    normalized heading text for matching, and the body lines beneath it."""

    heading: str | None
    norm: str | None
    body: list[str]


class VoiceUpdateError(RuntimeError):
    """Raised for user-facing errors in voice-update processing."""


@dataclass(frozen=True)
class Observation:
    """A single new voice observation to merge into the profile.

    `section` names the target heading (matched case-insensitively against the
    existing profile, or created if absent); `bullet` is the one-line text.
    """

    section: str
    bullet: str


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
    """Assemble the headless model prompt for updating the voice profile.

    Append mode asks for a JSON array of new, non-redundant observations (so the
    existing document is never regenerated — it is merged in deterministically by
    `merge_observations`). Replace mode asks for a complete rewritten profile.
    """
    samples_block = ""
    for f in source_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        samples_block += f"\n### {f.name}\n\n{content}\n"

    profile_block = (
        current_profile
        if current_profile.strip()
        else "(empty — there is no existing profile yet)"
    )

    if mode == "append":
        return f"""\
You are extending a writing voice profile for a professional communications system.

## Task

Analyze the writing samples below and identify NEW, non-redundant voice
observations not already captured in the current profile. Do NOT restate or
rewrite existing observations.

For each new observation, choose a target `section`: reuse the exact heading
text of an existing section when the observation fits one, otherwise name a
concise new section. Write `bullet` as a single, self-contained sentence.

## Current Voice Profile

{profile_block}

## Writing Samples

{samples_block}

## Output

Return ONLY a JSON array of objects, each with "section" and "bullet" string
keys — no preamble, no code fences, no explanation. Return an empty array `[]`
if the samples reveal no new observations. Example:

[{{"section": "Tone", "bullet": "Opens with one line of context, no greeting."}}]
"""

    return f"""\
You are rewriting a writing voice profile for a professional communications system.

## Task

Synthesize the existing profile with new observations from the writing samples
into a complete, updated voice-profile.md.

## Current Voice Profile

{profile_block}

## Writing Samples

{samples_block}

## Output

Return ONLY the complete updated voice-profile.md content — no preamble, no
explanation. The output will be written directly to voice-profile.md.
"""


def parse_observations(raw: str) -> list[Observation]:
    """Parse a model response into observations for append-mode merging.

    Tolerates a surrounding ```json fence or stray prose around the array. An
    empty array is a valid "no new observations" result (returns []); output
    that is not a recoverable JSON array raises VoiceUpdateError rather than
    silently yielding nothing (which would mask a model/contract failure).
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match is None:
            raise VoiceUpdateError(
                "could not parse observations from model output (expected a "
                "JSON array of {section, bullet} objects)"
            )
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise VoiceUpdateError(
                f"could not parse observations JSON from model output: {exc}"
            ) from exc

    if not isinstance(data, list):
        raise VoiceUpdateError("expected a JSON array of observations")

    observations: list[Observation] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        section = str(item.get("section", "")).strip()
        bullet = str(item.get("bullet", "")).strip()
        if section and bullet:
            observations.append(Observation(section=section, bullet=bullet))

    # A genuinely empty array is the model's "nothing new" signal. But a
    # NON-empty array that yields zero usable items means the model returned
    # observations in the wrong shape (e.g. keys other than section/bullet) —
    # a contract break that must fail loud, not masquerade as "no new
    # observations" and silently leave the profile unchanged.
    if data and not observations:
        raise VoiceUpdateError(
            "model returned observation items but none had usable 'section' "
            "and 'bullet' fields (contract violation)"
        )
    return observations


def _normalize_heading(text: str) -> str:
    return text.strip().lower().rstrip(":").strip()


def _bullet_line(bullet: str) -> str:
    # Accept bullets with or without a leading marker; emit a single "- " form.
    # Strip only one leading "- "/"* " marker so inline emphasis like *word* in
    # the text itself is preserved.
    return "- " + re.sub(r"^[-*]\s+", "", bullet.strip())


def merge_observations(current_profile: str, observations: list[Observation]) -> str:
    """Merge observations into the profile by section, preserving existing text.

    Each observation's `section` is matched case-insensitively against existing
    headings; its bullet is appended to that section's body (skipping exact
    duplicates). Observations whose section has no existing heading are grouped
    into new `##` sections appended at the end. Existing content is never
    rewritten — only added to.
    """
    if not observations:
        return current_profile

    # Segment the profile by heading. The first segment (heading=None) holds any
    # title / preamble before the first heading.
    segments: list[_Segment] = [{"heading": None, "norm": None, "body": []}]
    for line in current_profile.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            segments.append(
                {
                    "heading": line,
                    "norm": _normalize_heading(match.group(1)),
                    "body": [],
                }
            )
        else:
            segments[-1]["body"].append(line)

    # New sections are keyed by normalized name (matching the case-insensitive
    # section matching), so "Tone" and "tone" in one batch collapse into one.
    new_sections: dict[str, list[str]] = {}
    new_display: dict[str, str] = {}
    new_order: list[str] = []
    for obs in observations:
        line = _bullet_line(obs.bullet)
        target = _normalize_heading(obs.section)
        seg = next((s for s in segments if s["norm"] == target), None)
        if seg is None:
            if target not in new_sections:
                new_sections[target] = []
                new_display[target] = obs.section.strip()
                new_order.append(target)
            if line not in new_sections[target]:
                new_sections[target].append(line)
            continue
        body = seg["body"]
        if line in body:
            continue
        # Insert before the section's trailing blank lines so spacing is kept.
        end = len(body)
        while end > 0 and body[end - 1].strip() == "":
            end -= 1
        body.insert(end, line)

    out: list[str] = []
    for seg in segments:
        heading = seg["heading"]
        if heading is not None:
            out.append(heading)
        out.extend(seg["body"])
    for key in new_order:
        if out and out[-1].strip() != "":
            out.append("")
        out.append(f"## {new_display[key]}")
        out.append("")
        out.extend(new_sections[key])

    return "\n".join(out)


def apply_update(
    profile_path: Path,
    *,
    new_content: str,
    mode: str,
    current_profile: str = "",
    dry_run: bool = False,
) -> None:
    """Write new_content to profile_path, backing up any existing profile.

    Refuses empty/whitespace-only content (raises VoiceUpdateError) so a
    failed `claude` call cannot blank the profile. In append mode it also
    refuses content materially smaller than `current_profile`: at the file
    layer append and replace both fully overwrite (os.replace), so a short
    meta-summary passed as the full document would silently clobber the profile
    — the shrink check rejects that. Since the redesign, the CLI's append path
    merges new observations onto the verbatim profile (always growing), so this
    check is a defensive backstop for direct callers rather than the live path.
    The write is atomic:
    content lands in a same-directory tempfile and is moved into place via
    os.replace, so a mid-write crash leaves the original intact. A `.bak` is
    written whenever a profile already exists, regardless of mode, since the
    overwrite is full either way.

    In dry_run mode the file is not modified.
    """
    if dry_run:
        return

    if not new_content or not new_content.strip():
        raise VoiceUpdateError(
            "refusing to write empty voice profile; "
            "content must not be empty or whitespace-only"
        )

    if (
        mode == "append"
        and current_profile
        and len(new_content) < 0.8 * len(current_profile)
    ):
        raise VoiceUpdateError(
            f"append output is smaller than the current profile "
            f"({len(new_content)} < {len(current_profile)} chars); refusing — "
            "the model likely returned a summary instead of the full profile"
        )

    profile_path.parent.mkdir(parents=True, exist_ok=True)

    if profile_path.exists():
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
