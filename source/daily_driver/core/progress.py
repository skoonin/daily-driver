"""Phased live progress for long-running commands.

A small, domain-neutral facade over ``enlighten`` for any command that runs a
sequence of labelled groups and wants visible, monotonic progress while it
works. Two render modes, chosen once at construction (and downgraded if the
terminal will not cooperate):

* live (TTY): an enlighten ``Manager`` pins a region at the bottom of the
  terminal. Every row is one ``manager.counter`` created once and mutated in
  place -- the idiom every enlighten example uses; bars persist (``leave=True``)
  at their final state. Each group renders a header bar counting its finished
  children, with a red ``add_subcounter`` so ok (green) vs failed (red) sources
  show as coloured segments. Each source is its own bar: a known total fills by
  page (JobSpy), an unknown total counts up, and an instant/opaque source
  (``total=1``) fills 0->1 on finish; on finish the result is folded into the
  bar's label (``linkedin  61 found``, recoloured red on failure). Enrichment
  phases are their own fill bars. No per-line repaint and no background refresh
  thread -- bars redraw only on update.
* plain (non-TTY -- cron, launchd, CI, pipes -- or an unresponsive TTY):
  discrete plain lines on the bound console, no ANSI, no in-place rewrite.
  ``start``/``advance`` are silent; rows print one line when they finish.

The bound console is the stderr console; the enlighten manager binds the same
underlying ``sys.stderr`` object, so bars and the plain log stream interleave on
one channel and leave stdout a clean data channel.

Concurrency: ``on_source_*`` callbacks fire from Phase-1 worker threads. A
single :class:`threading.Lock` guards facade state and the (non-thread-safe)
enlighten calls; every facade method takes it once and never nests it. enlighten
issues a cursor-position query (``ESC[6n``, ``term.get_location``) on every bar
write while it maintains the scroll region, each with a 1s timeout -- so the lock
is, in principle, held across a blocking call. Two things keep that safe in
practice: the eager probe in ``__enter__`` downgrades a terminal that is
unresponsive *at startup* to plain mode (no manager, no queries), and on the
surviving live path a responsive terminal answers each query in well under a
millisecond. The residual risk is a terminal that stalls *mid-run* (an SSH or
multiplexer hiccup): bar updates then serialize behind a 1s-timeout query under
the lock -- degraded throughput, not deadlock or display corruption. Facade
methods are hard no-ops after ``__exit__`` (a ``_closed`` flag checked under the
lock) so a worker that finishes after a Ctrl-C teardown is safe.

Structure: a run owns one or more :class:`Group` blocks. A group renders a
header progress bar that counts its finished children, plus child rows of two
shapes:

* :meth:`Group.item` -> :class:`Item`: a status row (scraping sources) that
  goes pending -> running -> ok/failed.
* :meth:`Group.phase` -> :class:`Phase`: an N/total progress bar (enrichment
  loops). ``advance`` is :data:`ProgressCallback`-compatible for threading
  into worker loops.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from types import TracebackType
from typing import Optional

import enlighten
import rich.console as rich_console

from daily_driver.core.console import Console

# (advance_n, detail) -> None. Threaded into worker loops; never raises.
ProgressCallback = Callable[[int, Optional[str]], None]

# Bounded wait for the terminal to answer the cursor-position query (ESC[6n)
# issued during eager scroll-region setup. A responsive terminal replies in well
# under a millisecond; if none arrives in this window the terminal cannot host a
# pinned scroll region, so we fall back to plain line mode instead of stalling
# every later bar update for a full timeout. Kept short so entry never hangs.
_CURSOR_QUERY_TIMEOUT = 0.5

# Progress-bar layout: description, percentage, fill bar, count/total. No
# elapsed/rate fields -- enlighten has no refresh thread, so a time field would
# only move on update() and read as stale; the bar fill is the liveness signal.
_BAR_FORMAT = "{desc}{desc_pad}{percentage:3.0f}%|{bar}| {count:{len_total}d}/{total:d}"
# Shown before a total is known (or once count reaches total): just desc + count.
_COUNTER_FORMAT = "{desc}{desc_pad}{count:d}"

_OK_COLOR = "green"  # group-header fill (finished-ok children)
_FAIL_COLOR = "red"  # group-header failed subcounter + a failed source bar
_SOURCE_BAR_COLOR = "blue"  # per-source progress bar
_PHASE_COLOR = "cyan"  # enrichment phase bar


class RunProgress:
    """Owns the live display for one command run.

    Use as a context manager. ``__enter__`` decides between live (enlighten) and
    plain mode -- including a bounded-wait probe that downgrades an unresponsive
    TTY to plain -- and ``__exit__`` tears the manager down (restoring the
    terminal) even when the body raises, so a propagating ``KeyboardInterrupt``
    unwinds cleanly. After teardown every facade method is a hard no-op.
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
        self._manager: enlighten.Manager | None = None
        self._title_bar: enlighten.StatusBar | None = None
        self._lock = threading.Lock()
        self._closed = False
        # The title status bar is pinned lazily, on the first seat operation
        # (reserve() or the first counter). Pinning it up front would set the
        # scroll region once for the title, then again per counter -- each resize
        # a separate bottom-row line feed that real terminals (iTerm2, VS Code's
        # xterm.js) render inconsistently. Seating once, after the block height is
        # known, collapses that to a single region set.
        self._title_pending = False
        self._reserved = False
        # enlighten honours colour names regardless of the app's --no-color flag,
        # so gate colour ourselves to keep --no-color a true no-colour run.
        self._color = not Console._no_color

    def __enter__(self) -> RunProgress:
        if self._tty:
            self._manager = self._start_live()
        if self._manager is not None:
            # Enlighten anchors every bar to the bottom of the terminal. Printing
            # the title as an ordinary line leaves it where the cursor was (near
            # the top of a fresh terminal), so the whole terminal height shows as
            # blank space between the title and the bottom-pinned bars. Pinning
            # the title as a status bar instead -- created first, so it takes the
            # top slot of the block (see _add_counter's reversed-order placement)
            # -- sets it flush above the bars; the empty space falls above the
            # block, the normal bottom-anchored look.
            #
            # The title is pinned lazily (on reserve() or the first counter), not
            # here -- see _title_pending. No blank bottom-margin rows are pinned:
            # each pinned row grows enlighten's reserved scroll region by one fed
            # line feed (Manager._set_scroll_area emits '\n' per added offset), so
            # a cosmetic margin reads as a multi-line blank gap before the block.
            self._title_pending = bool(self._title)
        elif self._title:
            # Plain mode (non-TTY or unresponsive terminal): one ordinary line.
            self._plain_print(self._title)
        return self

    def _start_live(self) -> enlighten.Manager | None:
        """Bring up the enlighten manager, or return ``None`` for plain mode.

        Single-threaded and called once, before any worker spawns. The cursor
        query here is the eager scroll-region setup: if the terminal does not
        answer within the timeout we cannot pin a region, so we disable the
        manager and fall back to plain mode rather than letting every later
        bar update pay the timeout.
        """
        manager = enlighten.get_manager(stream=self._console.file)
        if not manager.enabled:
            # Stream is not a usable TTY (get_manager already disabled it).
            return None
        row, _col = manager.term.get_location(timeout=_CURSOR_QUERY_TIMEOUT)
        if row < 0:
            manager.enabled = False
            return None
        return manager

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        with self._lock:
            # A run that pinned no counters (e.g. zero sources) still shows its
            # title: seat it now so it persists, then close.
            self._ensure_title_locked()
            self._closed = True
            manager = self._manager
            self._manager = None
        # stop() restores the scroll region and cursor; run it outside the lock.
        # _closed is already set and _manager cleared, so no late worker call can
        # touch the manager concurrently.
        if manager is not None:
            manager.stop()

    def group(self, label: str) -> Group:
        """A header progress bar that counts its finished children, plus rows."""
        return Group(self, label)

    def reserve(self, rows: int) -> None:
        """Pre-size the live block to ``rows`` total lines in one operation.

        Each pinned bar normally grows enlighten's scroll region by one line
        (a separate ``CSI r`` scroll-region set plus a bottom-row line feed in
        :meth:`Manager._set_scroll_area`). Spread over a run that is many small
        region resizes, which iTerm2 and VS Code's xterm.js render
        inconsistently -- a large blank gap before the block, or a frozen
        duplicate of the block left in scrollback. Reserving the full height up
        front collapses that to a single scroll-region set and a single feed,
        which every terminal renders identically.

        ``rows`` is the total block height, counting the title row. Call once,
        before creating any group/row, with the known final count
        (e.g. ``1 title + 1 header + len(sources)``). Idempotent; a no-op in
        plain mode, after close, or if the block is already seated. Over-reserve
        is harmless (a row or two of slack above the block); under-reserve falls
        back to per-bar growth for the overflow.
        """
        with self._lock:
            if self._closed or self._manager is None or self._reserved:
                return
            self._reserved = True
            manager = self._manager
            term = manager.term
            # enlighten's scroll offset is (pinned rows + 1); clamp to the
            # terminal so a block taller than the screen does not over-feed.
            offset = max(2, min(rows + 1, manager.height))
            # Scroll existing output up by the block height in one feed (the same
            # bottom-anchored scroll enlighten does per bar, done once), then pin
            # the region at its final size so later bars seat without resizing it.
            manager.stream.write(term.move(max(0, manager.height - 1), 0))
            manager.stream.write("\n" * (offset - 1))
            manager.scroll_offset = offset
            manager._set_scroll_area(force=True)
            manager._flush_streams()
            self._ensure_title_locked()

    def _ensure_title_locked(self) -> None:
        """Pin the title status bar on the first seat operation. Lock held.

        Created before any counter so enlighten's reversed-order placement gives
        it the top slot of the bottom block (flush above the bars).
        """
        if self._manager is None or not self._title_pending:
            return
        self._title_pending = False
        self._title_bar = self._manager.status_bar(self._title, leave=True)

    def _plain_print(self, message: str) -> None:
        """Print one plain-mode line, unless quiet mode is suppressing routine
        output. Quiet keeps only warnings/errors, so progress lines are dropped.
        """
        if Console.quiet_mode:
            return
        self._console.print(message)

    def note(self, message: str) -> None:
        """Emit a one-off informational line above the live bars.

        In live mode this is a plain write to the same stderr stream the warning
        handler uses -- enlighten's scroll region carries it above the pinned
        bars. We deliberately avoid the Rich console here: its cursor management
        competes with enlighten's and clips the start of the line. In plain mode
        it is an ordinary printed line.
        """
        with self._lock:
            if self._closed:
                return
            if self._manager is not None:
                stream = self._console.file
                stream.write(message + "\n")
                stream.flush()
            else:
                self._plain_print(message)

    # ----------------------------------------------------------------- #
    # Lock-held primitives. Callers hold ``self._lock`` and have checked
    # ``self._closed``; these touch the manager and never re-acquire the lock.
    # ----------------------------------------------------------------- #

    def _open_counter_locked(
        self, desc: str, total: int, *, color: str | None = None
    ) -> enlighten.Counter | None:
        """Create a persistent (``leave=True``) counter, or None in plain mode.

        Every row is one counter created once and mutated in place (``count``,
        ``total``, ``desc``, ``color``) through its whole lifecycle -- the idiom
        every enlighten example uses. ``leave=True`` keeps it pinned at its final
        state; creating/closing/recreating bars instead makes enlighten thrash
        the reserved scroll region.
        """
        if self._manager is None:
            return None
        # Seat the title (top slot) before the first real bar, whether or not
        # the caller reserved the block first.
        self._ensure_title_locked()
        counter = self._manager.counter(
            total=total,
            desc=desc,
            bar_format=_BAR_FORMAT,
            counter_format=_COUNTER_FORMAT,
            color=(color if self._color else None),
            leave=True,
        )
        counter.refresh()  # a fresh counter is invisible until refreshed; show pending now
        return counter


