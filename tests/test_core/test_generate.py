from __future__ import annotations

import json
import logging
import multiprocessing
from dataclasses import dataclass
from pathlib import Path

import pytest
from rich.console import Console

from daily_driver.core import generate, version_stamp


# Minimal stand-in for Workspace — avoids the .dd-config.yaml discovery / config-loading
# machinery from Stream B's Workspace.discover_or_fail. This is intentional test isolation:
# we test generate's logic directly without exercising config parsing.
@dataclass
class _FakeWorkspace:
    root: Path
    state_dir: Path
    version: str
    logger: logging.Logger
    console: Console

    @property
    def ephemeral_dir(self) -> Path:
        return self.state_dir / "state"

    @classmethod
    def make(cls, root: Path, version: str = "1.0.0") -> _FakeWorkspace:
        state_dir = root / ".daily-driver"
        state_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            root=root,
            state_dir=state_dir,
            version=version,
            logger=logging.getLogger("test.generate"),
            console=Console(stderr=True),
        )


def test_fast_path_when_stamp_matches(tmp_path: Path) -> None:
    ws = _FakeWorkspace.make(tmp_path)
    version_stamp.write(ws.state_dir, ws.version)

    # No .claude tree exists yet; fast-path must not create it.
    generate.generate(ws)

    assert not (tmp_path / ".claude" / "commands" / "daily-driver").exists()


def test_fast_path_returns_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _FakeWorkspace.make(tmp_path)
    version_stamp.write(ws.state_dir, ws.version)

    calls: list[str] = []
    original_wipe = generate._wipe_and_recreate

    def tracking_wipe(path: Path) -> None:
        calls.append(str(path))
        original_wipe(path)

    monkeypatch.setattr(generate, "_wipe_and_recreate", tracking_wipe)
    generate.generate(ws)
    assert calls == [], "fast-path must not trigger any wipe"


def test_full_path_runs_when_no_stamp(tmp_path: Path) -> None:
    ws = _FakeWorkspace.make(tmp_path)
    # No stamp written — should trigger full generation.
    generate.generate(ws)

    # Stamp must be written.
    assert version_stamp.read(ws.state_dir) == ws.version

    # Destination dirs must exist (even with no source .md files).
    assert (tmp_path / ".claude" / "commands" / "daily-driver").is_dir()
    assert (tmp_path / ".claude" / "agents" / "daily-driver").is_dir()


def test_full_path_runs_when_stamp_differs(tmp_path: Path) -> None:
    ws = _FakeWorkspace.make(tmp_path, version="2.0.0")
    version_stamp.write(ws.state_dir, "1.0.0")
    generate.generate(ws)
    assert version_stamp.read(ws.state_dir) == "2.0.0"


def test_ignore_drift_runs_when_stamp_matches(tmp_path: Path) -> None:
    ws = _FakeWorkspace.make(tmp_path)
    version_stamp.write(ws.state_dir, ws.version)

    generate.generate(ws, ignore_drift=True)

    # Stamp still correct after forced re-run.
    assert version_stamp.read(ws.state_dir) == ws.version
    assert (tmp_path / ".claude" / "commands" / "daily-driver").is_dir()


