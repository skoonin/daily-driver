# Implementation prompt: enlighten migration

Implement the migration described in `.claude/plan-enlighten-migration.md` (read it
first, in full). Replace the Rich `Progress` live block in `daily-driver jobs run`
with an enlighten `Manager`, deleting the coordination code that existed to make
Rich's live region coexist with the log stream.

Work in the plan's **Work sequence**, one gated step at a time. Stop at each gate
and run the named tests before moving on. Do not batch the steps.

## Non-negotiable constraints (flag before deviating)

1. **Keep the public interface** `RunProgress`/`Group`/`Item`/`Phase` in
   `core/progress.py` stable — `runner.py` is the sole consumer and hands
   `Phase.advance` to three enrichment functions as a `(int, Optional[str]) -> None`
   callback. Swap internals only.
2. **One `threading.Lock`** in the facade, guarding state mutation + the enlighten
   call. **Never hold it across a call that can block** (notably enlighten's
   scroll-area setup, which issues a blocking `ESC[6n` cursor query).
3. **Eager scroll-region setup in `__enter__`**, single-threaded before any worker
   spawns, with a **bounded-wait fallback to plain line mode** if the terminal does
   not answer the cursor query. No hang on entry, ever.
4. **Facade methods are hard no-ops after `__exit__`/close** (a `_closed` flag
   checked under the lock) — a worker thread can call `finish()` after teardown on
   Ctrl-C.
5. **Option B logging:** plain counting `StreamHandler` (rename
   `_LiveAwareRichHandler`; nothing live-aware remains), `Formatter` for level/
   timestamp. Rework `live_log_window` off `handler.console`/`handler._show_time`
   (a plain handler has neither). Keep routing `JobSpy:*` loggers through the
   counting handler (so their warnings still count toward `Warnings: N`) while
   aligning their level to verbosity.
6. **Bars persist at every verbosity**; verbosity controls only how much log volume
   scrolls above them. Compute `tty` purely from `Console.is_tty()`.
7. **Slow-source note:** `Item.start()` takes an optional note; the plugin passes a
   per-source note for slow boards (`linkedin -> "running -- can take several
   minutes"`).

## Hygiene

- Delete dead code (Rich columns `_LabelColumn`/`_CountColumn`/`_columns()`,
  `_LEGEND`, now-dead Rich imports) — do not comment it out.
- Fix the stale module docstrings in `progress.py`, `logging.py`, and the
  `get_log_console` docstring in `console.py:82`.
- Add `enlighten` to `pyproject.toml` dependencies and the isort
  `known_third_party` list; add a CHANGELOG `[Unreleased]` entry for the dependency
  and the UX change.
- Update all documentation related to this work once it is complete and working, including the `docs/` files and the `README.md` usage example.

## Tests to add (beyond the rewrites named in the plan)

- concurrent `finish()` from multiple threads;
- `finish()` after close is a no-op;
- unresponsive-TTY falls back to plain mode within the timeout (no hang);
- JobSpy WARN+ still counted in `Warnings: N` after the adoption change;
- failed-source exit code `1` survives the teardown rewrite.

## Done

Run `make test` (full tox envlist) green, then live-verify a real `jobs run`
against `dd-sk` per the plan's Verification section (zero stranded frames, clean
teardown on exit and Ctrl-C). Use `/sk-review` before committing.

When the migration is complete and merged, delete this file (`prompt-enlighten-migration.md`).
