"""Init contract: the set of artifacts that every workspace must contain after init.

This module codifies what ``daily-driver init`` promises to produce. Any gap
between what the package ships and what lands on disk (e.g. the broken-wheel
gap that slipped into v0.1.0) becomes a detectable ERROR rather than a silent
failure.

The public surface is intentionally small: ``check()`` returns a list of
``ContractViolation`` entries, empty iff the contract is satisfied. The
individual check logic is inline — there's one caller (doctor.py) and the
per-kind dispatch table this replaced was harder to read than the linear
checks it mediated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# Minimum file counts the materialize step must produce. Exposed as module-level
# constants so tests and materialize can assert against them without depending
# on the check() implementation.
MIN_COMMANDS = 5
MIN_AGENTS = 1


@dataclass(frozen=True)
class ContractViolation:
    rel_path: str
    detail: str


def check(workspace_root: Path) -> list[ContractViolation]:
    """Verify the workspace contains every artifact ``daily-driver init`` promises.

    Returns an empty list iff the full contract is satisfied.
    """
    v: list[ContractViolation] = []

    # Workspace config — must parse as the pydantic Config model.
    cfg = workspace_root / ".dd-config.yaml"
    if not cfg.is_file():
        v.append(ContractViolation(".dd-config.yaml", "missing file"))
    else:
        try:
            from daily_driver.core.config import load

            load(cfg)
        except Exception as exc:  # noqa: BLE001
            v.append(ContractViolation(".dd-config.yaml", f"Config parse error: {exc}"))

    # State dir + version stamp + manifest.
    state = workspace_root / ".daily-driver"
    if not state.is_dir():
        v.append(ContractViolation(".daily-driver", "missing directory"))
    else:
        if not (state / "version").is_file():
            v.append(ContractViolation(".daily-driver/version", "missing file"))
        manifest = state / "manifest.json"
        if not manifest.is_file():
            v.append(ContractViolation(".daily-driver/manifest.json", "missing file"))
        else:
            try:
                json.loads(manifest.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                v.append(
                    ContractViolation(
                        ".daily-driver/manifest.json", f"JSON parse error: {exc}"
                    )
                )

    # Claude settings.
    settings = workspace_root / ".claude" / "settings.local.json"
    if not settings.is_file():
        v.append(ContractViolation(".claude/settings.local.json", "missing file"))
    else:
        try:
            json.loads(settings.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            v.append(
                ContractViolation(
                    ".claude/settings.local.json", f"JSON parse error: {exc}"
                )
            )

    # Static files seeded by init.
    for rel in ("context.md", "voice-profile.md", ".gitignore"):
        if not (workspace_root / rel).is_file():
            v.append(ContractViolation(rel, "missing file"))

    # Package-managed Claude trees — count_gte on bundled .md files.
    _count_check(
        v,
        workspace_root / ".claude" / "commands" / "daily-driver",
        ".claude/commands/daily-driver",
        "*.md",
        MIN_COMMANDS,
    )
    _count_check(
        v,
        workspace_root / ".claude" / "agents" / "daily-driver",
        ".claude/agents/daily-driver",
        "*.md",
        MIN_AGENTS,
    )

    # .claude/commands/user and .claude/agents/user are user territory:
    # init seeds the dirs but materialize never writes there, so a missing
    # dir cannot be repaired by `doctor --fix` and reporting it as ERROR
    # produces a stuck state. Per CLAUDE.md these are intentionally not
    # package-managed — they are excluded from the contract entirely.

    return v


def _count_check(
    out: list[ContractViolation],
    path: Path,
    rel: str,
    glob: str,
    minimum: int,
) -> None:
    if not path.is_dir():
        out.append(ContractViolation(rel, "missing directory"))
        return
    found = list(path.glob(glob))
    if len(found) < minimum:
        out.append(
            ContractViolation(
                rel,
                f"found {len(found)} {glob} file(s), need >= {minimum}",
            )
        )
