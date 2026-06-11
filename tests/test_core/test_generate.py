from __future__ import annotations

import json
import logging
import multiprocessing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

    commands_pkg = ir.files("daily_driver.resources.slash_commands").joinpath(
        "daily-driver"
    )
    agents_pkg = ir.files("daily_driver.resources.agents").joinpath("daily-driver")
    templates_pkg = ir.files("daily_driver.resources.templates")

    assert (
        commands_pkg.is_dir()
    ), "daily_driver.resources.slash_commands.daily-driver missing from package"
    assert (
        agents_pkg.is_dir()
    ), "daily_driver.resources.agents.daily-driver missing from package"
    assert (
        templates_pkg.is_dir()
    ), "daily_driver.resources.templates missing from package"

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
        if anchor == "daily_driver.resources.slash_commands":

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


def _wipe_counting_worker(
    root: Path,
    version: str,
    results: multiprocessing.Queue[str],  # type: ignore[type-arg]
    wipe_calls: Any,
) -> None:
    """Worker that patches _wipe_and_recreate to count invocations via a shared Manager list."""
    try:
        from unittest.mock import patch

        from daily_driver.core import generate as gen_mod
        from tests.test_core.test_generate import _FakeWorkspace

        real_wipe = gen_mod._wipe_and_recreate

        def counting_wipe(path: Path) -> None:
            wipe_calls.append(1)
            real_wipe(path)

        import logging

        from rich.console import Console as RichConsole

        ws = _FakeWorkspace(
            root=root,
            state_dir=root / ".daily-driver",
            version=version,
            logger=logging.getLogger("test.wipe_count"),
            console=RichConsole(stderr=True),
        )
        with patch.object(gen_mod, "_wipe_and_recreate", counting_wipe):
            gen_mod.generate(ws, ignore_drift=False, force_overwrite=True)
        results.put("ok")
    except Exception as exc:
        results.put(f"error: {exc}")


def test_concurrent_invocations_only_one_wipe(tmp_path: Path) -> None:
    """Double-checked locking: only one process runs the generate body.

    Both processes see a stale stamp before entering the lock. After the
    first process generates and writes the stamp, the second re-checks drift
    inside the lock and short-circuits. On the force_overwrite path
    _wipe_and_recreate must be called exactly three times total (commands +
    agents + hooks from one process, not six).
    """
    state_dir = tmp_path / ".daily-driver"
    state_dir.mkdir()
    version = "2.0.0"

    manager = multiprocessing.Manager()
    wipe_calls = manager.list()  # ListProxy; supports len() and append()
    results: multiprocessing.Queue[str] = multiprocessing.Queue()

    p1 = multiprocessing.Process(
        target=_wipe_counting_worker, args=(tmp_path, version, results, wipe_calls)
    )
    p2 = multiprocessing.Process(
        target=_wipe_counting_worker, args=(tmp_path, version, results, wipe_calls)
    )
    p1.start()
    p2.start()
    p1.join(timeout=15)
    p2.join(timeout=15)

    outcomes = [results.get_nowait() for _ in range(2)]
    assert outcomes == ["ok", "ok"], f"unexpected outcomes: {outcomes}"

    # _wipe_and_recreate runs once each for commands/, agents/, and hooks/.
    # If the double-check fails, both processes would wipe (total 6 calls).
    total_wipes = len(wipe_calls)
    assert total_wipes == 3, (
        f"expected 3 wipe calls (one process), got {total_wipes} — "
        "double-checked locking may be missing or broken"
    )


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
        if anchor == "daily_driver.resources.slash_commands":

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
        if anchor == "daily_driver.resources.slash_commands":

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


# ---------------------------------------------------------------------------
# Workspace README lifecycle
# ---------------------------------------------------------------------------


def test_workspace_readme_manifest_recorded(tmp_path: Path) -> None:
    """generate() must write README.md to workspace root and record its SHA."""
    from daily_driver.core import manifest as _manifest

    ws = _FakeWorkspace.make(tmp_path, version="1.0.0")
    generate.generate(ws, ignore_drift=True, force_overwrite=True)

    readme = tmp_path / "README.md"
    assert readme.exists(), "README.md must exist in workspace root after generate"
    assert "daily-driver" in readme.read_text(encoding="utf-8")

    stored = _manifest.load(ws.state_dir)
    assert "README.md" in stored, "manifest must record SHA for README.md"


