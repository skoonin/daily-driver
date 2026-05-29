"""Subprocess wrapper for the Playwright browser-binary lifecycle.

The `playwright` pip package is a hard dependency, but the Firefox browser
build (~100 MB) is a separate download that wheels cannot fetch at install
time. Playwright-backed sources (Apple) fail at launch until it is present.
These helpers probe for and install that build via `python -m playwright`.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_DRY_RUN = [sys.executable, "-m", "playwright", "install", "--dry-run", "firefox"]
_INSTALL = [sys.executable, "-m", "playwright", "install", "firefox"]

# Dry-run prints "  Install location:    <version-pinned cache path>" for each
# requested browser (firefox plus any transitive entry like ffmpeg). We match
# the firefox path specifically rather than trust output ordering.
_INSTALL_LOCATION_RE = re.compile(r"Install location:\s*(.+)")


class PlaywrightError(RuntimeError):
    """A `python -m playwright` subprocess exited non-zero.

    Domain wrapper so callers outside `integrations/` never import
    `subprocess` to inspect a `CalledProcessError`. `returncode`, `cmd`, and
    `stderr` mirror the underlying failure for diagnostics.
    """

    def __init__(self, returncode: int, cmd: list[str], *, stderr: str = "") -> None:
        super().__init__(f"playwright exited {returncode}")
        self.returncode = returncode
        self.cmd = cmd
        self.stderr = stderr


def firefox_installed() -> bool:
    """True if the Playwright Firefox browser build is downloaded.

    `playwright install --dry-run firefox` resolves the version-pinned cache
    path (e.g. .../ms-playwright/firefox-1511) without downloading anything;
    that path's existence on disk is authoritative. Dry-run exits 0 whether or
    not the build is present, so the path check — not the exit code — is the
    signal. Returns False if playwright cannot be invoked at all.
    """
    try:
        proc = subprocess.run(_DRY_RUN, capture_output=True, text=True)
    except FileNotFoundError:
        return False
    if proc.returncode != 0:
        return False
    for match in _INSTALL_LOCATION_RE.finditer(proc.stdout):
        location = Path(match.group(1).strip())
        if "firefox" in location.name.lower():
            return location.exists()
    return False


def install_firefox() -> None:
    """Download the Playwright Firefox browser build (~100 MB).

    Raises PlaywrightError on any non-zero exit so the doctor --fix path can
    surface the failure instead of silently leaving the browser missing.
    """
    try:
        proc = subprocess.run(_INSTALL, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise PlaywrightError(127, _INSTALL, stderr=str(exc)) from exc
    if proc.returncode != 0:
        raise PlaywrightError(proc.returncode, _INSTALL, stderr=proc.stderr)