def test_stamp_not_written_if_copy_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant: stamp is written last. A crash before write leaves stamp stale."""
    ws = _FakeWorkspace.make(tmp_path, version="2.0.0")
    version_stamp.write(ws.state_dir, "1.0.0")

    def _boom(state_dir: Path, version: str) -> None:
        raise RuntimeError("simulated crash before stamp")

    monkeypatch.setattr(version_stamp, "write", _boom)
    with pytest.raises(RuntimeError):
        generate.generate(ws)

    assert version_stamp.is_drifted(ws.state_dir, "2.0.0")


def test_stale_files_wiped_before_copy(tmp_path: Path) -> None:
    ws = _FakeWorkspace.make(tmp_path, version="2.0.0")
    version_stamp.write(ws.state_dir, "1.0.0")

    # Plant a stale file that should be wiped on next generate.
    stale_dir = tmp_path / ".claude" / "commands" / "daily-driver"
    stale_dir.mkdir(parents=True)
    stale_file = stale_dir / "stale.md"
    stale_file.write_text("old content", encoding="utf-8")

    generate.generate(ws)

    # Phase 1: no source .md files exist, so dest dir is empty after wipe.
    assert not stale_file.exists(), "stale file must be wiped by generate"
    assert stale_dir.is_dir(), "destination dir must be recreated"


def _worker(root: Path, version: str, results: multiprocessing.Queue[str]) -> None:  # type: ignore[type-arg]
    try:
        from daily_driver.core import generate
        from tests.test_core.test_generate import _FakeWorkspace

        ws = _FakeWorkspace(
            root=root,
            state_dir=root / ".daily-driver",
            version=version,
            logger=__import__("logging").getLogger("test.concurrent"),
            console=__import__("rich.console", fromlist=["Console"]).Console(
                stderr=True
            ),
        )
        generate.generate(ws)
        results.put("ok")
    except Exception as exc:
        results.put(f"error: {exc}")


# ---------------------------------------------------------------------------
# Package-data parity (Task P7.3: wheel-parity smoke + settings.json)
# ---------------------------------------------------------------------------


def test_package_data_resources_are_importable() -> None:
    """Package-data must be reachable via importlib.resources — catches MANIFEST.in /
    package-data / __init__.py gaps that would break a built wheel."""
    import importlib.resources as ir

    commands_pkg = ir.files("daily_driver.commands").joinpath("daily-driver")
    agents_pkg = ir.files("daily_driver.agents").joinpath("daily-driver")
    templates_pkg = ir.files("daily_driver.templates")

    assert (
        commands_pkg.is_dir()
    ), "daily_driver.commands.daily-driver missing from package"
    assert agents_pkg.is_dir(), "daily_driver.agents.daily-driver missing from package"
    assert templates_pkg.is_dir(), "daily_driver.templates missing from package"

    # settings.local.json.j2 must be shipped for generate() to render it.
    settings_tmpl = templates_pkg.joinpath("settings.local.json.j2")
    assert (
        settings_tmpl.is_file()
    ), "templates/settings.local.json.j2 missing from package"


def test_generate_renders_settings_json(tmp_path: Path) -> None:
    """settings.local.json is produced from the packaged template on generate."""
    ws = _FakeWorkspace.make(tmp_path, version="1.2.3")

    generate.generate(ws)

    settings_path = tmp_path / ".claude" / "settings.local.json"
    assert (
        settings_path.exists()
    ), "settings.local.json must be rendered on first generate"

    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert data["metadata"]["daily_driver_version"] == "1.2.3"
    assert "permissions" in data


def test_generate_drops_commands_removed_from_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Commands absent from the new package snapshot must vanish from .claude/commands/.

    Simulates a version-B release that no longer ships `old-command.md`. The stale
    file planted under version A must be removed, not silently retained alongside
    the new package manifest.
    """
    ws = _FakeWorkspace.make(tmp_path, version="2.0.0")
    version_stamp.write(ws.state_dir, "1.0.0")

    # Plant a stale file from the "version A" snapshot.
    commands_dest = tmp_path / ".claude" / "commands" / "daily-driver"
    commands_dest.mkdir(parents=True)
    (commands_dest / "old-command.md").write_text("dropped in v2", encoding="utf-8")

    # Simulate a version-B package that ships only one command.
    fake_pkg_root = tmp_path / "fake-package"
    fake_pkg_root.mkdir()
    (fake_pkg_root / "new-only.md").write_text("ships in v2", encoding="utf-8")

    real_files = generate.importlib.resources.files

    def fake_files(anchor: str):
        if anchor == "daily_driver.commands":

            class _Stub:
                def joinpath(self, name: str):
                    assert name == "daily-driver"
                    return fake_pkg_root

            return _Stub()
        return real_files(anchor)

    monkeypatch.setattr(generate.importlib.resources, "files", fake_files)

    generate.generate(ws)

    assert not (
        commands_dest / "old-command.md"
    ).exists(), "command dropped in new package snapshot must be wiped on regenerate"
    assert (commands_dest / "new-only.md").exists(), "new command must be copied in"


