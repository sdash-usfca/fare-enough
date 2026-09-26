"""STUB providers — deterministic estimates so the app runs with no API keys.

Every class here is marked STUB and each docstring says what the real
implementation needs. The orchestrator doesn't care: it only sees the
interfaces in base.py. Replace stubs one by one as real keys arrive.

Convention: stub quotes always use confidence=ESTIMATED and source="stub-*"
so the UI can label them honestly.
"""

import hashlib
import math
from datetime import date

from app.config import settings
from app.models import FlightPrefs, QuoteConfidence
from app.providers.base import (
    DrivingProvider,
    DrivingQuote,
    FlightProvider,
    FlightQuote,
    FuelPriceProvider,
    GroundTransportProvider,
    MoneyQuote,
    RentalCarProvider,
)

# City → (lat, lon, state). STUB data: real impl geocodes via an API.
_CITY_COORDS: dict[str, tuple[float, float, str]] = {
    "seattle": (47.6062, -122.3321, "WA"),
    "auburn": (47.3073, -122.2284, "WA"),
    "los angeles": (34.0522, -118.2437, "CA"),
    "burbank": (34.1808, -118.3080, "CA"),
    "long beach": (33.7701, -118.1937, "CA"),
    "santa ana": (33.7455, -117.8677, "CA"),
    "ontario": (34.0633, -117.6509, "CA"),
    "san francisco": (37.7749, -122.4194, "CA"),
    "san jose": (37.3382, -121.8863, "CA"),
    "portland": (45.5152, -122.6784, "OR"),
    "las vegas": (36.1699, -115.1398, "NV"),
    "san diego": (32.7157, -117.1611, "CA"),
    "phoenix": (33.4484, -112.0740, "AZ"),
    "denver": (39.7392, -104.9903, "CO"),
}

# STUB: real impl reads EIA/AAA weekly averages.
_GAS_PRICE: dict[str, float] = {
    "WA": 4.20, "CA": 5.10, "OR": 4.00, "NV": 3.90,
    "AZ": 3.60, "CO": 3.55,
}

# STUB: real impl scrapes airport parking pages or a parking API.
_PARKING_DAILY: dict[str, float] = {
    "SEA": 30.0, "SFO": 36.0, "LAX": 40.0, "BUR": 22.0,
    "LGB": 20.0, "SNA": 24.0, "ONT": 18.0,
}


def _haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 3959 * math.asin(math.sqrt(h))


def _city_key(place: str) -> str:
    low = place.lower()
    for city in _CITY_COORDS:
        if city in low:
            return city
    return "seattle"  # STUB fallback


class StubFlightProvider(FlightProvider):
    """STUB. Real impl: Duffel offer requests for the airport pair + date."""

    async def search(
        self,
        origin_airport: str,
        dest_airport: str,
        depart: date,
        prefs: FlightPrefs,
        return_date: date | None = None,
    ) -> list[FlightQuote]:
        seed = f"{origin_airport}{dest_airport}{depart.isoformat()}"
        h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
        base = 79 + (h % 180)  # deterministic $79–$259
        trip_note = "round trip" if return_date else "one-way"
        # STUB simplification: round trip ≈ 2× one-way (Duffel prices it properly).
        mult = 2 if return_date else 1
        quotes = [
            FlightQuote(round(base * mult, 2), QuoteConfidence.ESTIMATED, "stub-flight",
                        f"Stub Air {100 + h % 800}, nonstop (illustrative, {trip_note})"),
            FlightQuote(round(base * 1.35 * mult, 2), QuoteConfidence.ESTIMATED,
                        "stub-flight",
                        f"Stub Air, 1 stop (illustrative, {trip_note})"),
        ]
        if not prefs.red_eye_ok:
            # STUB: real impl filters departures by time window server-side.
            quotes = [q for q in quotes if "red-eye" not in q.detail.lower()]
        return sorted(quotes, key=lambda q: q.amount_usd)


class StubDrivingProvider(DrivingProvider):
    """STUB. Real impl: OSRM / Google Distance Matrix route()."""

    async def route(self, origin: str, destination: str) -> DrivingQuote:
        a = _CITY_COORDS[_city_key(origin)][:2]
        b = _CITY_COORDS[_city_key(destination)][:2]
        miles = _haversine_miles(a, b) * 1.15  # road-distance fudge factor
        return DrivingQuote(
            miles=round(miles, 1),
            hours=round(miles / 60, 1),
            confidence=QuoteConfidence.ESTIMATED,
            source="stub-driving",
        )


class HeuristicGroundProvider(GroundTransportProvider):
    """Heuristic — there is no public Uber/Lyft fare API, so this one stays
    heuristic even in prod (that's why confidence is always ESTIMATED)."""

    async def rideshare_estimate(self, miles: float) -> MoneyQuote:
        fare = settings.rideshare_base_fare_usd + settings.rideshare_per_mile_usd * miles
        return MoneyQuote(round(max(fare, 8.0), 2), QuoteConfidence.ESTIMATED,
                          "heuristic-rideshare",
                          f"~{miles:.0f} mi @ ${settings.rideshare_per_mile_usd}/mi")

    async def airport_parking(self, airport: str, days: int) -> MoneyQuote:
        daily = _PARKING_DAILY.get(airport.upper(), 25.0)
        return MoneyQuote(round(daily * days, 2), QuoteConfidence.ESTIMATED,
                          "stub-parking", f"{airport.upper()} ~${daily:.0f}/day x {days}d")


class StubRentalCarProvider(RentalCarProvider):
    """STUB. Real impl: Amadeus rental-car search API."""

    async def quote(self, days: int, pickup_airport: str) -> MoneyQuote:
        total = (settings.rental_car_daily_usd + settings.rental_insurance_daily_usd) * days
        return MoneyQuote(round(total, 2), QuoteConfidence.ESTIMATED, "stub-rental",
                          f"${settings.rental_car_daily_usd:.0f}/day + "
                          f"${settings.rental_insurance_daily_usd:.0f}/day insurance x {days}d")


class StubFuelProvider(FuelPriceProvider):
    """STUB. Real impl: EIA weekly retail gasoline API by state."""

    async def price_per_gallon(self, state: str) -> MoneyQuote:
        price = _GAS_PRICE.get(state.upper(), 3.80)
        return MoneyQuote(price, QuoteConfidence.ESTIMATED, "stub-fuel",
                          f"regular gasoline, {state.upper()} avg")
