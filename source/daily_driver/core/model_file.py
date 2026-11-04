"""The model file: one key in a program's own JSON config, set before a launch.

Some programs read the model of an interactive session only from their config,
such as opencode from `opencode.json` in its project folder. Daily Driver
changes the bytes of that one value and leaves every other byte of the file
as the user wrote it: layout, escapes, number spelling, line endings, file
mode, and a symlink to a file kept elsewhere.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

# The four whitespace characters JSON allows between tokens.
_JSON_SPACE = " \t\n\r"
_DECODER = json.JSONDecoder()


class ModelFileError(RuntimeError):
    """The model cannot be set. The file is not changed.

    Causes: the file is not UTF-8, is not valid JSON, or is not a JSON object.
    The path is a link to a missing file. Another launch makes and removes the
    file at the same time.
    """


def set_model(path: Path, key: str, model: str) -> None:
    """Set top-level `key` to `model` in the JSON object at `path`.

    A missing file is created with only that key. A file whose value already
    matches is not written.
    """
    target = path.resolve()
    text = _read(path, target, key)
    if text is None:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            _create(target, json.dumps({key: model}, ensure_ascii=False) + "\n")
            return
        except FileExistsError as exc:
            # Another launch made the file first; set the model in that one.
            # Read it once only: a file that is gone again ends the launch
            # rather than a loop.
            text = _read(path, target, key)
            if text is None:
                raise ModelFileError(
                    f"{path} is being made and removed by another launch; "
                    "run the command again"
                ) from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise _unusable(path, key, f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise _unusable(path, key, "not a JSON object")
    if key in data and data[key] == model:
        return
    _replace(target, _with_value(text, key, model))


def _read(path: Path, target: Path, key: str) -> str | None:
    """The text of the file, or None when there is no file to read."""
    try:
        with open(target, encoding="utf-8", newline="") as handle:
            return handle.read()
    except FileNotFoundError:
        # A link the user made to a file that is gone is a mistake to report,
        # not a place, possibly outside the workspace, to create a file.
        if path.is_symlink():
            raise _unusable(path, key, "a link to a missing file") from None
        return None
    except UnicodeDecodeError as exc:
        raise _unusable(path, key, f"not UTF-8: {exc.reason}") from exc


def _unusable(path: Path, key: str, why: str) -> ModelFileError:
    return ModelFileError(
        f"{path} is {why}; fix it or delete it so Daily Driver can set {key!r}"
    )


def _with_value(text: str, key: str, model: str) -> str:
    """`text` with `key`'s value replaced, or the pair added after the last one."""
    value = json.dumps(model, ensure_ascii=False)
    span, close, indent = _top_level(text, key)
    if span is not None:
        start, end = span
        return text[:start] + value + text[end:]
    pair = f"{json.dumps(key, ensure_ascii=False)}: {value}"
    body_end = len(text[:close].rstrip(_JSON_SPACE))
    if text[body_end - 1] == "{":
        return text[:body_end] + pair + text[body_end:]
    return text[:body_end] + "," + (indent or " ") + pair + text[body_end:]


def _top_level(text: str, key: str) -> tuple[tuple[int, int] | None, int, str]:
    """Walk the top-level object of `text`, which `json.loads` already accepted.

    Returns the span of the last value under `key` (the one `json.loads`
    keeps), the index of the closing brace, and the whitespace before the
    first key, which a new pair copies so it lines up with the others.
    """
    pos = _skip_space(text, 0) + 1
    first_key = _skip_space(text, pos)
    indent = text[pos:first_key]
    span = None
    pos = first_key
    while text[pos] != "}":
        name, pos = _DECODER.raw_decode(text, pos)
        pos = _skip_space(text, _skip_space(text, pos) + 1)
        _, end = _DECODER.raw_decode(text, pos)
        if name == key:
            span = (pos, end)
        pos = _skip_space(text, end)
        if text[pos] == ",":
            pos = _skip_space(text, pos + 1)
    return span, pos, indent


def _skip_space(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] in _JSON_SPACE:
        pos += 1
    return pos


def _create(target: Path, text: str) -> None:
    """Make `target` holding `text` in one step; FileExistsError if it exists.

    The text is written to a temporary file that is then linked into place, so
    a failed write leaves no part of a file, and the link refuses to replace a
    file another launch made in the meantime. The new file gets the mode a
    plain create would: 0o666 less the umask.
    """
    # os.umask can only be read by setting it; nothing else creates files
    # while a launch prepares its session.
    umask = os.umask(0)
    os.umask(umask)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.chmod(tmp, 0o666 & ~umask)
        try:
            os.link(tmp, target)
        except FileExistsError:
            raise
        except OSError:
            # Some folders, such as exFAT drives and some network shares, have
            # no hard links.
            _create_in_place(target, text)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _create_in_place(target: Path, text: str) -> None:
    """Make `target` holding `text` without a link; remove it if the write fails.

    O_EXCL still refuses a file another launch made, and the umask applies as
    to any create.
    """
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        data = text.encode("utf-8")
        while data:
            data = data[os.write(fd, data) :]
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        os.close(fd)


def _replace(target: Path, text: str) -> None:
    """Replace `target` with `text` in one step, keeping its mode."""
    mode = stat.S_IMODE(target.stat().st_mode)
    # A unique name in the same folder: two launches at once never share a
    # temporary file, and os.replace stays on one filesystem.
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
