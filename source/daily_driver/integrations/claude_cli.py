from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class ClaudeNotFoundError(RuntimeError):
    """Raised when the `claude` CLI binary is not on PATH."""


def available() -> bool:
    """True if `claude` is on PATH."""
    return shutil.which("claude") is not None


def _build_args(
    prompt: str | None,
    *,
    agent: str | None,
    session_name: str | None,
    headless: bool,
    add_dirs: list[Path] | None,
    model: str | None,
    output_format: str | None,
    session_persistence: bool = True,
) -> list[str]:
    args: list[str] = ["claude"]
    if headless:
        args.append("-p")
    # --no-session-persistence only works with -p (claude CLI constraint)
    if headless and not session_persistence:
        args.append("--no-session-persistence")
    if agent:
        args.extend(["--agent", agent])
    if session_name:
        args.extend(["-n", session_name])
    if model:
        args.extend(["--model", model])
    if output_format:
        args.extend(["--output-format", output_format])
    # Prompt MUST come before --add-dir: claude's --add-dir is variadic and
    # silently absorbs trailing positionals as extra directories, leaving the
    # prompt empty. Symptom (review §8): "Input must be provided either through
    # stdin or as a prompt argument when using --print".
    if prompt is not None:
        args.append(prompt)
    if add_dirs:
        args.append("--add-dir")
        args.extend(str(p) for p in add_dirs)
    return args


def invoke(
    prompt: str | None = None,
    *,
    agent: str | None = None,
    session_name: str | None = None,
    headless: bool = False,
    input_text: str | None = None,
    timeout: int | None = None,
    add_dirs: list[Path] | None = None,
    model: str | None = None,
    output_format: str | None = None,
    session_persistence: bool = True,
) -> str:
    """Invoke the `claude` CLI and return its stdout.

    Stdout/stderr are captured -- suitable for headless / scripted use.
    For TTY-attached interactive sessions, use `spawn_interactive` instead.
    """
    if shutil.which("claude") is None:
        raise ClaudeNotFoundError("claude CLI not found on PATH")

    args = _build_args(
        prompt,
        agent=agent,
        session_name=session_name,
        headless=headless,
        add_dirs=add_dirs,
        model=model,
        output_format=output_format,
        session_persistence=session_persistence,
    )

    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ClaudeNotFoundError("claude CLI not found on PATH") from exc

    try:
        stdout, _stderr = proc.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise

    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, args, output=stdout, stderr=_stderr
        )

    return stdout


def spawn_interactive(
    prompt: str | None = None,
    *,
    agent: str | None = None,
    session_name: str | None = None,
    add_dirs: list[Path] | None = None,
    model: str | None = None,
) -> int:
    """Spawn `claude` with inherited stdin/stdout/stderr and return its exit code.

    Used for interactive sessions (day-start, day-end, check-in) where the user
    drives the conversation. Does not capture output -- the terminal is handed
    off to the claude process.
    """
    if shutil.which("claude") is None:
        raise ClaudeNotFoundError("claude CLI not found on PATH")

    args = _build_args(
        prompt,
        agent=agent,
        session_name=session_name,
        headless=False,
        add_dirs=add_dirs,
        model=model,
        output_format=None,
    )

    try:
        completed = subprocess.run(args, check=False)
    except FileNotFoundError as exc:
        raise ClaudeNotFoundError("claude CLI not found on PATH") from exc

    return completed.returncode
