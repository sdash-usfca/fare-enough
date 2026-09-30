"""EIA fuel provider tests — httpx.MockTransport stands in for api.eia.gov,
so the adapter is fully tested with no network and no API key."""

import httpx
import pytest

from app.models import QuoteConfidence
from app.providers.eia import (
    EIAError,
    EIAFuelProvider,
    FallbackFuelProvider,
)
from app.providers.stubs import StubFuelProvider


def _eia_response(price: str, period: str = "2026-09-21") -> dict:
    return {
        "response": {
            "data": [
                {
                    "period": period,
                    "duoarea": "SWA",
                    "product": "EPM0",
                    "value": price,
                    "units": "$/GAL",
                }
            ]
        }
    }


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_maps_latest_weekly_price_with_live_confidence():
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert params["facets[duoarea][]"] == "SWA"
        assert params["facets[product][]"] == "EPM0"
        assert params["frequency"] == "weekly"
        assert params["length"] == "1"
        assert params["api_key"] == "test-key"
        return httpx.Response(200, json=_eia_response("5.516"))

    quote = await EIAFuelProvider("test-key",
                                 client=_mock_client(handler)).price_per_gallon("WA")
    assert quote.amount_usd == 5.52
    assert quote.confidence == QuoteConfidence.LIVE
    assert quote.source == "eia"
    assert "2026-09-21" in quote.detail
    assert "all grades" in quote.detail


async def test_state_code_is_case_insensitive():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["duoarea"] = request.url.params["facets[duoarea][]"]
        return httpx.Response(200, json=_eia_response("4.478"))

    quote = await EIAFuelProvider("k", client=_mock_client(handler)).price_per_gallon("ca")
    assert seen["duoarea"] == "SCA"
    assert quote.amount_usd == 4.48


async def test_bad_state_code_fails_fast_without_network():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP call should be made")

    with pytest.raises(EIAError):
        await EIAFuelProvider("k",
                              client=_mock_client(handler)).price_per_gallon("XX1")


async def test_http_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(EIAError):
        await EIAFuelProvider("k",
                              client=_mock_client(handler)).price_per_gallon("WA")


async def test_malformed_shape_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": {"data": []}})

    with pytest.raises(EIAError):
        await EIAFuelProvider("k",
                              client=_mock_client(handler)).price_per_gallon("WA")


async def test_fallback_serves_stub_when_eia_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="bad key")

    provider = FallbackFuelProvider(
        [EIAFuelProvider("bad-key", client=_mock_client(handler)), StubFuelProvider()]
    )
    quote = await provider.price_per_gallon("WA")
    assert quote.confidence == QuoteConfidence.ESTIMATED
    assert quote.source == "stub-fuel"
    assert quote.amount_usd == 4.20


async def test_fallback_prefers_eia_when_it_succeeds():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_eia_response("5.516"))

    provider = FallbackFuelProvider(
        [EIAFuelProvider("good-key", client=_mock_client(handler)), StubFuelProvider()]
    )
    quote = await provider.price_per_gallon("WA")
    assert quote.confidence == QuoteConfidence.LIVE
    assert quote.source == "eia"
