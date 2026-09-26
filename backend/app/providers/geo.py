"""Geocoding providers: place name → coordinates.

The orchestrator needs two things from geography: where the user starts
(→ nearest origin airport) and how far home is from that airport (→ the
rideshare leg). Both come through the GeocodingProvider interface so the
real implementation swaps with the stub in one place — the same provider
pattern as flights (ARCHITECTURE.md decisions 3–4).
"""

import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

import httpx

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lon: float


class GeoError(Exception):
    """Raised when an origin can't be located at all."""


def haversine_miles(a: GeoPoint, b: GeoPoint) -> float:
    """Straight-line distance in miles.

    Good enough for 'nearest airport' and rough rideshare estimates;
    real road distance is a driving provider's job (roadmap #5)."""
    r = 3958.8  # Earth radius in miles
    dlat = math.radians(b.lat - a.lat)
    dlon = math.radians(b.lon - a.lon)
    h = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(a.lat)) * math.cos(math.radians(b.lat))
         * math.sin(dlon / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(h))


class GeocodingProvider(ABC):
    @abstractmethod
    async def geocode(self, place: str) -> GeoPoint | None:
        """Coordinates for a place name, or None if unknown/unreachable."""


class StubGeocoder(GeocodingProvider):
    """Curated coordinates for the demo corridor."""

    _PLACES: ClassVar[dict[str, GeoPoint]] = {
        "auburn": GeoPoint(47.3073, -122.2284),
        "seattle": GeoPoint(47.6062, -122.3321),
        "san francisco": GeoPoint(37.7749, -122.4194),
        "san jose": GeoPoint(37.3382, -121.8863),
        "portland": GeoPoint(45.5152, -122.6784),
        "los angeles": GeoPoint(34.0522, -118.2437),
    }

    async def geocode(self, place: str) -> GeoPoint | None:
        low = place.lower()
        for name, point in self._PLACES.items():
            if name in low:
                return point
        return None


class NominatimGeocoder(GeocodingProvider):
    """Real geocoding via OpenStreetMap Nominatim — no API key needed.

    Why Nominatim: Google/Mapbox need billing-enabled keys; for a portfolio
    project the open, keyless option is the honest default. Tradeoff: strict
    usage policy (identify your app, ~1 req/s) and no SLA — hence the
    fallback chain in api/trips.py.
    """

    _URL = "https://nominatim.openstreetmap.org/search"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client  # injectable — tests pass a MockTransport client

    async def geocode(self, place: str) -> GeoPoint | None:
        client = self._client or httpx.AsyncClient(timeout=10.0)
        try:
            resp = await client.get(
                self._URL,
                params={"q": place, "format": "json", "limit": 1},
                headers={"User-Agent": "fare-enough/1.0 (portfolio trip planner)"},
            )
            if resp.status_code != 200:
                return None
            results = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("nominatim geocode failed for %r: %r", place, exc)
            return None
        finally:
            if self._client is None:
                await client.aclose()
        if not results:
            return None
        try:
            return GeoPoint(lat=float(results[0]["lat"]),
                            lon=float(results[0]["lon"]))
        except (KeyError, TypeError, ValueError):
            return None


class FallbackGeocoder(GeocodingProvider):
    """Try providers in order; first hit wins.

    Lets the app prefer the real geocoder with the curated stub as a safety
    net for known places — a small Chain of Responsibility. Results are
    memoized per instance: one trip geocodes each place once even though
    the origin airport resolution and both drive branches all ask."""

    def __init__(self, providers: list[GeocodingProvider]):
        self._providers = providers
        self._cache: dict[str, GeoPoint | None] = {}

    async def geocode(self, place: str) -> GeoPoint | None:
        if place not in self._cache:
            self._cache[place] = await self._geocode_uncached(place)
        return self._cache[place]

    async def _geocode_uncached(self, place: str) -> GeoPoint | None:
        for provider in self._providers:
            point = await provider.geocode(place)
            if point is not None:
                return point
        return None
