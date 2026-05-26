"""SHA-256 manifest for package-managed files in the workspace .claude/ tree.

The manifest records the SHA-256 of each file at the time it was last written
by generate(). On the next generate run the recorded hash is compared
against the file on disk; a mismatch means the user edited the file and the
overwrite is suppressed (unless force=True).

Manifest file: {state_dir}/manifest.json
Schema:
  {
    "version": 1,
    "files": {
      "<relative-path-from-workspace-root>": "<hex-sha256>"
    }
  }
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

_MANIFEST_FILENAME = "manifest.json"
_SCHEMA_VERSION = 1


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def load(state_dir: Path) -> dict[str, str]:
    """Return the stored {rel_path: sha256} map, or {} if absent / corrupt."""
    manifest_path = state_dir / _MANIFEST_FILENAME
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if raw.get("version") != _SCHEMA_VERSION:
            return {}
        files = raw.get("files", {})
        if not isinstance(files, dict):
            return {}
        return {str(k): str(v) for k, v in files.items()}
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        return {}


def save(state_dir: Path, files: dict[str, str]) -> None:
    """Atomically write the manifest to {state_dir}/manifest.json."""
    state_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = state_dir / _MANIFEST_FILENAME
    tmp = manifest_path.with_suffix(".json.tmp")
    payload = {"version": _SCHEMA_VERSION, "files": files}
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, manifest_path)


def is_user_edited(workspace_root: Path, state_dir: Path, rel_path: str) -> bool:
    """Return True if the file at workspace_root/rel_path has been modified since last generate.

    A file is considered user-edited when:
    - it exists on disk, AND
    - its SHA-256 differs from the hash recorded in the manifest (or no record exists).
    A missing file is NOT considered user-edited (it will be written fresh).
    """
    target = workspace_root / rel_path
    if not target.exists():
        return False
    stored = load(state_dir)
    recorded = stored.get(rel_path)
    if recorded is None:
        # No record means we wrote it before the manifest feature existed — treat as ours.
        return False
    return _sha256(target) != recorded


def missing_files(workspace_root: Path, state_dir: Path) -> list[str]:
    """Return rel_paths recorded in the manifest that are absent from disk.

    Used by doctor to detect deletions of package-managed files. A file in the
    manifest but not on disk is drift the user can repair with `doctor --fix`.
    """
    stored = load(state_dir)
    return sorted(rel for rel in stored if not (workspace_root / rel).exists())


def record(state_dir: Path, workspace_root: Path, paths: list[Path]) -> None:
    """Add or update SHA-256 entries for the given absolute paths, preserving existing entries."""
    stored = load(state_dir)
    for path in paths:
        rel = str(path.relative_to(workspace_root))
        stored[rel] = _sha256(path)
    save(state_dir, stored)
