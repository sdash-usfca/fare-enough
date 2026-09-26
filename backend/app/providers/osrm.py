"""Real driving distances via OSRM (Open Source Routing Machine).

Why OSRM: it computes actual routed distance/time on OpenStreetMap road
data — no more haversine × 1.15 fudge factor. The public demo server needs
no API key (same tradeoff as Nominatim: free, no SLA, be polite). For
production you'd self-host the engine; the base URL is a constructor arg
so that swap is one line.

Note the honest limitation: OSRM has no traffic data, so durations are
traffic-free. The stub remains as a labeled-ESTIMATED fallback for when
the demo server is unreachable.
"""

import logging

import httpx

from app.models import QuoteConfidence
from app.providers.base import DrivingProvider, DrivingQuote
from app.providers.geo import GeocodingProvider

log = logging.getLogger(__name__)

_METERS_PER_MILE = 1609.344


class OSRMError(Exception):
    """Raised when OSRM can't route (or a place can't be geocoded)."""


class OSRMDrivingProvider(DrivingProvider):
    """DrivingProvider backed by OSRM route(). Takes place names like the
    stub did — it geocodes them internally, so the orchestrator doesn't
    change."""

    def __init__(
        self,
        geocoder: GeocodingProvider,
        base_url: str = "https://router.project-osrm.org",
        client: httpx.AsyncClient | None = None,
    ):
        self._geocoder = geocoder
        self._base_url = base_url.rstrip("/")
        self._client = client  # injectable — tests pass a MockTransport client

    async def route(self, origin: str, destination: str) -> DrivingQuote:
        a = await self._geocoder.geocode(origin)
        b = await self._geocoder.geocode(destination)
        if a is None or b is None:
            missing = origin if a is None else destination
            raise OSRMError(f"could not geocode {missing!r} for routing")
        # OSRM coordinate order is lon,lat — the classic gotcha.
        coords = f"{a.lon},{a.lat};{b.lon},{b.lat}"
        url = f"{self._base_url}/route/v1/driving/{coords}"
        client = self._client or httpx.AsyncClient(timeout=15.0)
        try:
            resp = await client.get(
                url,
                params={"overview": "false"},
                headers={"User-Agent": "fare-enough/1.0 (portfolio trip planner)"},
            )
        except httpx.HTTPError as exc:
            raise OSRMError(f"OSRM request failed: {exc}") from exc
        finally:
            if self._client is None:
                await client.aclose()

        if resp.status_code != 200:
            raise OSRMError(f"OSRM HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            r = resp.json()["routes"][0]
            meters, seconds = float(r["distance"]), float(r["duration"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise OSRMError(f"unexpected OSRM response shape: {exc}") from exc
        return DrivingQuote(
            miles=round(meters / _METERS_PER_MILE, 1),
            hours=round(seconds / 3600, 1),
            # Real routed data, not a heuristic — but traffic-free.
            confidence=QuoteConfidence.LIVE,
            source="osrm",
        )


class FallbackDrivingProvider(DrivingProvider):
    """Try providers in order; first success wins.

    Same Chain of Responsibility as FallbackGeocoder: OSRM first, the stub
    as a labeled-ESTIMATED safety net so a demo-server hiccup doesn't nuke
    both drive options."""

    def __init__(self, providers: list[DrivingProvider]):
        self._providers = providers

    async def route(self, origin: str, destination: str) -> DrivingQuote:
        last_exc: Exception | None = None
        for provider in self._providers:
            try:
                return await provider.route(origin, destination)
            except Exception as exc:  # noqa: BLE001 — try the next provider
                last_exc = exc
                log.warning("driving provider %s failed: %r",
                            type(provider).__name__, exc)
        raise OSRMError(f"all driving providers failed: {last_exc}")
