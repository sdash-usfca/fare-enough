"""Traveler-supplied fuel price — the override at the top of the fuel chain.

Why a user price beats every API: EIA reports what Washington averaged
last week; the traveler knows what the Costco in Auburn charged this
morning. No key, no network, no .gov, nothing to break — and for a
trip-cost comparison, the pump you actually use is the ground truth.

Sits first in FallbackFuelProvider (see app/api/trips.py): user price →
EIA (if keyed) → labeled-ESTIMATED stub. Never raises — the price is
validated by the TripRequest model (gt=0, le=30) before it gets here.
"""

from __future__ import annotations

from app.models import QuoteConfidence
from app.providers.base import FuelPriceProvider, MoneyQuote


class UserFuelProvider(FuelPriceProvider):
    """FuelPriceProvider that quotes the traveler's own per-gallon price."""

    def __init__(self, price_per_gal: float):
        self._price = round(price_per_gal, 2)

    async def price_per_gallon(self, state: str) -> MoneyQuote:
        # One price for the whole trip (documented simplification — most
        # people fill up near home). The state arg is accepted to satisfy
        # the interface; the traveler's pump beats the state average.
        return MoneyQuote(
            amount_usd=self._price,
            confidence=QuoteConfidence.USER,
            source="user",
            detail=f"price you entered: ${self._price:.2f}/gal",
        )
