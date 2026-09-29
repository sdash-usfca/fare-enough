"""Cache decorators for providers — same Chain of Responsibility shape as the
Fallback* providers: wrap any provider, change nothing about its interface.

CachedDrivingProvider wraps the (fallback) driving provider so a repeated
Auburn → Los Angeles search never re-pays the OSRM round trip. CachedGeocoder
wraps the geocoder so repeated place names never re-hit Nominatim.

One honesty rule: only results from a real API are cached (confidence LIVE
or SANDBOX). A fallback ESTIMATED heuristic cached for 7 days would keep
serving stale estimates after the real provider recovers — cache only
results worth keeping.
"""

import logging

from app.core.cache import GEOCODE_TTL_S, ROUTE_TTL_S, Cache
from app.models import QuoteConfidence
from app.providers.base import DrivingProvider, DrivingQuote
from app.providers.geo import GeocodingProvider, GeoPoint

log = logging.getLogger(__name__)

# Confidence levels worth caching: real API data, live or sandbox.
_CACHEABLE = {QuoteConfidence.LIVE, QuoteConfidence.SANDBOX}


def _norm(place: str) -> str:
    return " ".join(place.strip().lower().split())


class CachedDrivingProvider(DrivingProvider):
    def __init__(self, inner: DrivingProvider, cache: Cache,
                 ttl_s: int = ROUTE_TTL_S):
        self._inner = inner
        self._cache = cache
        self._ttl_s = ttl_s

    async def route(self, origin: str, destination: str) -> DrivingQuote:
        key = f"route:{_norm(origin)}|{_norm(destination)}"
        cached = await self._cache.get_json(key)
        if cached is not None:
            log.debug("route cache hit: %s → %s", origin, destination)
            return DrivingQuote(
                miles=cached["miles"],
                hours=cached["hours"],
                confidence=QuoteConfidence(cached["confidence"]),
                source=cached["source"],
            )
        quote = await self._inner.route(origin, destination)
        # Don't cache fallback heuristics — see module docstring.
        if quote.confidence in _CACHEABLE:
            await self._cache.set_json(
                key,
                {"miles": quote.miles, "hours": quote.hours,
                 "confidence": quote.confidence.value, "source": quote.source},
                self._ttl_s,
            )
        return quote


class CachedGeocoder(GeocodingProvider):
    def __init__(self, inner: GeocodingProvider, cache: Cache,
                 ttl_s: int = GEOCODE_TTL_S):
        self._inner = inner
        self._cache = cache
        self._ttl_s = ttl_s

    async def geocode(self, place: str) -> GeoPoint | None:
        key = f"geocode:{_norm(place)}"
        cached = await self._cache.get_json(key)
        if cached is not None:
            log.debug("geocode cache hit: %r", place)
            return GeoPoint(**cached)
        point = await self._inner.geocode(place)
        # Don't cache misses: a transient Nominatim failure must not become
        # a month-long "unknown place".
        if point is not None:
            await self._cache.set_json(
                key, {"lat": point.lat, "lon": point.lon}, self._ttl_s)
        return point
