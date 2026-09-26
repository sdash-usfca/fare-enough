"""OSRM driving provider tests — the HTTP layer is mocked."""

import httpx
import pytest

from app.models import QuoteConfidence
from app.providers.base import DrivingQuote
from app.providers.geo import StubGeocoder
from app.providers.osrm import (
    FallbackDrivingProvider,
    OSRMDrivingProvider,
    OSRMError,
)
from app.providers.stubs import StubDrivingProvider


def _osrm_client(handler, seen=None):
    def wrapped(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["url"] = str(request.url)
        return handler(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(wrapped))


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={
        "code": "Ok",
        "routes": [{"distance": 1738000.0, "duration": 61200.0}],
    })


async def test_route_converts_meters_and_seconds():
    client = _osrm_client(_ok_handler)
    quote = await OSRMDrivingProvider(StubGeocoder(), client=client).route(
        "Auburn, WA 98092", "Los Angeles")
    assert quote.miles == 1079.9  # 1738000 m → mi
    assert quote.hours == 17.0  # 61200 s → h
    assert quote.confidence == QuoteConfidence.LIVE
    assert quote.source == "osrm"


async def test_coordinates_are_sent_lon_lat():
    seen = {}
    client = _osrm_client(_ok_handler, seen)
    await OSRMDrivingProvider(StubGeocoder(), client=client).route(
        "Auburn, WA 98092", "Los Angeles")
    # Auburn (47.3073, -122.2284) → LA (34.0522, -118.2437), OSRM wants lon,lat
    assert "-122.2284,47.3073;-118.2437,34.0522" in seen["url"]


async def test_http_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    with pytest.raises(OSRMError):
        await OSRMDrivingProvider(
            StubGeocoder(), client=_osrm_client(handler)).route(
                "Auburn, WA 98092", "Los Angeles")


async def test_malformed_response_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"routes": []})

    with pytest.raises(OSRMError):
        await OSRMDrivingProvider(
            StubGeocoder(), client=_osrm_client(handler)).route(
                "Auburn, WA 98092", "Los Angeles")


async def test_ungeocodable_place_raises():
    client = _osrm_client(_ok_handler)
    with pytest.raises(OSRMError, match="could not geocode"):
        await OSRMDrivingProvider(StubGeocoder(), client=client).route(
            "Nowhereville, ZZ", "Los Angeles")


class _FailingDrivingProvider(StubDrivingProvider):
    async def route(self, origin, destination):
        raise OSRMError("boom")


async def test_fallback_uses_second_when_first_fails():
    fb = FallbackDrivingProvider(
        [_FailingDrivingProvider(), StubDrivingProvider()])
    quote = await fb.route("Auburn, WA 98092", "Los Angeles")
    assert isinstance(quote, DrivingQuote)
    assert quote.source == "stub-driving"
    assert quote.confidence == QuoteConfidence.ESTIMATED


async def test_fallback_prefers_first_when_it_works():
    fb = FallbackDrivingProvider(
        [OSRMDrivingProvider(StubGeocoder(), client=_osrm_client(_ok_handler)),
         StubDrivingProvider()])
    quote = await fb.route("Auburn, WA 98092", "Los Angeles")
    assert quote.source == "osrm"
    assert quote.miles == 1079.9


async def test_fallback_raises_when_all_fail():
    fb = FallbackDrivingProvider(
        [_FailingDrivingProvider(), _FailingDrivingProvider()])
    with pytest.raises(OSRMError):
        await fb.route("Auburn, WA 98092", "Los Angeles")
