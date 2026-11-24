from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from daily_driver.gathers.git import GitCommit, gather_commits
from daily_driver.integrations.git import GitCommandError


def _make_run_stub(stdout="", stderr="", rc=0):
    def _run(args, **kw):
        return subprocess.CompletedProcess(
            args=args, returncode=rc, stdout=stdout, stderr=stderr
        )

    return _run


_SINCE = datetime(2026, 4, 1, 0, 0, 0)
_UNTIL = datetime(2026, 4, 20, 23, 59, 59)
_REPO = Path("/some/repo")


def _commit_line(
    sha="abc1234",
    ts="2026-04-10T09:00:00+00:00",
    author="Alice",
    subject="Initial commit",
):
    return f"{sha}\x00{ts}\x00{author}\x00{subject}"


def test_gather_commits_parses_nul_separated_output(monkeypatch):
    lines = "\n".join(
        [
            _commit_line(
                "abc1234", "2026-04-10T09:00:00+00:00", "Alice", "feat: add login"
            ),
            _commit_line(
                "def5678", "2026-04-11T10:30:00+00:00", "Bob", "fix: null pointer"
            ),
            _commit_line(
                "ghi9012", "2026-04-12T14:00:00+00:00", "Carol", "chore: update deps"
            ),
        ]
    )
    monkeypatch.setattr(
        "daily_driver.integrations.git.subprocess.run", _make_run_stub(stdout=lines)
    )

    commits = gather_commits(_REPO, _SINCE)

    assert len(commits) == 3
    assert isinstance(commits[0], GitCommit)
    assert commits[0].sha == "abc1234"
    assert commits[0].author == "Alice"
    assert commits[0].subject == "feat: add login"
    assert commits[1].sha == "def5678"
    assert commits[2].author == "Carol"


def test_gather_commits_returns_empty_for_non_git_dir(monkeypatch):
    monkeypatch.setattr(
        "daily_driver.integrations.git.subprocess.run",
        _make_run_stub(
            rc=128,
            stderr="fatal: not a git repository (or any of the parent directories)",
        ),
    )

    result = gather_commits(_REPO, _SINCE)

    assert result == []


def test_gather_commits_raises_on_other_git_error(monkeypatch):
    monkeypatch.setattr(
        "daily_driver.integrations.git.subprocess.run",
        _make_run_stub(rc=1, stderr="fatal: bad revision 'X'"),
    )

    with pytest.raises(GitCommandError):
        gather_commits(_REPO, _SINCE)


def test_gather_commits_empty_output(monkeypatch):
    monkeypatch.setattr(
        "daily_driver.integrations.git.subprocess.run", _make_run_stub(stdout="")
    )

    result = gather_commits(_REPO, _SINCE)

    assert result == []


def test_gather_commits_passes_until_when_provided(monkeypatch):
    captured = {}

    def _recording_run(args, **kw):
        captured["args"] = args
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr("daily_driver.integrations.git.subprocess.run", _recording_run)

    gather_commits(_REPO, _SINCE, until=_UNTIL)
    assert "--until" in captured["args"]

    gather_commits(_REPO, _SINCE)
    assert "--until" not in captured["args"]


def test_gather_commits_handles_subject_with_special_chars(monkeypatch):
    # NUL separator means pipes, commas, and embedded text that looks like separators are safe in subject
    subject = "fix: handle edge case | commas, slashes / and more"
    line = _commit_line("aaa0001", "2026-04-15T08:00:00+00:00", "Dev", subject)
    monkeypatch.setattr(
        "daily_driver.integrations.git.subprocess.run", _make_run_stub(stdout=line)
    )

    commits = gather_commits(_REPO, _SINCE)

    assert len(commits) == 1
    assert commits[0].subject == subject


_needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@_needs_git
def test_gather_commits_returns_empty_for_repo_with_no_commits(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    assert gather_commits(tmp_path, _SINCE) == []


@_needs_git
def test_gather_commits_reads_no_commits_under_a_translated_locale(
    tmp_path, monkeypatch
):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    probe = subprocess.run(
        ["git", "-C", str(tmp_path), "log"], capture_output=True, text=True
    )
    if "does not have any commits yet" in probe.stderr:
        pytest.skip("git prints English under de_DE here; the locale is missing")

    assert gather_commits(tmp_path, _SINCE) == []


@_needs_git
def test_gather_commits_raises_on_a_broken_gitfile(tmp_path):
    (tmp_path / ".git").write_text("")

    with pytest.raises(GitCommandError) as excinfo:
        gather_commits(tmp_path, _SINCE)

    assert "gitfile" in excinfo.value.stderr
