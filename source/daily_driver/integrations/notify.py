from __future__ import annotations

import shutil
import subprocess


def desktop_notify(
    title: str,
    message: str,
    *,
    open_url: str | None = None,
    subtitle: str | None = None,
    execute: str | None = None,
) -> bool:
    """Send a best-effort macOS desktop notification.

    Prefers terminal-notifier (clickable, honoring ``open_url`` /
    ``execute``) and falls back to osascript. Both calls use ``check=False``
    and a 5s timeout — notifications are best-effort and must never abort or
    hang the caller. A generic OS capability: callers supply their own
    title/message plus optional ``open_url`` (a link opened on click),
    ``execute`` (a shell command run on click), and ``subtitle``.

    Returns True when the click actions were delivered (terminal-notifier
    path); False on the osascript fallback, which renders the notification
    but cannot honor ``open_url`` / ``execute`` — callers with a
    click-critical flow can then degrade their message.
    """
    try:
        if shutil.which("terminal-notifier"):
            args = ["terminal-notifier", "-title", title, "-message", message]
            if open_url is not None:
                args += ["-open", open_url]
            if execute is not None:
                args += ["-execute", execute]
            subprocess.run(args, check=False, timeout=5)
            return True
        script = f'display notification "{message}" with title "{title}"'
        if subtitle is not None:
            script += f' subtitle "{subtitle}"'
        subprocess.run(["osascript", "-e", script], check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return False


__all__ = ["desktop_notify"]
