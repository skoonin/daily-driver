"""Board discovery: sweep the ATS slug universe for boards with in-scope roles.

`jobs discover-boards` fetches per-platform company-slug lists (seeded from the
job-board-aggregator project's published data), probes each candidate board's
public listing for titles, and records boards with at least one role match in a
per-platform sweep cache under the workspace state dir. `jobs run` scrapes the
matched boards (union hand-pinned config entries) so discovery never requires a
config edit.

Three cache files per platform under ``<state>/discovery/``:

- ``slugs-<platform>.json`` — the upstream slug universe + fetched_at; the
  sweep falls back to this cache when the upstream fetch fails (offline or
  upstream gone).
- ``sweep-<platform>.json`` — per-slug sweep outcomes (``last_swept`` +
  ``matched`` title count). Entries with ``matched > 0`` ARE the matched-board
  cache. Incremental sweeps probe only slugs never swept; ``--full`` re-probes
  everything, so a board that stopped matching drops out then.
- ``dead-<platform>.json`` — slugs that answered 404/410 (or Ashby's
  ``jobBoard: null``): permanently gone, never probed again. Transient
  failures (429 after retries, timeouts, 5xx) are NEVER cached as dead — they
  simply stay unswept and retry on the next sweep.
"""

from __future__ import annotations

import json
import random
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from daily_driver.core.clock import now
from daily_driver.core.locking import file_lock
from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.scraper.roles import matches_roles
from daily_driver.plugins.job_search.scraper.sources._http import (
    Session,
    _api_request,
    _http_session,
)

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.config import JobSearchPlugin
    from daily_driver.plugins.job_search.scraper.runner import ScrapeContext

log = get_logger(__name__)

# Platforms swept in v1. Lever joins once its adapter exists (ratified
# sequencing: adapter first, then sweep).
SWEEP_PLATFORMS: tuple[str, ...] = ("greenhouse", "ashby")

# Upstream slug lists: flat JSON arrays of board slugs, refreshed daily by the
# job-board-aggregator project (MIT code / CC BY-NC data — fine for this
# private tool). Later upgrade path: port its Common Crawl harvester and
# self-generate these lists into the same cache files.
_SLUG_LIST_URLS: dict[str, str] = {
    "greenhouse": (
        "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/"
        "main/data/greenhouse_companies.json"
    ),
    "ashby": (
        "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/"
        "main/data/ashby_companies.json"
    ),
}

# Per-platform worker caps (aggregator production defaults): greenhouse's API
# tolerates wide fan-out; Ashby's GraphQL endpoint rate-limits aggressively.
_WORKER_CAPS: dict[str, int] = {"greenhouse": 30, "ashby": 5}

# Jitter before each probe spreads concurrent workers so a full sweep never
# fires request bursts in lockstep (aggregator-proven pacing).
_JITTER_RANGE_SECONDS: tuple[float, float] = (0.2, 0.8)

# Statuses that mean the board is permanently gone — the ONLY statuses that
# enter the dead-slug cache.
_DEAD_STATUSES: frozenset[int] = frozenset({404, 410})

# Ashby's posting API always ships full descriptions (multi-MB for big
# boards), so the sweep uses the lighter titles-only GraphQL query the
# aggregator runs in production. `jobs run` scraping stays on the official
# posting API.
_ASHBY_GRAPHQL_URL = (
    "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobBoardWithTeams"
)
_ASHBY_TITLES_QUERY = (
    "query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) { "
    "jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: "
    "$organizationHostedJobsPageName) { jobPostings { id title } } }"
)


class DiscoveryError(RuntimeError):
    """Raised when a sweep cannot proceed (no slug universe available)."""


# ── Cache files ──────────────────────────────────────────────────────────────


def discovery_dir(state_dir: Path) -> Path:
    return state_dir / "discovery"


def _slugs_path(state_dir: Path, platform: str) -> Path:
    return discovery_dir(state_dir) / f"slugs-{platform}.json"


def _sweep_path(state_dir: Path, platform: str) -> Path:
    return discovery_dir(state_dir) / f"sweep-{platform}.json"


def _dead_path(state_dir: Path, platform: str) -> Path:
    return discovery_dir(state_dir) / f"dead-{platform}.json"


def _lock_path(state_dir: Path) -> Path:
    return discovery_dir(state_dir) / "discovery.lock"


