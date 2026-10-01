"""API contract: every request/response schema for the trip-pricing API.

These Pydantic models are the shared language between backend and frontends.
"""

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


class TravelMode(str, Enum):
    FLY = "fly"
    DRIVE = "drive"
    EITHER = "either"


class FlightPrefs(BaseModel):
    red_eye_ok: bool = True
    # Optional acceptable departure window (local time at origin airport).
    earliest_departure: str | None = None  # "HH:MM"
    latest_departure: str | None = None  # "HH:MM"


class TripRequest(BaseModel):
    origin: str = Field(examples=["Auburn, WA 98092"])
    destination_city: str = Field(examples=["Los Angeles"])
    depart_date: date
    return_date: date | None = None
    mode: TravelMode = TravelMode.EITHER
    flight_prefs: FlightPrefs = Field(default_factory=FlightPrefs)
    own_car: bool = True
    mpg: float = 28.0
    # Optional price override: what the traveler actually pays per gallon.
    # Beats every average (EIA included) because the pump you use is more
    # accurate than a state survey. One price applies to the whole trip —
    # most people fill up near home, and per-state prices would be
    # over-engineering for v1. The le=30 bound catches $479 typos, not
    # real prices (the US record is under $8).
    fuel_price_per_gal: float | None = Field(
        default=None, gt=0, le=30, examples=[4.79])


class QuoteConfidence(str, Enum):
    LIVE = "live"  # priced from a real API just now
    SANDBOX = "sandbox"  # real API response, but test-mode data (not bookable)
    USER = "user"  # supplied by the traveler — most accurate when fresh
    ESTIMATED = "estimated"  # heuristic / cached / stub


class LegQuote(BaseModel):
    """One priced leg of a trip option, e.g. 'flight SEA→LAX' or 'rental car x3 days'."""

    label: str
    amount_usd: float
    confidence: QuoteConfidence
    source: str  # which provider priced it, e.g. "duffel", "osrm", "heuristic"
    detail: str = ""


class TripOption(BaseModel):
    id: str
    mode: TravelMode
    summary: str  # one-line human description, e.g. "Fly SEA→BUR + rental car"
    legs: list[LegQuote]
    total_usd: float
    warnings: list[str] = []  # e.g. "rental-car pricing unavailable; leg excluded"


class TripPlan(BaseModel):
    origin: str
    destination_city: str
    options: list[TripOption]  # ranked cheapest-first
    warnings: list[str] = []  # plan-level notes, e.g. dropped provider branches


class JobStatus(str, Enum):
    PENDING = "pending"  # accepted, worker hasn't picked it up yet
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class TripJob(BaseModel):
    job_id: str
    status: JobStatus
    plan: TripPlan | None = None
    error: str | None = None


class RecentTrip(BaseModel):
    """One row of the operator view: what the trip was, and how it ended.

    Built from the stored request (always present) plus the plan (when the
    job completed) — the full plan stays behind GET /trips/{job_id} so this
    listing stays light."""
    job_id: str
    status: JobStatus
    created_at: datetime
    origin: str
    destination_city: str
    depart_date: date
    return_date: date | None = None
    option_count: int = 0
    cheapest_usd: float | None = None  # cheapest option, when complete
    error: str | None = None
    mode: TravelMode = TravelMode.EITHER  # which search produced this row
