"""Tests for daily_driver.cli.cli.app().

Command module imports (init, doctor) are stubbed via monkeypatch so these
tests remain independent of Stream B/C landing status.
"""

from __future__ import annotations

import importlib
import logging
import re

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


def test_workspace_before_subcommand_dispatches(tmp_path):
    """`--workspace PATH <cmd>` dispatches under lazy command selection.

    Regression: the argv scan must skip the `--workspace` value token so the
    real subcommand parser is built (not the help-only stub), otherwise the
    path value is misread as the command and parsing exits 2.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    rc = app(["--workspace", str(ws), "doctor"])
    assert rc != 2


# ---------------------------------------------------------------------------
# 8. Top-level error handler shows the exception class without -v
# ---------------------------------------------------------------------------


def test_unhandled_error_reports_class_name(monkeypatch, capsys):
    """Non-verbose error path prints `ClassName: msg`, not bare str(exc)."""

    class _Stub:
        @staticmethod
        def add_parser(subparsers, parents):
            return subparsers.add_parser("doctor")

        @staticmethod
        def run(args) -> int:
            raise ValueError("boom")

    import sys
    import types

    doctor_mod = types.ModuleType("daily_driver.cli.commands.doctor")
    doctor_mod.add_parser = _Stub.add_parser
    doctor_mod.run = _Stub.run

    # Restrict the command set to the stub so app() builds only this subparser;
    # otherwise it imports all _COMMANDS and the first real module's import
    # ordering leaks between tests.
    monkeypatch.setitem(sys.modules, "daily_driver.cli.commands.doctor", doctor_mod)
    monkeypatch.setattr(
        "daily_driver.cli.cli._COMMANDS",
        [("doctor", "daily_driver.cli.commands.doctor")],
    )

    rc = app(["doctor"])
    assert rc == 1
    assert "ValueError: boom" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 9. A config error prints its own message, without a class name
# ---------------------------------------------------------------------------


def test_removed_setting_error_shows_its_fix(tmp_path, capsys):
    from daily_driver.core.workspace import Workspace

    Workspace.init(tmp_path)
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(
        cfg_file.read_text(encoding="utf-8") + "daily_driver:\n  output_dir: notes\n",
        encoding="utf-8",
    )
    rc = app(["--workspace", str(tmp_path), "tracker", "list"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "output_dir" in err
    assert "Move the files" in err
    assert "Extra inputs are not permitted" not in err
    assert "ConfigError" not in err


def test_ignored_setting_warns_on_stderr_and_leaves_stdout_clean(tmp_path, capsys):
    """Prompts read `paths` output, so a warning must not reach stdout."""
    from daily_driver.core.workspace import Workspace

    Workspace.init(tmp_path)
    cfg_file = tmp_path / ".dd-config.yaml"
    cfg_file.write_text(
        cfg_file.read_text(encoding="utf-8") + "nope: 1\n", encoding="utf-8"
    )
    rc = app(["--workspace", str(tmp_path), "paths", "root"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.strip() == str(tmp_path.resolve())
    assert "ignoring nope" in captured.err


def _listed_commands(capsys, argv):
    with pytest.raises(SystemExit):
        app([*argv, "--help"])
    out = capsys.readouterr().out
    # Each listed command starts its own line with a four-space indent.
    return re.findall(r"^    ([a-z][a-z-]*)", out, re.MULTILINE)


def test_top_level_help_lists_commands_alphabetically(tmp_path, capsys):
    from daily_driver.core.workspace import Workspace

    Workspace.init(tmp_path, ("job_search",))
    listed = _listed_commands(capsys, ["--workspace", str(tmp_path)])
    assert "jobs" in listed
    assert "config" in listed
    assert listed == sorted(listed)


@pytest.mark.parametrize("in_workspace", [True, False])
def test_top_level_help_leaves_out_a_plugin_that_is_off(
    tmp_path, monkeypatch, capsys, in_workspace
):
    from daily_driver.core.workspace import Workspace

    if in_workspace:
        Workspace.init(tmp_path)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        app(["--help"])
    out = capsys.readouterr().out
    assert "tracker" in out
    assert "job" not in out.lower()


@pytest.mark.parametrize("argv", [["--help"], ["jobs", "status"]])
@pytest.mark.parametrize("broken", [b"schedule: [\n", b"\xff\xfe\x80\n"])
def test_a_config_that_cannot_load_does_not_crash_the_parser(
    tmp_path, capsys, argv, broken
):
    """The config is read before argparse; an error must still reach the
    user as the command's own error, not as a traceback."""
    from daily_driver.core.workspace import Workspace

    Workspace.init(tmp_path, ("job_search",))
    config = tmp_path / ".dd-config.yaml"
    config.write_bytes(config.read_bytes() + broken)
    try:
        rc = app(["--workspace", str(tmp_path), *argv])
    except SystemExit as exc:
        rc = exc.code
    assert rc in (0, 1)
    if argv == ["jobs", "status"]:
        assert "Error" in capsys.readouterr().err