# ---------------------------------------------------------------------------
# Narrowed fallback catches (W8): silent install corruption / user-data loss
# ---------------------------------------------------------------------------


def test_render_settings_jinja_error_logged_and_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """A syntax error in settings.local.json.j2 must surface, not be swallowed.

    Previously a bare ``except Exception`` skipped writing settings.local.json while
    init/doctor still reported success. The narrowed catch only handles
    (jinja2.TemplateError, OSError); a template *syntax* error raises
    jinja2.TemplateSyntaxError, which is a TemplateError, so it is caught and logged
    rather than silently producing nothing — and the warning names the target path.
    """
    import jinja2

    ws = _FakeWorkspace.make(tmp_path, version="1.0.0")

    real_files = generate.importlib.resources.files

    def fake_files(anchor: str):  # type: ignore[return]
        if anchor == "daily_driver.resources.templates":

            class _Stub:
                def joinpath(self, name: str) -> object:
                    if name == "settings.local.json.j2":

                        class _Tmpl:
                            def read_text(self, encoding: str = "utf-8") -> str:
                                # Unterminated block tag — invalid Jinja2 syntax.
                                return "{% if %}"

                        return _Tmpl()
                    return real_files(anchor).joinpath(name)

            return _Stub()
        return real_files(anchor)

    monkeypatch.setattr(generate.importlib.resources, "files", fake_files)

    # The syntax error is a TemplateSyntaxError — confirm the parser would raise it,
    # proving the narrowed catch is responsible for the (logged) skip, not a no-op.
    with pytest.raises(jinja2.TemplateError):
        jinja2.Environment(undefined=jinja2.StrictUndefined).from_string("{% if %}")

    settings_path = tmp_path / ".claude" / "settings.local.json"
    with caplog.at_level(logging.WARNING, logger="daily_driver"):
        generate._render_settings(ws)

    assert (
        not settings_path.exists()
    ), "broken template must not silently produce a settings file"
    assert any(
        "settings.local.json" in r.getMessage() for r in caplog.records
    ), "template error must be logged at WARNING, not silently swallowed"


def test_render_settings_unexpected_error_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An error outside (TemplateError, OSError) must propagate, not be masked."""
    ws = _FakeWorkspace.make(tmp_path, version="1.0.0")

    real_files = generate.importlib.resources.files

    def fake_files(anchor: str):  # type: ignore[return]
        if anchor == "daily_driver.resources.templates":

            class _Stub:
                def joinpath(self, name: str) -> object:
                    if name == "settings.local.json.j2":

                        class _Tmpl:
                            def read_text(self, encoding: str = "utf-8") -> str:
                                return "{{ workspace.version }}"

                        return _Tmpl()
                    return real_files(anchor).joinpath(name)

            return _Stub()
        return real_files(anchor)

    monkeypatch.setattr(generate.importlib.resources, "files", fake_files)

    def _boom(*args: object, **kwargs: object) -> str:
        raise ValueError("unexpected render failure")

    # Patch the StrictUndefined render path to raise a non-narrowed error.
    import jinja2

    monkeypatch.setattr(jinja2.Template, "render", _boom)

    with pytest.raises(ValueError, match="unexpected render failure"):
        generate._render_settings(ws)


def test_merge_settings_backs_up_malformed_file(tmp_path: Path, caplog) -> None:
    """A malformed existing settings.local.json must be copied to .invalid before discard."""
    ws = _FakeWorkspace.make(tmp_path, version="1.0.0")

    # First generate writes a valid settings file.
    generate.generate(ws)

    settings_path = tmp_path / ".claude" / "settings.local.json"
    corrupt = "{ this is not valid json"
    settings_path.write_text(corrupt, encoding="utf-8")

    version_stamp.write(ws.state_dir, "0.8.0")
    with caplog.at_level(logging.WARNING, logger="daily_driver"):
        generate.generate(ws)

    backup_path = tmp_path / ".claude" / "settings.local.json.invalid"
    assert backup_path.exists(), "malformed settings file must be backed up to .invalid"
    assert (
        backup_path.read_text(encoding="utf-8") == corrupt
    ), "backup must preserve the original malformed bytes verbatim"

    # The replacement file must now be valid defaults.
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "metadata" in data

    assert any(
        "settings.local.json.invalid" in r.getMessage() for r in caplog.records
    ), "backup must be logged at WARNING with the backup path"


def test_render_initial_config_missing_template_raises_workspace_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A *missing* .dd-config.yaml.j2 means a broken wheel; init must fail loudly
    (WorkspaceError) rather than silently scaffold a half-configured workspace.

    (A template *render* error still degrades to the minimal fallback; that path
    is covered in tests/test_core/test_workspace.py.)
    """
    from daily_driver.core import workspace as ws_mod
    from daily_driver.core.workspace import WorkspaceError

    real_files = ws_mod.importlib.resources.files

    def fake_files(anchor: str):  # type: ignore[return]
        if anchor == "daily_driver.resources.templates":

            class _Stub:
                def joinpath(self, name: str) -> object:
                    if name == ".dd-config.yaml.j2":

                        class _Missing:
                            def read_text(self, encoding: str = "utf-8") -> str:
                                raise FileNotFoundError(name)

                        return _Missing()
                    return real_files(anchor).joinpath(name)

            return _Stub()
        return real_files(anchor)

    monkeypatch.setattr(ws_mod.importlib.resources, "files", fake_files)

    with pytest.raises(WorkspaceError, match="wheel is broken"):
        ws_mod._render_initial_config()