def _read_json(path: Path) -> dict[str, Any] | None:
    """Parsed JSON object from path, or None when absent/corrupt."""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        log.warning("unreadable discovery cache %s; ignoring", path)
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomic JSON write (tmp + replace) so a crash never truncates a cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def load_matched_boards(state_dir: Path, platform: str) -> dict[str, dict[str, Any]]:
    """The matched-board cache: swept slugs with at least one role match.

    Returns ``{slug: {"matched": N, "last_swept": iso}}``; empty when no sweep
    has run. This is the seam `jobs run` unions with hand-pinned config boards.
    """
    payload = _read_json(_sweep_path(state_dir, platform)) or {}
    swept = payload.get("swept", {})
    if not isinstance(swept, dict):
        return {}
    return {
        slug: info
        for slug, info in swept.items()
        if isinstance(info, dict) and (info.get("matched") or 0) > 0
    }


def sweep_ages(state_dir: Path) -> dict[str, dict[str, Any]]:
    """Per-platform sweep metadata for `jobs status`.

    ``{platform: {"boards_matched": N, "slugs_swept": M, "last_swept": iso|None}}``
    for platforms that have sweep state on disk.
    """
    out: dict[str, dict[str, Any]] = {}
    for platform in SWEEP_PLATFORMS:
        payload = _read_json(_sweep_path(state_dir, platform))
        if payload is None:
            continue
        swept = payload.get("swept", {})
        if not isinstance(swept, dict):
            continue
        stamps = [
            str(info["last_swept"])
            for info in swept.values()
            if isinstance(info, dict) and info.get("last_swept")
        ]
        out[platform] = {
            "boards_matched": sum(
                1
                for info in swept.values()
                if isinstance(info, dict) and (info.get("matched") or 0) > 0
            ),
            "slugs_swept": len(swept),
            "last_swept": max(stamps) if stamps else None,
        }
    return out


# ── Slug universe ────────────────────────────────────────────────────────────


def fetch_slug_universe(
    platform: str, ctx: ScrapeContext, session: Session, state_dir: Path
) -> tuple[list[str], Literal["fetched", "cache"]]:
    """The platform's slug list: fresh from upstream, else the local cache.

    A successful fetch refreshes the cache; a failed fetch (offline, upstream
    gone) falls back to the cached copy so sweeps keep working. Raises
    DiscoveryError when neither is available.
    """
    url = _SLUG_LIST_URLS[platform]
    resp = _api_request(session, "GET", url, ctx, label=f"discover/{platform}-slugs")
    if resp is not None:
        try:
            slugs = resp.json()
        except ValueError:
            slugs = None
        if isinstance(slugs, list) and all(isinstance(s, str) for s in slugs):
            _write_json(
                _slugs_path(state_dir, platform),
                {"fetched_at": now().isoformat(), "slugs": slugs},
            )
            return slugs, "fetched"
        log.warning(
            "[discover/%s] slug list at %s is not a string array", platform, url
        )

    cached = _read_json(_slugs_path(state_dir, platform))
    if cached and isinstance(cached.get("slugs"), list):
        log.info(
            "[discover/%s] upstream fetch failed; using cached slug list from %s",
            platform,
            cached.get("fetched_at", "unknown"),
        )
        return list(cached["slugs"]), "cache"

    raise DiscoveryError(
        f"no slug list for {platform}: upstream fetch failed and no cache at "
        f"{_slugs_path(state_dir, platform)}"
    )


# ── Probes ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of probing one slug: matched title count, dead, or transient."""

    slug: str
    outcome: Literal["swept", "dead", "transient"]
    matched: int = 0


def _count_matches(titles: list[str], plugin: JobSearchPlugin) -> int:
    return sum(1 for t in titles if t and matches_roles(t, plugin))


