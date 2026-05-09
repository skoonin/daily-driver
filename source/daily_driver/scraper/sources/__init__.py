"""Per-source scraper implementations and the SOURCE_REGISTRY."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from daily_driver.core.clock import today

from .apple import scrape_apple
from .greenhouse import scrape_greenhouse
from .hn_who_is_hiring import scrape_hn_who_is_hiring
from .jobspy import scrape_jobspy
from .remoteok import scrape_remoteok
from .wellfound import scrape_wellfound
from .weworkremotely import scrape_weworkremotely

log = logging.getLogger(__name__)


SCRAPERS: dict[str, Callable[[dict], list[dict]]] = {
    "remoteok": scrape_remoteok,
    "weworkremotely": scrape_weworkremotely,
    "hn_who_is_hiring": scrape_hn_who_is_hiring,
    "greenhouse": scrape_greenhouse,
    "jobspy": scrape_jobspy,
    "wellfound": scrape_wellfound,
    "apple": scrape_apple,
}


def _typed_source(
    fn: Callable[[dict[str, Any]], list[dict[str, Any]]],
) -> Callable[[dict[str, Any]], list[Any]]:
    """Wrap a dict-returning scraper into a Source-protocol callable.

    Validates each row through ``RawScrapedJob`` (Q15: ``extra='ignore'``).
    Rows that fail validation (empty role, etc.) are dropped with a debug log
    rather than aborting the whole source.
    """
    from daily_driver.scraper.models import RawScrapedJob

    def wrapped(config: dict[str, Any]) -> list[Any]:
        out: list[Any] = []
        for row in fn(config):
            try:
                out.append(
                    RawScrapedJob.model_validate(
                        {
                            "company": row.get("company", ""),
                            "role": row.get("role", ""),
                            "url": row.get("url", ""),
                            "source": row.get("source", ""),
                            "location": row.get("location", ""),
                            "comp_display": row.get("comp", "") or "",
                            "date_found": row.get("date_found") or today().isoformat(),
                        }
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("[%s] dropped invalid row: %s", fn.__name__, exc)
        return out

    wrapped.__name__ = f"typed_{fn.__name__}"
    return wrapped


# Q16: explicit Source registry — no dynamic dispatch, all 7 sources known
# at import time. Each entry is a Source-protocol callable that validates
# rows through RawScrapedJob.
SOURCE_REGISTRY: dict[str, Callable[[dict[str, Any]], list[Any]]] = {
    sid: _typed_source(fn) for sid, fn in SCRAPERS.items()
}


__all__ = ["SCRAPERS", "SOURCE_REGISTRY", "_typed_source"]