# Plugin package-data extension point (W15). job_search ships no slash-commands,
# so a synthetic plugin exercises the plugin branch of the package-data walk.


def test_synthetic_plugin_package_data_copied_and_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plugin's package_data_dirs entry copies its .md and records it in the manifest."""
    from daily_driver.core import manifest as _manifest
    from daily_driver.plugins._base import PackageDataDir

    ws = _FakeWorkspace.make(tmp_path, version="1.0.0")

    plugin_src = tmp_path / "plugin-commands"
    plugin_src.mkdir()
    (plugin_src / "plugin-cmd.md").write_text("plugin command", encoding="utf-8")

    plugin_dir = PackageDataDir("fake.plugin.commands", "commands/fake-plugin")
    monkeypatch.setattr(
        generate,
        "_package_data_dirs",
        lambda: [*generate._CORE_PACKAGE_DATA, plugin_dir],
    )

    real_files = generate.importlib.resources.files

    def fake_files(anchor: str):  # type: ignore[return]
        if anchor == "fake.plugin.commands":

            class _Stub:
                def joinpath(self, name: str) -> object:
                    assert name == "fake-plugin"
                    return plugin_src

            return _Stub()
        return real_files(anchor)

    monkeypatch.setattr(generate.importlib.resources, "files", fake_files)
    generate.generate(ws)

    dest = tmp_path / ".claude" / "commands" / "fake-plugin" / "plugin-cmd.md"
    assert dest.is_file(), "plugin .md must land under its declared dest"
    assert dest.read_text(encoding="utf-8") == "plugin command"

    stored = _manifest.load(ws.state_dir)
    rel = ".claude/commands/fake-plugin/plugin-cmd.md"
    assert rel in stored, "plugin .md must be recorded in the SHA-256 manifest"


# ---------------------------------------------------------------------------
# Hook scripts join the SHA-256 manifest contract (W1.2)
# ---------------------------------------------------------------------------


def _point_hooks_pkg_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *files: tuple[str, str]
) -> Path:
    """Make generate read its hook scripts from a fake package dir, not the wheel.

    _copy_hooks resolves importlib.resources.files("...templates").joinpath("hooks");
    redirect only that joinpath so the real templates package (settings, contract
    templates) keeps working.
    """
    fake_hooks = tmp_path / "fake-hooks"
    fake_hooks.mkdir(exist_ok=True)
    for name, content in files:
        (fake_hooks / name).write_text(content, encoding="utf-8")

    real_files = generate.importlib.resources.files

    def fake_files(anchor: str):  # type: ignore[return]
        if anchor == "daily_driver.resources.templates":
            real = real_files(anchor)

            class _Stub:
                def joinpath(self, name: str) -> object:
                    if name == "hooks":
                        return fake_hooks
                    return real.joinpath(name)

            return _Stub()
        return real_files(anchor)

    monkeypatch.setattr(generate.importlib.resources, "files", fake_files)
    return fake_hooks