class Group:
    """A labelled block: a header progress bar plus :class:`Item`/:class:`Phase` rows."""

    def __init__(self, owner: RunProgress, label: str) -> None:
        self._owner = owner
        self._label = label
        self._completed = 0
        self._total = 0
        self._bar: enlighten.Counter | None = None
        self._failed: enlighten.SubCounter | None = None

    def item(self, label: str) -> Item:
        """A status sub-row (pending -> running -> ok/failed)."""
        self._register_child()
        return Item(self, label)

    def phase(self, label: str, total: int | None = None) -> Phase:
        """A progress-bar sub-row (advance is ProgressCallback-compatible)."""
        self._register_child()
        return Phase(self, label, total)

    def _register_child(self) -> None:
        owner = self._owner
        with owner._lock:
            if owner._closed:
                return
            self._total += 1
            if owner._manager is None:
                return
            if self._bar is None:
                # Header counts finished children; a red subcounter marks failures
                # so the bar shows ok (green) vs failed (red) segments.
                self._bar = owner._open_counter_locked(
                    self._label, self._total, color=_OK_COLOR
                )
                if self._bar is not None and owner._color:
                    self._failed = self._bar.add_subcounter(_FAIL_COLOR)
            else:
                self._bar.total = self._total
                self._bar.refresh()

    def _child_finished_locked(self, ok: bool) -> None:
        """Advance the header (red segment on failure). Lock held by caller."""
        self._completed += 1
        if self._bar is None:
            return
        if ok or self._failed is None:
            self._bar.update()
        else:
            self._failed.update()

    def done(self, summary: str | None = None) -> None:
        owner = self._owner
        with owner._lock:
            if owner._closed:
                return
            if self._bar is not None:
                # Persist the header at its final count; fold the summary into its
                # label (e.g. "Scraping sources: 142 found, 138 new  8/8").
                if summary is not None:
                    self._bar.desc = f"{self._label}: {summary}"
                    self._bar.refresh()
            elif owner._manager is None and summary is not None:
                owner._plain_print(f"{self._label}: {summary}")


