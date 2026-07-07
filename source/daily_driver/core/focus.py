"""Read-only view of the focus lock for check-in suppression.

The lock file is lock-as-data (see ``cli/commands/focus.py``): its existence
plus an unexpired ``end_epoch`` in the JSON payload means focus mode is on.
This helper never mutates the lock — expiry cleanup stays with the `focus`
command — so it is safe from any context, including launchd firings.
"""

from __future__ import annotations

import json

from daily_driver.core.clock import now
from daily_driver.core.workspace import Workspace


def is_active(workspace: Workspace) -> bool:
    """True when focus mode is on (lock present and not yet expired).

    An unreadable or malformed lock reads as inactive: check-in suppression
    is a convenience, and failing open only risks one extra notification.
    """
    lock = workspace.ephemeral_dir / "focus.lock"
    if not lock.exists():
        return False
    try:
        payload = json.loads(lock.read_text(encoding="utf-8"))
        end_epoch = int(payload.get("end_epoch", 0))
    except (OSError, ValueError, AttributeError, TypeError):
        return False
    return int(now().timestamp()) < end_epoch


__all__ = ["is_active"]
