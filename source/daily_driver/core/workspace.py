from __future__ import annotations

import importlib.resources
import logging
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

import daily_driver
from daily_driver.core.config import load
from daily_driver.core.config_models import Config
from daily_driver.core.console import Console as UserConsole
from daily_driver.core.logging import get_logger

_logger = get_logger("workspace")

_MARKER = ".dd-config.yaml"
_STATE_DIR = ".daily-driver"

# Fallback used only when the bundled template fails to render (a missing
# template raises WorkspaceError instead).
_MINIMAL_CONFIG_FALLBACK = """\
# daily-driver workspace configuration
# Edit this file to configure your workspace.
# All structured settings live here; narrative prose stays in Markdown files.
daily_driver:
  output_dir: .
tracker:
  default_category: task
  categories:
    task: {required: [title]}
"""


def _render_initial_config(plugins: Collection[str] = ()) -> str:
    """Render the initial .dd-config.yaml from the bundled Jinja2 template.

    `plugins` names the plugins chosen at init; their settings sections are
    written switched on, and every other plugin's section is left out.

    A *missing* packaged template means the wheel is broken (it should always
    ship); that is not a recoverable user condition, so raise WorkspaceError and
    let init report it and exit non-zero -- the same loud-failure stance the
    contract check takes for an empty-wheel install, rather than silently
    scaffolding a half-configured workspace. A *render* error (a malformed
    template) still degrades to the minimal fallback, but is surfaced on the
    terminal (not just the log) so the user knows the config is incomplete.
    """
    import jinja2

    from daily_driver.core.templating import render_for_plugins

    tmpl_traversable = importlib.resources.files(
        "daily_driver.resources.templates"
    ).joinpath(".dd-config.yaml.j2")
    try:
        template_text = tmpl_traversable.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise WorkspaceError(
            "bundled .dd-config.yaml.j2 template is missing from the installed "
            "package; the wheel is broken -- reinstall daily-driver"
        ) from exc
    try:
        return render_for_plugins(template_text, plugins)
    except jinja2.TemplateError as exc:
        # The fallback has only the required settings and a default block for
        # each chosen plugin -- surface it on the terminal rather than only
        # logging, or the user later finds settings missing with no idea why.
        message = (
            f"could not render the bundled config template ({exc}); wrote a "
            "minimal fallback config -- optional settings and plugin options "
            "are absent. Edit .dd-config.yaml or reinstall daily-driver."
        )
        _logger.warning(message)
        UserConsole.warning(message)
        # Keep the plugins the user chose switched on, with default settings.
        chosen = "".join(f"  {name}: {{}}\n" for name in sorted(plugins))
        return _MINIMAL_CONFIG_FALLBACK + (f"plugins:\n{chosen}" if chosen else "")


class WorkspaceError(Exception):
    pass


@dataclass
class Workspace:
    root: Path
    config: Config
    state_dir: Path
    version: str
    logger: logging.Logger
    console: Console

    @property
    def ephemeral_dir(self) -> Path:
        return self.state_dir / "state"

    @property
    def output_dir(self) -> Path:
        """Durable output directory; resolves a relative config path against the workspace root."""
        raw = self.config.daily_driver.output_dir
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (self.root / path).resolve()
        return path

    @classmethod
    def discover_or_fail(cls, override: Path | None = None) -> Workspace:
        """Walk up from CWD to find workspace root, or use override."""
        if override is not None:
            marker = override / _MARKER
            if not marker.exists():
                raise WorkspaceError(
                    f"no .dd-config.yaml found in specified workspace: {override}"
                )
            root_path = override
        else:
            current = Path.cwd()
            discovered_root: Path | None = None
            for candidate in [current, *current.parents]:
                if (candidate / _MARKER).exists():
                    discovered_root = candidate
                    break

            if discovered_root is None:
                raise WorkspaceError(
                    "no daily-driver workspace found"
                    " (no .dd-config.yaml in CWD or any parent)"
                )

            root_path = discovered_root

        config = load(root_path / _MARKER)
        state_dir = root_path / _STATE_DIR

        return cls(
            root=root_path,
            config=config,
            state_dir=state_dir,
            version=daily_driver.__version__,
            logger=get_logger("workspace"),
            console=Console(stderr=True),
        )

    @classmethod
    def init(cls, root: Path, plugins: Collection[str] = ()) -> Workspace:
        """Scaffold a new workspace at root; raises if already initialized.

        `plugins` names the plugins to switch on in the new config.
        """
        marker = root / _MARKER
        if marker.exists():
            raise WorkspaceError("workspace already initialized")

        marker.write_text(_render_initial_config(plugins), encoding="utf-8")

        state_dir = root / _STATE_DIR
        state_dir.mkdir(exist_ok=True)

        return cls.discover_or_fail(override=root)
