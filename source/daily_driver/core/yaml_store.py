"""Shared flock-guarded, atomic read/write for single-file pydantic YAML stores.

`core/daily_state.py` (per-day state) and `core/session_pointer.py` (workspace
session pointer) both persist one pydantic model to one YAML file with identical
durability requirements: a shared-locked read that tolerates an absent file, and
an exclusive-locked atomic write (tempfile + fsync + `os.replace`). That
safety-critical logic lives here once so a fix to it can't drift between the two
stores; each store keeps its own model, path, and error type and delegates the
I/O.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from daily_driver.core.locking import file_lock

ModelT = TypeVar("ModelT", bound=BaseModel)


def lock_path(target: Path) -> Path:
    """Return the sibling ``.lock`` path guarding ``target``."""
    return target.with_suffix(target.suffix + ".lock")


def read_model(
    target: Path, model_cls: type[ModelT], error_cls: type[Exception]
) -> ModelT | None:
    """Read a YAML file into ``model_cls`` under a shared flock.

    Returns None when the file is absent. Raises ``error_cls`` (with the on-disk
    path baked in) when the file exists but is unparseable, is not a top-level
    mapping, or fails schema validation — so callers can surface actionable
    context instead of a bare YAMLError / ValidationError.
    """
    with file_lock(lock_path(target), shared=True):
        try:
            raw = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise error_cls(f"{target}: invalid YAML: {exc}") from exc
    if data is None:
        return None
    if not isinstance(data, dict):
        raise error_cls(
            f"{target}: expected a YAML mapping at the top level, "
            f"got {type(data).__name__}"
        )
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise error_cls(f"{target}: schema validation failed: {exc}") from exc


def write_model(target: Path, model: BaseModel) -> None:
    """Atomic + flock-guarded write of ``model`` to ``target`` as YAML."""
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = model.model_dump(mode="json")
    serialized = yaml.safe_dump(payload, sort_keys=False)

    with file_lock(lock_path(target)):
        fd, tmp_name = tempfile.mkstemp(
            prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(serialized)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, target)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise


__all__ = ["lock_path", "read_model", "write_model"]