def test_user_edited_hook_survives_drift_regenerate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user edit to a managed hook script must survive a drift-triggered generate."""
    ws = _FakeWorkspace.make(tmp_path, version="1.0.0")
    _point_hooks_pkg_at(tmp_path, monkeypatch, ("hook-x.sh", "#!/bin/sh\noriginal\n"))

    generate.generate(ws)

    hook = tmp_path / ".claude" / "hooks" / "hook-x.sh"
    assert hook.read_text(encoding="utf-8") == "#!/bin/sh\noriginal\n"

    # Simulate a user edit, then force drift.
    hook.write_text("#!/bin/sh\noriginal\n# USER MARKER\n", encoding="utf-8")
    version_stamp.write(ws.state_dir, "0.9.0")

    generate.generate(ws, ignore_drift=False, force_overwrite=False)

    assert "# USER MARKER" in hook.read_text(
        encoding="utf-8"
    ), "user edit to hook script must be preserved across a drift regenerate"


def test_pristine_hook_updated_on_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unedited hook must be refreshed when the package ships new content."""
    ws = _FakeWorkspace.make(tmp_path, version="1.0.0")
    fake_hooks = _point_hooks_pkg_at(
        tmp_path, monkeypatch, ("hook-x.sh", "#!/bin/sh\nv1\n")
    )

    generate.generate(ws)

    hook = tmp_path / ".claude" / "hooks" / "hook-x.sh"
    assert hook.read_text(encoding="utf-8") == "#!/bin/sh\nv1\n"

    # New package content, no user edit, drift the stamp.
    (fake_hooks / "hook-x.sh").write_text("#!/bin/sh\nv2\n", encoding="utf-8")
    version_stamp.write(ws.state_dir, "0.9.0")

    generate.generate(ws, ignore_drift=False, force_overwrite=False)

    assert (
        hook.read_text(encoding="utf-8") == "#!/bin/sh\nv2\n"
    ), "pristine hook must be updated from the new package snapshot"


def test_hook_recorded_in_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hook scripts must be recorded in the SHA-256 manifest after generate."""
    from daily_driver.core import manifest as _manifest

    ws = _FakeWorkspace.make(tmp_path, version="1.0.0")
    _point_hooks_pkg_at(tmp_path, monkeypatch, ("hook-x.sh", "#!/bin/sh\nbody\n"))

    generate.generate(ws)

    stored = _manifest.load(ws.state_dir)
    rel = ".claude/hooks/hook-x.sh"
    assert rel in stored, "hook script must be recorded in the SHA-256 manifest"


def test_force_overwrite_replaces_user_edited_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force_overwrite=True (doctor --reset) must overwrite a user-edited hook."""
    ws = _FakeWorkspace.make(tmp_path, version="1.0.0")
    _point_hooks_pkg_at(tmp_path, monkeypatch, ("hook-x.sh", "#!/bin/sh\noriginal\n"))

    generate.generate(ws)

    hook = tmp_path / ".claude" / "hooks" / "hook-x.sh"
    hook.write_text("#!/bin/sh\nuser edit\n", encoding="utf-8")

    generate.generate(ws, ignore_drift=True, force_overwrite=True)

    assert (
        hook.read_text(encoding="utf-8") == "#!/bin/sh\noriginal\n"
    ), "force_overwrite must reset a user-edited hook to package content"


