"""set_model changes one value in a program's JSON config and no other byte."""

from __future__ import annotations

import errno
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from daily_driver.core import model_file
from daily_driver.core.model_file import ModelFileError, set_model


def _set(tmp_path: Path, original: str, model: str = "new/model") -> str:
    path = tmp_path / "opencode.json"
    path.write_bytes(original.encode("utf-8"))
    set_model(path, "model", model)
    return path.read_bytes().decode("utf-8")


def test_missing_file_is_created_with_only_the_key(tmp_path: Path) -> None:
    path = tmp_path / "opencode.json"

    set_model(path, "model", "anthropic/claude-sonnet-5-5")

    assert [entry.name for entry in tmp_path.iterdir()] == ["opencode.json"]
    assert json.loads(path.read_text()) == {"model": "anthropic/claude-sonnet-5-5"}


def test_missing_parent_folder_is_created(tmp_path: Path) -> None:
    path = tmp_path / "cfg" / "tool.json"

    set_model(path, "model", "m")

    assert json.loads(path.read_text()) == {"model": "m"}


def test_only_the_value_changes(tmp_path: Path) -> None:
    original = (
        '{\r\n    "$schema": "https://opencode.ai/config.json",\r\n'
        '    "model": "old/model",\r\n    "note": "café \\u2014 a\\/b",\r\n'
        '    "limit": 1.0e2,\r\n    "provider": {"a": {"b": 1}}\r\n}'
    )

    assert _set(tmp_path, original) == original.replace("old/model", "new/model")


def test_a_nested_key_of_the_same_name_is_not_touched(tmp_path: Path) -> None:
    original = '{"agent": {"build": {"model": "x/y"}}, "model": "old/model"}'

    assert _set(tmp_path, original) == original.replace("old/model", "new/model")


def test_a_non_string_value_is_replaced_whole(tmp_path: Path) -> None:
    assert _set(tmp_path, '{"model": null, "a": 1}') == '{"model": "new/model", "a": 1}'


def test_the_last_of_duplicate_keys_is_the_one_changed(tmp_path: Path) -> None:
    # json.loads, like opencode's parser, keeps the last duplicate.
    assert (
        _set(tmp_path, '{"model": "a", "model": "b"}')
        == '{"model": "a", "model": "new/model"}'
    )


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        (
            '{\n  "share": "manual"\n}\n',
            '{\n  "share": "manual",\n  "model": "new/model"\n}\n',
        ),
        ('{"share":"manual"}', '{"share":"manual", "model": "new/model"}'),
        ("{}", '{"model": "new/model"}'),
        ("{ }\n", '{"model": "new/model" }\n'),
        (
            '{\r\n  "share": "manual"\r\n}\r\n',
            '{\r\n  "share": "manual",\r\n  "model": "new/model"\r\n}\r\n',
        ),
    ],
    ids=["indented", "compact", "empty", "empty-spaced", "crlf"],
)
def test_a_missing_key_is_added_last_in_the_files_layout(
    tmp_path: Path, original: str, expected: str
) -> None:
    assert _set(tmp_path, original) == expected


def test_a_matching_value_is_not_written(tmp_path: Path) -> None:
    # The escaped slash would be respelled by any write, and a write makes a
    # new file, so the bytes and the inode show the file was left alone.
    path = tmp_path / "opencode.json"
    original = '{"share":"manual",   "model": "same\\/model"}'
    path.write_text(original)
    inode = path.stat().st_ino

    set_model(path, "model", "same/model")

    assert path.read_text() == original
    assert path.stat().st_ino == inode


@pytest.mark.parametrize(("umask", "mode"), [(0o022, 0o644), (0o077, 0o600)])
def test_a_new_file_follows_the_umask(tmp_path: Path, umask: int, mode: int) -> None:
    before = os.umask(umask)
    try:
        set_model(tmp_path / "opencode.json", "model", "m")
        after = os.umask(umask)
    finally:
        os.umask(before)

    assert stat.S_IMODE((tmp_path / "opencode.json").stat().st_mode) == mode
    assert after == umask


def test_a_file_made_by_another_launch_meanwhile_is_kept_and_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "opencode.json"
    real_link = os.link

    def other_launch_first(src: str, dst: str) -> None:
        Path(dst).write_text('{"model": "other", "x": 1}')
        real_link(src, dst)

    monkeypatch.setattr(model_file.os, "link", other_launch_first)

    set_model(path, "model", "m")

    assert path.read_text() == '{"model": "m", "x": 1}'
    assert [entry.name for entry in tmp_path.iterdir()] == ["opencode.json"]


