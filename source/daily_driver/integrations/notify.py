from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def desktop_notify(message: str, title: str, csv_path: Path) -> None:
    """Send a macOS desktop notification for a completed scrape run.

    Prefers terminal-notifier (opens the CSV on click) and falls back to
    osascript. Both calls use check=False — notifications are best-effort and
    must never abort the scraper.
    """
    file_url = csv_path.as_uri()

    try:
        if shutil.which("terminal-notifier"):
            subprocess.run(
                [
                    "terminal-notifier",
                    "-title",
                    title,
                    "-message",
                    message,
                    "-open",
                    file_url,
                ],
                check=False,
            )
        else:
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{message}" with title "{title}" subtitle "{csv_path.name}"',
                ],
                check=False,
            )
    except OSError:
        pass


__all__ = ["desktop_notify"]