def test_preserved_hook_counted_in_generation_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user-edited hook skipped on drift must increment GenerationResult.n_preserved."""
    ws = _FakeWorkspace.make(tmp_path, version="1.0.0")
    _point_hooks_pkg_at(tmp_path, monkeypatch, ("hook-x.sh", "#!/bin/sh\noriginal\n"))

    generate.generate(ws)

    hook = tmp_path / ".claude" / "hooks" / "hook-x.sh"
    hook.write_text("#!/bin/sh\nuser edit\n", encoding="utf-8")
    version_stamp.write(ws.state_dir, "0.9.0")

    result = generate.generate(ws, ignore_drift=False, force_overwrite=False)

    assert result is not None
    assert (
        result.n_preserved >= 1
    ), "preserved hook must be folded into n_preserved, not invisible"


def test_stale_hook_removed_on_regenerate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hook dropped from package data must be reaped from .claude/hooks/ on regenerate."""
    ws = _FakeWorkspace.make(tmp_path, version="1.0.0")
    fake_hooks = _point_hooks_pkg_at(
        tmp_path,
        monkeypatch,
        ("hook-keep.sh", "#!/bin/sh\nkeep\n"),
        ("hook-drop.sh", "#!/bin/sh\ndrop\n"),
    )

    generate.generate(ws)

    drop = tmp_path / ".claude" / "hooks" / "hook-drop.sh"
    assert drop.is_file()

    # New package snapshot no longer ships hook-drop.sh.
    (fake_hooks / "hook-drop.sh").unlink()
    version_stamp.write(ws.state_dir, "0.9.0")

    generate.generate(ws, ignore_drift=False, force_overwrite=False)

    assert (
        not drop.exists()
    ), "hook absent from new package snapshot must be removed, not left as a zombie"
    assert (
        tmp_path / ".claude" / "hooks" / "hook-keep.sh"
    ).is_file(), "shipped hook must remain"


def test_user_edited_stale_hook_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user-edited hook dropped from package data must be preserved (matching .md)."""
    ws = _FakeWorkspace.make(tmp_path, version="1.0.0")
    fake_hooks = _point_hooks_pkg_at(
        tmp_path, monkeypatch, ("hook-drop.sh", "#!/bin/sh\noriginal\n")
    )

    generate.generate(ws)

    drop = tmp_path / ".claude" / "hooks" / "hook-drop.sh"
    drop.write_text("#!/bin/sh\nuser edit\n", encoding="utf-8")

    # Drop it from the package and regenerate on drift.
    (fake_hooks / "hook-drop.sh").unlink()
    version_stamp.write(ws.state_dir, "0.9.0")

    generate.generate(ws, ignore_drift=False, force_overwrite=False)

    assert drop.exists(), "user-edited hook absent from package must be preserved"
    assert drop.read_text(encoding="utf-8") == "#!/bin/sh\nuser edit\n"


def test_two_sources_sharing_a_dest_keep_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plugin co-located with core in one dest must not drop the other's files.

    Guards the union stale-removal: the second source's files are not treated as
    stale relative to the first source's package snapshot.
    """
    ws = _FakeWorkspace.make(tmp_path, version="1.0.0")

    core_src = tmp_path / "core-commands"
    core_src.mkdir()
    (core_src / "core-cmd.md").write_text("core", encoding="utf-8")

    plugin_src = tmp_path / "plugin-commands"
    plugin_src.mkdir()
    (plugin_src / "plugin-cmd.md").write_text("plugin", encoding="utf-8")

    from daily_driver.plugins._base import PackageDataDir

    shared = "commands/daily-driver"
    monkeypatch.setattr(
        generate,
        "_package_data_dirs",
        lambda: [
            PackageDataDir("fake.core.commands", shared),
            PackageDataDir("fake.plugin.commands", shared),
        ],
    )

    real_files = generate.importlib.resources.files

    def fake_files(anchor: str):  # type: ignore[return]
        mapping = {
            "fake.core.commands": core_src,
            "fake.plugin.commands": plugin_src,
        }
        if anchor in mapping:
            target = mapping[anchor]

            class _Stub:
                def joinpath(self, name: str) -> object:
                    return target

            return _Stub()
        return real_files(anchor)

    monkeypatch.setattr(generate.importlib.resources, "files", fake_files)
    generate.generate(ws)

    dest_dir = tmp_path / ".claude" / "commands" / "daily-driver"
    assert (dest_dir / "core-cmd.md").is_file()
    assert (
        dest_dir / "plugin-cmd.md"
    ).is_file(), "co-located source must not be treated as stale"