def test_concurrent_invocations_no_corruption(tmp_path: Path) -> None:
    """Two concurrent generate calls must both finish cleanly without corrupting the stamp."""
    state_dir = tmp_path / ".daily-driver"
    state_dir.mkdir()
    version = "1.0.0"

    results: multiprocessing.Queue[str] = multiprocessing.Queue()

    p1 = multiprocessing.Process(target=_worker, args=(tmp_path, version, results))
    p2 = multiprocessing.Process(target=_worker, args=(tmp_path, version, results))
    p1.start()
    p2.start()
    p1.join(timeout=15)
    p2.join(timeout=15)

    outcomes = [results.get_nowait() for _ in range(2)]
    assert outcomes == ["ok", "ok"], f"unexpected outcomes: {outcomes}"
    assert version_stamp.read(state_dir) == version


# ---------------------------------------------------------------------------
# Settings merge and manifest (Task #59: UserSafety)
# ---------------------------------------------------------------------------


def test_settings_merge_preserves_user_keys(tmp_path: Path) -> None:
    """settings.local.json regenerate must preserve user-added top-level keys."""
    ws = _FakeWorkspace.make(tmp_path, version="1.0.0")

    generate.generate(ws)

    settings_path = tmp_path / ".claude" / "settings.local.json"
    existing = json.loads(settings_path.read_text(encoding="utf-8"))
    existing["userKey"] = "preserved"
    settings_path.write_text(json.dumps(existing), encoding="utf-8")

    # Trigger regenerate by drifting the stamp.
    version_stamp.write(ws.state_dir, "0.8.0")
    generate.generate(ws)

    merged = json.loads(settings_path.read_text(encoding="utf-8"))
    assert merged.get("userKey") == "preserved", "user key must survive settings merge"
    assert "metadata" in merged, "package defaults must still be present"


def test_settings_merge_updates_version_on_upgrade(tmp_path: Path) -> None:
    """Package defaults (e.g. version) must be refreshed on regenerate."""
    ws_v1 = _FakeWorkspace.make(tmp_path, version="1.0.0")
    generate.generate(ws_v1)

    ws_v2 = _FakeWorkspace(
        root=tmp_path,
        state_dir=tmp_path / ".daily-driver",
        version="2.0.0",
        logger=ws_v1.logger,
        console=ws_v1.console,
    )
    version_stamp.write(ws_v2.state_dir, "1.0.0")
    generate.generate(ws_v2)

    settings_path = tmp_path / ".claude" / "settings.local.json"
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert data["metadata"]["daily_driver_version"] == "2.0.0"


