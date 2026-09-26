"""API contract: every request/response schema for the trip-pricing API.

These Pydantic models are the shared language between backend and frontends.
"""

from datetime import date
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


class QuoteConfidence(str, Enum):
    LIVE = "live"  # priced from a real API just now
    SANDBOX = "sandbox"  # real API response, but test-mode data (not bookable)
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
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class TripJob(BaseModel):
    job_id: str
    status: JobStatus
    plan: TripPlan | None = None
    error: str | None = None
