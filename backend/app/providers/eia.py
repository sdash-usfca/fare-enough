"""Live fuel prices via the U.S. Energy Information Administration API.

Why EIA: it's the federal government's weekly retail gasoline survey —
free, reliable, ~9,000 req/hr, published every Monday. A key is free and
emailed instantly (https://www.eia.gov/opendata/register.php), so this is
the rare real price source with no partnership gate — unlike rental cars
(ARCHITECTURE.md decision 11).

Endpoint: GET https://api.eia.gov/v2/petroleum/pri/gnd/data/
  ?api_key={KEY}
  &frequency=weekly
  &data[0]=value
  &facets[duoarea][]=S{STATE}      # SCA=California, SWA=Washington, ...
  &facets[product][]=EPM0           # motor gasoline, all grades
  &sort[0][column]=period &sort[0][direction]=desc
  &length=1                        # latest week only

Honesty notes:
- EIA publishes weekly, so the price can lag the pump by up to ~7 days
  (longer across holiday weeks). The quote detail names the survey week.
- product=EPM0 is the all-grades average, not regular-grade specifically —
  the detail says so instead of pretending it's the stub's "regular" number.
- No key, HTTP failure, or empty data → EIAError → FallbackFuelProvider
  serves the labeled-ESTIMATED stub, same Chain of Responsibility as
  FallbackDrivingProvider in osrm.py.
"""

from __future__ import annotations

import logging

import httpx

from app.models import QuoteConfidence
from app.providers.base import FuelPriceProvider, MoneyQuote
from app.providers.stubs import StubFuelProvider

log = logging.getLogger(__name__)

_BASE_URL = "https://api.eia.gov/v2/petroleum/pri/gnd/data/"
# Keep comfortably under the orchestrator's per-branch timeout (20s).
_TIMEOUT_S = 15.0
# EIA's gasoline product code in this dataset. Constructor arg so it can be
# tuned (e.g. to a regular-grade-only series) without code changes.
_DEFAULT_PRODUCT = "EPM0"


class EIAError(Exception):
    """EIA call failed — the fallback serves the labeled stub instead."""


def _state_duoarea(state: str) -> str:
    """EIA area code for a US state: 'S' + 2-letter code (SWA, SCA, ...)."""
    code = state.strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise EIAError(f"not a US state code: {state!r}")
    return f"S{code}"


class EIAFuelProvider(FuelPriceProvider):
    """FuelPriceProvider backed by EIA weekly retail gasoline prices."""

    def __init__(
        self,
        api_key: str,
        product: str = _DEFAULT_PRODUCT,
        client: httpx.AsyncClient | None = None,
    ):
        self._api_key = api_key
        self._product = product
        self._client = client  # injectable — tests pass a MockTransport client

    def _params(self, state: str) -> dict:
        return {
            "api_key": self._api_key,
            "frequency": "weekly",
            "data[0]": "value",
            "facets[duoarea][]": _state_duoarea(state),
            "facets[product][]": self._product,
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": 1,
        }

    async def price_per_gallon(self, state: str) -> MoneyQuote:
        # Validate the state code before any network call — a bad code is a
        # programming error, not an EIA outage, and should fail fast.
        _state_duoarea(state)
        client = self._client or httpx.AsyncClient(timeout=_TIMEOUT_S)
        try:
            resp = await client.get(_BASE_URL, params=self._params(state))
        except httpx.HTTPError as exc:
            raise EIAError(f"EIA request failed: {exc}") from exc
        finally:
            if self._client is None:
                await client.aclose()

        if resp.status_code != 200:
            raise EIAError(f"EIA HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            row = resp.json()["response"]["data"][0]
            price = float(row["value"])
            week = str(row["period"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise EIAError(f"unexpected EIA response shape: {exc}") from exc

        log.info("EIA %s: $%.3f/gal (week of %s)", state.upper(), price, week)
        return MoneyQuote(
            amount_usd=round(price, 2),
            # Real government survey data, fetched just now.
            confidence=QuoteConfidence.LIVE,
            source="eia",
            detail=f"EIA weekly retail avg, all grades, week of {week}",
        )


class FallbackFuelProvider(FuelPriceProvider):
    """Try providers in order; first success wins.

    Same Chain of Responsibility as FallbackDrivingProvider: EIA first, the
    stub as a labeled-ESTIMATED safety net so an EIA hiccup (or a missing
    key) doesn't nuke both drive options."""

    def __init__(self, providers: list[FuelPriceProvider]):
        self._providers = providers

    async def price_per_gallon(self, state: str) -> MoneyQuote:
        last_exc: Exception | None = None
        for provider in self._providers:
            try:
                return await provider.price_per_gallon(state)
            except Exception as exc:  # noqa: BLE001 — try the next provider
                last_exc = exc
                log.warning("fuel provider %s failed: %r",
                            type(provider).__name__, exc)
        raise EIAError(f"all fuel providers failed: {last_exc}")
