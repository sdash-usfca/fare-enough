"""Provider interfaces.

The orchestrator ONLY talks to these abstract classes — never to a concrete
API. To add a real price source (e.g. Duffel for flights), write one new class
implementing the interface. To test, inject stubs. See ARCHITECTURE.md
decisions 3 and 4.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

from app.models import FlightPrefs, QuoteConfidence


@dataclass
class FlightQuote:
    amount_usd: float
    confidence: QuoteConfidence
    source: str
    detail: str = ""  # e.g. "Alaska 1234, 06:10→08:45, nonstop"


@dataclass
class DrivingQuote:
    miles: float
    hours: float
    confidence: QuoteConfidence
    source: str


@dataclass
class MoneyQuote:
    amount_usd: float
    confidence: QuoteConfidence
    source: str
    detail: str = ""
    warnings: list[str] = field(default_factory=list)


class FlightProvider(ABC):
    @abstractmethod
    async def search(
        self,
        origin_airport: str,
        dest_airport: str,
        depart: date,
        prefs: FlightPrefs,
        return_date: date | None = None,
    ) -> list[FlightQuote]:
        """Cheapest bookable options for one airport pair, cheapest-first.

        If return_date is given, quotes cover the whole round trip (the
        provider prices both directions); otherwise they are one-way."""


class DrivingProvider(ABC):
    @abstractmethod
    async def route(self, origin: str, destination: str) -> DrivingQuote:
        """Road distance/time between two place names."""


class GroundTransportProvider(ABC):
    @abstractmethod
    async def rideshare_estimate(self, miles: float) -> MoneyQuote:
        """Uber/Lyft-style estimate. Always heuristic — no public fare API exists."""

    @abstractmethod
    async def airport_parking(self, airport: str, days: int) -> MoneyQuote:
        """Drive-yourself + park at the origin airport for the whole trip."""


class RentalCarProvider(ABC):
    @abstractmethod
    async def quote(self, days: int, pickup_airport: str) -> MoneyQuote:
        """Rental rate + insurance for `days`, picked up at the given airport."""


class FuelPriceProvider(ABC):
    @abstractmethod
    async def price_per_gallon(self, state: str) -> MoneyQuote:
        """Current regular-gasoline price for a US state code (e.g. 'WA')."""