def test_generate_records_manifest_for_copied_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SHA-256 entries must be written to manifest after generate copies .md files."""
    from daily_driver.core import manifest as _manifest

    ws = _FakeWorkspace.make(tmp_path, version="1.0.0")

    fake_pkg_root = tmp_path / "fake-commands"
    fake_pkg_root.mkdir()
    (fake_pkg_root / "hello.md").write_text("hello world", encoding="utf-8")

    real_files = generate.importlib.resources.files

    def fake_files(anchor: str):  # type: ignore[return]
        if anchor == "daily_driver.commands":

            class _Stub:
                def joinpath(self, name: str) -> object:
                    return fake_pkg_root

            return _Stub()
        return real_files(anchor)

    monkeypatch.setattr(generate.importlib.resources, "files", fake_files)
    generate.generate(ws)

    stored = _manifest.load(ws.state_dir)
    rel = ".claude/commands/daily-driver/hello.md"
    assert rel in stored, "manifest must record SHA for copied file"


# ---------------------------------------------------------------------------
# User-edit guard: ignore_drift vs force_overwrite are separate concerns
# ---------------------------------------------------------------------------


def _setup_fake_pkg_with_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    content: str,
) -> None:
    """Point importlib.resources at a fake commands package containing one file."""
    fake_pkg_root = tmp_path / "fake-pkg"
    fake_pkg_root.mkdir(exist_ok=True)
    (fake_pkg_root / filename).write_text(content, encoding="utf-8")

    real_files = generate.importlib.resources.files

    def fake_files(anchor: str):  # type: ignore[return]
        if anchor == "daily_driver.commands":

            class _Stub:
                def joinpath(self, name: str) -> object:
                    return fake_pkg_root

            return _Stub()
        return real_files(anchor)

    monkeypatch.setattr(generate.importlib.resources, "files", fake_files)


def test_ignore_drift_runs_even_when_stamp_matches(tmp_path: Path) -> None:
    """ignore_drift=True bypasses the stamp fast-path; force_overwrite not needed for this."""
    ws = _FakeWorkspace.make(tmp_path)
    version_stamp.write(ws.state_dir, ws.version)

    # Stamp matches — without ignore_drift this would be a no-op.
    generate.generate(ws, ignore_drift=True)

    # Dirs must be created even though stamp was current.
    assert (tmp_path / ".claude" / "commands" / "daily-driver").is_dir()


def test_force_overwrite_false_preserves_user_edited_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force_overwrite=False must preserve files the user has edited."""
    ws = _FakeWorkspace.make(tmp_path, version="1.0.0")
    _setup_fake_pkg_with_file(
        tmp_path, monkeypatch, "hello.md", "original package content"
    )

    # First generate — writes file and records manifest SHA.
    generate.generate(ws)

    dest_file = tmp_path / ".claude" / "commands" / "daily-driver" / "hello.md"
    assert dest_file.read_text(encoding="utf-8") == "original package content"

    # Simulate user edit.
    dest_file.write_text("user customization", encoding="utf-8")

    # Drift the stamp so the next call runs (ignore_drift=False still needs drift).
    version_stamp.write(ws.state_dir, "0.9.0")

    generate.generate(ws, ignore_drift=False, force_overwrite=False)

    # User edit must be preserved.
    assert dest_file.read_text(encoding="utf-8") == "user customization"


def test_force_overwrite_true_overwrites_user_edited_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force_overwrite=True must overwrite even user-edited package-managed files."""
    ws = _FakeWorkspace.make(tmp_path, version="1.0.0")
    _setup_fake_pkg_with_file(
        tmp_path, monkeypatch, "hello.md", "original package content"
    )

    # First generate.
    generate.generate(ws)

    dest_file = tmp_path / ".claude" / "commands" / "daily-driver" / "hello.md"
    # Simulate user edit.
    dest_file.write_text("user customization", encoding="utf-8")

    # force_overwrite=True + ignore_drift=True (simulates --reset).
    generate.generate(ws, ignore_drift=True, force_overwrite=True)

    assert dest_file.read_text(encoding="utf-8") == "original package content"


def test_ignore_drift_true_force_overwrite_false_preserves_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ignore_drift=True with force_overwrite=False skips drift check but still
    respects user edits — the two booleans are independently controlled."""
    ws = _FakeWorkspace.make(tmp_path, version="1.0.0")
    _setup_fake_pkg_with_file(tmp_path, monkeypatch, "hello.md", "package content")

    generate.generate(ws)

    dest_file = tmp_path / ".claude" / "commands" / "daily-driver" / "hello.md"
    dest_file.write_text("user edit", encoding="utf-8")

    # Stamp still matches current version (no drift), but ignore_drift skips that check.
    version_stamp.write(ws.state_dir, ws.version)
    generate.generate(ws, ignore_drift=True, force_overwrite=False)

    assert dest_file.read_text(encoding="utf-8") == "user edit"
