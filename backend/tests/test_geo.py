"""Geocoding provider tests — no network needed except via MockTransport."""

import httpx

from app.core.orchestrator import _nearest_origin_airport
from app.providers.geo import (
    FallbackGeocoder,
    GeoPoint,
    NominatimGeocoder,
    StubGeocoder,
    haversine_miles,
)


def test_haversine_sea_to_sfo_is_about_680_miles():
    sea = GeoPoint(47.4502, -122.3088)
    sfo = GeoPoint(37.6213, -122.3790)
    assert 600 < haversine_miles(sea, sfo) < 760


def test_haversine_same_point_is_zero():
    p = GeoPoint(47.6, -122.3)
    assert haversine_miles(p, p) == 0.0


async def test_stub_geocoder_knows_demo_places():
    point = await StubGeocoder().geocode("Auburn, WA 98092")
    assert point is not None
    assert abs(point.lat - 47.3073) < 0.001


async def test_stub_geocoder_returns_none_for_unknown():
    assert await StubGeocoder().geocode("Nowhereville, ZZ") is None


def test_nearest_origin_airport_auburn_is_sea():
    code, miles = _nearest_origin_airport(GeoPoint(47.3073, -122.2284))
    assert code == "SEA"
    assert 5 < miles < 20  # Auburn → Sea-Tac is ~11 mi straight-line


def test_nearest_origin_airport_sf_is_sfo():
    code, _ = _nearest_origin_airport(GeoPoint(37.7749, -122.4194))
    assert code == "SFO"


def test_nearest_origin_airport_portland_is_pdx():
    code, _ = _nearest_origin_airport(GeoPoint(45.5152, -122.6784))
    assert code == "PDX"


class _NullGeocoder(StubGeocoder):
    async def geocode(self, place: str):
        return None


class _FixedGeocoder(StubGeocoder):
    def __init__(self, point: GeoPoint):
        self._point = point

    async def geocode(self, place: str):
        return self._point


async def test_fallback_uses_first_hit():
    fb = FallbackGeocoder([_NullGeocoder(), StubGeocoder()])
    point = await fb.geocode("Seattle, WA")
    assert point is not None
    assert abs(point.lat - 47.6062) < 0.001


async def test_fallback_returns_none_when_all_miss():
    fb = FallbackGeocoder([_NullGeocoder(), _NullGeocoder()])
    assert await fb.geocode("Seattle, WA") is None


async def test_nominatim_parses_result():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "nominatim.openstreetmap.org" in str(request.url)
        return httpx.Response(
            200, json=[{"lat": "47.6062", "lon": "-122.3321",
                        "display_name": "Seattle"}])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    point = await NominatimGeocoder(client).geocode("Seattle, WA")
    assert point == GeoPoint(lat=47.6062, lon=-122.3321)


async def test_nominatim_empty_results_is_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await NominatimGeocoder(client).geocode("Nowhereville") is None


async def test_nominatim_http_error_is_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await NominatimGeocoder(client).geocode("Seattle, WA") is None


async def test_fallback_geocoder_memoizes_per_instance():
    calls = []

    class _Counting(StubGeocoder):
        async def geocode(self, place: str):
            calls.append(place)
            return await super().geocode(place)

    fb = FallbackGeocoder([_Counting()])
    await fb.geocode("Seattle, WA")
    await fb.geocode("Seattle, WA")
    await fb.geocode("Los Angeles, CA")
    assert calls == ["Seattle, WA", "Los Angeles, CA"]
