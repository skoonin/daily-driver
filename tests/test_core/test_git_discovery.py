from __future__ import annotations

from pathlib import Path

from daily_driver.core.git_discovery import discover_repos


def _make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()
    return path


def test_discover_repos_empty_when_no_repos(tmp_path):
    (tmp_path / "empty").mkdir()
    assert discover_repos([tmp_path / "empty"]) == []


def test_discover_repos_finds_repo_at_depth_zero(tmp_path):
    repo = _make_repo(tmp_path / "myrepo")
    assert discover_repos([repo]) == [repo.resolve()]


def test_discover_repos_finds_repos_under_root(tmp_path):
    _make_repo(tmp_path / "a")
    _make_repo(tmp_path / "sub" / "b")
    found = discover_repos([tmp_path])
    assert {p.name for p in found} == {"a", "b"}


def test_discover_repos_respects_max_depth(tmp_path):
    # Repo at depth 5 from root; with max_depth=4, should be missed.
    deep = tmp_path / "a" / "b" / "c" / "d" / "deep-repo"
    _make_repo(deep)
    # Repo at depth 1: should be found at max_depth=4.
    shallow = _make_repo(tmp_path / "shallow")
    found = discover_repos([tmp_path], max_depth=4)
    assert shallow.resolve() in found
    assert deep.resolve() not in found


def test_discover_repos_does_not_descend_into_repo(tmp_path):
    # A repo that itself contains a nested .git/ shouldn't yield two results.
    outer = _make_repo(tmp_path / "outer")
    (outer / "vendor").mkdir()
    (outer / "vendor" / ".git").mkdir()
    found = discover_repos([tmp_path])
    assert found == [outer.resolve()]


def test_discover_repos_skips_dotdirs_and_symlinks(tmp_path):
    _make_repo(tmp_path / ".cache" / "hidden-repo")
    real = _make_repo(tmp_path / "real")
    link = tmp_path / "link"
    link.symlink_to(real)
    found = discover_repos([tmp_path])
    # `real` is found via direct walk; the symlink is skipped (no double-count).
    assert found == [real.resolve()]


def test_discover_repos_skips_missing_root(tmp_path):
    missing = tmp_path / "does-not-exist"
    real = _make_repo(tmp_path / "real")
    found = discover_repos([missing, tmp_path])
    assert real.resolve() in found


def test_discover_repos_dedupes_overlapping_roots(tmp_path):
    repo = _make_repo(tmp_path / "shared" / "repo")
    found = discover_repos([tmp_path, tmp_path / "shared"])
    assert found == [repo.resolve()]
