from __future__ import annotations

import importlib.resources
import logging
from dataclasses import dataclass
from pathlib import Path

import jinja2
from rich.console import Console

import daily_driver
from daily_driver.core.config import load
from daily_driver.core.config_models import Config
from daily_driver.core.logging import get_logger

_logger = get_logger("workspace")

_MARKER = ".dd-config.yaml"
_STATE_DIR = ".daily-driver"

# Fallback used only when the package template cannot be found.
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


def _render_initial_config() -> str:
    """Render the initial .dd-config.yaml from the bundled Jinja2 template."""
    try:
        tmpl_traversable = importlib.resources.files(
            "daily_driver.resources.templates"
        ).joinpath(".dd-config.yaml.j2")
        template_text = tmpl_traversable.read_text(encoding="utf-8")
        env = jinja2.Environment(autoescape=False, keep_trailing_newline=True)
        return env.from_string(template_text).render()
    except (FileNotFoundError, jinja2.TemplateError) as exc:
        # The fallback omits the plugins.job_search block, so the workspace will be
        # missing job-search config — surface it rather than failing later in `jobs run`.
        _logger.warning(
            "Could not render .dd-config.yaml.j2 (%s); writing minimal fallback config",
            exc,
        )
        return _MINIMAL_CONFIG_FALLBACK


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
    def init(cls, root: Path) -> Workspace:
        """Scaffold a new workspace at root; raises if already initialized."""
        marker = root / _MARKER
        if marker.exists():
            raise WorkspaceError("workspace already initialized")

        marker.write_text(_render_initial_config(), encoding="utf-8")

        state_dir = root / _STATE_DIR
        state_dir.mkdir(exist_ok=True)

        return cls.discover_or_fail(override=root)
