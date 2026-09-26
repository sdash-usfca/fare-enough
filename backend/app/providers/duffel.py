"""Live flight prices via the Duffel API — the first REAL provider.

It replaces StubFlightProvider whenever DUFFEL_API_KEY is set, with zero
changes to the orchestrator. That's the provider-interface pattern
(ARCHITECTURE.md decision 3) doing its job.

Duffel flow: POST /air/offer_requests with return_offers=true → the response
carries bookable offers inline. We map the cheapest ones to FlightQuotes.

Get a free test key: https://duffel.com → dashboard → test mode API key
(starts with duffel_test_, returns realistic sandbox fares, no real booking).
"""

from __future__ import annotations

import logging
from datetime import date

import httpx

from app.models import FlightPrefs, QuoteConfidence
from app.providers.base import FlightProvider, FlightQuote

log = logging.getLogger(__name__)

_BASE_URL = "https://api.duffel.com"
_API_VERSION = "v2"
# Keep comfortably under the orchestrator's per-branch timeout (20s).
_SUPPLIER_TIMEOUT_MS = 15000


class DuffelError(Exception):
    """Duffel call failed — the orchestrator drops this branch gracefully."""


def _is_red_eye(departure_hhmm: str) -> bool:
    """Overnight departure: 21:00–04:59 local."""
    hour = int(departure_hhmm.split(":")[0])
    return hour >= 21 or hour < 5


def _slice_detail(s: dict) -> str | None:
    """Human one-liner for one slice's segments. None if malformed."""
    try:
        segments = s["segments"]
        first, last = segments[0], segments[-1]
        dep = first["departing_at"][11:16]  # "2026-10-16T06:10:00" → "06:10"
        arr = last["arriving_at"][11:16]
        carrier = first.get("marketing_carrier", {}).get("iata_code", "")
        number = first.get("marketing_carrier_flight_number", "")
        stops = len(segments) - 1
        stops_label = "nonstop" if stops == 0 else f"{stops} stop"
        return f"{carrier} {number}, {dep}→{arr}, {stops_label}".strip()
    except (KeyError, IndexError, TypeError):
        return None


def _offer_to_quote(
    offer: dict, confidence: QuoteConfidence
) -> tuple[str, FlightQuote] | None:
    """Map one Duffel offer → (outbound departure HH:MM, FlightQuote).

    None if malformed. For round trips, total_amount already covers both
    directions — the orchestrator must NOT double it again."""
    try:
        amount = float(offer["total_amount"])
        slices = offer["slices"]
    except (KeyError, TypeError, ValueError):
        return None
    out_detail = _slice_detail(slices[0]) if slices else None
    if out_detail is None:
        return None
    dep = slices[0]["segments"][0]["departing_at"][11:16]
    detail = out_detail
    if len(slices) > 1:
        ret_detail = _slice_detail(slices[1])
        if ret_detail:
            detail += f" + return {ret_detail}"
    quote = FlightQuote(
        amount_usd=round(amount, 2),
        confidence=confidence,
        source="duffel",
        detail=detail,
    )
    return dep, quote


class DuffelFlightProvider(FlightProvider):
    """FlightProvider backed by Duffel offer requests (live prices)."""

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None):
        self._api_key = api_key
        self._client = client  # injectable — tests pass a MockTransport client
        # Test-mode keys return realistic sandbox fares that can't be booked,
        # so they must not be labeled LIVE. Production keys get LIVE.
        self._confidence = (
            QuoteConfidence.SANDBOX
            if api_key.startswith("duffel_test_")
            else QuoteConfidence.LIVE
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Duffel-Version": _API_VERSION,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request_body(
        self,
        origin_airport: str,
        dest_airport: str,
        depart: date,
        prefs: FlightPrefs,
        return_date: date | None = None,
    ) -> dict:
        slices = [
            {
                "origin": origin_airport,
                "destination": dest_airport,
                "departure_date": depart.isoformat(),
            }
        ]
        # Time windows are filtered server-side by Duffel — cheaper than
        # fetching everything and filtering here. Applied to the outbound
        # slice only; the return is left open.
        if prefs.earliest_departure or prefs.latest_departure:
            slices[0]["departure_time"] = {
                "from": prefs.earliest_departure or "00:00",
                "to": prefs.latest_departure or "23:59",
            }
        if return_date:
            slices.append(
                {
                    "origin": dest_airport,
                    "destination": origin_airport,
                    "departure_date": return_date.isoformat(),
                }
            )
        return {
            "data": {
                "slices": slices,
                "passengers": [{"type": "adult"}],
                "cabin_class": "economy",
                "max_connections": 1,
            }
        }

    async def search(
        self,
        origin_airport: str,
        dest_airport: str,
        depart: date,
        prefs: FlightPrefs,
        return_date: date | None = None,
    ) -> list[FlightQuote]:
        url = (
            f"{_BASE_URL}/air/offer_requests"
            f"?return_offers=true&supplier_timeout={_SUPPLIER_TIMEOUT_MS}"
        )
        body = self._request_body(
            origin_airport, dest_airport, depart, prefs, return_date
        )
        client = self._client or httpx.AsyncClient(timeout=25.0)
        try:
            resp = await client.post(url, headers=self._headers(), json=body)
        except httpx.HTTPError as exc:
            raise DuffelError(f"Duffel request failed: {exc}") from exc
        finally:
            if self._client is None:
                await client.aclose()

        if resp.status_code not in (200, 201):
            raise DuffelError(f"Duffel HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            offers = resp.json()["data"].get("offers") or []
        except (ValueError, KeyError, AttributeError) as exc:
            raise DuffelError(f"unexpected Duffel response shape: {exc}") from exc

        quotes: list[FlightQuote] = []
        for offer in offers:
            mapped = _offer_to_quote(offer, self._confidence)
            if mapped is None:
                continue
            dep_hhmm, quote = mapped
            # Red-eye preference is client-side: Duffel has no "no red-eyes" flag.
            if not prefs.red_eye_ok and _is_red_eye(dep_hhmm):
                continue
            quotes.append(quote)
        quotes.sort(key=lambda q: q.amount_usd)
        log.info("Duffel %s→%s: %d offers, cheapest $%.2f",
                 origin_airport, dest_airport, len(quotes),
                 quotes[0].amount_usd if quotes else 0)
        return quotes
