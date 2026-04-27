"""Root conftest — pytest fixtures shared across all test suites.

Full fixture set (tmp_workspace, sample_workspace, mock_claude, mock_icalbuddy,
mock_jobspy) lands later as each layer is ported. For P0, this file only
ensures PYTHONPATH points at source/ when pytest is invoked directly.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

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
