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

    product: str = "(auto-scraped -- needs fill)"
    gd_rating: str = ""
    fit: int | None = Field(default=None, ge=1, le=10)
    notes: str = ""
    posted_date: dt.date | None = None
    description_text: str = ""

    status: JobStatus = JobStatus.FOUND
    skip_reason: str = ""
    date_applied: dt.date | None = None

    CSV_COLUMN_TO_ATTR: ClassVar[dict[str, str]] = {
        "Status": "status",
        "Notes": "notes",
        "Company": "company",
        "Location": "location",
        "Role": "role",
        "Fit": "fit",
        "Comp": "comp",
        "Date Found": "date_found",
        "Date Applied": "date_applied",
        "Link": "url",
        "Product/Purpose": "product",
        "GD Rating": "gd_rating",
        "Source": "source",
    }
    CANONICAL_HEADER: ClassVar[list[str]] = list(CSV_COLUMN_TO_ATTR)

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

    def with_details(self, details: JobDetails) -> EnrichedJob:
        updates: dict[str, Any] = {}
        if details.comp and not self.comp:
            updates["comp"] = details.comp
        if details.posted_date and self.posted_date is None:
            updates["posted_date"] = details.posted_date
        if details.description_text and not self.description_text:
            updates["description_text"] = details.description_text
        return self.model_copy(update=updates)

    def with_fit(self, score: int, notes: str) -> EnrichedJob:
        return self.model_copy(update={"fit": score, "notes": notes})

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
            "Notes": notes,
            "Company": self.company,
            "Location": self.location,
            "Role": self.role,
            "Fit": "" if self.fit is None else str(self.fit),
            "Comp": self.comp,
            "Date Found": self.date_found.isoformat(),
            "Date Applied": (
                "" if self.date_applied is None else self.date_applied.isoformat()
            ),
            "Link": self.url,
            "Product/Purpose": self.product,
            "GD Rating": self.gd_rating,
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
            url=row.get("Link", ""),
            product=(row.get("Product/Purpose", "") or "(auto-scraped -- needs fill)"),
            gd_rating=row.get("GD Rating", ""),
            source=source,
            source_canonical=canonical,
            source_board=board,
        )


@runtime_checkable
class Source(Protocol):
    """Protocol for a scraper source callable.

    ``SOURCE_REGISTRY: dict[str, Source]`` is the explicit dispatch dict.

    The callable receives a ``ScrapeContext`` carrying the validated
    ``JobSearchPlugin`` model plus the run's known-URL dedup set; sources read
    their transport/role knobs off ``ctx.plugin``.
    """

    def __call__(self, ctx: ScrapeContext) -> list[RawScrapedJob]: ...


__all__ = [
    "EnrichedJob",
    "JobDetails",
    "JobStatus",
    "NonEmptyStr",
    "NormalizedJob",
    "RawScrapedJob",
    "Source",
]
