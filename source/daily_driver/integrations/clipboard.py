from __future__ import annotations

import shutil
import subprocess


def available() -> bool:
    """True if both pbcopy and pbpaste exist on PATH."""
    return bool(shutil.which("pbcopy")) and bool(shutil.which("pbpaste"))


def copy(text: str) -> None:
    """Pipe text to pbcopy stdin.

    Raises FileNotFoundError if pbcopy is not on PATH.
    Raises subprocess.CalledProcessError on non-zero exit.
    """
    subprocess.run(["pbcopy"], input=text, text=True, check=True, capture_output=True)
