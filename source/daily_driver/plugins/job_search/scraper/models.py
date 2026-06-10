"""Typed pipeline records for the scraper.

Three-stage taxonomy: every job flows
    RawScrapedJob -> NormalizedJob -> EnrichedJob

Each stage is immutable (frozen=True). Transitions use model_copy(update=...)
or explicit `.from_raw()` / `.from_normalized()` helpers that return the next
stage's instance.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    Protocol,
    runtime_checkable,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

if TYPE_CHECKING:
    from daily_driver.plugins.job_search.scraper.runner import ScrapeContext


class JobStatus(str, Enum):
    FOUND = "found"
    SKIPPED = "skipped"
    APPLIED = "applied"
    REJECTED = "rejected"
    DROPPED = "dropped"


# Statuses surfaced in jobs.csv but excluded from the costly LLM enrichment
# passes (fit/notes/product): kept for visibility, not worth spending
# enrichment calls on.
ENRICH_SKIP_STATUSES: frozenset[str] = frozenset({JobStatus.SKIPPED.value})


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

# Product/Purpose default for un-enriched rows. Both fresh scraped jobs and
# blank CSV cells carry this, so enrichment can tell "needs fill" from a real
# value without a separate flag.
_PLACEHOLDER_PRODUCT = "(auto-scraped -- needs fill)"


def _parse_k_salary(s: str) -> int | None:
    """Parse a salary amount that may use K/M shorthand into a plain integer.

    Greenhouse boards (e.g. Anthropic) post "$150K-$200K" instead of full numbers.
    Returns None on unrecognized input so callers leave comp blank.
    """
    s = s.replace(",", "").strip()
    try:
        if s.upper().endswith("K"):
            return int(float(s[:-1]) * 1_000)
        if s.upper().endswith("M"):
            return int(float(s[:-1]) * 1_000_000)
        return int(float(s))
    except (ValueError, TypeError):
        return None


class RawScrapedJob(BaseModel):
    """Wire-format record from a scraper adapter.

    ``extra="ignore"`` — third-party libraries (jobspy especially) evolve
    their output schema; tolerating unknown keys is the right boundary policy.
    """

    model_config = ConfigDict(frozen=True, extra="ignore", str_strip_whitespace=True)

    company: str
    role: NonEmptyStr
    url: str
    source: NonEmptyStr
    location: str = ""
    comp_display: str = ""
    date_found: dt.date = Field(default_factory=lambda: dt.date.today())  # noqa: DTZ011

    @field_validator("url")
    @classmethod
    def _strip_url(cls, v: str) -> str:
        return v.strip()


class NormalizedJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    company: str
    role: NonEmptyStr
    location: str
    url: str
    source: NonEmptyStr
    source_canonical: NonEmptyStr
    source_board: str = ""
    comp: str = ""
    date_found: dt.date

    @classmethod
    def from_raw(cls, raw: RawScrapedJob) -> NormalizedJob:
        from daily_driver.plugins.job_search.scraper.runner import (
            _REMOTE_LOCATION_ALIASES,
            _REMOTE_ROLE_SUFFIXES,
        )

        loc = raw.location.strip()
        location = "Remote" if loc.lower() in _REMOTE_LOCATION_ALIASES else loc
        role = raw.role
        rl = role.lower()
        for suf in _REMOTE_ROLE_SUFFIXES:
            if rl.endswith(suf):
                role = role[: -len(suf)].rstrip()
                break
        src = raw.source
        if src.startswith("Greenhouse (") and src.endswith(")"):
            canonical = "greenhouse"
            board = src[len("Greenhouse (") : -1]
        else:
            canonical = src.split("/")[0].lower()
            board = ""
        return cls(
            company=raw.company,
            role=role,
            location=location,
            url=raw.url,
            source=raw.source,
            source_canonical=canonical,
            source_board=board,
            comp=raw.comp_display,
            date_found=raw.date_found,
        )


class JobDetails(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    comp: str = ""
    posted_date: dt.date | None = None
    description_text: str = ""


class EnrichedJob(BaseModel):
    """``frozen=True`` — enrichers must return ``model_copy(update=...)``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    company: str
    role: NonEmptyStr
    location: str
    url: str
    source: NonEmptyStr
    source_canonical: NonEmptyStr
    source_board: str = ""
    comp: str = ""
    date_found: dt.date

    product: str = _PLACEHOLDER_PRODUCT
    gd_rating: str = ""
    fit: int | None = Field(default=None, ge=1, le=10)
    notes: str = ""
    posted_date: dt.date | None = None
    description_text: str = ""

    status: JobStatus = JobStatus.FOUND
    skip_reason: str = ""
    date_applied: dt.date | None = None
    # Drives `jobs prune --older-than`. Defaults to Date Found on write when
    # unset (no upsert-on-rescan path yet), so prune ages from first-discovery.
    date_last_seen: dt.date | None = None

    # Ordered to match the on-disk jobs.csv column layout; CANONICAL_HEADER is
    # derived from this map's key order and must stay byte-for-byte identical to
    # existing jobs.csv files.
    CSV_COLUMN_TO_ATTR: ClassVar[dict[str, str]] = {
        "Status": "status",
        "Company": "company",
        "Role": "role",
        "Fit": "fit",
        "Comp": "comp",
        "Location": "location",
        "Product/Purpose": "product",
        "GD Rating": "gd_rating",
        "Notes": "notes",
        "Date Found": "date_found",
        "Date Applied": "date_applied",
        "Date Last Seen": "date_last_seen",
        "Link": "url",
        "Source": "source",
    }
    CANONICAL_HEADER: ClassVar[list[str]] = list(CSV_COLUMN_TO_ATTR)

    @property
    def product_filled(self) -> bool:
        """Whether Product/Purpose holds a real value (not the scrape placeholder).

        Fresh scraped rows and blank CSV cells both surface as the placeholder
        default, so company enrichment treats the placeholder as "still empty".
        """
        return bool(self.product) and self.product != _PLACEHOLDER_PRODUCT

    @property
    def product_or_blank(self) -> str:
        """Product text for prompts, blanked when it is only the placeholder."""
        return self.product if self.product_filled else ""

    @classmethod
    def from_normalized(cls, n: NormalizedJob) -> EnrichedJob:
        return cls(
            company=n.company,
            role=n.role,
            location=n.location,
            url=n.url,
            source=n.source,
            source_canonical=n.source_canonical,
            source_board=n.source_board,
            comp=n.comp,
            date_found=n.date_found,
        )

    def with_updates(self, **updates: Any) -> EnrichedJob:
        """Return a copy with ``updates`` applied through full pydantic validation.

        ``model_copy(update=...)`` bypasses validators, so field constraints
        (e.g. ``fit`` ``ge=1``) go unenforced — the W1.1 fit-bounds finding.
        Routing every update through ``model_validate`` keeps constraints live.
        """
        return type(self).model_validate({**self.model_dump(), **updates})

    def with_details(self, details: JobDetails) -> EnrichedJob:
        updates: dict[str, Any] = {}
        if details.comp and not self.comp:
            updates["comp"] = details.comp
        if details.posted_date and self.posted_date is None:
            updates["posted_date"] = details.posted_date
        if details.description_text and not self.description_text:
            updates["description_text"] = details.description_text
        return self.with_updates(**updates)

    def with_fit(self, score: int, notes: str) -> EnrichedJob:
        return self.with_updates(fit=score, notes=notes)

    def to_csv_row(self) -> dict[str, str]:
        notes = self.notes
        if self.status is JobStatus.SKIPPED and self.skip_reason:
            notes = (
                f"{self.notes} | {self.skip_reason}".strip(" |")
                if self.notes
                else self.skip_reason
            )
        return {
            "Status": self.status.value,
            "Company": self.company,
            "Role": self.role,
            "Fit": "" if self.fit is None else str(self.fit),
            "Comp": self.comp,
            "Location": self.location,
            "Product/Purpose": self.product,
            "GD Rating": self.gd_rating,
            "Notes": notes,
            "Date Found": self.date_found.isoformat(),
            "Date Applied": (
                "" if self.date_applied is None else self.date_applied.isoformat()
            ),
            # Seed from Date Found on insert so prune ages from first-discovery.
            "Date Last Seen": (self.date_last_seen or self.date_found).isoformat(),
            "Link": self.url,
            "Source": self.source,
        }

    @classmethod
    def from_csv_row(cls, row: dict[str, str]) -> EnrichedJob:
        def _opt_date(s: str) -> dt.date | None:
            s = (s or "").strip()
            return dt.date.fromisoformat(s) if s else None

        def _opt_int(s: str) -> int | None:
            s = (s or "").strip()
            return int(s) if s.isdigit() else None

        source = row.get("Source", "").strip() or "unknown"
        if source.startswith("Greenhouse (") and source.endswith(")"):
            canonical = "greenhouse"
            board = source[len("Greenhouse (") : -1]
        else:
            canonical = source.split("/")[0].lower() or "unknown"
            board = ""
        return cls(
            status=JobStatus(row.get("Status", "found") or "found"),
            notes=row.get("Notes", ""),
            company=row.get("Company", ""),
            location=row.get("Location", ""),
            role=row.get("Role", "") or "(unknown)",
            fit=_opt_int(row.get("Fit", "")),
            comp=row.get("Comp", ""),
            date_found=_opt_date(row.get("Date Found", ""))
            or dt.date.today(),  # noqa: DTZ011
            date_applied=_opt_date(row.get("Date Applied", "")),
            date_last_seen=_opt_date(row.get("Date Last Seen", "")),
            url=row.get("Link", ""),
            # Preserve a blank Product cell as blank (do not substitute the
            # placeholder) so a backfill rewrite is byte-identical for unenriched
            # rows; product_filled treats blank and placeholder alike.
            product=row.get("Product/Purpose", ""),
            gd_rating=row.get("GD Rating", ""),
            source=source,
            source_canonical=canonical,
            source_board=board,
        )


@runtime_checkable
class Source(Protocol):
    """Protocol for a scraper source callable.

    ``SCRAPERS: dict[str, Source]`` (in ``scraper/sources/__init__.py``) is the
    explicit dispatch dict.

    The callable receives a ``ScrapeContext`` carrying the validated
    ``JobSearchPlugin`` model plus the run's known-URL dedup set; sources read
    their transport/role knobs off ``ctx.plugin``. Adapters emit wire-format
    dicts; ``runner._enriched_from_scraped`` performs the one dict -> model
    validation step downstream.
    """

    def __call__(self, ctx: ScrapeContext) -> list[dict[str, Any]]: ...


__all__ = [
    "EnrichedJob",
    "JobDetails",
    "JobStatus",
    "NonEmptyStr",
    "NormalizedJob",
    "RawScrapedJob",
    "Source",
]
