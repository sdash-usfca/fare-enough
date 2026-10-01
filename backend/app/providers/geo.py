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


@dataclass(frozen=True)
class PlaceSuggestion:
    """One type-ahead address candidate: display label + coordinates."""

    label: str
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


def _suggestion_label(props: dict) -> str:
    """Human label from a Photon feature's properties.

    Prefers the specific address ("350 5th Ave, New York, NY 10118") over
    the bare place name, so the From/To fields resolve to exact spots —
    a hotel address, not just "Los Angeles"."""
    street = " ".join(
        str(props[k]) for k in ("housenumber", "street") if props.get(k)
    )
    head = street or str(props.get("name") or "")
    city = props.get("city") or props.get("district") or props.get("locality")
    region = " ".join(
        str(props[k]) for k in ("state", "postcode") if props.get(k)
    )
    parts = [head, str(city) if city else "", region,
             str(props.get("country") or "")]
    return ", ".join(p for p in parts if p)


class PhotonSuggester:
    """Type-ahead address suggestions via Photon (Komoot) — no API key.

    Why Photon instead of Nominatim for this: Nominatim's usage policy
    asks for ~1 req/s and it's a full geocoder; Photon is purpose-built
    for autocomplete (partial input, fast, lenient). The UI debounces
    keystrokes so we only ask as the user pauses. Any failure returns []
    — the form still works with free-typed text via the geocode path.
    """

    _URL = "https://photon.komoot.io/api/"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client  # injectable — tests pass a MockTransport client

    async def suggest(self, query: str, limit: int = 5) -> list[PlaceSuggestion]:
        query = query.strip()
        if len(query) < 2:
            return []
        # Broad catch by design: this endpoint's contract is "never break
        # the form" — client construction itself can raise (e.g. a broken
        # proxy env), so everything from construction through parsing is
        # inside the try and any failure degrades to an empty list.
        try:
            client = self._client or httpx.AsyncClient(timeout=8.0)
            try:
                resp = await client.get(
                    self._URL,
                    params={"q": query, "limit": max(1, min(limit, 10))},
                    headers={"User-Agent": "fare-enough/1.0 (portfolio trip planner)"},
                )
                if resp.status_code != 200:
                    return []
                data = resp.json()
            finally:
                if self._client is None:
                    await client.aclose()
        except Exception as exc:  # noqa: BLE001 — see contract note above
            log.warning("photon suggest failed for %r: %r", query, exc)
            return []
        suggestions: list[PlaceSuggestion] = []
        for feature in data.get("features", []) or []:
            try:
                props = feature.get("properties") or {}
                lon, lat = feature["geometry"]["coordinates"][:2]
                label = _suggestion_label(props)
                if not label:
                    continue
                suggestions.append(PlaceSuggestion(
                    label=label, lat=float(lat), lon=float(lon)))
            except (KeyError, TypeError, ValueError, IndexError):
                continue
        return suggestions
