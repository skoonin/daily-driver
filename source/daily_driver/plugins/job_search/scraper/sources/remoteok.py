"""RemoteOK source: public JSON API."""

from __future__ import annotations

from daily_driver.core.clock import today
from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.scraper.sources._http import (
    _api_get,
    _http_session,
)

log = get_logger(__name__)


def scrape_remoteok(config: dict) -> list[dict]:
    """Fetch jobs from RemoteOK's public JSON API.

    GET https://remoteok.com/api returns all current listings as JSON.
    No auth or browser required. We filter client-side with matches_roles().
    """
    from daily_driver.plugins.job_search.scraper.runner import matches_roles, roles_list

    roles = roles_list(config)
    session = _http_session(config)
    jobs: list[dict] = []
    seen_ids: set[str] = set()

    resp = _api_get(session, "https://remoteok.com/api", config, label="remoteok")
    if not resp:
        return jobs

    for item in resp.json():
        if "position" not in item:
            continue
        role = item["position"]
        if not matches_roles(role, roles, config):
            continue
        job_id = str(item.get("id", ""))
        if job_id in seen_ids:
            continue
        if job_id:
            seen_ids.add(job_id)
        sal_min = item.get("salary_min")
        sal_max = item.get("salary_max")
        currency = item.get("salary_currency") or "USD"
        prefix = "$" if currency == "USD" else f"{currency} "
        comp = (
            f"{prefix}{int(sal_min):,}-{prefix}{int(sal_max):,}/yr"
            if sal_min and sal_max
            else ""
        )
        job: dict = {
            "company": item.get("company", ""),
            "role": role,
            "location": item.get("location", "") or "Remote",
            "url": item.get("url", ""),
            "source": "RemoteOK",
            "date_found": today().isoformat(),
        }
        if comp:
            job["comp"] = comp
        jobs.append(job)

    log.info("[remoteok] %d jobs matched", len(jobs))
    return jobs


__all__ = ["scrape_remoteok"]
