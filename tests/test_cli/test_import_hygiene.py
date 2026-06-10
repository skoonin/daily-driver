"""Import-cost guards for the always-loaded CLI entry path.

Heavyweight modules used only on rare code paths (e.g. jinja2, reachable solely
from ``Workspace.init``) must not load when the CLI is merely imported, since
that cost is paid on every invocation. Each guard imports the entry module in a
fresh interpreter and asserts the heavy dependency stayed unimported.
"""

from __future__ import annotations

import subprocess
import sys


def test_cli_import_does_not_load_jinja2():
    """Importing the CLI entry point must not pull in jinja2.

    jinja2 is needed only for the config-template render in ``Workspace.init``;
    importing it eagerly added ~15ms warm / ~26ms cold to every CLI invocation.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import daily_driver.cli.cli, sys; " "print('jinja2' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False", (
        "jinja2 was imported by daily_driver.cli.cli; "
        "keep its import lazy (inside the render helper)"
    )