def _probe_greenhouse(slug: str, ctx: ScrapeContext, session: Session) -> ProbeResult:
    """Titles-only board listing (no ``content=true``): cheap and complete."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    resp = _api_request(
        session,
        "GET",
        url,
        ctx,
        label=f"discover/greenhouse/{slug}",
        return_error_responses=True,
    )
    if resp is None:
        return ProbeResult(slug, "transient")
    if resp.status_code in _DEAD_STATUSES:
        return ProbeResult(slug, "dead")
    if resp.status_code != 200:
        return ProbeResult(slug, "transient")
    try:
        jobs = resp.json().get("jobs", [])
    except ValueError:
        return ProbeResult(slug, "transient")
    titles = [entry.get("title", "") for entry in jobs if isinstance(entry, dict)]
    return ProbeResult(slug, "swept", _count_matches(titles, ctx.plugin))


def _probe_ashby(slug: str, ctx: ScrapeContext, session: Session) -> ProbeResult:
    """Titles-only GraphQL query; ``jobBoard: null`` means the org is gone."""
    payload = {
        "operationName": "ApiJobBoardWithTeams",
        "variables": {"organizationHostedJobsPageName": slug},
        "query": _ASHBY_TITLES_QUERY,
    }
    resp = _api_request(
        session,
        "POST",
        _ASHBY_GRAPHQL_URL,
        ctx,
        json=payload,
        label=f"discover/ashby/{slug}",
        return_error_responses=True,
    )
    if resp is None:
        return ProbeResult(slug, "transient")
    if resp.status_code in _DEAD_STATUSES:
        return ProbeResult(slug, "dead")
    if resp.status_code != 200:
        return ProbeResult(slug, "transient")
    try:
        body = resp.json()
    except ValueError:
        return ProbeResult(slug, "transient")
    # GraphQL reports resolver failures as HTTP 200 with `errors` and/or a
    # null top-level `data` — transient conditions that must NEVER feed the
    # permanent dead cache. Only a present `data` whose `jobBoard` is null is
    # the endpoint's true 404 (verified live for unknown orgs).
    if body.get("errors") or not isinstance(body.get("data"), dict):
        return ProbeResult(slug, "transient")
    board = body["data"].get("jobBoard")
    if board is None:
        return ProbeResult(slug, "dead")
    postings = board.get("jobPostings") or []
    titles = [p.get("title", "") for p in postings if isinstance(p, dict)]
    return ProbeResult(slug, "swept", _count_matches(titles, ctx.plugin))


_PROBES: dict[str, Callable[[str, ScrapeContext, Session], ProbeResult]] = {
    "greenhouse": _probe_greenhouse,
    "ashby": _probe_ashby,
}


# ── Sweep ────────────────────────────────────────────────────────────────────


@dataclass
class PlatformSweep:
    """Summary of one platform's sweep, for the CLI table / JSON payload."""

    platform: str
    universe: int = 0
    universe_source: str = "fetched"
    candidates: int = 0
    swept: int = 0
    matched_new: int = 0
    matched_total: int = 0
    dead_new: int = 0
    transient: int = 0

    def as_dict(self) -> dict[str, Any]:
        return dict(vars(self))


# Flush sweep state every N completed probes so an interrupted multi-minute
# sweep keeps its finished work (incremental resume skips completed slugs).
_FLUSH_EVERY = 200


@dataclass
class _SweepState:
    """Mutable per-platform sweep bookkeeping.

    Mutated ONLY on the main thread: workers run probes and return results;
    ``_record``/``_flush`` consume them in the main-thread wait loop, so no
    lock is needed. Moving ``_record`` into workers would make ``_flush``'s
    ``json.dumps`` race the mutations — keep recording on the main thread.
    """

    sweep: dict[str, Any]
    dead: dict[str, Any]
    dirty: int = 0


