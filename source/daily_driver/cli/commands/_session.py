"""Shared launcher helpers for interactive AI sessions.

Each interactive subcommand (day-start, day-end, check-in, resume) resolves the
workspace, finds the provider routed for the `interactive` task, and hands the
terminal to that provider's `run` or `resume` template with the launcher's
slash command as the opening prompt.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date
from pathlib import Path

from daily_driver.core import clock, focus
from daily_driver.core.console import Console
from daily_driver.core.daily_state import DailyStateError
from daily_driver.core.logging import get_logger
from daily_driver.core.model_file import ModelFileError, set_model
from daily_driver.core.prompt_file import write_prompt_file
from daily_driver.core.providers import Provider, Template, provider_for
from daily_driver.core.session_pointer import (
    SessionPointer,
    SessionPointerError,
    clear_pointer,
    read_pointer,
    write_pointer,
)
from daily_driver.core.workspace import Workspace, WorkspaceError
from daily_driver.integrations import notify
from daily_driver.integrations import provider as ai
from daily_driver.integrations import terminal_launcher
from daily_driver.tasks import INTERACTIVE

_log = get_logger(__name__)


class SessionError(RuntimeError):
    """Wraps expected launcher failures that should print cleanly and exit 1."""


def add_launch_mode_arg(parser: argparse.ArgumentParser) -> None:
    """Add the scheduler-facing --launch flag to a session launcher parser."""
    parser.add_argument(
        "--launch",
        choices=("terminal", "notify"),
        default=None,
        help=(
            "Scheduler firing mode: 'terminal' opens the session in a new"
            " iTerm2/Terminal tab; 'notify' posts a clickable notification"
            " that starts it. Omit for a normal interactive run."
        ),
    )


def _self_argv(cmd_name: str, workspace: Workspace) -> list[str]:
    """Rebuild the argv that relaunches this command interactively."""
    dd_bin = sys.argv[0]
    if not Path(dd_bin).is_absolute():
        dd_bin = shutil.which("daily-driver") or "daily-driver"
    return [dd_bin, cmd_name, "--workspace", str(workspace.root)]


def handle_launch_mode(
    args: argparse.Namespace,
    workspace: Workspace,
    cmd_name: str,
    *,
    respect_focus: bool = False,
) -> int | None:
    """Divert a scheduler firing per its --launch mode.

    Returns an exit code when the firing was handled (terminal tab opened,
    notification posted, or focus-suppressed), or None for a normal
    interactive run. launchd has no TTY, so the scheduled plists always pass
    a mode; the relaunched command carries no --launch flag and runs the
    ordinary interactive path in the fresh tab.
    """
    mode = getattr(args, "launch", None)
    if mode is None:
        return None

    relaunch = _self_argv(cmd_name, workspace)
    if mode == "terminal":
        try:
            terminal_launcher.open_in_terminal(relaunch)
        except terminal_launcher.TerminalLaunchError as exc:
            # Most commonly a denied/never-granted Automation permission.
            # Fall back to a notification so the firing is never fully silent.
            _log.warning("terminal launch failed for %s: %s", cmd_name, exc)
            notify.desktop_notify(
                "Daily Driver",
                f"Could not open a terminal for {cmd_name} ({exc});"
                f" run `daily-driver {cmd_name}` manually",
            )
            return 1
        notify.desktop_notify(
            "Daily Driver", f"{cmd_name} session opened in a terminal tab"
        )
        return 0

    # mode == "notify"
    if respect_focus and focus.is_active(workspace):
        _log.info("focus mode active; suppressing scheduled %s", cmd_name)
        return 0
    click_cmd = terminal_launcher.shell_command(relaunch + ["--launch", "terminal"])
    # Message is useful with or without click support: terminal-notifier makes
    # it clickable; osascript shows the manual command the user can copy.
    notify.desktop_notify(
        "Daily Driver",
        f"Time for {cmd_name} — run: daily-driver {cmd_name}",
        execute=click_cmd,
    )
    return 0


def interactive_provider(workspace: Workspace) -> Provider:
    """The provider routed for `interactive`, checked to be launchable."""
    provider = provider_for(workspace.config, INTERACTIVE)
    if not ai.available(provider):
        raise SessionError(
            f"{ai.program(provider.settings)} not found on PATH; provider "
            f"{provider.name} runs interactive sessions. Install it, or give "
            "another provider the interactive task under ai.providers"
        )
    return provider


def default_session_name(command: str, override: str | None = None) -> str:
    if override:
        return override
    return f"{command}-{date.today().isoformat()}"


def _session_model(
    provider: Provider, workspace: Workspace, model: str | None
) -> str | None:
    """The session's model: `--model`, else the provider's; set in its model file.

    Called before the session pointer is written, so a model file that cannot
    be changed stops the launch with the last session still resumable.
    """
    model = model or provider.settings.model
    model_file = provider.settings.model_file
    if model_file is not None and model is not None:
        try:
            set_model(workspace.root / model_file.path, model_file.key, model)
        except ModelFileError as exc:
            raise SessionError(str(exc)) from exc
    return model


def _template(provider: Provider, template_name: str) -> Template:
    template = getattr(provider.settings, template_name)
    if template is None:
        raise SessionError(
            f"{provider.name} has no {template_name} template; daily-driver "
            f"resume and ai.interactive.resume_check_in need one"
        )
    return template


def _spawn(
    provider: Provider,
    template_name: str,
    *,
    workspace: Workspace,
    command: str | None,
    session_id: str | None,
    session_name: str,
    agent: str,
    model: str | None,
) -> int:
    template = _template(provider, template_name)
    values: dict[str, str | None] = {
        "workspace": str(workspace.root),
        "command": command,
        "session_id": session_id,
        "session_name": session_name,
        "agent": agent,
        "model": model,
    }
    if command is not None and ai.uses(template, "prompt_file"):
        values["prompt_file"] = str(
            write_prompt_file(workspace, command=command, agent=agent)
        )
    argv = ai.render(template, values, provider=provider.name)
    return ai.spawn(provider, argv)


def launch_fresh_and_record(
    *,
    workspace: Workspace,
    provider: Provider,
    command: str,
    session_name: str,
    agent: str,
    model: str | None,
) -> int:
    """Start a fresh session, record it as the workspace's most recent, spawn.

    The pointer is written BEFORE the spawn so a still-running session is
    reattachable mid-life (a crashed tab can be resumed while it is up). A
    provider with no `session_id` format gets no ID, so its session cannot be
    resumed; the old pointer is cleared so nothing reattaches to an older one.
    """
    model = _session_model(provider, workspace, model)
    session_id = ai.mint_session_id(provider.settings)
    if session_id is None:
        clear_pointer(workspace)
    else:
        write_pointer(
            workspace,
            SessionPointer(
                last_session_id=session_id,
                last_session_at=clock.now(),
                provider=provider.name,
            ),
        )
    return _spawn(
        provider,
        "run",
        workspace=workspace,
        command=command,
        session_id=session_id,
        session_name=session_name,
        agent=agent,
        model=model,
    )


def reattach_or_fresh(
    *,
    workspace: Workspace,
    provider: Provider,
    command: str | None,
    session_name: str,
    agent: str,
    model: str | None,
) -> int:
    """Reattach to the most-recent workspace session, or start fresh if none exists.

    Reattaching replays `command` into the resumed conversation. When the
    recorded session can no longer be resumed, the provider reports it and
    exits non-zero; that code propagates rather than silently launching an
    untethered fresh session. A session recorded by another provider means
    nothing to this one: a launcher with a `command` (check-in) starts fresh,
    and a plain `resume` is refused.
    """
    pointer = read_pointer(workspace)
    resume_id = pointer.last_session_id if pointer else None
    other_provider = pointer is not None and pointer.provider != provider.name
    if pointer is None or resume_id is None or (other_provider and command):
        if command is None:
            raise SessionError(
                "no prior session to resume — run day-start, day-end, or "
                "check-in first"
            )
        return launch_fresh_and_record(
            workspace=workspace,
            provider=provider,
            command=command,
            session_name=session_name,
            agent=agent,
            model=model,
        )
    if other_provider:
        raise SessionError(
            f"the last session was started with {pointer.provider}, but "
            f"interactive sessions now run on {provider.name}; start a fresh "
            "one with day-start or check-in --no-resume"
        )
    # Before the model file is set, so a launch that cannot happen changes nothing.
    _template(provider, "resume")
    return _spawn(
        provider,
        "resume",
        workspace=workspace,
        command=command,
        session_id=resume_id,
        session_name=session_name,
        agent=agent,
        model=_session_model(provider, workspace, model),
    )


def handle_launch_exception(exc: BaseException) -> int:
    """Translate subprocess / launcher failures into CLI exit codes with a user-visible message."""
    if isinstance(exc, (SessionError, WorkspaceError, ai.AIInvocationError)):
        Console.error(str(exc))
        return 1
    if isinstance(exc, (DailyStateError, SessionPointerError)):
        # Both raise with the on-disk path baked in; surface it cleanly so the
        # user can hand-edit / delete the offending YAML.
        Console.error(str(exc))
        return 1
    if isinstance(exc, ValueError):
        # Config validation errors and programming-error guards. Exit 2 = usage
        # error, distinct from runtime failure.
        Console.error(str(exc))
        return 2
    if isinstance(exc, OSError):
        # Disk full / permission denied during plan-stub or state write. Print
        # the system error verbatim — the message already includes the path.
        Console.error(str(exc))
        return 1
    raise
