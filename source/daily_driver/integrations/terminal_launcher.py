"""Open a command in a fresh macOS terminal tab (iTerm2, else Terminal.app).

Used by the scheduled session launchers: launchd provides no TTY, so an
interactive `claude` session cannot run in the launchd context itself. This
seam relaunches the command in a real terminal via AppleScript (osascript).
The first invocation triggers macOS's one-time Automation permission prompt.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path


class TerminalLaunchError(Exception):
    """osascript failed to open the terminal session."""


_ITERM_APP_PATHS = ("/Applications/iTerm.app", "~/Applications/iTerm.app")

# osascript is local and fast; the timeout only guards a hung Apple Events
# handshake (e.g. the Automation permission dialog dismissed by a screen lock).
_OSASCRIPT_TIMEOUT = 30


def _applescript_quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _iterm_installed() -> bool:
    return any(Path(p).expanduser().exists() for p in _ITERM_APP_PATHS)


def _iterm_script(command: str) -> str:
    # A tab in the current window, not a new window, and no `activate` --
    # the session should land in the existing iTerm2 without stealing focus.
    quoted = _applescript_quote(command)
    return (
        'tell application "iTerm"\n'
        "  if (count of windows) = 0 then\n"
        "    create window with default profile\n"
        "  else\n"
        "    tell current window to create tab with default profile\n"
        "  end if\n"
        f'  tell current session of current window to write text "{quoted}"\n'
        "end tell\n"
    )


def _terminal_script(command: str) -> str:
    quoted = _applescript_quote(command)
    return f'tell application "Terminal" to do script "{quoted}"\n'


def shell_command(argv: list[str]) -> str:
    """Join argv into one shell-safe command line for the terminal tab."""
    return shlex.join(argv)


def open_in_terminal(argv: list[str]) -> None:
    """Run ``argv`` interactively in a new terminal tab.

    Prefers iTerm2 when installed, else Terminal.app. Raises
    :class:`TerminalLaunchError` on osascript failure (most commonly a denied
    Automation permission), so callers can surface an actionable message.
    """
    command = shell_command(argv)
    script = _iterm_script(command) if _iterm_installed() else _terminal_script(command)
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=_OSASCRIPT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TerminalLaunchError(f"osascript failed: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"osascript exited {proc.returncode}"
        raise TerminalLaunchError(detail)


__all__ = ["TerminalLaunchError", "open_in_terminal", "shell_command"]
