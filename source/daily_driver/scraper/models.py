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
    Annotated,
    Any,
    ClassVar,
    Literal,
    Protocol,
    Self,
    runtime_checkable,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from daily_driver.core.config_models import ScraperConfig


class JobStatus(str, Enum):
    FOUND = "found"
    SKIPPED = "skipped"
    APPLIED = "applied"
    REJECTED = "rejected"
    ARCHIVED = "archived"


CompPeriod = Literal["hour", "month", "year"]
CurrencyCode = Literal["USD", "CAD", "GBP", "EUR"]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


_FX_TABLE: dict[CurrencyCode, float] = {
    "USD": 1.0,
    "CAD": 0.73,
    "GBP": 1.26,
    "EUR": 1.09,
}


def _fx(currency: CurrencyCode | None) -> float:
    if currency is None:
        return 1.0
    return _FX_TABLE.get(currency, 1.0)


class Comp(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    min_native: int | None = Field(default=None, ge=0)
    max_native: int | None = Field(default=None, ge=0)
    currency: CurrencyCode | None = None
    period: CompPeriod = "year"
    raw_display: str = ""

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        has_amount = self.min_native is not None or self.max_native is not None
        if has_amount and self.currency is None:
            raise ValueError("Comp.currency required when any amount is set")
        if (
            self.min_native is not None
            and self.max_native is not None
            and self.min_native > self.max_native
        ):
            raise ValueError(
                f"Comp.min_native ({self.min_native}) > max_native ({self.max_native})"
            )
        return self

    @property
    def is_known(self) -> bool:
        return self.min_native is not None or self.max_native is not None

    @property
    def min_usd(self) -> int | None:
        if self.min_native is None:
            return None
        return int(self.min_native * _fx(self.currency))

    @property
    def max_usd(self) -> int | None:
        if self.max_native is None:
            return None
        return int(self.max_native * _fx(self.currency))

    def meets_threshold(self, threshold_usd: int) -> tuple[bool, str]:
        if self.max_usd is None:
            return True, ""
        if self.max_usd >= threshold_usd:
            return True, ""
        return (
            False,
            f"below comp threshold (max ${self.max_usd:,} < ${threshold_usd:,})",
        )

    def __str__(self) -> str:
        if not self.is_known:
            return self.raw_display
        sym = "$" if self.currency == "USD" else f"{self.currency} "
        suffix = {"hour": "/hr", "month": "/mo", "year": "/yr"}[self.period]
        if (
            self.min_native is not None
            and self.max_native is not None
            and self.min_native != self.max_native
        ):
            return f"{sym}{self.min_native:,}-{sym}{self.max_native:,}{suffix}"
        amt = self.min_native if self.min_native is not None else self.max_native
        return f"{sym}{amt:,}{suffix}"

    @classmethod
    def parse(cls, display: str) -> Comp:
        # Deferred import: _parse_comp moves into this class in K-later;
        # for K1 we wrap the legacy parser so callers can adopt Comp first.
        from daily_driver.scraper._impl import _parse_comp

        parsed = _parse_comp(display)
        if parsed["comp_min_native"] is None:
            return cls(raw_display=display)
        period_raw = parsed["comp_period"] or "year"
        period: CompPeriod = (
            "hour"
            if period_raw == "hour"
            else "month" if period_raw == "month" else "year"
        )
        currency_raw = parsed["comp_currency"] or None
        currency: CurrencyCode | None
        if currency_raw == "USD":
            currency = "USD"
        elif currency_raw == "CAD":
            currency = "CAD"
        elif currency_raw == "GBP":
            currency = "GBP"
        elif currency_raw == "EUR":
            currency = "EUR"
        else:
            currency = None
        return cls(
            min_native=parsed["comp_min_native"],
            max_native=parsed["comp_max_native"],
            currency=currency,
            period=period,
            raw_display=display,
        )


class RawScrapedJob(BaseModel):
    """Wire-format record from a scraper adapter.

    Q15: ``extra="ignore"`` — third-party libraries (jobspy especially) evolve
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
    comp: Comp = Field(default_factory=Comp)
    date_found: dt.date

    @classmethod
    def from_raw(cls, raw: RawScrapedJob) -> NormalizedJob:
        from daily_driver.scraper._impl import (
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
            comp=Comp.parse(raw.comp_display),
            date_found=raw.date_found,
        )


class JobDetails(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    comp: Comp | None = None
    posted_date: dt.date | None = None
    description_text: str = ""


class EnrichedJob(BaseModel):
    """Q14: ``frozen=True`` — enrichers must return ``model_copy(update=...)``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    company: str
    role: NonEmptyStr
    location: str
    url: str
    source: NonEmptyStr
    source_canonical: NonEmptyStr
    source_board: str = ""
    comp: Comp = Field(default_factory=Comp)
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
        if details.comp is not None and not self.comp.is_known:
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
            "Comp": str(self.comp),
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
            comp=Comp.parse(row.get("Comp", "")),
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

    Q16: ``SOURCE_REGISTRY: dict[str, Source]`` lands in K9 — explicit dict,
    no dynamic dispatch.
    """

    def __call__(self, config: ScraperConfig) -> list[RawScrapedJob]: ...


__all__ = [
    "Comp",
    "CompPeriod",
    "CurrencyCode",
    "EnrichedJob",
    "JobDetails",
    "JobStatus",
    "NonEmptyStr",
    "NormalizedJob",
    "RawScrapedJob",
    "Source",
]
