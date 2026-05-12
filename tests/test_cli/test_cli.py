"""Tests for daily_driver.cli.cli.app().

Command module imports (init, doctor) are stubbed via monkeypatch so these
tests remain independent of Stream B/C landing status.
"""

from __future__ import annotations

import logging

import pytest

import daily_driver
from daily_driver.cli.cli import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stub_cmd(run_return: int = 0):
    """Return a minimal command-module stub with add_parser + run."""

    class _Stub:
        @staticmethod
        def add_parser(subparsers, parents):
            p = subparsers.add_parser("_stub")
            return p

        @staticmethod
        def run(args) -> int:
            return run_return

    return _Stub()


def _patch_commands(monkeypatch, init_stub=None, doctor_stub=None):
    """Patch the two deferred imports inside cli.app() so tests are isolated.

    Uses monkeypatch.setitem on sys.modules so entries auto-revert after the
    test — avoids leaking stubs across tests.
    """
    import sys
    import types

    pkg = types.ModuleType("daily_driver.cli.commands")
    monkeypatch.setitem(sys.modules, "daily_driver.cli.commands", pkg)

    if init_stub is not None:
        init_mod = types.ModuleType("daily_driver.cli.commands.init")
        init_mod.add_parser = init_stub.add_parser
        init_mod.run = init_stub.run
        monkeypatch.setitem(sys.modules, "daily_driver.cli.commands.init", init_mod)
        monkeypatch.setattr(pkg, "init", init_mod, raising=False)

    if doctor_stub is not None:
        doctor_mod = types.ModuleType("daily_driver.cli.commands.doctor")
        doctor_mod.add_parser = doctor_stub.add_parser
        doctor_mod.run = doctor_stub.run
        monkeypatch.setitem(sys.modules, "daily_driver.cli.commands.doctor", doctor_mod)
        monkeypatch.setattr(pkg, "doctor", doctor_mod, raising=False)


# ---------------------------------------------------------------------------
# 1. --version exits 0 and prints version string
# ---------------------------------------------------------------------------


def test_version_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc_info:
        app(["--version"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "daily-driver" in captured.out
    assert daily_driver.__version__ in captured.out


# ---------------------------------------------------------------------------
# 2. Bare invocation returns 2
# ---------------------------------------------------------------------------


def test_bare_invocation_returns_2(capsys):
    # No subcommand → help printed to stderr, exit code 2
    result = app([])
    assert result == 2


# ---------------------------------------------------------------------------
# 3. --help exits 0
# ---------------------------------------------------------------------------


def test_help_exits_zero():
    with pytest.raises(SystemExit) as exc_info:
        app(["--help"])
    assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# 4. -v and -q are mutually exclusive → SystemExit(2)
# ---------------------------------------------------------------------------


def test_verbose_and_quiet_mutually_exclusive():
    with pytest.raises(SystemExit) as exc_info:
        app(["-v", "-q"])
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# 5. Unknown subcommand → exit 2
# ---------------------------------------------------------------------------


def test_unknown_subcommand_exits_2(monkeypatch):
    # With no commands registered, parse_args succeeds but cmd will be None
    # or argparse will error (depending on version). Either way: non-zero.
    _patch_commands(monkeypatch)
    with pytest.raises(SystemExit) as exc_info:
        app(["definitely-not-a-subcommand"])
    # argparse errors exit 2; we also return 2 for missing cmd
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# 6. -v sets INFO logging level
# ---------------------------------------------------------------------------


def _make_doctor_stub_for_verbosity():
    """Doctor stub that records the logger level at run() time."""

    class _Stub:
        captured_level: int | None = None

        @staticmethod
        def add_parser(subparsers, parents):
            p = subparsers.add_parser("doctor")
            return p

        @staticmethod
        def run(args) -> int:
            _Stub.captured_level = logging.getLogger("daily_driver").level
            return 0

    return _Stub


def test_verbose_flag_sets_info_level(monkeypatch):
    DoctorStub = _make_doctor_stub_for_verbosity()

    import sys
    import types

    pkg = types.ModuleType("daily_driver.cli.commands")
    doctor_mod = types.ModuleType("daily_driver.cli.commands.doctor")
    doctor_mod.add_parser = DoctorStub.add_parser
    doctor_mod.run = DoctorStub.run

    monkeypatch.setitem(sys.modules, "daily_driver.cli.commands", pkg)
    monkeypatch.setitem(
        sys.modules,
        "daily_driver.cli.commands.init",
        types.ModuleType("daily_driver.cli.commands.init"),
    )
    monkeypatch.setitem(sys.modules, "daily_driver.cli.commands.doctor", doctor_mod)

    # Make init stub a no-op add_parser (so registration doesn't fail)
    init_mod = sys.modules["daily_driver.cli.commands.init"]
    init_mod.add_parser = lambda subparsers, parents: subparsers.add_parser("init")

    result = app(["-v", "doctor"])
    assert result == 0
    assert DoctorStub.captured_level == logging.INFO


def test_double_verbose_flag_sets_debug_level(monkeypatch):
    DoctorStub = _make_doctor_stub_for_verbosity()

    import sys
    import types

    pkg = types.ModuleType("daily_driver.cli.commands")
    doctor_mod = types.ModuleType("daily_driver.cli.commands.doctor")
    doctor_mod.add_parser = DoctorStub.add_parser
    doctor_mod.run = DoctorStub.run

    monkeypatch.setitem(sys.modules, "daily_driver.cli.commands", pkg)
    monkeypatch.setitem(
        sys.modules,
        "daily_driver.cli.commands.init",
        types.ModuleType("daily_driver.cli.commands.init"),
    )
    monkeypatch.setitem(sys.modules, "daily_driver.cli.commands.doctor", doctor_mod)

    init_mod = sys.modules["daily_driver.cli.commands.init"]
    init_mod.add_parser = lambda subparsers, parents: subparsers.add_parser("init")

    result = app(["-vv", "doctor"])
    assert result == 0
    assert DoctorStub.captured_level == logging.DEBUG


# ---------------------------------------------------------------------------
# 7. Global flags accept on either side of the subcommand (Q5 migration)
# ---------------------------------------------------------------------------


def test_verbose_after_subcommand_sets_info(tmp_path):
    """`daily-driver doctor -v` was a parse error pre-Q5; now valid."""
    ws = tmp_path / "ws"
    ws.mkdir()
    # doctor on missing-but-creatable path returns 1 — we only care about parse.
    rc = app(["doctor", "-v", "--workspace", str(ws)])
    # Either 0 (passes) or 1 (workspace not initialized) — must not be 2 (parse error).
    assert rc != 2


def test_workspace_after_subcommand(tmp_path):
    """`--workspace PATH` after the subcommand name parses correctly."""
    ws = tmp_path / "ws"
    ws.mkdir()
    rc = app(["doctor", "--workspace", str(ws)])
    assert rc != 2
