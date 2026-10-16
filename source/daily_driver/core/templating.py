"""Render package text templates written into a workspace.

Shipped prompts, the workspace README, seeded files such as context.md, and the
initial .dd-config.yaml are Jinja templates so plugin-specific sections appear
only when that plugin is on:

    {% if "job_search" in plugins %}...{% endif %}

Its own module (not generate.py) so init and workspace scaffolding can render
without importing generate, which imports Workspace.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from jinja2 import Environment, StrictUndefined

# trim_blocks + lstrip_blocks: a `{% if %}` line leaves no blank line behind.
# StrictUndefined: a typo in a shipped template fails loudly instead of
# rendering an empty string into the user's prompt.
_ENV = Environment(
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    autoescape=False,
)


def render_for_plugins(
    template_text: str, plugins: Collection[str], **context: Any
) -> str:
    """Render a template with `plugins` set to the given plugin names."""
    return _ENV.from_string(template_text).render(plugins=set(plugins), **context)


def render_text(template_text: str, config: object, **context: Any) -> str:
    """Render a template for the plugins switched on in a workspace config."""
    from daily_driver.plugins import enabled_plugins

    plugins = {plugin.name for plugin in enabled_plugins(config)}
    return render_for_plugins(template_text, plugins, **context)
