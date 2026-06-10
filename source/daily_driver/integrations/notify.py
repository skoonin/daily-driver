from __future__ import annotations

import shutil
import subprocess


def desktop_notify(
    title: str,
    message: str,
    *,
    open_url: str | None = None,
    subtitle: str | None = None,
) -> None:
    """Send a best-effort macOS desktop notification.

    Prefers terminal-notifier (clickable, honoring ``open_url``) and falls back
    to osascript. Both calls use ``check=False`` and a 5s timeout —
    notifications are best-effort and must never abort or hang the caller. A
    generic OS capability: callers supply
    their own title/message and an optional ``open_url`` (e.g. a ``file://``
    link opened on click) plus an optional ``subtitle``.
    """
    try:
        if shutil.which("terminal-notifier"):
            args = ["terminal-notifier", "-title", title, "-message", message]
            if open_url is not None:
                args += ["-open", open_url]
            subprocess.run(args, check=False, timeout=5)
        else:
            script = f'display notification "{message}" with title "{title}"'
            if subtitle is not None:
                script += f' subtitle "{subtitle}"'
            subprocess.run(["osascript", "-e", script], check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


__all__ = ["desktop_notify"]
