from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from daily_driver.core.clock import today
from daily_driver.core.logging import get_logger, log_query_window

log = get_logger(__name__)


def gather_note_paths(
    output_dir: Path, since: datetime, until: datetime | None = None
) -> list[Path]:
    """Return .md files under {output_dir}/YYYY/MM/YYYY-MM-DD-*.md whose filename-date is in window."""
    since_date = since.date()
    until_date = until.date() if until is not None else today() + timedelta(days=1)
    log_query_window(
        log,
        f"notes ({output_dir})",
        datetime.combine(since_date, datetime.min.time()),
        datetime.combine(until_date, datetime.min.time()),
    )

    results: list[Path] = []
    for p in output_dir.glob("*/*/*.md"):
        try:
            fdate = datetime.strptime(p.stem[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if since_date <= fdate < until_date:
            results.append(p)

    return sorted(results)