def test_a_config_that_cannot_load_lists_no_plugin_command(tmp_path, capsys):
    from daily_driver.core.workspace import Workspace

    Workspace.init(tmp_path, ("job_search",))
    config = tmp_path / ".dd-config.yaml"
    config.write_text(config.read_text() + "schedule: [\n", encoding="utf-8")
    assert "jobs" not in _listed_commands(capsys, ["--workspace", str(tmp_path)])
    assert "jobs" not in _listed_commands(
        capsys, ["--workspace", str(tmp_path / "missing")]
    )


@pytest.mark.parametrize("form", ["-w={ws}", "-w{ws}", "--workspace={ws}"])
def test_every_workspace_flag_form_hides_a_plugin_that_is_off(
    tmp_path, monkeypatch, capsys, form
):
    """The current folder has job search on, so only the flag can hide `jobs`."""
    from daily_driver.core.workspace import Workspace

    Workspace.init(tmp_path, ("job_search",))
    ws = tmp_path / "ws"
    ws.mkdir()
    Workspace.init(ws)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        app(["jobs", "status", form.format(ws=ws)])
    assert exc.value.code == 2


def test_workspace_flag_scan_stops_at_double_dash():
    from daily_driver.cli.cli import _workspace_flag

    assert _workspace_flag(["--", "-w", "x"]) is None
    assert _workspace_flag(["-w", "a", "--", "-w", "x"]) == "a"


def test_a_yaml_error_names_the_settings_file(tmp_path, capsys):
    from daily_driver.core.workspace import Workspace

    Workspace.init(tmp_path)
    config = tmp_path / ".dd-config.yaml"
    config.write_text(config.read_text() + "schedule: [\n", encoding="utf-8")
    assert app(["--workspace", str(tmp_path), "status"]) == 1
    err = " ".join(capsys.readouterr().err.split())
    assert str(config) in err
    assert "ParserError" not in err
    assert "<unicode string>" not in err
    assert "line " in err


def test_a_config_that_is_not_utf8_names_the_settings_file(tmp_path, capsys):
    from daily_driver.core.workspace import Workspace

    Workspace.init(tmp_path)
    config = tmp_path / ".dd-config.yaml"
    config.write_bytes(config.read_bytes() + b"\xff\xfe\n")
    assert app(["--workspace", str(tmp_path), "status"]) == 1
    err = " ".join(capsys.readouterr().err.split())
    assert str(config) in err
    assert "UnicodeDecodeError" not in err


def test_a_control_character_in_the_config_says_what_is_wrong(tmp_path, capsys):
    from daily_driver.core.workspace import Workspace

    Workspace.init(tmp_path)
    config = tmp_path / ".dd-config.yaml"
    original = config.read_bytes()
    config.write_bytes(original + b"note: a\x00b\n")
    assert app(["--workspace", str(tmp_path), "status"]) == 1
    err = " ".join(capsys.readouterr().err.split())
    assert str(config) in err
    line = original.count(b"\n") + 1
    assert f"at line {line}, column 8: special characters are not allowed" in err


def test_a_config_that_cannot_be_read_names_the_settings_file(tmp_path, capsys):
    from daily_driver.core.workspace import Workspace

    Workspace.init(tmp_path)
    config = tmp_path / ".dd-config.yaml"
    config.unlink()
    config.mkdir()
    assert app(["--workspace", str(tmp_path), "status"]) == 1
    err = " ".join(capsys.readouterr().err.split())
    assert f"{config} cannot be read" in err
    assert "IsADirectoryError" not in err


def test_help_shows_the_aliases(capsys):
    with pytest.raises(SystemExit):
        app(["--help"])
    out = capsys.readouterr().out
    assert "day-start (start)" in out
    assert "day-end (end)" in out


@pytest.mark.parametrize(
    ("alias", "module_path"),
    [
        ("start", "daily_driver.cli.commands.day_start"),
        ("end", "daily_driver.cli.commands.day_end"),
    ],
)
def test_alias_runs_the_same_command(monkeypatch, tmp_path, alias, module_path):
    module = importlib.import_module(module_path)
    calls = []
    monkeypatch.setattr(module, "run", lambda args: calls.append(args) or 0)
    # `run` is bound by add_parser's set_defaults and by the final dispatch.
    assert app([alias, "--workspace", str(tmp_path)]) == 0
    assert len(calls) == 1


def test_alias_help_is_the_help_of_the_command(capsys):
    with pytest.raises(SystemExit) as excinfo:
        app(["start", "--help"])
    assert excinfo.value.code == 0
    assert "--session-name" in capsys.readouterr().out