def sweep_platform(
    platform: str,
    ctx: ScrapeContext,
    state_dir: Path,
    *,
    full: bool = False,
    stop_event: threading.Event | None = None,
    progress: Callable[[str, int], Callable[[], None]] | None = None,
    jitter: Callable[[], None] | None = None,
) -> PlatformSweep:
    """Sweep one platform's slug universe into its sweep/dead caches.

    Incremental by default (probe only slugs never swept); ``full`` re-probes
    every non-dead slug so stale matches age out. ``progress(platform, total)``
    is called once the candidate count is known and returns the per-slug
    advance callback; ``jitter`` overrides the pre-probe pacing sleep (tests
    pass a no-op). ``stop_event`` defaults to ``ctx.stop_event`` so callers
    share one cooperative stop signal.
    """
    session = _http_session(ctx)
    result = PlatformSweep(platform=platform)

    slugs, source = fetch_slug_universe(platform, ctx, session, state_dir)
    result.universe = len(slugs)
    result.universe_source = source

    sweep_payload = _read_json(_sweep_path(state_dir, platform)) or {}
    swept: dict[str, Any] = (
        sweep_payload.get("swept", {})
        if isinstance(sweep_payload.get("swept"), dict)
        else {}
    )
    dead_payload = _read_json(_dead_path(state_dir, platform)) or {}
    dead: dict[str, Any] = (
        dead_payload.get("dead", {})
        if isinstance(dead_payload.get("dead"), dict)
        else {}
    )

    previously_matched = {
        slug for slug, info in swept.items() if (info.get("matched") or 0) > 0
    }

    # dict.fromkeys dedups a slug the upstream list repeats while preserving
    # order, so summary counts never double-count a board.
    candidates = [
        slug
        for slug in dict.fromkeys(slugs)
        if slug not in dead and (full or slug not in swept)
    ]
    result.candidates = len(candidates)
    advance = progress(platform, len(candidates)) if progress is not None else None

    state = _SweepState(sweep=swept, dead=dead)
    probe = _PROBES[platform]
    stop = stop_event if stop_event is not None else ctx.stop_event

    # One requests.Session per worker thread: requests documents no
    # thread-safety guarantee for a shared Session, and one shared session's
    # default 10-connection pool would thrash under 30 workers anyway.
    thread_state = threading.local()

    def _thread_session() -> Session:
        sess = getattr(thread_state, "session", None)
        if sess is None:
            sess = _http_session(ctx)
            thread_state.session = sess
        return sess

    def _pace() -> None:
        if jitter is not None:
            jitter()
        else:
            time.sleep(random.uniform(*_JITTER_RANGE_SECONDS))

    def _flush() -> None:
        _write_json(_sweep_path(state_dir, platform), {"swept": state.sweep})
        _write_json(_dead_path(state_dir, platform), {"dead": state.dead})

    def _probe_one(slug: str) -> ProbeResult:
        if stop.is_set():
            return ProbeResult(slug, "transient")
        _pace()
        return probe(slug, ctx, _thread_session())

    def _record(res: ProbeResult) -> None:
        stamp = now().isoformat()
        if res.outcome == "swept":
            state.sweep[res.slug] = {"last_swept": stamp, "matched": res.matched}
            result.swept += 1
            if res.matched > 0:
                # INFO so -v answers "which boards matched, how strongly".
                log.info(
                    "[discover/%s] %s: %d matching titles",
                    platform,
                    res.slug,
                    res.matched,
                )
                if res.slug not in previously_matched:
                    result.matched_new += 1
            else:
                log.debug("[discover/%s] %s: no matching titles", platform, res.slug)
        elif res.outcome == "dead":
            state.dead[res.slug] = stamp
            state.sweep.pop(res.slug, None)
            result.dead_new += 1
            log.debug("[discover/%s] %s: gone (cached dead)", platform, res.slug)
        else:
            result.transient += 1
            # INFO so -v lists WHICH slugs will retry next sweep (the request
            # itself already warned with the failure detail).
            log.info("[discover/%s] %s: transient failure", platform, res.slug)
        state.dirty += 1
        if state.dirty >= _FLUSH_EVERY:
            state.dirty = 0
            _flush()

    workers = _WORKER_CAPS.get(platform, 10)
    if candidates:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending: set[Future[ProbeResult]] = {
                pool.submit(_probe_one, slug) for slug in candidates
            }
            try:
                while pending:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for fut in done:
                        _record(fut.result())
                        if advance is not None:
                            advance()
            except KeyboardInterrupt:
                # Keep recorded probes: cancel what has not started and flush
                # below. In-flight requests still run to completion on pool
                # shutdown (worst case: HTTP timeout plus remaining retry
                # backoff each), but their results are NOT recorded — those
                # slugs simply stay unswept and retry next sweep.
                stop.set()
                for fut in pending:
                    fut.cancel()
                raise
            finally:
                _flush()
    else:
        _flush()

    result.matched_total = sum(
        1 for info in state.sweep.values() if (info.get("matched") or 0) > 0
    )
    return result


def run_discovery(
    plugin: JobSearchPlugin,
    state_dir: Path,
    *,
    full: bool = False,
    platforms: tuple[str, ...] = SWEEP_PLATFORMS,
    progress: Callable[[str, int], Callable[[], None]] | None = None,
) -> dict[str, Any]:
    """Run the sweep across platforms under the discovery lock.

    ``progress(platform, total)`` returns the per-slug advance callback for
    that platform's phase (the CLI binds live bars here; None = quiet).
    Returns the summary payload for the CLI table / ``--json``. Interruption
    (Ctrl-C / SIGTERM raising KeyboardInterrupt on the main thread) unwinds
    through ``sweep_platform``'s drain-and-flush handler.
    """
    from daily_driver.plugins.job_search.scraper.runner import ScrapeContext

    ctx = ScrapeContext(plugin=plugin)
    started = now()
    summaries: list[PlatformSweep] = []
    with file_lock(_lock_path(state_dir), timeout=10):
        for platform in platforms:
            summaries.append(
                sweep_platform(
                    platform,
                    ctx,
                    state_dir,
                    full=full,
                    progress=progress,
                )
            )
    return {
        "started_at": started.isoformat(),
        "full": full,
        "platforms": {s.platform: s.as_dict() for s in summaries},
    }


__all__ = [
    "DiscoveryError",
    "PlatformSweep",
    "ProbeResult",
    "SWEEP_PLATFORMS",
    "discovery_dir",
    "fetch_slug_universe",
    "load_matched_boards",
    "run_discovery",
    "sweep_ages",
    "sweep_platform",
]