def test_a_failed_create_leaves_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def disk_full(path: str, mode: int) -> None:
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(model_file.os, "chmod", disk_full)

    with pytest.raises(OSError, match="disk full"):
        set_model(tmp_path / "opencode.json", "model", "m")

    assert list(tmp_path.iterdir()) == []


def _no_hard_links(src: str, dst: str) -> None:
    raise OSError(errno.ENOTSUP, "Operation not supported")


def test_a_folder_without_hard_links_still_gets_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_file.os, "link", _no_hard_links)
    before = os.umask(0o022)
    try:
        set_model(tmp_path / "opencode.json", "model", "m")
    finally:
        os.umask(before)

    assert (tmp_path / "opencode.json").read_text() == '{"model": "m"}\n'
    assert stat.S_IMODE((tmp_path / "opencode.json").stat().st_mode) == 0o644
    assert [entry.name for entry in tmp_path.iterdir()] == ["opencode.json"]


def test_a_failed_create_without_hard_links_leaves_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def disk_full(fd: int, data: bytes) -> int:
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(model_file.os, "link", _no_hard_links)
    monkeypatch.setattr(model_file.os, "write", disk_full)

    with pytest.raises(OSError, match="disk full"):
        set_model(tmp_path / "opencode.json", "model", "m")

    assert list(tmp_path.iterdir()) == []


def test_a_race_that_never_ends_is_an_error_not_a_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def always_taken(src: str, dst: str) -> None:
        raise FileExistsError(errno.EEXIST, "File exists", dst)

    monkeypatch.setattr(model_file.os, "link", always_taken)

    with pytest.raises(ModelFileError, match="another launch.*run the command again"):
        set_model(tmp_path / "opencode.json", "model", "m")

    assert list(tmp_path.iterdir()) == []


def test_a_file_made_meanwhile_on_a_folder_without_hard_links_is_kept_and_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "opencode.json"
    real_open = os.open

    def other_launch_first(file: Any, flags: int, *args: Any) -> int:
        # Only the create of the target: mkstemp also calls os.open.
        if Path(file) == path:
            path.write_text('{"model": "other", "x": 1}')
        return real_open(file, flags, *args)

    monkeypatch.setattr(model_file.os, "link", _no_hard_links)
    monkeypatch.setattr(model_file.os, "open", other_launch_first)

    set_model(path, "model", "m")

    assert path.read_text() == '{"model": "m", "x": 1}'
    assert [entry.name for entry in tmp_path.iterdir()] == ["opencode.json"]


def test_a_link_to_a_missing_file_is_refused(tmp_path: Path) -> None:
    link = tmp_path / "opencode.json"
    link.symlink_to(tmp_path / "elsewhere" / "gone.json")

    with pytest.raises(ModelFileError, match="link to a missing file"):
        set_model(link, "model", "m")

    assert not (tmp_path / "elsewhere").exists()


def test_a_symlink_stays_a_link_and_its_target_changes(tmp_path: Path) -> None:
    target = tmp_path / "dotfiles.json"
    target.write_text('{"model": "old/model"}')
    link = tmp_path / "opencode.json"
    link.symlink_to(target)

    set_model(link, "model", "new/model")

    assert link.is_symlink()
    assert target.read_text() == '{"model": "new/model"}'


def test_the_file_mode_is_kept(tmp_path: Path) -> None:
    path = tmp_path / "opencode.json"
    path.write_text('{"model": "old/model"}')
    path.chmod(0o640)

    set_model(path, "model", "new/model")

    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_no_temporary_file_is_left_behind(tmp_path: Path) -> None:
    path = tmp_path / "opencode.json"
    path.write_text('{"model": "old/model"}')

    set_model(path, "model", "new/model")

    assert [entry.name for entry in tmp_path.iterdir()] == ["opencode.json"]


def test_a_failed_write_leaves_the_file_and_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "opencode.json"
    path.write_text('{"model": "old/model"}')

    def fail(src: str, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(model_file.os, "replace", fail)

    with pytest.raises(OSError, match="disk full"):
        set_model(path, "model", "new/model")

    assert [entry.name for entry in tmp_path.iterdir()] == ["opencode.json"]
    assert path.read_text() == '{"model": "old/model"}'


@pytest.mark.parametrize(
    "content",
    [
        b'{"model": "x", // a comment\n}',
        b"[1, 2]",
        b'"model"',
        b"",
        b"  \n",
        b'{"model": "caf\xe9"}',
    ],
    ids=["comment", "array", "string", "empty", "blank", "not-utf8"],
)
def test_a_file_that_is_not_a_json_object_is_left_alone(
    tmp_path: Path, content: bytes
) -> None:
    path = tmp_path / "opencode.json"
    path.write_bytes(content)

    with pytest.raises(ModelFileError, match="opencode.json"):
        set_model(path, "model", "new/model")

    assert path.read_bytes() == content
