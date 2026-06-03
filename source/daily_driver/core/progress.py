"""Phased live progress for long-running commands.

A small, domain-neutral wrapper over ``rich.progress.Progress`` for any
command that runs a sequence of labelled phases and wants visible, monotonic
progress while it works. Two render modes, chosen once at construction:

* TTY: an animated live block on the bound console (spinner + label +
  N/total + elapsed), redrawn in place, persisting each phase's final state.
* non-TTY (cron, launchd, CI, pipes): discrete plain lines, no ANSI, no
  in-place rewrite. ``advance`` is silent; phases print on done.

The bound console is expected to be the stderr console so the live block
coexists with the RichHandler verbose stream (same console -> Rich
interleaves log lines above the live region) and leaves stdout a clean data
channel.

Two phase shapes:

* ``phase(label, total)`` -> :class:`Phase`: a single N/total counter, for
  work measured as "M of N units done" (e.g. enrichment loops). ``advance``
  is :data:`ProgressCallback`-compatible for threading into worker loops.
* ``checklist(label)`` -> :class:`Checklist`: a header counter plus one row
  per dynamically-discovered item (e.g. per-source scraping), each row
  flipping from spinner to a final ok/failed status.
"""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Optional

import rich.console as rich_console
from rich.progress import (
    Progress,
    ProgressColumn,
    SpinnerColumn,
)
from rich.progress import Task as _Task
from rich.progress import (
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Column
from rich.text import Text

# (advance_n, detail) -> None. Threaded into worker loops; never raises.
ProgressCallback = Callable[[int, Optional[str]], None]


class _CountColumn(ProgressColumn):
    """Render the completed count, adding ``/total`` only when a total is known.

    ``MofNCompleteColumn`` renders ``N/?`` for indeterminate tasks; this keeps
    a live spinner-driven count readable as just ``N`` until a total is set.
    Compact rows (checklist sub-items) render nothing -- a per-item ``1/1`` is
    noise, and the meaningful count lives on the checklist header.
    """

    def render(self, task: _Task) -> Text:
        if task.fields.get("compact"):
            return Text("")
        if task.total is None:
            return Text(str(int(task.completed)), style="progress.percentage")
        return Text(f"{int(task.completed)}/{int(task.total)}")


class _ElapsedColumn(TimeElapsedColumn):
    """Elapsed time, suppressed for compact rows.

    Checklist sub-items are created at completion, so their own elapsed reads
    ``0:00:00`` and contradicts the real duration already in the row note.
    """

    def render(self, task: _Task) -> Text:
        if task.fields.get("compact"):
            return Text("")
        return super().render(task)


def _columns() -> list[ProgressColumn]:
    """Uniform column set for every task: spinner, label, count, note, time."""
    return [
        SpinnerColumn(finished_text="-"),
        TextColumn("{task.description}"),
        _CountColumn(),
        TextColumn(
            "{task.fields[note]}",
            style="dim",
            table_column=Column(overflow="ellipsis", no_wrap=True),
        ),
        _ElapsedColumn(),
    ]


class RunProgress:
    """Owns the live display for one command run.

    Use as a context manager. In TTY mode ``__enter__`` starts the live
    region and ``__exit__`` stops it (restoring the terminal) even when the
    body raises -- so a propagating ``KeyboardInterrupt`` unwinds cleanly.
    """

    def __init__(
        self,
        console: rich_console.Console,
        *,
        tty: bool,
        title: str | None = None,
    ) -> None:
        self._console = console
        self._tty = tty
        self._title = title
        self._progress: Progress | None = None

    def __enter__(self) -> RunProgress:
        if self._title:
            self._console.print(self._title)
        if self._tty:
            self._progress = Progress(
                *_columns(),
                console=self._console,
                transient=False,
                redirect_stdout=False,
                redirect_stderr=False,
            )
            self._progress.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = None

    def phase(self, label: str, total: int | None = None) -> Phase:
        """A single N/total counter phase."""
        return Phase(self._progress, self._console, label, total)

    def checklist(self, label: str) -> Checklist:
        """A header counter plus one row per item discovered at runtime."""
        return Checklist(self._progress, self._console, label)


class Phase:
    """A single counter phase.

    ``advance`` counts a unit of completed work and doubles as a
    :data:`ProgressCallback`. ``done`` pins the live total to the count reached
    and freezes the row with a caller-supplied summary.
    """

    def __init__(
        self,
        progress: Progress | None,
        console: rich_console.Console,
        label: str,
        total: int | None,
    ) -> None:
        self._progress = progress
        self._console = console
        self._label = label
        self.completed = 0
        self._task_id = None
        if progress is not None:
            self._task_id = progress.add_task(label, total=total, note="")

    def advance(self, n: int = 1, detail: str | None = None) -> None:
        self.completed += n
        if self._progress is not None and self._task_id is not None:
            if detail is not None:
                self._progress.update(self._task_id, advance=n, note=detail)
            else:
                self._progress.update(self._task_id, advance=n)

    def done(self, summary: str | None = None) -> None:
        text = summary if summary is not None else f"{self.completed} done"
        if self._progress is not None and self._task_id is not None:
            # Pin the total to the work actually counted so the task reads as
            # finished: the spinner resolves to its finished marker and the
            # elapsed timer freezes.
            self._progress.update(self._task_id, total=self.completed, note=text)
            self._progress.stop_task(self._task_id)
        else:
            self._console.print(f"{self._label}: {text}")


class Checklist:
    """A header counter with one sub-row per item.

    ``start(label)`` adds a spinner row; ``ChecklistItem.finish`` flips it to a
    final status and advances the header counter.
    """

    def __init__(
        self,
        progress: Progress | None,
        console: rich_console.Console,
        label: str,
    ) -> None:
        self._progress = progress
        self._console = console
        self._label = label
        self._count = 0
        self._header_id = None
        if progress is not None:
            self._header_id = progress.add_task(label, total=None, note="")
        else:
            self._console.print(f"{label}...")

    def start(self, label: str) -> ChecklistItem:
        return ChecklistItem(self, f"  {label}")

    def _finish(self, n: int = 1) -> None:
        self._count += n
        if self._progress is not None and self._header_id is not None:
            self._progress.update(self._header_id, advance=n)

    def done(self, summary: str | None = None) -> None:
        if self._progress is not None and self._header_id is not None:
            self._progress.update(
                self._header_id, total=self._count, note=summary or ""
            )
            self._progress.stop_task(self._header_id)
        elif summary is not None:
            self._console.print(f"{self._label}: {summary}")


class ChecklistItem:
    """One row of a :class:`Checklist`."""

    def __init__(self, parent: Checklist, label: str) -> None:
        self._parent = parent
        self._label = label
        self._task_id = None
        progress = parent._progress
        if progress is not None:
            self._task_id = progress.add_task(label, total=1, note="", compact=True)

    def finish(self, ok: bool = True, detail: str | None = None) -> None:
        note = detail if detail is not None else ("ok" if ok else "failed")
        progress = self._parent._progress
        if progress is not None and self._task_id is not None:
            progress.update(self._task_id, completed=1, note=note)
            progress.stop_task(self._task_id)
        else:
            self._parent._console.print(f"{self._label.strip()}: {note}")
        self._parent._finish(1)
