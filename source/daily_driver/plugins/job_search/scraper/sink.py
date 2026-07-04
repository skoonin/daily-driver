"""The per-run durable-record sink: append, rescan, upgrade, flush.

:class:`_JobSink` owns one run's growing rows list and dedup state, appending
each source's rows to jobs.csv as it lands and rewriting the file in place as
enrichment fills cells. Built and driven by :func:`runner._run_impl` and
:func:`runner.run_backfill`.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any

from daily_driver.core.clock import today
from daily_driver.core.locking import file_lock
from daily_driver.core.logging import get_logger
from daily_driver.plugins.job_search.config import JobSearchPlugin
from daily_driver.plugins.job_search.jobs_lock import workspace_busy_notice
from daily_driver.plugins.job_search.scraper.models import (
    ENRICH_ELIGIBLE_STATUSES,
    split_source,
)
from daily_driver.plugins.job_search.scraper.rows import (
    _better_sighting,
    _board_upgrade_target,
    _csv_row_identity,
    _enriched_from_scraped,
    dedup_key,
    location_matches,
)

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.models import EnrichedJob

log = get_logger(__name__)


# Cells the enrichment waves may fill on a pre-existing (backlog) row. The
# in-place write-back is restricted to these so a concurrent hand-edit to any
# other cell (Status, Location, dates, ...) survives the run.
_ENRICHMENT_OWNED_CELLS: tuple[str, ...] = (
    "Fit",
    "Comp",
    "Notes",
    "Remote",
    "Date Enriched",
)


class _JobSink:
    """The durable-record checkpoint for one run: appends each source's rows to
    jobs.csv as it lands, then lets enrichment update those rows in place with
    periodic flushes.

    The sink is the single owner of the run's growing ``rows`` list and the
    dedup state. ``append_source`` dedups one source's scraped dicts (against the
    known url/key sets PLUS rows appended earlier this run), drops url-less rows,
    location-filters, lifts survivors to ``EnrichedJob``, appends them to
    jobs.csv under the sentinel lock, and folds them into ``rows`` so a later
    source (the Apple wave) dedups against them. ``flush`` rewrites the whole
    file -- ``preexisting_rows`` (what was on disk before the run, carried
    through verbatim) followed by ``rows`` -- through the one backfill rewrite
    path so a mid-enrichment crash loses at most one flush window. All sink
    methods run on the
    coordinator thread; no internal locking beyond the per-call sentinel lock,
    which is held only around each I/O burst — never across LLM calls.
    """

    def __init__(
        self,
        *,
        csv_path: Path,
        lock_path: Path,
        header: list[str],
        known_urls: set[str],
        known_keys: set[str],
        plugin: JobSearchPlugin,
        external_lock_held: bool = False,
        preexisting_rows: list[dict[str, str]] | None = None,
        descriptions: dict[str, str] | None = None,
    ) -> None:
        self.csv_path = csv_path
        self.lock_path = lock_path
        self.header = header
        # Sidecar description store (url -> text), loaded once by the caller.
        # Folded into by append_source, persisted by flush -- see the
        # descriptions module docstring for why this lives outside jobs.csv.
        self._descriptions = descriptions or {}
        self._descriptions_dirty = False
        # backfill holds the sentinel ``file_lock`` for its whole lifecycle (read
        # -> enrich -> rewrite) so a concurrent run's appends can't slip into the
        # window a backfill rewrite would clobber. flock is not reentrant across
        # fds in one process, so the sink must NOT re-take it per flush in that
        # mode -- it would deadlock against the lock the caller already holds.
        self._external_lock_held = external_lock_held
        # Dedup state: seeded from jobs.csv + the archive table, then GROWN as
        # rows are appended this run. A hit means the row is already on disk (or
        # was just written by an earlier source) -> "known". The Apple wave
        # dedups against phase-1's appended rows through this same set, so a
        # cross-source duplicate is phase-1-wins and counts as known (deliberate;
        # the row genuinely is in the csv by then). Intra-source exact duplicates
        # (one source's list repeating a row) are caught per-call and counted as
        # the funnel's "other", matching the old _per_source_funnel.
        self._known_urls = known_urls
        self._known_keys = known_keys
        self._plugin = plugin
        # Rows that were already in jobs.csv before this run, as raw CSV dicts.
        # ``flush`` rewrites the WHOLE file, so it must write these back first
        # (field-for-field, re-serialized -- never lifted through EnrichedJob,
        # so hand-edited cells round-trip untouched) or every pre-existing row
        # would be wiped. The run path captures them under the same initial
        # lock that seeds the dedup state; backfill leaves this empty (its
        # ``rows`` holds the whole file).
        self.preexisting_rows: list[dict[str, str]] = list(preexisting_rows or [])
        # Rows this run re-saw in a scrape (the deduped "known" branch): identity
        # (_csv_row_identity-compatible -- stripped URL, else (company, role) key)
        # -> the scraped job dict. Consumed once by ``_apply_rescan_updates`` in
        # ``flush`` to refresh Date Verified and heal a missing description on the
        # carried pre-existing row. Empty for backfill (it never scrapes).
        self._reseen: dict[str, dict[str, Any]] = {}
        # Pre-upgrade identities of THIS-RUN rows whose Source/Link the board
        # preference replaced. flush must exclude the on-disk pre-upgrade row
        # by its OLD identity too, or it would survive as a carried row beside
        # the upgraded rewrite (a duplicate).
        self._upgraded_identities: set[str] = set()
        # URLs that already carried a non-empty sidecar description at run start.
        # ``rescan_summary`` uses this to count how many re-sightings actually
        # HEAL a gap (fill-only) independently of when the persisting flush runs,
        # so the scrape-phase summary is accurate even before the final flush and
        # on --no-enrich runs.
        self._original_desc_urls: set[str] = {
            url for url, text in self._descriptions.items() if (text or "").strip()
        }
        # Master list every enricher mutates in place; also the flush source.
        self.rows: list[EnrichedJob] = []
        # Never-enriched backlog rows this run re-enriches in place. Kept OFF
        # ``rows``: routing them there would append them at the bottom of
        # jobs.csv (reorder) and inflate ``written``/new_jobs. Must be THE
        # SAME list object the backlog wave mutates via slot replacement so
        # flush_periodic observes in-flight results (cf. backfill's
        # ``sink.rows = jobs``). Landed at flush by _apply_folded_updates:
        # enrichment-owned cells only, position preserved.
        self.folded_rows: list[EnrichedJob] = []
        # Unknown / hand-added columns (e.g. a user's "Priority"), one dict per
        # row in ``rows``, aligned POSITIONALLY and 1:1. Positional (not Link-
        # keyed) carry-through is the only lossless identity: an empty-Link row
        # keeps its own extras, and two rows sharing a Link keep their DISTINCT
        # extras. The run path appends an empty dict per appended row (canonical-
        # only); backfill preloads one per pre-existing row. Enrichment replaces
        # row slots in place and never adds/drops rows, so this stays aligned.
        self.row_extras: list[dict[str, str]] = []
        # Ordered non-canonical column labels, appended after the canonical
        # header on rewrite. Empty for the run path (scraped rows are canonical);
        # set by backfill from the stored file's header so column ORDER is stable.
        self.extra_columns: list[str] = []
        # Optional one-shot hook fired inside ``flush`` immediately before the
        # first ``atomic_write_rows``. backfill uses it to take the pre-mutation
        # backup LAZILY -- only when a write actually happens -- so a no-op
        # backfill (e.g. ollama down, nothing to persist) neither backs up nor
        # rewrites the file. ``_pre_write_done`` makes it fire exactly once.
        self.pre_write_hook: Callable[[], None] | None = None
        self._pre_write_done = False
        # When set (backfill), ``flush`` reads the current file and SKIPS the
        # write -- and so the lazy backup -- when the rendered rows are byte-for-
        # byte identical to what is already on disk. This is the no-churn
        # guarantee: an ollama-down / nothing-changed backfill must not rewrite
        # the file or drop a backup. The run path leaves this False -- it always
        # appends new rows, so its flush always has something to persist, and
        # paying a per-flush full-file read+compare there would be wasted work.
        self.skip_flush_if_unchanged = False
        # Serializes every disk-mutating section against every other one. An
        # append (extend rows + write the rows to disk) and a flush (snapshot +
        # rewrite the whole file) must each be atomic relative to the other:
        # during scrape/enrich overlap the Apple wave appends on the coordinator
        # thread while the wave-1 enrichment thread flushes. Held for the whole
        # critical section, NOT just the in-memory step -- a flush interleaving
        # between an append's list-extend and its file-write would otherwise
        # duplicate or drop rows on disk. Order is always this lock then the
        # cross-process ``file_lock``, everywhere. Slot replacement
        # (out[i] = job) inside an enricher is GIL-atomic and only ever touches
        # an index that wave already owns, so it needs no lock.
        self._rows_lock = threading.Lock()
        # Per-source funnel accumulated at append time (mirrors _per_source_funnel).
        self.funnel: dict[str, dict[str, int]] = {}
        # Run totals for the reconciling Completed line.
        self.raw_found = 0
        # URLs of previously VERIFICATION-CLOSED archived rows (url -> closed
        # date). A re-discovered watch URL is a reopened job: appended normally
        # but announced loudly, never silently slipped back in.
        self.reopen_watch: dict[str, str] = {}
        self.reopened = 0
        # Aggregator rows rewritten to their board twin's record this run
        # (see _maybe_upgrade_source / _apply_run_row_upgrades). Surfaced in
        # the run summary and manifest -- a Source/Link rewrite on the durable
        # record must never be invisible to the user auditing jobs.csv.
        self.upgraded = 0
        # Board-diff closures decided this run: row URL -> the cell updates
        # (Status/Date Closed/Notes) the flush applies to the matching leading
        # row in place. Populated after the scrape by the closure engine.
        self.closures: dict[str, dict[str, str]] = {}
        self.pre_filter = 0  # new (deduped, url-bearing, pre-location) survivors
        self.loc_filtered = 0
        # Sticky: a periodic flush hit an OSError and the rows it would have
        # persisted live only in memory. Enrichment keeps going (the in-memory
        # state is intact and the final flush retries), but run() warns at the
        # end so a silent disk problem can't masquerade as a healthy run.
        self.persistence_degraded = False
        self._degraded_reason = ""

    def _disk_lock(self) -> AbstractContextManager[None]:
        """The cross-process lock to take around a disk-mutating section.

        When the caller already holds the sentinel ``file_lock`` for the whole
        lifecycle (backfill), this is a no-op so the inner section does not
        re-acquire a non-reentrant flock and deadlock. Otherwise (run) it is the
        real per-section ``file_lock``.
        """
        if self._external_lock_held:
            return nullcontext()
        # Notice-only (no timeout): a mid-run flush must never abort a healthy
        # run on a slow peer. The non-blocking-first acquire keeps the notice
        # silent unless a flush genuinely contends with another command.
        return file_lock(self.lock_path, on_contention=workspace_busy_notice)

    def append_source(
        self, source_id: str, jobs: list[dict[str, Any]]
    ) -> dict[str, int]:
        """Dedup/filter/lift one source's rows, append them, return its funnel.

        Returns the per-source counts (found/new/known/loc_skip). The dedup-set
        growth, the funnel/totals, and the master ``rows`` list are committed
        ONLY after the file append succeeds, so the in-memory state never claims
        rows that aren't on disk (no phantom dedup entries). A file-write failure
        leaves all state untouched and raises ``ScraperError`` for the caller to
        isolate as a failed source.

        Safe to call repeatedly for the SAME ``source_id``: the funnel/totals
        accumulate (``setdefault`` + ``+=``) and the dedup sets grow, so the slow
        jobspy-backed sources checkpoint each finished (term x country) unit
        and the board sources each finished board by calling this per unit. Later units dedup against earlier ones through the
        grown ``_known_urls`` / ``_known_keys``, so an intra-source duplicate
        spanning units is counted "known", never double-appended.

        Thread-safe for concurrent callers: the WHOLE body (dedup-set reads,
        classification, the file write, and every commit) runs under
        ``_rows_lock``, so a jobspy worker thread checkpointing a unit and the
        coordinator appending another source serialize cleanly. The disk lock is
        taken inside, around the write only (lock order is always _rows_lock then
        file_lock, matching :meth:`flush`).
        """
        from daily_driver.plugins.job_search.scraper.csv_io import append_jobs_typed

        with self._rows_lock:
            counts = self.funnel.setdefault(
                source_id, {"found": 0, "new": 0, "known": 0, "loc_skip": 0}
            )
            # Classify the source's rows into a deferred-commit plan WITHOUT
            # mutating any shared state yet. ``found`` is the only counter touched
            # here (the raw scrape tally; it survives a write failure); every
            # classified count and dedup-set growth is applied below, after the
            # write lands.
            counts["found"] += len(jobs)
            self.raw_found += len(jobs)
            # Intra-source dedup: a single source's list may repeat a row (e.g.
            # Apple's locale loop). Those are the funnel's "other" -- found but not
            # classified -- so detect them here without counting them as "known".
            seen_urls: set[str] = set()
            seen_keys: set[str] = set()
            to_append: list[EnrichedJob] = []
            commit_urls: list[str] = []
            commit_keys: list[str] = []
            known = 0
            loc_skip = 0
            new = 0
            for job in jobs:
                # Strip the url at the dedup boundary so the in-memory known-set
                # key matches the stripped value RawScrapedJob writes to disk and
                # load_existing_jobs seeds back -- otherwise a padded url dedups
                # unstripped here but stripped on disk, re-appearing as a dup.
                url = (job.get("url") or "").strip()
                key = dedup_key(job.get("company", ""), job.get("role", ""))
                if (url and url in seen_urls) or (key and key in seen_keys):
                    continue  # within-call duplicate -> "other"
                if url:
                    seen_urls.add(url)
                if key:
                    seen_keys.add(key)
                if (url and url in self._known_urls) or (
                    key and key in self._known_keys
                ):
                    known += 1
                    # Record the re-sighting so ``_apply_rescan_updates`` can act
                    # on the carried row at flush -- under BOTH identities: a
                    # cross-source twin matches by company+role key while the
                    # stored row's identity is its own (different) Link, so the
                    # key entry is the only handle that row can find it by.
                    for identity in (url, key):
                        if identity and _better_sighting(
                            self._reseen.get(identity), job
                        ):
                            self._reseen[identity] = job
                    continue
                if not url:
                    # url-less new jobs cannot be deduped on future runs; drop them.
                    log.warning(
                        "Dropping job with no URL (cannot dedup on future runs): %s",
                        job.get("company", ""),
                    )
                    continue
                if not location_matches(job, self._plugin):
                    loc_skip += 1
                    continue
                new += 1
                commit_urls.append(url)
                if key:
                    commit_keys.append(key)
                to_append.append(_enriched_from_scraped(job))

            # A watch-listed URL belongs to a job that verification once
            # closed: announce the reopen loudly (it still appends below).
            for enriched_job in to_append:
                closed_on = self.reopen_watch.get(enriched_job.url)
                if closed_on is not None:
                    self.reopened += 1
                    log.warning(
                        "Reopened (previously closed %s): %s -- %s %s",
                        closed_on or "unknown date",
                        enriched_job.company,
                        enriched_job.role,
                        enriched_job.url,
                    )

            # Fold any scrape-time descriptions (e.g. JobSpy's LinkedIn body)
            # into the sidecar store so flush persists them alongside the row.
            for enriched_job in to_append:
                if (
                    enriched_job.url
                    and enriched_job.description_text
                    and self._descriptions.get(enriched_job.url)
                    != enriched_job.description_text
                ):
                    self._descriptions[enriched_job.url] = enriched_job.description_text
                    self._descriptions_dirty = True

            if to_append:
                # Write to disk first, then commit the in-memory state, all under
                # the held _rows_lock so a concurrent flush can't interleave
                # between extend and write (which would duplicate/drop rows on
                # disk). append_jobs_typed raises ScraperError on an OSError, which
                # propagates here BEFORE the rows.extend and the dedup/funnel
                # commit below -- so a write failure rolls back to nothing
                # committed and the caller isolates the source.
                with self._disk_lock():
                    append_jobs_typed(self.csv_path, to_append, self.header)
                self.rows.extend(to_append)
                # Newly scraped rows carry only canonical columns; keep the
                # extras list aligned 1:1 so a later flush merges by index.
                self.row_extras.extend({} for _ in to_append)
            # Commit dedup state + funnel/totals only now that the rows are durable.
            self._known_urls.update(commit_urls)
            self._known_keys.update(commit_keys)
            counts["known"] += known
            counts["loc_skip"] += loc_skip
            counts["new"] += new
            self.loc_filtered += loc_skip
            self.pre_filter += new
            return dict(counts)

    def _apply_rescan_updates(self, leading_rows: list[dict[str, str]]) -> None:
        """Apply updates to rows we re-saw this run (deduped 'known' rows).

        The single seam for on-rescan behavior -- add/remove actions here.
        Backfill never scrapes, so ``_reseen`` is empty and this is a no-op.
        """
        if not self._reseen:
            return
        stamp = today().isoformat()
        for row in leading_rows:
            job = self._reseen_for(row)
            if job is None:
                continue
            # Before the description heal: the upgrade rewrites Link, and the
            # heal must file the description under the URL the row ends up with.
            self._maybe_upgrade_source(row, job)
            # Freshness: a re-sighting is affirmative liveness evidence (drives
            # stale detection / prune). Log only on an actual change so periodic
            # + final flushes don't repeat the line for the same row.
            if row.get("Date Verified") != stamp:
                log.info(
                    "Re-seen, Date Verified -> %s: %s", stamp, _csv_row_identity(row)
                )
                row["Date Verified"] = stamp
            # Heal a missing sidecar description (Indeed etc. carry it only at
            # scrape; the detail enricher is bot-walled). Fill-only; never
            # overwrite an existing entry (a re-scrape may be truncated).
            url = (row.get("Link") or "").strip()
            if url:
                desc = (job.get("description_text") or "").strip()
                if desc and not self._descriptions.get(url, "").strip():
                    self._descriptions[url] = desc
                    self._descriptions_dirty = True
                    log.info(
                        "Healed missing description (%d chars): %s", len(desc), url
                    )

    def _reseen_for(self, row: dict[str, str]) -> dict[str, Any] | None:
        """This run's best re-sighting for a stored row: Link slot vs key slot.

        A cross-source twin was classified known via the company+role key but
        carries its own URL, so the stored row's Link never appears in the
        collector -- the key entry is the only handle. When BOTH slots hit
        (the row's own source re-saw it AND a twin arrived), the same
        preference that fills the slots picks between them, so a board
        sighting is never shadowed by a same-URL aggregator one.
        """
        url_job = self._reseen.get(_csv_row_identity(row))
        key = dedup_key(row.get("Company") or "", row.get("Role") or "")
        key_job = self._reseen.get(key) if key else None
        if url_job is None:
            return key_job
        if (
            key_job is not None
            and key_job is not url_job
            and _better_sighting(url_job, key_job)
        ):
            return key_job
        return url_job

    def _maybe_upgrade_source(self, row: dict[str, str], job: dict[str, Any]) -> None:
        """Upgrade an aggregator row in place to the board record that re-sighted it.

        Board sources enumerate a company's complete listing: their hosted URL
        is stable and board-diff closure can verify it, while an aggregator
        Link (LinkedIn especially) rots behind a signup wall and verifies
        nothing. First-arrival-wins dedup means the aggregator row is already
        on disk when the board twin lands -- so preference is applied here, at
        flush, by rewriting Source/Link on the stored row. Untriaged rows only
        (the record behind a user's application never changes under them);
        board-to-board collisions keep first-wins; there is no downgrade path.
        """
        from daily_driver.core.statuses import normalize_status

        target = _board_upgrade_target(row.get("Source") or "", job)
        if target is None:
            return
        if normalize_status(row.get("Status") or "") not in ENRICH_ELIGIBLE_STATUSES:
            return
        new_source, new_url = target
        log.info(
            "Upgraded to board record: %s -- %s (%s %s -> %s %s)",
            row.get("Company", ""),
            row.get("Role", ""),
            row.get("Source", ""),
            row.get("Link", ""),
            new_source,
            new_url,
        )
        row["Source"] = new_source
        row["Link"] = new_url
        # Fill-only comp: the board record may carry scrape-time pay
        # (greenhouse/lever) the aggregator row never had.
        if not (row.get("Comp") or "").strip() and (job.get("comp") or "").strip():
            row["Comp"] = job["comp"]
        self.upgraded += 1

    def _apply_run_row_upgrades(self) -> None:
        """Apply the board-record upgrade to rows appended THIS run.

        ``_apply_rescan_updates`` sees only carried (pre-run) rows; a fast
        aggregator source's row appended earlier this same run collides the
        same way when the slow board walk reaches its twin, so it is upgraded
        here in the in-memory list flush writes out. Caller holds _rows_lock.
        """
        if not self._reseen:
            return
        for i, enriched in enumerate(self.rows):
            if enriched.status not in ENRICH_ELIGIBLE_STATUSES:
                continue
            key = dedup_key(enriched.company, enriched.role)
            job = self._reseen.get(key) if key else None
            if job is None:
                continue
            target = _board_upgrade_target(enriched.source, job)
            if target is None:
                continue
            new_source, new_url = target
            canonical, board = split_source(new_source)
            updates: dict[str, Any] = {
                "source": new_source,
                "url": new_url,
                "source_canonical": canonical,
                "source_board": board,
            }
            if not enriched.comp.strip() and (job.get("comp") or "").strip():
                updates["comp"] = job["comp"]
            if not enriched.description_text.strip():
                desc = (job.get("description_text") or "").strip()
                if desc:
                    updates["description_text"] = desc
            log.info(
                "Upgraded to board record: %s -- %s (%s %s -> %s %s)",
                enriched.company,
                enriched.role,
                enriched.source,
                enriched.url,
                new_source,
                new_url,
            )
            self._upgraded_identities.add(_csv_row_identity(enriched.to_csv_row()))
            self.rows[i] = enriched.model_copy(update=updates)
            self.upgraded += 1

    def _apply_folded_updates(self, leading_rows: list[dict[str, str]]) -> None:
        """Land backlog-wave enrichment on the matching leading rows in place.

        Writes ONLY the enrichment-owned cells: leading_rows is a fresh disk
        re-read, so a full-row overwrite from the run-start snapshot would
        revert any concurrent hand-edit (e.g. a Status flip mid-run). Rows are
        matched by identity and iterated row-side (like _apply_rescan_updates)
        so duplicate-identity rows all update. A folded row whose identity
        vanished from disk (concurrent prune) matches nothing -- its
        enrichment is discarded, never resurrected.
        """
        if not self.folded_rows:
            return
        by_identity: dict[str, dict[str, str]] = {}
        for job in self.folded_rows:
            csv_row = job.to_csv_row()
            by_identity[_csv_row_identity(csv_row)] = csv_row
        for row in leading_rows:
            src = by_identity.get(_csv_row_identity(row))
            if src is None:
                continue
            for cell in _ENRICHMENT_OWNED_CELLS:
                row[cell] = src[cell]

    def _apply_closure_updates(self, leading_rows: list[dict[str, str]]) -> None:
        """Land board-diff closures on the matching leading rows in place.

        Writes only Status / Date Closed / Notes, keyed by the row's Link, so
        position, extras, and every other cell survive. A closed row later
        leaves for the archive via prune (closed is a default prune status).
        """
        if not self.closures:
            return
        for row in leading_rows:
            updates = self.closures.get((row.get("Link") or "").strip())
            if updates:
                row.update(updates)

    def has_resightings(self) -> bool:
        """True when this run re-saw a known row.

        A --no-enrich run makes no final rewrite, so it must flush explicitly to
        persist the refreshed Date Verified / healed description this signals.
        """
        return bool(self._reseen)

    def rescan_summary(self) -> tuple[int, int, int]:
        """Freshness split for the scraping summary, independent of flush timing.

        Returns ``(still_visible, not_seen, healed)``: pre-existing rows the
        scrape re-confirmed live, pre-existing rows it did not re-return, and
        re-sightings that fill a previously-empty sidecar description. Derived
        from the re-sighting collector and the run-start pre-existing rows, so it
        is accurate before the persisting flush runs (and for --no-enrich runs).
        """
        still_visible = 0
        not_seen = 0
        healed = 0
        for row in self.preexisting_rows:
            job = self._reseen_for(row)
            if job is None:
                not_seen += 1
                continue
            still_visible += 1
            url = (row.get("Link") or "").strip()
            if (
                url
                and url not in self._original_desc_urls
                and (job.get("description_text") or "").strip()
            ):
                healed += 1
        return still_visible, not_seen, healed

    def flush(self) -> None:
        """Rewrite jobs.csv -- carried non-run rows then ``rows`` -- through the
        one backfill rewrite path.

        Snapshot AND rewrite happen under one held ``_rows_lock`` + ``file_lock``
        so the whole flush is atomic relative to an append (no interleave between
        reading the rows and writing them out). The locks are held only for the
        rewrite, never across the slow LLM calls that produced the updates, so a
        concurrent prune/backfill is serialized but not starved.

        On the run path the sentinel lock was released for the long scrape/enrich
        phase, so the rows that existed at run start may have changed since. Each
        flush therefore RE-READS the current on-disk rows under the held lock and
        keeps the ones that are not this run's -- so a row a concurrent ``prune``
        or hand-edit removed stays removed and a field edit this run did not make
        survives -- then layers this run's enriched rows on top by identity (Link
        URL, falling back to the ``(company, role)`` dedup key). backfill keeps the
        sentinel lock for its whole lifecycle, so it replays its in-memory
        ``preexisting_rows`` directly without a re-read.

        Unknown / hand-added columns are carried through POSITIONALLY via
        ``row_extras`` (aligned 1:1 with ``rows``), not keyed by Link: every row
        keeps exactly ITS OWN extras even when its Link is blank or shared with
        another row.

        Raises ``OSError`` on a write failure -- the caller decides whether to
        degrade (periodic flush) or propagate (final flush). See
        :meth:`flush_periodic`.
        """
        from daily_driver.plugins.job_search.scraper.csv_io import (
            atomic_write_rows,
            read_rows,
        )
        from daily_driver.plugins.job_search.scraper.descriptions import (
            atomic_write_descriptions,
        )

        with self._rows_lock, self._disk_lock():
            # Board-record upgrades for this run's own rows, BEFORE the
            # snapshot, so the rewrite below carries the upgraded records.
            self._apply_run_row_upgrades()
            snapshot = list(self.rows)
            extras_snapshot = list(self.row_extras)
            out_header = self.header + self.extra_columns
            if self._external_lock_held:
                # backfill holds the sentinel lock for its whole lifecycle, so the
                # ``preexisting_rows`` snapshot can't have gone stale under it.
                leading_rows: list[dict[str, str]] = list(self.preexisting_rows)
            else:
                # run path: the sentinel lock was RELEASED for the minutes-long
                # scrape/enrich phase, so the snapshot captured at run start may be
                # stale -- a concurrent prune/hand-edit could have removed or
                # changed rows since. Re-read the current on-disk rows under the
                # held flush lock and keep every row that is NOT one of this run's
                # rows. A row a concurrent writer removed stays removed; a field
                # edit this run did not make survives; this run's enriched rows are
                # layered on top below by append order. (The run's own appended
                # rows are already on disk -- exclude them by identity so the
                # in-memory enriched version wins instead of being duplicated.)
                _, on_disk_rows = read_rows(self.csv_path)
                run_identities = {
                    ident
                    for job in snapshot
                    if (ident := _csv_row_identity(job.to_csv_row()))
                }
                # An upgraded run row changed its Link mid-run: its on-disk
                # pre-upgrade append must be excluded by the OLD identity too.
                run_identities |= self._upgraded_identities
                leading_rows = [
                    row
                    for row in on_disk_rows
                    if _csv_row_identity(row) not in run_identities
                ]
            # Act on rows we re-saw this run BEFORE the no-churn comparison, so a
            # re-seen-only run (Date Verified bumped, no new rows) is detected as
            # changed and rewrites jobs.csv. Mutates ``leading_rows`` in place
            # under the held ``_rows_lock``. Order IS load-bearing since the
            # source-upgrade preference: folded updates match rows by identity
            # (Link), so they must land before rescan rewrites Link on upgraded
            # rows. Closures stay safe after either: they are decided from the
            # pre-upgrade disk state, and an aggregator-sourced row matches no
            # enumerated board, so no closure ever targets a Link the upgrade
            # replaces.
            self._apply_folded_updates(leading_rows)
            self._apply_rescan_updates(leading_rows)
            self._apply_closure_updates(leading_rows)
            # Carried rows lead, field-for-field; this run's rows follow in append
            # order, so the rewrite preserves the on-disk layout.
            out_rows: list[dict[str, str]] = list(leading_rows)
            for i, job in enumerate(snapshot):
                csv_row = job.to_csv_row()
                if i < len(extras_snapshot):
                    csv_row.update(extras_snapshot[i])
                out_rows.append(csv_row)
            if self.skip_flush_if_unchanged:
                # No-churn: skip the write (and the lazy backup) when the file
                # already holds exactly these rows. read_rows returns only the
                # cells; compare against the same projection of out_rows.
                cur_header, cur_rows = read_rows(self.csv_path)
                projected = [
                    {c: row.get(c, "") for c in out_header} for row in out_rows
                ]
                if cur_header == out_header and cur_rows == projected:
                    return
            # Fire the one-shot pre-write hook (backfill's lazy backup) BEFORE the
            # write, inside the lock, so the backup snapshots the on-disk state
            # that this write is about to replace.
            if self.pre_write_hook is not None and not self._pre_write_done:
                self._pre_write_done = True
                self.pre_write_hook()
            atomic_write_rows(self.csv_path, out_header, out_rows)
            # Detail enrichment sets description_text via with_updates directly
            # on sink.rows, never through append_source -- fold the snapshot's
            # current descriptions in here so flush is the one place that
            # reliably observes every description, however it was filled.
            for enriched_job in snapshot:
                if (
                    enriched_job.url
                    and enriched_job.description_text
                    and self._descriptions.get(enriched_job.url)
                    != enriched_job.description_text
                ):
                    self._descriptions[enriched_job.url] = enriched_job.description_text
                    self._descriptions_dirty = True
            if self._descriptions_dirty:
                atomic_write_descriptions(self.csv_path, self._descriptions)
                self._descriptions_dirty = False

    def flush_periodic(self) -> None:
        """Best-effort flush for the in-loop resilience hook.

        A periodic flush that fails must NOT abort enrichment -- the in-memory
        state is intact and the final flush will retry. Instead it sets the
        sticky ``persistence_degraded`` flag (logged once at WARN as a
        PERSISTENCE failure, distinct from an enrichment failure) so run() can
        surface it at the end. Distinguishing this from an enrichment failure is
        the whole point: repeated disk errors must not masquerade as a healthy
        run while hours of enrichment live only in memory.
        """
        try:
            self.flush()
        except OSError as exc:
            self._degraded_reason = str(exc)
            if not self.persistence_degraded:
                self.persistence_degraded = True
                log.warning(
                    "PERSISTENCE failure: periodic save of %s failed (%s); "
                    "enrichment continues in memory and the final save will "
                    "retry",
                    self.csv_path,
                    exc,
                )
