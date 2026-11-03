"""Root conftest: import paths and the autouse guards every test suite shares.

Puts source/ and the repo root on the import path (and on PYTHONPATH for
multiprocessing spawn workers), pins the console width, bridges caplog to the daily_driver logger, and stops
any test from launching a real AI program.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).parent
SOURCE_DIR = ROOT_DIR / "source"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Ensure multiprocessing spawn subprocesses can resolve the `tests` package
# (pytest's pythonpath isn't inherited by spawned workers; PYTHONPATH is).
_existing_pp = os.environ.get("PYTHONPATH", "")
if str(ROOT_DIR) not in _existing_pp.split(os.pathsep):
    os.environ["PYTHONPATH"] = (
        f"{ROOT_DIR}{os.pathsep}{_existing_pp}" if _existing_pp else str(ROOT_DIR)
    )


@pytest.fixture(autouse=True)
def _fixed_console_width(monkeypatch):
    """Render Rich output at CI's width whatever terminal runs the tests.

    Rich takes its width from COLUMNS, which tox sets to the width of the
    terminal that started it and a developer shell may export, so a table
    cell wraps differently per terminal and a substring assertion can pass
    in CI (no terminal, 80 columns) and fail locally.
    """
    monkeypatch.setenv("COLUMNS", "80")


@pytest.fixture(autouse=True)
def _bridge_caplog_to_dd_logger(request):
    """Mirror pytest's caplog handler onto the `daily_driver` logger.

    ``daily_driver.core.logging.configure()`` sets ``propagate=False`` on the
    package logger, so pytest's caplog (attached to root) never sees records
    once any prior test has called ``configure()``. This fixture attaches
    caplog's underlying handler to the daily_driver logger for the duration
    of any test that requests caplog, and removes it on teardown.
    """
    if "caplog" not in request.fixturenames:
        yield
        return

    caplog = request.getfixturevalue("caplog")
    dd_logger = logging.getLogger("daily_driver")
    handler = caplog.handler
    dd_logger.addHandler(handler)
    prev_level = dd_logger.level
    if prev_level == logging.NOTSET or prev_level > logging.DEBUG:
        dd_logger.setLevel(logging.DEBUG)
    try:
        yield
    finally:
        dd_logger.removeHandler(handler)
        dd_logger.setLevel(prev_level)


# Real AI programs a provider template can name. Tests launch fake programs by
# absolute path; reaching one of these means a test forgot to stub the provider
# layer and would spend real model calls.
_REAL_AI_PROGRAMS = frozenset({"claude", "opencode"})


@pytest.fixture(autouse=True)
def _no_real_ai_programs(monkeypatch):
    from daily_driver.integrations import provider

    real_run = provider._run
    real_cleanup = provider._cleanup

    def _refuse(argv0: str) -> None:
        if Path(argv0).name in _REAL_AI_PROGRAMS:
            raise AssertionError(
                f"test tried to launch the real {argv0!r}; stub "
                "daily_driver.integrations.provider.invoke instead"
            )

    def guarded_run(name, argv, prompt, **kwargs):
        _refuse(argv[0])
        return real_run(name, argv, prompt, **kwargs)

    def guarded_cleanup(name, template, values):
        first = template[0] if template else ""
        _refuse(first if isinstance(first, str) else "")
        return real_cleanup(name, template, values)

    monkeypatch.setattr(provider, "_run", guarded_run)
    monkeypatch.setattr(provider, "_cleanup", guarded_cleanup)


@pytest.fixture(autouse=True)
def _no_real_interactive_programs(monkeypatch):
    from daily_driver.integrations import provider

    real_spawn = provider.spawn

    def guarded_spawn(chosen, argv):
        if Path(argv[0]).name in _REAL_AI_PROGRAMS:
            raise AssertionError(
                f"test tried to launch the real {argv[0]!r}; stub "
                "daily_driver.integrations.provider.spawn instead"
            )
        return real_spawn(chosen, argv)

    monkeypatch.setattr(provider, "spawn", guarded_spawn)
