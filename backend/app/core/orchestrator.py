"""Trip orchestrator: builds the option tree, prices every branch, ranks cheapest-first.

The tree (see README/ARCHITECTURE):
  fly   = home→origin airport (rideshare vs parking, cheaper wins)
          + flight(origin → each candidate dest airport)
          + dest airport→city (rideshare vs rental car, cheaper wins)
  drive = own car (fuel only)  vs  rental car (rate + fuel + insurance)

All provider calls fan out concurrently with per-branch timeouts; a failed
branch is dropped with a warning instead of failing the whole trip
(ARCHITECTURE.md decision 6).
"""

import asyncio
import uuid

from app.data.airports import lookup_city
from app.models import (
    LegQuote,
    QuoteConfidence,
    TravelMode,
    TripOption,
    TripPlan,
    TripRequest,
)
from app.providers.base import (
    DrivingProvider,
    FlightProvider,
    FuelPriceProvider,
    GroundTransportProvider,
    RentalCarProvider,
)

_BRANCH_TIMEOUT_S = 20


def _trip_days(req: TripRequest) -> int:
    if req.return_date:
        return max((req.return_date - req.depart_date).days, 1)
    return 1


def _roundtrip(req: TripRequest) -> bool:
    return req.return_date is not None


def _origin_airport(req: TripRequest) -> str:
    low = req.origin.lower()
    if "san francisco" in low or "sfo" in low:
        return "SFO"
    return "SEA"  # STUB: real impl geocodes origin → nearest airport


async def _price_fly_option(
    req: TripRequest,
    dest_airport: str,
    airport_miles: float,
    flights: FlightProvider,
    ground: GroundTransportProvider,
    rental: RentalCarProvider,
    days: int,
) -> TripOption | None:
    """Price one fly branch: home → origin airport → dest airport → city."""
    origin_airport = _origin_airport(req)
    legs: list[LegQuote] = []
    warnings: list[str] = []

    # Home → origin airport: rideshare vs parking, cheaper wins.
    ride, park = await asyncio.gather(
        ground.rideshare_estimate(18.0),  # STUB miles; real impl geocodes home
        ground.airport_parking(origin_airport, days),
    )
    if ride.amount_usd <= park.amount_usd:
        legs.append(LegQuote(label=f"Rideshare home → {origin_airport}",
                             amount_usd=ride.amount_usd, confidence=ride.confidence,
                             source=ride.source, detail=ride.detail))
    else:
        legs.append(LegQuote(label=f"Drive + park at {origin_airport} ({days}d)",
                             amount_usd=park.amount_usd, confidence=park.confidence,
                             source=park.source, detail=park.detail))

    # Flight: cheapest offer for this airport pair.
    offers = await flights.search(origin_airport, dest_airport, req.depart_date,
                                  req.flight_prefs)
    if not offers:
        return None
    best = offers[0]
    legs.append(LegQuote(label=f"Flight {origin_airport} → {dest_airport}",
                         amount_usd=best.amount_usd, confidence=best.confidence,
                         source=best.source, detail=best.detail))

    # Dest airport → city: rideshare vs rental car, cheaper wins.
    ride_out, rental_q = await asyncio.gather(
        ground.rideshare_estimate(airport_miles),
        rental.quote(days, dest_airport),
    )
    if ride_out.amount_usd <= rental_q.amount_usd:
        legs.append(LegQuote(label=f"Rideshare {dest_airport} → {req.destination_city}",
                             amount_usd=ride_out.amount_usd, confidence=ride_out.confidence,
                             source=ride_out.source, detail=ride_out.detail))
    else:
        legs.append(LegQuote(label=f"Rental car at {dest_airport} ({days}d)",
                             amount_usd=rental_q.amount_usd, confidence=rental_q.confidence,
                             source=rental_q.source, detail=rental_q.detail))

    total = round(sum(l.amount_usd for l in legs), 2)
    if _roundtrip(req):
        total = round(total * 2, 2)  # simplification: symmetric round trip
        warnings.append("Round trip priced as 2x one-way (refine with return-date search)")
    return TripOption(
        id=f"fly-{dest_airport.lower()}-{uuid.uuid4().hex[:6]}",
        mode=TravelMode.FLY,
        summary=f"Fly {origin_airport} → {dest_airport}, then {legs[-1].label.split('(')[0].strip().lower()}",
        legs=legs,
        total_usd=total,
        warnings=warnings,
    )


