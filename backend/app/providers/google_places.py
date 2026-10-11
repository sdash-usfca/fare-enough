"""Live pump prices via Google Places API (New) — Nearby Search + fuelOptions.

Why Google Places: it reports the last known price at individual stations,
which is the closest an API gets to "what the pump near me charges today".
Every alternative was worse for this project: AAA and GasBuddy have no
public API (scraping breaks their terms), TomTom fuel prices need a
business contract, and the state-average APIs are EIA's weekly survey
underneath (ARCHITECTURE.md decision 14).

Endpoint: POST https://places.googleapis.com/v1/places:searchNearby
  X-Goog-Api-Key: {KEY}
  X-Goog-FieldMask: places.fuelOptions
  {"includedTypes": ["gas_station"], "maxResultCount": 20,
   "rankPreference": "DISTANCE",
   "locationRestriction": {"circle": {"center": {...}, "radius": 8000}}}

Cost: fuelOptions bills as "Nearby Search Enterprise + Atmosphere" — 1,000
free calls/month, paid after that. Two guards keep a trip at ≤ 1 call:
  - one fetch per provider instance (both drive branches share it), and
  - results cached FUEL_TTL_S on a ~1 km grid, so neighbors share an entry.

Honesty notes:
- Google doesn't document the unit. US listings show $/gal; anything that
  isn't USD, or falls outside a plausible $/gal band, is dropped rather
  than trusted — a $/liter price would otherwise slip in at ~1/4 the cost.
- Prices older than _MAX_AGE_DAYS are ignored; the detail names how many
  stations were used and the newest update date.
- Station prices near the origin stand in for the whole trip — the same
  "fill up near home" simplification as UserFuelProvider.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from datetime import UTC, datetime, timedelta

import httpx

from app.core.cache import FUEL_TTL_S, Cache
from app.models import QuoteConfidence
from app.providers.base import FuelPriceProvider, MoneyQuote
from app.providers.geo import GeocodingProvider

log = logging.getLogger(__name__)

_URL = "https://places.googleapis.com/v1/places:searchNearby"
# Keep comfortably under the orchestrator's per-branch timeout (20s).
_TIMEOUT_S = 10.0
_RADIUS_M = 8000.0  # ~5 mi: "near home" without crossing into another metro
_MAX_AGE_DAYS = 7
# Plausible US $/gal band. Rejects $/liter values (~$1) and typo-grade data.
_MIN_USD, _MAX_USD = 1.50, 15.00


class GooglePlacesFuelError(Exception):
    """No usable price from Google — the fallback chain serves the next one."""


def _money(price: dict) -> float:
    return int(price.get("units", 0)) + int(price.get("nanos", 0)) / 1e9


def _parse_time(ts: str) -> datetime:
    # "2026-10-09T14:03:11.123Z" → aware datetime; fromisoformat takes "Z" in 3.11+.
    return datetime.fromisoformat(ts)


def _regular_prices(places: list[dict], now: datetime) -> list[tuple[float, datetime]]:
    """Fresh, plausible regular-unleaded USD prices, one per station."""
    cutoff = now - timedelta(days=_MAX_AGE_DAYS)
    found: list[tuple[float, datetime]] = []
    for place in places:
        for fp in (place.get("fuelOptions") or {}).get("fuelPrices", []):
            try:
                if fp["type"] != "REGULAR_UNLEADED":
                    continue
                if fp["price"].get("currencyCode") != "USD":
                    continue
                amount = _money(fp["price"])
                updated = _parse_time(fp["updateTime"])
            except (KeyError, TypeError, ValueError):
                continue  # one malformed station never sinks the rest
            if updated >= cutoff and _MIN_USD <= amount <= _MAX_USD:
                found.append((amount, updated))
    return found


class GooglePlacesFuelProvider(FuelPriceProvider):
    """Median regular-unleaded price at stations near the trip's origin.

    Built per trip (like UserFuelProvider) because it needs the origin, not
    just the state the interface passes in."""

    def __init__(
        self,
        api_key: str,
        origin: str,
        geocoder: GeocodingProvider,
        cache: Cache,
        client: httpx.AsyncClient | None = None,
    ):
        self._api_key = api_key
        self._origin = origin
        self._geocoder = geocoder
        self._cache = cache
        self._client = client  # injectable — tests pass a MockTransport client
        # Both drive branches ask concurrently; they share one fetch.
        self._fetch: asyncio.Task[MoneyQuote] | None = None

    async def price_per_gallon(self, state: str) -> MoneyQuote:
        # state is unused: station prices near the origin beat a state average.
        if self._fetch is None:
            self._fetch = asyncio.ensure_future(self._quote())
        return await self._fetch

    async def _quote(self) -> MoneyQuote:
        point = await self._geocoder.geocode(self._origin)
        if point is None:
            raise GooglePlacesFuelError(f"could not locate origin {self._origin!r}")
        # ~1 km grid: two addresses on the same street share an entry.
        key = f"fuel:gplaces:{point.lat:.2f},{point.lon:.2f}"
        cached = await self._cache.get_json(key)
        if cached is not None:
            log.debug("fuel cache hit: %s", key)
            return MoneyQuote(amount_usd=cached["amount_usd"],
                              confidence=QuoteConfidence.LIVE,
                              source="google-places", detail=cached["detail"])

        places = await self._search(point.lat, point.lon)
        prices = _regular_prices(places, datetime.now(UTC))
        if not prices:
            raise GooglePlacesFuelError(
                f"no fresh regular-unleaded prices within {_RADIUS_M / 1609.344:.0f} mi "
                f"of {self._origin!r} ({len(places)} stations returned)")
        median = round(statistics.median(p for p, _ in prices), 2)
        newest = max(t for _, t in prices).date().isoformat()
        detail = (f"median regular price at {len(prices)} station"
                  f"{'s' if len(prices) != 1 else ''} near your origin, "
                  f"newest update {newest}")
        log.info("Google Places fuel %s: $%.2f/gal from %d stations",
                 self._origin, median, len(prices))
        # Only real prices reach this line — failures raised above, so a
        # transient "no data" never sticks for the TTL.
        await self._cache.set_json(key, {"amount_usd": median, "detail": detail},
                                   FUEL_TTL_S)
        return MoneyQuote(amount_usd=median, confidence=QuoteConfidence.LIVE,
                          source="google-places", detail=detail)

    async def _search(self, lat: float, lon: float) -> list[dict]:
        body = {
            "includedTypes": ["gas_station"],
            "maxResultCount": 20,
            "rankPreference": "DISTANCE",
            "locationRestriction": {"circle": {
                "center": {"latitude": lat, "longitude": lon},
                "radius": _RADIUS_M,
            }},
        }
        headers = {
            "X-Goog-Api-Key": self._api_key,
            # Field mask = billing tier: ask for fuelOptions only.
            "X-Goog-FieldMask": "places.fuelOptions",
            "Content-Type": "application/json",
        }
        client = self._client or httpx.AsyncClient(timeout=_TIMEOUT_S)
        try:
            resp = await client.post(_URL, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise GooglePlacesFuelError(f"Google Places request failed: {exc}") from exc
        finally:
            if self._client is None:
                await client.aclose()

        if resp.status_code != 200:
            raise GooglePlacesFuelError(
                f"Google Places HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            # No stations in range → Google omits "places" entirely.
            return resp.json().get("places") or []
        except (ValueError, AttributeError) as exc:
            raise GooglePlacesFuelError(
                f"unexpected Google Places response shape: {exc}") from exc
