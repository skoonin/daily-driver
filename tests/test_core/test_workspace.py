from __future__ import annotations

import pytest

from daily_driver.core.workspace import Workspace, WorkspaceError

_MINIMAL_YAML = """\
daily_driver:
  output_dir: .
tracker:
  categories:
    task: {required: [title]}
"""


# ---------------------------------------------------------------------------
# Workspace.init
# ---------------------------------------------------------------------------


def test_init_creates_marker_and_state_dir(tmp_path):
    ws = Workspace.init(tmp_path)
    assert (tmp_path / ".dd-config.yaml").exists()
    assert (tmp_path / ".daily-driver").is_dir()
    assert ws.root == tmp_path
    assert ws.state_dir == tmp_path / ".daily-driver"


def test_init_loads_valid_config(tmp_path):
    ws = Workspace.init(tmp_path)
    assert ws.config.daily_driver.output_dir == "."
    assert "task" in ws.config.tracker.categories


def test_init_raises_if_already_initialized(tmp_path):
    Workspace.init(tmp_path)
    with pytest.raises(WorkspaceError, match="already initialized"):
        Workspace.init(tmp_path)


# ---------------------------------------------------------------------------
# Workspace.discover_or_fail — override path
# ---------------------------------------------------------------------------


def test_discover_override_valid(tmp_path):
    (tmp_path / ".dd-config.yaml").write_text(_MINIMAL_YAML, encoding="utf-8")
    ws = Workspace.discover_or_fail(override=tmp_path)
    assert ws.root == tmp_path


def test_discover_override_missing_marker_raises(tmp_path):
    with pytest.raises(WorkspaceError, match=".dd-config.yaml"):
        Workspace.discover_or_fail(override=tmp_path)


# ---------------------------------------------------------------------------
# Workspace.discover_or_fail — CWD walk
# ---------------------------------------------------------------------------


def test_discover_finds_marker_in_cwd(tmp_path, monkeypatch):
    (tmp_path / ".dd-config.yaml").write_text(_MINIMAL_YAML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    ws = Workspace.discover_or_fail()
    assert ws.root == tmp_path


def test_discover_walks_up_from_nested_cwd(tmp_path, monkeypatch):
    (tmp_path / ".dd-config.yaml").write_text(_MINIMAL_YAML, encoding="utf-8")
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    ws = Workspace.discover_or_fail()
    assert ws.root == tmp_path


def test_discover_no_marker_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(WorkspaceError, match="no daily-driver workspace found"):
        Workspace.discover_or_fail()


# ---------------------------------------------------------------------------
# Workspace fields
# ---------------------------------------------------------------------------


def test_workspace_version_matches_package(tmp_path):
    import daily_driver

    ws = Workspace.init(tmp_path)
    assert ws.version == daily_driver.__version__


def test_workspace_state_dir_resolves_correctly(tmp_path):
    ws = Workspace.init(tmp_path)
    assert ws.state_dir == tmp_path / ".daily-driver"


# ---------------------------------------------------------------------------
# Workspace.output_dir
# ---------------------------------------------------------------------------


def test_output_dir_defaults_to_workspace_root(tmp_path):
    ws = Workspace.init(tmp_path)
    assert ws.output_dir == tmp_path.resolve()


def test_output_dir_relative_resolves_against_root(tmp_path):
    (tmp_path / ".dd-config.yaml").write_text(
        _MINIMAL_YAML.replace("output_dir: .", "output_dir: notes"),
        encoding="utf-8",
    )
    ws = Workspace.discover_or_fail(override=tmp_path)
    assert ws.output_dir == (tmp_path / "notes").resolve()


def test_output_dir_absolute_passes_through(tmp_path):
    absolute = tmp_path / "abs-out"
    (tmp_path / ".dd-config.yaml").write_text(
        _MINIMAL_YAML.replace("output_dir: .", f"output_dir: {absolute}"),
        encoding="utf-8",
    )
    ws = Workspace.discover_or_fail(override=tmp_path)
    assert ws.output_dir == absolute


def test_output_dir_expands_home_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".dd-config.yaml").write_text(
        _MINIMAL_YAML.replace("output_dir: .", "output_dir: ~/dd-output"),
        encoding="utf-8",
    )
    ws = Workspace.discover_or_fail(override=tmp_path)
    assert ws.output_dir == tmp_path / "dd-output"
