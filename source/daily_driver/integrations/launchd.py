"""macOS launchd integration: install / unload / remove LaunchAgent plists."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


class LaunchdUnavailableError(Exception):
    """Raised when launchd operations are attempted on non-darwin platforms."""


class LaunchdLoadError(Exception):
    """Raised when `launchctl load` exits non-zero."""

    def __init__(self, label: str, returncode: int, stderr: str) -> None:
        self.label = label
        self.returncode = returncode
        self.stderr = stderr.strip()
        msg = f"launchctl load failed for {label!r} (exit {returncode})"
        if self.stderr:
            msg += f": {self.stderr}"
        super().__init__(msg)


def require_macos() -> None:
    if sys.platform != "darwin":
        raise LaunchdUnavailableError(
            f"launchd is macOS-only (current platform: {sys.platform})"
        )


def launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def plist_path(label: str) -> Path:
    return launch_agents_dir() / f"{label}.plist"


def write_plist(label: str, content: str) -> Path:
    require_macos()
    path = plist_path(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def unload(label: str) -> None:
    """launchctl unload the plist; silent no-op if not loaded."""
    require_macos()
    path = plist_path(label)
    if not path.exists():
        return
    subprocess.run(
        ["launchctl", "unload", str(path)],
        check=False,
        capture_output=True,
    )


def load(label: str) -> None:
    """launchctl load the plist; raises LaunchdLoadError on non-zero exit."""
    require_macos()
    path = plist_path(label)
    result = subprocess.run(
        ["launchctl", "load", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise LaunchdLoadError(label, result.returncode, result.stderr)


def remove(label: str) -> bool:
    """Delete the plist file. Returns True if it was removed, False if absent."""
    require_macos()
    path = plist_path(label)
    if not path.exists():
        return False
    path.unlink()
    return True


def is_loaded(label: str) -> bool:
    require_macos()
    result = subprocess.run(
        ["launchctl", "list", label],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0
