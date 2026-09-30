"""User fuel-price override tests.

The override sits at the top of the fuel chain: traveler price → EIA →
stub. No network, no key needed for any of these."""

import httpx
import pytest
from pydantic import ValidationError

from app.models import QuoteConfidence, TripRequest
from app.providers.eia import EIAFuelProvider, FallbackFuelProvider
from app.providers.stubs import StubFuelProvider
from app.providers.user import UserFuelProvider


def _req(**kwargs) -> TripRequest:
    return TripRequest(origin="Auburn, WA 98092",
                       destination_city="Los Angeles",
                       depart_date="2026-10-16", **kwargs)


async def test_user_price_quoted_verbatim_with_user_confidence():
    quote = await UserFuelProvider(4.79).price_per_gallon("WA")
    assert quote.amount_usd == 4.79
    assert quote.confidence == QuoteConfidence.USER
    assert quote.source == "user"
    assert "4.79" in quote.detail


async def test_user_price_beats_eia_and_stub():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "response": {"data": [{"period": "2026-09-21", "value": "5.516"}]}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    chain = FallbackFuelProvider([
        UserFuelProvider(4.79),
        EIAFuelProvider("test-key", client=client),
        StubFuelProvider(),
    ])
    quote = await chain.price_per_gallon("WA")
    assert quote.amount_usd == 4.79
    assert quote.confidence == QuoteConfidence.USER


async def test_eia_still_beats_stub_without_override():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "response": {"data": [{"period": "2026-09-21", "value": "5.516"}]}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    chain = FallbackFuelProvider([
        EIAFuelProvider("test-key", client=client),
        StubFuelProvider(),
    ])
    quote = await chain.price_per_gallon("WA")
    assert quote.amount_usd == 5.52
    assert quote.confidence == QuoteConfidence.LIVE


def test_override_is_optional_and_defaults_to_none():
    assert _req().fuel_price_per_gal is None
    assert _req(fuel_price_per_gal=4.79).fuel_price_per_gal == 4.79


@pytest.mark.parametrize("bad", [0, -1.5, 30.01, 479.0])
def test_absurd_prices_rejected(bad):
    # 0/negative is nonsense; >30 catches $479 typos (US record is <$8).
    with pytest.raises(ValidationError):
        _req(fuel_price_per_gal=bad)
