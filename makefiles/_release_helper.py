"""Release helper invoked by `make release`.

Subcommands:

* ``cut VERSION DATE``
  - Rewrites ``CHANGELOG.md`` so ``## [Unreleased]`` is followed by a new
    ``## [VERSION] — DATE`` section (the ``[Unreleased]`` header stays,
    ready for the next cycle).
  - Bumps ``__version__`` in ``source/daily_driver/__init__.py``.

* ``tag-message VERSION``
  - Prints the content of the ``## [VERSION]`` section from ``CHANGELOG.md``
    to stdout, suitable for ``git tag -a -m "$(release-helper tag-message X.Y.Z)"``.
"""

from __future__ import annotations

import pathlib
import re
import sys

CHANGELOG = pathlib.Path("CHANGELOG.md")
INIT_FILE = pathlib.Path("source/daily_driver/__init__.py")


def cut(version: str, date: str) -> None:
    text = CHANGELOG.read_text()
    if not re.search(r"^## \[Unreleased\]", text, flags=re.M):
        sys.exit("CHANGELOG.md is missing `## [Unreleased]` header")
    new_text = re.sub(
        r"^## \[Unreleased\]",
        f"## [Unreleased]\n\n## [{version}] — {date}",
        text,
        count=1,
        flags=re.M,
    )
    CHANGELOG.write_text(new_text)

    init_text = INIT_FILE.read_text()
    if not re.search(r"^__version__\s*=", init_text, flags=re.M):
        sys.exit(f"{INIT_FILE} missing __version__")
    INIT_FILE.write_text(
        re.sub(
            r"^__version__\s*=.*$",
            f'__version__ = "{version}"',
            init_text,
            count=1,
            flags=re.M,
        )
    )


def tag_message(version: str) -> None:
    text = CHANGELOG.read_text()
    pattern = rf"^## \[{re.escape(version)}\].*?$(.*?)(?=^## \[|\Z)"
    m = re.search(pattern, text, flags=re.M | re.S)
    if not m:
        sys.exit(f"CHANGELOG.md has no section for version {version}")
    sys.stdout.write(m.group(1).strip())


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: _release_helper.py {cut,tag-message} ...")
    cmd = sys.argv[1]
    if cmd == "cut":
        if len(sys.argv) != 4:
            sys.exit("usage: _release_helper.py cut VERSION DATE")
        cut(sys.argv[2], sys.argv[3])
    elif cmd == "tag-message":
        if len(sys.argv) != 3:
            sys.exit("usage: _release_helper.py tag-message VERSION")
        tag_message(sys.argv[2])
    else:
        sys.exit(f"unknown subcommand: {cmd}")


if __name__ == "__main__":
    main()
