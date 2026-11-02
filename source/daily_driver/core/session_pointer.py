"""Workspace-level pointer to the most recent interactive session.

Every launcher that spawns a fresh session (day-start, day-end, check-in's
fresh path) mints a session ID in its provider's `session_id` format and
records it here with the provider's name; `resume` and `check-in` reattach to
it through the provider's `resume` template. Unlike
`core/daily_state.py` this is date-independent — a single file per workspace
holding "the last session started here", so `resume` works across day
boundaries (job-search / errand / project work spans days).

The file lives at `<workspace.ephemeral_dir>/session.yaml`
(i.e. `<root>/.daily-driver/state/session.yaml`). Durable read/write (shared-lock
read, exclusive-lock atomic write) is delegated to `core/yaml_store.py`, shared
with `core/daily_state.py`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from daily_driver.core import yaml_store
from daily_driver.core.providers import DEFAULT_PROVIDER
from daily_driver.core.workspace import Workspace


class SessionPointerError(RuntimeError):
    """Raised when the session-pointer YAML on disk cannot be parsed."""


class SessionPointer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_session_id: str | None = None
    last_session_at: datetime | None = None
    # A pointer file with no provider key was written by claude.
    provider: str = DEFAULT_PROVIDER


def pointer_path(workspace: Workspace) -> Path:
    """Return the session-pointer YAML path. Pure; does not create parents."""
    return workspace.ephemeral_dir / "session.yaml"


def read_pointer(workspace: Workspace) -> SessionPointer | None:
    """Read the workspace session pointer (None when absent).

    Raises SessionPointerError (with the on-disk path) on corrupt / invalid YAML.
    """
    return yaml_store.read_model(
        pointer_path(workspace), SessionPointer, SessionPointerError
    )


def write_pointer(workspace: Workspace, pointer: SessionPointer) -> None:
    """Atomic + flock-guarded write of the workspace session pointer."""
    yaml_store.write_model(pointer_path(workspace), pointer)


def clear_pointer(workspace: Workspace) -> None:
    """Forget the most recent session; a no-op when none is recorded."""
    pointer_path(workspace).unlink(missing_ok=True)


__all__ = [
    "SessionPointer",
    "SessionPointerError",
    "clear_pointer",
    "pointer_path",
    "read_pointer",
    "write_pointer",
]