class _Row:
    """Shared state for a group's sub-rows (one persistent counter each)."""

    def __init__(self, group: Group, label: str) -> None:
        self._group = group
        self._label = label
        self._bar: enlighten.Counter | None = None
        self._started = False

    def _owner(self) -> RunProgress:
        return self._group._owner

    def _fill_to_total_locked(self) -> None:
        """Drive the bar to 100% (lock held). Used to mark a row complete."""
        bar = self._bar
        if bar is None:
            return
        total = bar.total or 1
        if bar.count < total:
            bar.update(total - bar.count)
        else:
            bar.refresh()


class Item(_Row):
    """A per-source progress bar: one enlighten counter, mutated in place."""

    def start(self, note: str | None = None) -> None:
        """Pin the source's progress bar. ``note`` (e.g. a slow-source
        expectation) shows in the label until real progress arrives. Idempotent;
        silent in plain mode."""
        owner = self._owner()
        with owner._lock:
            if owner._closed or self._started:
                return
            self._started = True
            if owner._manager is None or self._bar is not None:
                return
            desc = f"{self._label}  {note}" if note else self._label
            # total=1 is the default for instant/opaque sources (fill 0->1 on
            # finish); JobSpy sources get their real page total via progress().
            self._bar = owner._open_counter_locked(desc, 1, color=_SOURCE_BAR_COLOR)

    def progress(self, completed: int, total: int | None = None) -> None:
        """Advance the bar from an external signal (e.g. JobSpy's per-page logs):
        set the page total (grows on overshoot) and the count, dropping the
        slow-source note once real progress shows. Silent in plain mode."""
        owner = self._owner()
        with owner._lock:
            if owner._closed or owner._manager is None:
                return
            self._started = True
            if self._bar is None:
                self._bar = owner._open_counter_locked(
                    self._label, total or 1, color=_SOURCE_BAR_COLOR
                )
            if self._bar is None:
                return
            self._bar.desc = self._label
            if total is not None:
                self._bar.total = max(total, completed)
            self._bar.update(max(0, completed - self._bar.count))

    def finish(self, ok: bool = True, detail: str | None = None) -> None:
        note = detail if detail is not None else ("ok" if ok else "failed")
        owner = self._owner()
        with owner._lock:
            if owner._closed:
                return
            if owner._manager is not None:
                if self._bar is not None:
                    # Freeze the row at its result: fold the detail into the label,
                    # fill to 100% on success, recolour red on failure.
                    self._bar.desc = f"{self._label}  {note}"
                    if ok:
                        self._fill_to_total_locked()
                    else:
                        if owner._color:
                            self._bar.color = _FAIL_COLOR
                        self._bar.refresh()
                self._group._child_finished_locked(ok)
            else:
                owner._plain_print(f"{self._label}: {note}")

    def show_breakdown(self, segments: list[tuple[int, str]]) -> None:
        """Re-base the finished bar as stacked coloured segments.

        Each ``(count, colour)`` becomes one subcounter; the bar's total is reset
        to their sum, so the bar fills completely as a stacked breakdown of the
        row's result (e.g. new / duplicate / skipped portions of what a source
        found).
        Domain-neutral -- the caller owns what each colour means. Call after
        :meth:`finish`. Silent in plain mode, after close, or before a bar exists;
        colours collapse to the base fill under ``--no-color``.
        """
        owner = self._owner()
        with owner._lock:
            if owner._closed or owner._manager is None or self._bar is None:
                return
            total = sum(count for count, _ in segments if count > 0)
            if total <= 0:
                return
            bar = self._bar
            # The page-progress bar has done its job; rebuild it as an outcome
            # breakdown. Reset the count and stack one coloured subcounter per
            # segment -- each update advances the parent too (enlighten), so the
            # fill sums back to the found total with each segment in its colour.
            bar.count = 0
            bar.total = total
            for count, color in segments:
                if count > 0:
                    bar.add_subcounter(color if owner._color else None).update(count)
            bar.refresh()