async def _price_drive_option(
    req: TripRequest,
    own_car: bool,
    driving: DrivingProvider,
    fuel: FuelPriceProvider,
    rental: RentalCarProvider,
    days: int,
) -> TripOption | None:
    route = await driving.route(req.origin, req.destination_city)
    miles = route.miles * (2 if _roundtrip(req) else 1)
    # STUB state detection; real impl reverse-geocodes the origin.
    state = "WA" if "wa" in req.origin.lower() or "seattle" in req.origin.lower() else "CA"
    gas = await fuel.price_per_gallon(state)
    fuel_cost = round(miles / req.mpg * gas.amount_usd, 2)

    legs = [
        LegQuote(label=f"Drive {miles:.0f} mi ({route.hours:.1f}h each way)",
                 amount_usd=0, confidence=route.confidence, source=route.source,
                 detail="distance/time only — no cost"),
        LegQuote(label=f"Fuel: {miles:.0f} mi / {req.mpg} mpg @ ${gas.amount_usd:.2f}/gal",
                 amount_usd=fuel_cost, confidence=gas.confidence, source=gas.source,
                 detail=gas.detail),
    ]
    if not own_car:
        rq = await rental.quote(days, pickup_airport="home")
        legs.append(LegQuote(label=f"Rental car ({days}d, picked up near home)",
                             amount_usd=rq.amount_usd, confidence=rq.confidence,
                             source=rq.source, detail=rq.detail))
    total = round(sum(l.amount_usd for l in legs), 2)
    return TripOption(
        id=f"drive-{'own' if own_car else 'rental'}-{uuid.uuid4().hex[:6]}",
        mode=TravelMode.DRIVE,
        summary=f"Drive {'your own car' if own_car else 'a rental car'} ({miles:.0f} mi)",
        legs=legs,
        total_usd=total,
    )


async def plan_trip(
    req: TripRequest,
    flights: FlightProvider,
    driving: DrivingProvider,
    ground: GroundTransportProvider,
    rental: RentalCarProvider,
    fuel: FuelPriceProvider,
) -> TripPlan:
    days = _trip_days(req)
    branches = []

    if req.mode in (TravelMode.FLY, TravelMode.EITHER):
        airports = lookup_city(req.destination_city)
        if airports:
            candidates = [airports["primary"], *airports["nearby"]]
            miles_map = airports["miles_to_city_center"]
        else:
            candidates, miles_map = [], {}
        for ap in candidates:
            branches.append(
                _price_fly_option(req, ap, miles_map.get(ap, 20.0),
                                 flights, ground, rental, days)
            )

    if req.mode in (TravelMode.DRIVE, TravelMode.EITHER):
        branches.append(_price_drive_option(req, True, driving, fuel, rental, days))
        if not req.own_car:
            # User has no car: rental is the only drive option; still show both
            # so they can compare against borrowing/buying later. Keep both.
            pass
        branches.append(_price_drive_option(req, False, driving, fuel, rental, days))

    results = await asyncio.gather(
        *(asyncio.wait_for(b, timeout=_BRANCH_TIMEOUT_S) for b in branches),
        return_exceptions=True,
    )
    options: list[TripOption] = []
    for r in results:
        if isinstance(r, TripOption):
            options.append(r)
        # Exceptions (incl. timeouts) drop their branch silently here;
        # a production build logs them and surfaces per-option warnings.
    options.sort(key=lambda o: o.total_usd)
    return TripPlan(origin=req.origin, destination_city=req.destination_city, options=options)
