"""Cache tests — all hermetic (MemoryCache; no Redis server needed).

The RedisCache failure mode (dead server → misses, never exceptions) is
covered by build_cache's contract, not by a live server here.
"""

from datetime import date

from app.core.cache import MemoryCache, RedisCache, build_cache
from app.models import FlightPrefs, QuoteConfidence
from app.providers.base import DrivingQuote
from app.providers.cached import (
    CachedDrivingProvider,
    CachedFlightProvider,
    CachedGeocoder,
)
from app.providers.geo import GeoPoint, StubGeocoder
from app.providers.stubs import StubDrivingProvider, StubFlightProvider


async def test_memory_cache_hit_and_miss():
    c = MemoryCache()
    assert await c.get_json("k") is None
    await c.set_json("k", {"a": 1}, ttl_s=60)
    assert await c.get_json("k") == {"a": 1}


async def test_memory_cache_expiry():
    c = MemoryCache()
    await c.set_json("k", 1, ttl_s=0)  # already expired
    assert await c.get_json("k") is None


def test_build_cache_defaults_to_memory():
    assert isinstance(build_cache(None), MemoryCache)


def test_build_cache_returns_redis_when_configured():
    assert isinstance(build_cache("redis://localhost:6379/0"), RedisCache)


class _CountingDriver(StubDrivingProvider):
    """Stub geometry, but labeled LIVE so the cache keeps it."""

    def __init__(self):
        self.calls = 0

    async def route(self, origin, destination):
        self.calls += 1
        quote = await super().route(origin, destination)
        quote.confidence = QuoteConfidence.LIVE
        quote.source = "osrm"
        return quote


async def test_cached_driving_provider_caches_live_results():
    inner = _CountingDriver()
    cached = CachedDrivingProvider(inner, MemoryCache(), ttl_s=60)
    first = await cached.route("Auburn, WA 98092", "Los Angeles")
    second = await cached.route("Auburn, WA 98092", "Los Angeles")
    assert inner.calls == 1  # second call served from cache
    assert second == first
    assert second.confidence == QuoteConfidence.LIVE
    # Key normalization: case/whitespace differences hit the same entry.
    await cached.route("  auburn, wa 98092 ", "LOS ANGELES")
    assert inner.calls == 1


async def test_cached_driving_provider_never_caches_stub_estimates():
    class _StubSourceDriver(_CountingDriver):
        async def route(self, origin, destination):
            self.calls += 1
            return DrivingQuote(miles=1000.0, hours=15.0,
                                confidence=QuoteConfidence.ESTIMATED,
                                source="stub")

    inner = _StubSourceDriver()
    cached = CachedDrivingProvider(inner, MemoryCache(), ttl_s=60)
    await cached.route("A", "B")
    await cached.route("A", "B")
    assert inner.calls == 2  # estimates are never cached


class _CountingGeocoder(StubGeocoder):
    def __init__(self):
        self.calls = 0

    async def geocode(self, place):
        self.calls += 1
        return await super().geocode(place)


async def test_cached_geocoder_caches_hits_not_misses():
    inner = _CountingGeocoder()
    cached = CachedGeocoder(inner, MemoryCache(), ttl_s=60)
    p1 = await cached.geocode("Auburn, WA 98092")
    p2 = await cached.geocode("auburn, wa 98092")
    assert isinstance(p1, GeoPoint)
    assert p2 == p1
    assert inner.calls == 1

    # Misses are not cached: a transient failure must not stick.
    assert await cached.geocode("Nowhere, XX 00000") is None
    assert await cached.geocode("Nowhere, XX 00000") is None
    assert inner.calls == 3


class _CountingFlight(StubFlightProvider):
    """Stub fares, but labeled LIVE so the cache keeps them."""

    def __init__(self):
        self.calls = 0

    async def search(self, origin_airport, dest_airport, depart, prefs,
                     return_date=None):
        self.calls += 1
        quotes = await super().search(
            origin_airport, dest_airport, depart, prefs, return_date)
        for q in quotes:
            q.confidence = QuoteConfidence.LIVE
            q.source = "duffel"
        return quotes


async def test_cached_flight_provider_caches_live_results():
    inner = _CountingFlight()
    cached = CachedFlightProvider(inner, MemoryCache(), ttl_s=60)
    prefs = FlightPrefs()
    depart, ret = date(2026, 10, 16), date(2026, 10, 19)
    first = await cached.search("SEA", "LAX", depart, prefs, ret)
    second = await cached.search("sea", "lax", depart, prefs, ret)
    assert inner.calls == 1  # normalized key hits the same entry
    assert [q.amount_usd for q in second] == [q.amount_usd for q in first]
    assert all(q.confidence == QuoteConfidence.LIVE for q in second)
    assert all(q.source == "duffel" for q in second)
    # red_eye_ok is filtered client-side after the fetch → separate entry.
    await cached.search("SEA", "LAX", depart, FlightPrefs(red_eye_ok=False), ret)
    assert inner.calls == 2
    # One-way vs round trip → separate entry.
    await cached.search("SEA", "LAX", depart, prefs)
    assert inner.calls == 3


async def test_cached_flight_provider_never_caches_estimates():
    class _StubSourceFlight(StubFlightProvider):
        def __init__(self):
            self.calls = 0

        async def search(self, origin_airport, dest_airport, depart, prefs,
                         return_date=None):
            self.calls += 1
            return await super().search(
                origin_airport, dest_airport, depart, prefs, return_date)

    inner = _StubSourceFlight()
    cached = CachedFlightProvider(inner, MemoryCache(), ttl_s=60)
    prefs = FlightPrefs()
    depart = date(2026, 10, 16)
    await cached.search("SEA", "LAX", depart, prefs)
    await cached.search("SEA", "LAX", depart, prefs)
    assert inner.calls == 2  # ESTIMATED quotes are never cached


async def test_cached_flight_provider_never_caches_empty_results():
    class _EmptyFlight(StubFlightProvider):
        def __init__(self):
            self.calls = 0

        async def search(self, *args, **kwargs):
            self.calls += 1
            return []

    inner = _EmptyFlight()
    cached = CachedFlightProvider(inner, MemoryCache(), ttl_s=60)
    prefs = FlightPrefs()
    depart = date(2026, 10, 16)
    assert await cached.search("SEA", "LAX", depart, prefs) == []
    assert await cached.search("SEA", "LAX", depart, prefs) == []
    assert inner.calls == 2  # empty offer lists are not cached
