"""Google Places fuel provider tests — httpx.MockTransport stands in for
places.googleapis.com, so the adapter is fully tested with no network, no
key, and no billed calls."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.core.cache import MemoryCache
from app.models import QuoteConfidence
from app.providers.eia import FallbackFuelProvider
from app.providers.geo import GeoPoint, StubGeocoder
from app.providers.google_places import (
    GooglePlacesFuelError,
    GooglePlacesFuelProvider,
)
from app.providers.stubs import StubFuelProvider
from app.providers.user import UserFuelProvider

_NOW = datetime.now(UTC)


def _ts(days_ago: float = 0) -> str:
    return (_NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def _price(usd: float, fuel: str = "REGULAR_UNLEADED", currency: str = "USD",
           days_ago: float = 0) -> dict:
    units, cents = divmod(round(usd * 100), 100)
    return {"type": fuel,
            "price": {"currencyCode": currency, "units": str(units),
                      "nanos": cents * 10_000_000},
            "updateTime": _ts(days_ago)}


def _station(*prices: dict) -> dict:
    return {"fuelOptions": {"fuelPrices": list(prices)}}


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _respond(places: list[dict] | None, calls: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return httpx.Response(200, json={} if places is None else {"places": places})
    return handler


def _provider(handler, origin: str = "Auburn, WA 98092", cache=None):
    return GooglePlacesFuelProvider("test-key", origin, StubGeocoder(),
                                    cache or MemoryCache(), client=_client(handler))


async def test_median_regular_price_with_live_confidence():
    calls: list[httpx.Request] = []
    places = [_station(_price(4.19)), _station(_price(4.39), _price(5.09, "PREMIUM")),
              _station(_price(4.29))]
    quote = await _provider(_respond(places, calls)).price_per_gallon("WA")
    assert quote.amount_usd == 4.29  # median of 4.19 / 4.29 / 4.39; premium ignored
    assert quote.confidence == QuoteConfidence.LIVE
    assert quote.source == "google-places"
    assert "3 stations" in quote.detail

    req = calls[0]
    assert req.headers["X-Goog-Api-Key"] == "test-key"
    # The field mask sets the billing tier — keep it to fuelOptions only.
    assert req.headers["X-Goog-FieldMask"] == "places.fuelOptions"
    body = json.loads(req.content)
    assert body["includedTypes"] == ["gas_station"]
    assert body["rankPreference"] == "DISTANCE"
    center = body["locationRestriction"]["circle"]["center"]
    assert (center["latitude"], center["longitude"]) == (47.3073, -122.2284)  # Auburn


async def test_drops_stale_non_usd_and_implausible_prices():
    places = [
        _station(_price(4.49)),                    # the only usable one
        _station(_price(3.10, days_ago=10)),       # stale
        _station(_price(1.89, currency="CAD")),    # wrong currency
        _station(_price(1.12)),                    # looks like $/liter
        _station(_price(48.0)),                    # garbage
    ]
    quote = await _provider(_respond(places)).price_per_gallon("WA")
    assert quote.amount_usd == 4.49
    assert "1 station near" in quote.detail


async def test_malformed_station_does_not_sink_the_rest():
    bad = {"fuelOptions": {"fuelPrices": [{"type": "REGULAR_UNLEADED"}]}}
    quote = await _provider(_respond([bad, _station(_price(4.59))])).price_per_gallon("WA")
    assert quote.amount_usd == 4.59


@pytest.mark.parametrize("places", [None, [], [_station(_price(5.09, "DIESEL"))]])
async def test_no_usable_prices_raises(places):
    with pytest.raises(GooglePlacesFuelError, match="no fresh regular-unleaded"):
        await _provider(_respond(places)).price_per_gallon("WA")


async def test_http_error_raises():
    def handler(request):
        return httpx.Response(403, json={"error": {"message": "API key not valid"}})
    with pytest.raises(GooglePlacesFuelError, match="HTTP 403"):
        await _provider(handler).price_per_gallon("WA")


async def test_unlocatable_origin_raises_without_calling_google():
    calls: list = []
    with pytest.raises(GooglePlacesFuelError, match="could not locate origin"):
        await _provider(_respond([], calls), origin="Nowhere").price_per_gallon("WA")
    assert calls == []


async def test_both_drive_branches_share_one_call():
    calls: list = []
    provider = _provider(_respond([_station(_price(4.29))], calls))
    a, b = await asyncio.gather(provider.price_per_gallon("WA"),
                                provider.price_per_gallon("WA"))
    assert a == b
    assert len(calls) == 1


async def test_cached_across_trips_but_failures_are_not():
    cache = MemoryCache()
    calls: list = []
    handler = _respond([_station(_price(4.29))], calls)
    await _provider(handler, cache=cache).price_per_gallon("WA")
    hit = await _provider(handler, cache=cache).price_per_gallon("WA")
    assert hit.amount_usd == 4.29 and hit.confidence == QuoteConfidence.LIVE
    assert len(calls) == 1  # second trip served from cache

    empty_calls: list = []
    for _ in range(2):
        with pytest.raises(GooglePlacesFuelError):
            await _provider(_respond([], empty_calls), origin="Seattle, WA",
                            cache=cache).price_per_gallon("WA")
    assert len(empty_calls) == 2  # "no data" never sticks for the TTL


async def test_nearby_addresses_share_a_cache_entry():
    class _TwoPoints(StubGeocoder):
        async def geocode(self, place):
            return GeoPoint(47.3071, -122.2281) if "A" in place else GeoPoint(47.3069, -122.2289)

    cache, calls = MemoryCache(), []
    for origin in ("Home A", "Home B"):
        p = GooglePlacesFuelProvider("k", origin, _TwoPoints(), cache,
                                     client=_client(_respond([_station(_price(4.29))], calls)))
        await p.price_per_gallon("WA")
    assert len(calls) == 1


async def test_chain_order_user_then_google_then_stub():
    google = _provider(_respond([_station(_price(4.29))]))
    user_first = FallbackFuelProvider([UserFuelProvider(3.99), google, StubFuelProvider()])
    assert (await user_first.price_per_gallon("WA")).source == "user"

    google_next = FallbackFuelProvider([_provider(_respond([_station(_price(4.29))])),
                                        StubFuelProvider()])
    assert (await google_next.price_per_gallon("WA")).source == "google-places"

    google_down = FallbackFuelProvider([_provider(_respond([])), StubFuelProvider()])
    quote = await google_down.price_per_gallon("WA")
    assert quote.source == "stub-fuel"
    assert quote.confidence == QuoteConfidence.ESTIMATED


@pytest.mark.parametrize("google_key, eia_key, fuel_override, want", [
    (None, None, None, ["StubFuelProvider"]),
    ("g-key", None, None, ["GooglePlacesFuelProvider", "StubFuelProvider"]),
    ("g-key", "e-key", 3.99, ["UserFuelProvider", "GooglePlacesFuelProvider",
                              "EIAFuelProvider", "StubFuelProvider"]),
])
def test_fuel_chain_wiring_follows_configured_keys(monkeypatch, google_key, eia_key,
                                                   fuel_override, want):
    import app.api.trips as trips_mod
    from app.models import TripRequest

    monkeypatch.setattr(trips_mod.settings, "google_places_api_key", google_key)
    monkeypatch.setattr(trips_mod.settings, "eia_api_key", eia_key)
    req = TripRequest(origin="Auburn, WA", destination_city="Los Angeles",
                      depart_date="2026-10-16", fuel_price_per_gal=fuel_override)
    chain = trips_mod._fuel_provider(req, StubGeocoder())
    assert [type(p).__name__ for p in chain._providers] == want
