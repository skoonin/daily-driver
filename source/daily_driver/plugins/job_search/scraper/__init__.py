"""Job-board scraper package: public re-exports.

External callers should use ``run()`` / ``run_backfill()``. Internal modules
(``runner``, ``csv_io``, ``enrichment``, ``parsing``, ``comp``, ``sources/*``)
expose finer-grained surfaces for tests and CLI plumbing.
"""

from __future__ import annotations

from .context import ScraperError  # noqa: F401  (public API: run/run_backfill raise it)
from .parsing import _fix_mojibake  # noqa: F401  (re-export for tests)
from .runner import (  # noqa: F401  (public API + test re-exports)
    _print_dry_run_table,
    run,
    run_backfill,
)

__all__ = ["ScraperError", "run", "run_backfill"]
