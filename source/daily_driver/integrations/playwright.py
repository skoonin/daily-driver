"""Subprocess wrapper for the Playwright browser-binary lifecycle.

The `playwright` pip package is a hard dependency, but each browser build
(~100-200 MB) is a separate download that wheels cannot fetch at install time.
Playwright-backed sources (Apple) fail at launch until the configured engine's
build is present. These helpers probe for and install a build via
`python -m playwright`. The engine is selectable
(`plugins.job_search.scraper.browser`); Firefox is the default.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

DEFAULT_ENGINE = "firefox"


def _dry_run_cmd(engine: str) -> list[str]:
    return [sys.executable, "-m", "playwright", "install", "--dry-run", engine]


def _install_cmd(engine: str) -> list[str]:
    return [sys.executable, "-m", "playwright", "install", engine]


# Dry-run prints "  Install location:    <version-pinned cache path>" for each
# requested browser (the engine plus any transitive entry like ffmpeg). We match
# the engine's path specifically rather than trust output ordering.
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


def browser_installed(engine: str = DEFAULT_ENGINE) -> bool:
    """True if the Playwright build for ``engine`` is downloaded.

    `playwright install --dry-run <engine>` resolves the version-pinned cache
    path (e.g. .../ms-playwright/firefox-1511) without downloading anything;
    that path's existence on disk is authoritative. Dry-run exits 0 whether or
    not the build is present, so the path check — not the exit code — is the
    signal. Returns False if playwright cannot be invoked at all.
    """
    try:
        proc = subprocess.run(_dry_run_cmd(engine), capture_output=True, text=True)
    except FileNotFoundError:
        return False
    if proc.returncode != 0:
        return False
    for match in _INSTALL_LOCATION_RE.finditer(proc.stdout):
        location = Path(match.group(1).strip())
        # Version-pinned dir is "<engine>-<rev>" (e.g. firefox-1511). The
        # "<engine>-" prefix avoids matching siblings like
        # chromium_headless_shell when engine is "chromium".
        if location.name.lower().startswith(f"{engine}-"):
            return location.exists()
    return False


def install_browser(engine: str = DEFAULT_ENGINE) -> None:
    """Download the Playwright build for ``engine`` (~100-200 MB).

    Raises PlaywrightError on any non-zero exit so the doctor --fix path can
    surface the failure instead of silently leaving the browser missing.
    """
    cmd = _install_cmd(engine)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise PlaywrightError(127, cmd, stderr=str(exc)) from exc
    if proc.returncode != 0:
        raise PlaywrightError(proc.returncode, cmd, stderr=proc.stderr)
