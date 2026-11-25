"""Subprocess wrapper for the `opencode` CLI.

The only place in the codebase that shells out to `opencode` for sessions.
Callers in `gathers/` receive raw stdout (or ``None`` when there is nothing
usable) and own all parsing; they never import `subprocess`.
"""

from __future__ import annotations

import shutil
import subprocess

from daily_driver.core.logging import get_logger

log = get_logger(__name__)

_TIMEOUT_SECONDS = 30


def list_sessions(query: str) -> str | None:
    """Return raw JSON stdout of `opencode api GET /api/session?<query>`.

    `opencode api` calls the running opencode server, which lists sessions from
    every project, newest ``time.updated`` first. Returns ``None`` when
    `opencode` is not installed (debug only, since most users lack it) or the
    call fails or times out (warning). Never raises.
    """
    if not shutil.which("opencode"):
        log.debug("opencode not found on PATH; skipping opencode sessions")
        return None

    argv = ["opencode", "api", "GET", f"/api/session?{query}"]
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        log.warning("opencode: session list timed out after %ss", _TIMEOUT_SECONDS)
        return None
    except OSError as exc:
        log.warning("opencode: could not run session list: %s", exc)
        return None

    if result.returncode != 0:
        stderr_lines = result.stderr.strip().splitlines()
        reason = stderr_lines[0] if stderr_lines else f"exit {result.returncode}"
        log.warning("opencode: session list failed: %s", reason)
        return None
    return result.stdout
