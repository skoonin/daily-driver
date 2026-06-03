"""Phased live progress for long-running commands.

A small, domain-neutral wrapper over ``rich.progress.Progress`` for any
command that runs a sequence of labelled groups and wants visible, monotonic
progress while it works. Two render modes, chosen once at construction:

* TTY: an animated live block on the bound console (status marker + label +
  count + note + elapsed), redrawn in place, persisting each group's final
  state. A static status marker (``-`` done, ``x`` failed, ``>`` running,
  blank pending) replaces a spinner so the four states are distinguishable;
  the elapsed timer ticks for liveness during long blocking work.
* non-TTY (cron, launchd, CI, pipes, and ``-vv`` debug streaming): discrete
  plain lines, no ANSI, no in-place rewrite. ``start``/``advance`` are silent;
  rows print one line when they finish.

The bound console is expected to be the stderr console so the live block
coexists with the verbose log stream and leaves stdout a clean data channel.

Structure: a run owns one or more :class:`Group` blocks. A group renders a
header row that counts its finished children, plus child rows of two shapes:

* :meth:`Group.item` -> :class:`Item`: a status row (scraping sources) that
  goes pending -> running -> ok/failed.
* :meth:`Group.phase` -> :class:`Phase`: an N/total counter row (enrichment
  loops). ``advance`` is :data:`ProgressCallback`-compatible for threading
  into worker loops.
"""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Optional

import rich.console as rich_console
from rich.progress import Progress, ProgressColumn
from rich.progress import Task as _Task
from rich.progress import TextColumn, TimeElapsedColumn
from rich.table import Column
from rich.text import Text

# (advance_n, detail) -> None. Threaded into worker loops; never raises.
ProgressCallback = Callable[[int, Optional[str]], None]

# One-line key for the otherwise-cryptic status markers, shown under the title.
_LEGEND = "  > running   - done   x failed"


class _LabelColumn(ProgressColumn):
    """Status marker + label in one cell.

    Keeping the one-glyph state (``x`` failed, ``-`` done, ``>`` running, blank
    pending) in the same column as the label means a narrow terminal can't drop
    the marker independently -- a standalone 1-char column is the first thing
    Rich's table allocator discards.
    """

    def render(self, task: _Task) -> Text:
        if task.fields.get("failed"):
            marker = Text("x ", style="red")
        elif task.total is not None and task.completed >= task.total:
            marker = Text("- ", style="green")
        elif task.started:
            marker = Text("> ", style="cyan")
        else:
            marker = Text("  ")  # pending
        return marker + Text(str(task.description))


class _CountColumn(ProgressColumn):
    """``M/N`` for counter rows; bare ``N`` when the total is unknown.

    Blank for pending (not-yet-started) rows and for compact rows (status
    sub-items, whose ``1/1`` would be noise -- the count lives on the header).
    """

    def render(self, task: _Task) -> Text:
        if task.fields.get("compact") or not task.started:
            return Text("")
        if task.total is None:
            return Text(str(int(task.completed)), style="progress.percentage")
        return Text(f"{int(task.completed)}/{int(task.total)}")


def _columns() -> list[ProgressColumn]:
    """Uniform column set: marker+label, count, note, elapsed."""
    return [
        _LabelColumn(
            table_column=Column(overflow="ellipsis", no_wrap=True),
        ),
        _CountColumn(),
        TextColumn(
            "{task.fields[note]}",
            style="dim",
            table_column=Column(overflow="ellipsis", no_wrap=True),
        ),
        TimeElapsedColumn(),
    ]


