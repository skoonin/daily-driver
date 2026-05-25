from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class ClaudeNotFoundError(RuntimeError):
    """Raised when the `claude` CLI binary is not on PATH."""


class ClaudeInvocationError(RuntimeError):
    """The `claude` subprocess exited non-zero.

    Domain wrapper so callers outside `integrations/` never import
    `subprocess` to inspect a `CalledProcessError`. The `returncode`,
    `stdout`, and `stderr` attributes mirror `subprocess.CalledProcessError`
    so existing diagnostic code reads them unchanged. `cmd` is the argv list
    for logging.
    """

    def __init__(
        self,
        returncode: int,
        cmd: list[str],
        *,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(f"claude exited {returncode}")
        self.returncode = returncode
        self.cmd = cmd
        self.stdout = stdout
        self.stderr = stderr


class ClaudeTimeoutError(RuntimeError):
    """The `claude` subprocess exceeded its timeout.

    Domain wrapper around `subprocess.TimeoutExpired`. The `timeout`
    attribute mirrors that exception's field so callers can report the
    bound without importing `subprocess`.
    """

    def __init__(self, timeout: float | None, cmd: list[str]) -> None:
        super().__init__(f"claude timed out after {timeout}s")
        self.timeout = timeout
        self.cmd = cmd


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
    session_id: str | None = None,
    resume_session_id: str | None = None,
) -> list[str]:
    if session_id is not None and resume_session_id is not None:
        raise ValueError("session_id and resume_session_id are mutually exclusive")
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
    if session_id is not None:
        args.extend(["--session-id", session_id])
    if resume_session_id is not None:
        args.extend(["--resume", resume_session_id])
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
    session_id: str | None = None,
    resume_session_id: str | None = None,
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
        session_id=session_id,
        resume_session_id=resume_session_id,
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
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.wait()
        raise ClaudeTimeoutError(exc.timeout, args) from exc

    if proc.returncode != 0:
        raise ClaudeInvocationError(
            proc.returncode, args, stdout=stdout, stderr=_stderr
        )

    return stdout


def invoke_capture(
    prompt: str,
    *,
    agent: str | None = None,
    session_name: str | None = None,
    timeout: int | None = None,
    add_dirs: list[Path] | None = None,
    model: str | None = None,
) -> tuple[str, str, int]:
    """Headless `claude -p` wrapper that returns (stdout, stderr, rc).

    Unlike `invoke()`, this does NOT raise on non-zero rc — callers (including
    F5's check-in subagent dispatch path) need to inspect stderr verbatim and
    surface failures to the user with retry / continue / abort, which can't
    happen if the error is unwound through `CalledProcessError`. `claude` not
    being on PATH still raises `ClaudeNotFoundError` since that's a setup bug,
    not a subagent failure to forward.
    """
    if shutil.which("claude") is None:
        raise ClaudeNotFoundError("claude CLI not found on PATH")

    args = _build_args(
        prompt,
        agent=agent,
        session_name=session_name,
        headless=True,
        add_dirs=add_dirs,
        model=model,
        output_format=None,
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
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.wait()
        raise ClaudeTimeoutError(exc.timeout, args) from exc

    return stdout, stderr, proc.returncode


def spawn_interactive(
    prompt: str | None = None,
    *,
    agent: str | None = None,
    session_name: str | None = None,
    add_dirs: list[Path] | None = None,
    model: str | None = None,
    session_id: str | None = None,
    resume_session_id: str | None = None,
) -> int:
    """Spawn `claude` with inherited stdin/stdout/stderr and return its exit code.

    Used for interactive sessions (day-start, day-end, check-in) where the user
    drives the conversation. Does not capture output -- the terminal is handed
    off to the claude process. `session_id` pre-mints the session UUID so the
    program can record it before launch; `resume_session_id` reattaches to a
    prior session — the two are mutually exclusive.
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
        session_id=session_id,
        resume_session_id=resume_session_id,
    )

    try:
        completed = subprocess.run(args, check=False)
    except FileNotFoundError as exc:
        raise ClaudeNotFoundError("claude CLI not found on PATH") from exc

    return completed.returncode
