"""Duffel provider tests — httpx.MockTransport stands in for api.duffel.com,
so the adapter is fully tested with no network and no API key."""

import json
from datetime import date

import httpx
import pytest

from app.models import FlightPrefs, QuoteConfidence
from app.providers.duffel import DuffelError, DuffelFlightProvider


def _offer(amount: str, dep: str, arr: str, carrier="AS", number="1234", stops=0):
    segments = [
        {
            "departing_at": f"2026-10-16T{dep}:00",
            "arriving_at": f"2026-10-16T{arr}:00",
            "marketing_carrier": {"iata_code": carrier},
            "marketing_carrier_flight_number": number,
        }
        for _ in range(stops + 1)
    ]
    return {"total_amount": amount, "total_currency": "USD",
            "slices": [{"segments": segments}]}


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _prefs(**overrides):
    base = {"red_eye_ok": True}
    base.update(overrides)
    return FlightPrefs(**base)


async def test_maps_offers_cheapest_first_with_sandbox_confidence():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"offers": [
            _offer("259.99", "14:00", "16:30"),
            _offer("189.50", "08:00", "10:30"),
        ]}})

    quotes = await DuffelFlightProvider("duffel_test_x",
                                        _mock_client(handler)).search(
        "SEA", "LAX", date(2026, 10, 16), _prefs())
    assert [q.amount_usd for q in quotes] == [189.50, 259.99]
    # test-mode keys return sandbox fares: realistic, but not bookable
    assert all(q.confidence == QuoteConfidence.SANDBOX for q in quotes)
    assert all(q.source == "duffel" for q in quotes)
    assert "nonstop" in quotes[0].detail


async def test_live_key_gets_live_confidence():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"offers": [
            _offer("189.50", "08:00", "10:30"),
        ]}})

    quotes = await DuffelFlightProvider("duffel_live_x",
                                        _mock_client(handler)).search(
        "SEA", "LAX", date(2026, 10, 16), _prefs())
    assert all(q.confidence == QuoteConfidence.LIVE for q in quotes)


async def test_red_eye_filtered_when_not_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"offers": [
            _offer("120.00", "23:30", "01:45"),  # red-eye
            _offer("200.00", "08:00", "10:15"),
        ]}})

    quotes = await DuffelFlightProvider("duffel_test_x",
                                        _mock_client(handler)).search(
        "SEA", "LAX", date(2026, 10, 16), _prefs(red_eye_ok=False))
    assert len(quotes) == 1
    assert quotes[0].amount_usd == 200.00


async def test_time_window_is_sent_to_duffel():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"data": {"offers": []}})

    await DuffelFlightProvider("duffel_test_x",
                               _mock_client(handler)).search(
        "SEA", "LAX", date(2026, 10, 16),
        _prefs(earliest_departure="06:00", latest_departure="12:00"))
    slice_ = seen["body"]["data"]["slices"][0]
    assert slice_["departure_time"] == {"from": "06:00", "to": "12:00"}
    assert seen["body"]["data"]["passengers"] == [{"type": "adult"}]


async def test_api_error_raises_duffel_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"errors": [{"message": "bad slice"}]})

    with pytest.raises(DuffelError):
        await DuffelFlightProvider("duffel_test_x",
                                   _mock_client(handler)).search(
            "SEA", "XXX", date(2026, 10, 16), _prefs())


async def test_auth_and_version_headers():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["version"] = request.headers.get("duffel-version")
        return httpx.Response(200, json={"data": {"offers": []}})

    await DuffelFlightProvider("duffel_test_secret",
                               _mock_client(handler)).search(
        "SEA", "LAX", date(2026, 10, 16), _prefs())
    assert seen["auth"] == "Bearer duffel_test_secret"
    assert seen["version"] == "v2"


async def test_roundtrip_sends_two_slices_and_prices_both_directions():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        out_slice = _offer("0", "08:00", "10:30")["slices"][0]
        ret_slice = _offer("0", "18:00", "20:15")["slices"][0]
        return httpx.Response(200, json={"data": {"offers": [
            {"total_amount": "399.00", "total_currency": "USD",
             "slices": [out_slice, ret_slice]},
        ]}})

    quotes = await DuffelFlightProvider(
        "duffel_test_x", _mock_client(handler)).search(
            "SEA", "LAX", date(2026, 10, 16),
            _prefs(earliest_departure="06:00", latest_departure="12:00"),
            date(2026, 10, 19))
    slices = seen["body"]["data"]["slices"]
    assert len(slices) == 2
    assert (slices[0]["origin"], slices[0]["destination"]) == ("SEA", "LAX")
    assert slices[0]["departure_date"] == "2026-10-16"
    assert (slices[1]["origin"], slices[1]["destination"]) == ("LAX", "SEA")
    assert slices[1]["departure_date"] == "2026-10-19"
    # time window applies to the outbound slice only
    assert slices[0]["departure_time"] == {"from": "06:00", "to": "12:00"}
    assert "departure_time" not in slices[1]
    # the offer total already covers both directions — never doubled
    assert len(quotes) == 1
    assert quotes[0].amount_usd == 399.00
    assert "return" in quotes[0].detail


async def test_oneway_still_sends_single_slice():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"data": {"offers": []}})

    await DuffelFlightProvider("duffel_test_x",
                               _mock_client(handler)).search(
        "SEA", "LAX", date(2026, 10, 16), _prefs())
    assert len(seen["body"]["data"]["slices"]) == 1
