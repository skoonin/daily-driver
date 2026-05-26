"""Root conftest — pytest fixtures shared across all test suites.

Full fixture set (tmp_workspace, sample_workspace, mock_claude, mock_icalbuddy,
mock_jobspy) lands later as each layer is ported. For P0, this file only
ensures PYTHONPATH points at source/ when pytest is invoked directly.
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
