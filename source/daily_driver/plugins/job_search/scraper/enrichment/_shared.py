"""Cross-vertical enrichment infra: pool sizing, worker tags, interrupt notifier."""

from __future__ import annotations

import os
import signal
import sys
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.context import ScrapeContext


def _enrich_pool_size(ctx: ScrapeContext) -> int:
    """Worker count for enrichment, keyed off the active provider; 1 = serial.

    Each provider carries its own max_parallel: ollama is bounded by local RAM,
    claude by API rate limits. The phases route independently (a global
    `ai.provider: ollama` reaches enrichment even with the enrichment block
    unset), so resolve each phase rather than reading the raw provider field:
    use ollama's width if any phase resolves to ollama (the RAM-bound, more
    constrained case), else claude's. Parallelism only raises throughput — it
    does not change the max_enrich_* budget (which still caps how many jobs are
    enriched) or the per-call timeout.
    """
    from daily_driver.integrations import ai_provider

    ai_cfg = ctx.ai
    enrichment = ctx.plugin.enrichment
    any_ollama = (
        ai_provider.resolve_route(ai_cfg, task="fit_notes", domain_cfg=enrichment)[0]
        == "ollama"
    )
    if any_ollama:
        return max(1, ai_cfg.ollama.max_parallel)
    return max(1, ai_cfg.claude.max_parallel)


def _enrich_tag(prefix: str) -> str:
    """Return [prefix] in main thread, [prefix wN] in a pool worker."""
    import threading

    name = threading.current_thread().name
    if name == "MainThread":
        return f"[{prefix}]"
    suffix = name.rsplit("_", 1)
    if len(suffix) == 2 and suffix[1].isdigit():
        return f"[{prefix} w{suffix[1]}]"
    return f"[{prefix}]"


def _install_interrupt_notifier(
    futures: dict[Any, Any], timeout_s: int, item: str
) -> Any:
    """Install a SIGINT handler that prints a user-voice ack on first Ctrl-C.

    Second Ctrl-C restores the OS default handler and re-sends SIGINT so the
    process exits the way it would have without us. Returns the previous
    handler so the caller can restore it in a finally clause.

    `item` is the user-vocabulary noun ("companies" or "jobs"); `futures`
    is the live mapping so the message can name how many are in progress.

    signal.signal only works on the main thread, so an off-main-thread caller
    gets no notifier (returns None) instead of a ValueError crash. Pair the
    return with :func:`_restore_interrupt_handler`, which is likewise a no-op
    off the main thread.
    """
    if threading.current_thread() is not threading.main_thread():
        return None
    interrupt_count = [0]
    previous = signal.getsignal(signal.SIGINT)

    def handler(_signum: int, _frame: Any) -> None:
        interrupt_count[0] += 1
        if interrupt_count[0] == 1:
            in_flight = sum(1 for f in futures if not f.done())
            print(
                f"\nStopping — waiting for {in_flight} {item} still being "
                f"enriched (up to {timeout_s}s each). Press Ctrl-C again "
                "to quit now and lose what's in progress.",
                file=sys.stderr,
                flush=True,
            )
            raise KeyboardInterrupt
        # Second press: hand control back to whatever handler the parent had
        # before we installed ours, then re-raise the signal. Falls back to
        # SIG_DFL if `previous` isn't installable (e.g. a non-callable token).
        try:
            signal.signal(signal.SIGINT, previous)
        except (TypeError, ValueError):
            signal.signal(signal.SIGINT, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGINT)

    signal.signal(signal.SIGINT, handler)
    return previous


def _restore_interrupt_handler(previous: Any) -> None:
    """Restore the SIGINT handler returned by :func:`_install_interrupt_notifier`.

    A no-op off the main thread (where the install was also skipped, so
    ``previous`` is None) — guards the symmetric ``signal.signal`` restore
    against the same main-thread-only ValueError.
    """
    if threading.current_thread() is not threading.main_thread():
        return
    signal.signal(signal.SIGINT, previous)


# Records the signal that triggered a graceful stop so the CLI can pick the
# conventional exit code (130 for SIGINT, 143 for SIGTERM). A list so the SIGTERM
# handler (which must not rebind module globals from a signal context) can write
# it. Process-scoped, single-run state: the process runs at most one jobs run, and
# install_sigterm_handler() resets it at the start of each run, so there is no
# cross-run leakage.
_received_signal: list[int] = []


def install_sigterm_handler() -> Any:
    """Install a run-scoped SIGTERM handler that joins the SIGINT graceful path.

    A scheduled `jobs run` (launchd) is stopped with SIGTERM, not Ctrl-C; without
    a handler the default action kills the process immediately and the durable
    record loses any in-flight work. This handler raises ``KeyboardInterrupt`` so
    SIGTERM unwinds through the exact same drain/flush machinery as Ctrl-C, and
    records ``signal.SIGTERM`` so the CLI can exit 143 (128 + 15) rather than 130.

    ``signal.signal`` is main-thread-only, so an off-main-thread caller gets no
    handler (returns ``None``); pair the return with
    :func:`restore_sigterm_handler`, also a main-thread-only no-op. Returns the
    previous handler for restoration.
    """
    _received_signal.clear()
    if threading.current_thread() is not threading.main_thread():
        return None
    previous = signal.getsignal(signal.SIGTERM)

    def handler(_signum: int, _frame: Any) -> None:
        _received_signal.append(signal.SIGTERM)
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handler)
    return previous


def restore_sigterm_handler(previous: Any) -> None:
    """Restore the SIGTERM handler returned by :func:`install_sigterm_handler`."""
    if threading.current_thread() is not threading.main_thread():
        return
    signal.signal(signal.SIGTERM, previous)


def interrupted_by_sigterm() -> bool:
    """Whether the last graceful stop was triggered by SIGTERM (vs SIGINT)."""
    return bool(_received_signal) and _received_signal[-1] == signal.SIGTERM