class Phase(_Row):
    """An N/total enrichment progress bar (advance is ProgressCallback-compatible)."""

    def __init__(self, group: Group, label: str, total: int | None = None) -> None:
        super().__init__(group, label)
        self.completed = 0
        self._total = total

    def start(self) -> None:
        """Pin the phase's progress bar (0/total) so it reads as pending."""
        owner = self._owner()
        with owner._lock:
            if owner._closed or self._started:
                return
            self._started = True
            if owner._manager is not None and self._bar is None:
                self._bar = owner._open_counter_locked(
                    self._label, self._total or 1, color=_PHASE_COLOR
                )

    def advance(self, n: int = 1, detail: str | None = None) -> None:
        owner = self._owner()
        with owner._lock:
            if owner._closed:
                return
            self.completed += n
            self._started = True
            if owner._manager is None:
                return
            if self._bar is None:
                self._bar = owner._open_counter_locked(
                    self._label, self._total or 1, color=_PHASE_COLOR
                )
            if self._bar is not None:
                # Surface the item currently being processed (e.g. the company an
                # LLM enrichment call is on) in the bar label, so a long phase
                # shows live which work it is doing rather than only a count.
                if detail:
                    self._bar.desc = f"{self._label}  {detail}"
                self._bar.update(n)

    def done(self, summary: str | None = None) -> None:
        text = summary if summary is not None else f"{self.completed} done"
        owner = self._owner()
        with owner._lock:
            if owner._closed:
                return
            if owner._manager is not None:
                if self._bar is not None:
                    self._bar.desc = f"{self._label}  {text}"
                    self._fill_to_total_locked()
                self._group._child_finished_locked(True)
            else:
                owner._plain_print(f"{self._label}: {text}")