class RunProgress:
    """Owns the live display for one command run.

    Use as a context manager. In TTY mode ``__enter__`` starts the live region
    and ``__exit__`` stops it (restoring the terminal) even when the body
    raises -- so a propagating ``KeyboardInterrupt`` unwinds cleanly.
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
            self._console.print(_LEGEND, style="dim")
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

    def group(self, label: str) -> Group:
        """A header row that counts its finished children, plus child rows."""
        return Group(self._progress, self._console, label)


class Group:
    """A labelled block: a header counter plus :class:`Item`/:class:`Phase` rows."""

    def __init__(
        self,
        progress: Progress | None,
        console: rich_console.Console,
        label: str,
    ) -> None:
        self._progress = progress
        self._console = console
        self._label = label
        self._total = 0
        self._header_id = None
        if progress is not None:
            # Open total so the header reads as running (">"), not a momentary
            # done ("-") at 0/0 before any child is added.
            self._header_id = progress.add_task(label, total=None, note="", start=True)

    def item(self, label: str) -> Item:
        """A status sub-row (pending -> running -> ok/failed)."""
        self._total += 1
        self._sync_total()
        return Item(self, label)

    def phase(self, label: str) -> Phase:
        """A counter sub-row (advance is ProgressCallback-compatible)."""
        self._total += 1
        self._sync_total()
        return Phase(self, label)

    def _sync_total(self) -> None:
        if self._progress is not None and self._header_id is not None:
            self._progress.update(self._header_id, total=self._total)

    def _child_finished(self) -> None:
        # advance=1 is applied under rich's task lock, so this is safe to call
        # from the Phase-1 worker threads without a separate counter to race.
        if self._progress is not None and self._header_id is not None:
            self._progress.update(self._header_id, advance=1)

    def done(self, summary: str | None = None) -> None:
        if self._progress is not None and self._header_id is not None:
            self._progress.update(self._header_id, note=summary or "")
            self._progress.stop_task(self._header_id)
        elif summary is not None:
            self._console.print(f"{self._label}: {summary}")


class _Row:
    """Shared state/behaviour for a group's sub-rows."""

    def __init__(
        self, group: Group, label: str, *, compact: bool, total: int | None
    ) -> None:
        self._group = group
        self._label = label
        self._task_id = None
        self._started = False
        progress = group._progress
        if progress is not None:
            self._task_id = progress.add_task(
                f"  {label}",
                total=total,
                completed=0,
                note="pending",
                compact=compact,
                failed=False,
                start=False,
            )

    def start(self) -> None:
        """Flip pending -> running. Idempotent; the note guard keeps a later
        per-item detail from being clobbered back to ``running...``."""
        if self._started:
            return
        self._started = True
        progress = self._group._progress
        if progress is not None and self._task_id is not None:
            progress.start_task(self._task_id)
            progress.update(self._task_id, note="running...")


class Item(_Row):
    """A status sub-row for a discrete unit of work (e.g. one scraper source)."""

    def __init__(self, group: Group, label: str) -> None:
        super().__init__(group, label, compact=True, total=1)

    def finish(self, ok: bool = True, detail: str | None = None) -> None:
        note = detail if detail is not None else ("ok" if ok else "failed")
        progress = self._group._progress
        if progress is not None and self._task_id is not None:
            progress.start_task(self._task_id)
            progress.update(self._task_id, completed=1, note=note, failed=not ok)
            progress.stop_task(self._task_id)
        else:
            self._group._console.print(f"{self._label}: {note}")
        self._group._child_finished()


class Phase(_Row):
    """An N/total counter sub-row for a loop of completed units."""

    def __init__(self, group: Group, label: str) -> None:
        # Open total: a counter row shows a bare count until done() pins the
        # total to the count reached, so a partial run never shows a misleading
        # denominator.
        super().__init__(group, label, compact=False, total=None)
        self.completed = 0

    def advance(self, n: int = 1, detail: str | None = None) -> None:
        self.start()
        self.completed += n
        progress = self._group._progress
        if progress is not None and self._task_id is not None:
            if detail is not None:
                progress.update(self._task_id, completed=self.completed, note=detail)
            else:
                progress.update(self._task_id, completed=self.completed)

    def done(self, summary: str | None = None) -> None:
        text = summary if summary is not None else f"{self.completed} done"
        progress = self._group._progress
        if progress is not None and self._task_id is not None:
            progress.start_task(self._task_id)
            # Pin the total to the count reached so the row reads as finished:
            # the status marker resolves to done and the elapsed timer freezes.
            progress.update(
                self._task_id, total=self.completed, completed=self.completed, note=text
            )
            progress.stop_task(self._task_id)
        else:
            self._group._console.print(f"{self._label}: {text}")
        self._group._child_finished()
