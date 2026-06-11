"""Shared status vocabulary machinery for the tracker and jobs.csv.

One normalization rule and one warn-on-unknown helper, used by both the
tracker (core/tracker.py) and the job_search plugin (jobs.csv). The
*recommended sets* themselves stay with their domains — they differ — so this
module owns only the spelling rule and the warning, not any vocabulary.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence

_WHITESPACE = re.compile(r"\s+")


def normalize_status(value: str) -> str:
    """Canonicalize a status spelling: strip, lower-case, underscores/spaces -> hyphens.

    Spelling only, never meaning: ``ruled_out`` and ``Ruled Out`` both become
    ``ruled-out``. An empty or whitespace-only value normalizes to ``""``.
    """
    collapsed = _WHITESPACE.sub("-", value.strip())
    return collapsed.replace("_", "-").lower()


def warn_unknown_statuses(
    logger: logging.Logger,
    *,
    offending: Iterable[str],
    recommended: Sequence[str],
    hint: str | None = None,
) -> None:
    """Log one WARNING naming out-of-vocabulary statuses and the recommended set.

    A no-op when ``offending`` is empty. Callers own dedup (e.g. once per run);
    this emits a single record per call regardless of how many values it lists.
    """
    values = sorted({v for v in offending if v})
    if not values:
        return
    message = "status {} not in the recommended set ({})".format(
        ", ".join(repr(v) for v in values),
        ", ".join(recommended),
    )
    if hint:
        message = f"{message}\n         {hint}"
    logger.warning(message)
