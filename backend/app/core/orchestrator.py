"""Trip orchestrator: builds the option tree, prices every branch, ranks cheapest-first.

The tree (see README/ARCHITECTURE):
  fly   = home→origin airport (rideshare vs parking, cheaper wins)
          + flight(origin → each candidate dest airport)
          + dest airport→city (rideshare vs rental car, cheaper wins)
  drive = own car (fuel only)  vs  rental car (rate + fuel + insurance)

All provider calls fan out concurrently with per-branch timeouts; a failed
branch is dropped from the ranking but reported in the plan's warnings so
the user sees what went missing instead of silently fewer options
(ARCHITECTURE.md decision 6).
"""

import asyncio
import logging
import uuid

from app.data.airports import AIRPORTS, METRO_CENTERS, ORIGIN_AIRPORTS, lookup_city
from app.models import (
    LegQuote,
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
from app.providers.geo import (
    GeocodingProvider,
    GeoError,
    GeoPoint,
    haversine_miles,
)

_BRANCH_TIMEOUT_S = 20

log = logging.getLogger(__name__)


def _trip_days(req: TripRequest) -> int:
    if req.return_date:
        return max((req.return_date - req.depart_date).days, 1)
    return 1


def _roundtrip(req: TripRequest) -> bool:
    return req.return_date is not None


def _nearest_origin_airport(point: GeoPoint) -> tuple[str, float]:
    """Nearest origin airport to a geocoded point + straight-line miles."""
    best, best_mi = "SEA", float("inf")
    for code, (lat, lon) in ORIGIN_AIRPORTS.items():
        mi = haversine_miles(point, GeoPoint(lat, lon))
        if mi < best_mi:
            best, best_mi = code, mi
    return best, round(best_mi, 1)


def _nearest_metro(point: GeoPoint, max_miles: float = 75.0) -> str | None:
    """Nearest curated metro key within max_miles of a geocoded point.

    Lets full street addresses ("3411 S Las Vegas Blvd, Las Vegas, NV ...")
    resolve to their metro even though they'd never match the curated
    table by name. Returns None past the radius — better no flights than
    flights to the wrong city.
    """
    best, best_mi = None, max_miles
    for key, (lat, lon) in METRO_CENTERS.items():
        mi = haversine_miles(point, GeoPoint(lat, lon))
        if mi < best_mi:
            best, best_mi = key, mi
    return best


async def _resolve_origin(
    req: TripRequest, geocoder: GeocodingProvider
) -> tuple[str, float]:
    """Geocode the origin → (nearest origin airport, home→airport miles).

    Raises GeoError if the origin can't be located — the caller turns that
    into a user-visible warning and still prices drive options.
    """
    point = await geocoder.geocode(req.origin)
    if point is None:
        raise GeoError(f"could not locate origin {req.origin!r}")
    return _nearest_origin_airport(point)


async def _price_fly_option(
    req: TripRequest,
    dest_airport: str,
    airport_miles: float,
    origin_airport: str,
    home_miles: float,
    flights: FlightProvider,
    ground: GroundTransportProvider,
    rental: RentalCarProvider,
    days: int,
) -> TripOption | None:
    """Price one fly branch: home → origin airport → dest airport → city."""
    legs: list[LegQuote] = []
    warnings: list[str] = []
    roundtrip = _roundtrip(req)

    # Home → origin airport: rideshare vs parking, cheaper wins.
    # home_miles is straight-line from geocoding — the "~" in the detail
    # comes from the heuristic provider, so the estimate stays honest.
    ride, park = await asyncio.gather(
        ground.rideshare_estimate(home_miles),
        ground.airport_parking(origin_airport, days),
    )
    if ride.amount_usd <= park.amount_usd:
        legs.append(_roundtrip_leg(
            f"Rideshare home → {origin_airport}", ride, roundtrip))
    else:
        legs.append(LegQuote(label=f"Drive + park at {origin_airport} ({days}d)",
                             amount_usd=park.amount_usd, confidence=park.confidence,
                             source=park.source, detail=park.detail))

    # Flight: cheapest offer for this airport pair. The provider prices the
    # full round trip when return_date is set — never double it here.
    offers = await flights.search(origin_airport, dest_airport, req.depart_date,
                                  req.flight_prefs, req.return_date)
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
        legs.append(_roundtrip_leg(
            f"Rideshare {dest_airport} → {req.destination_city}",
            ride_out, roundtrip))
    else:
        legs.append(LegQuote(label=f"Rental car at {dest_airport} ({days}d)",
                             amount_usd=rental_q.amount_usd, confidence=rental_q.confidence,
                             source=rental_q.source, detail=rental_q.detail))

    total = round(sum(l.amount_usd for l in legs), 2)
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
    # Label shows one-way distance/hours honestly; round-trip totals stay in
    # parens so "each way" never mixes with a round-trip number.
    if _roundtrip(req):
        drive_label = (f"Drive {route.miles:.0f} mi each way "
                       f"({miles:.0f} mi round trip, {route.hours:.1f}h each way)")
    else:
        drive_label = f"Drive {route.miles:.0f} mi ({route.hours:.1f}h one way)"
    # STUB state detection; real impl reverse-geocodes the origin.
    state = "WA" if "wa" in req.origin.lower() or "seattle" in req.origin.lower() else "CA"
    gas = await fuel.price_per_gallon(state)
    fuel_cost = round(miles / req.mpg * gas.amount_usd, 2)

    legs = [
        LegQuote(label=drive_label,
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


def _error_summary(exc: BaseException) -> str:
    """Short human-readable cause for a dropped branch (no tracebacks)."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timed out"
    msg = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return msg[:120] if msg else type(exc).__name__


def _roundtrip_leg(label: str, quote, roundtrip: bool) -> LegQuote:
    """Build a leg from a one-way MoneyQuote, doubling it for round trips.

    Only per-direction legs (rideshares) go through here — duration-priced
    legs like parking and rentals are already round-trip-aware."""
    amount = quote.amount_usd * (2 if roundtrip else 1)
    detail = quote.detail + (" — ×2 covers the return trip" if roundtrip else "")
    return LegQuote(label=label, amount_usd=round(amount, 2),
                    confidence=quote.confidence, source=quote.source,
                    detail=detail)


async def plan_trip(
    req: TripRequest,
    flights: FlightProvider,
    driving: DrivingProvider,
    ground: GroundTransportProvider,
    rental: RentalCarProvider,
    fuel: FuelPriceProvider,
    geocoder: GeocodingProvider,
) -> TripPlan:
    days = _trip_days(req)
    branches: list[tuple[str, asyncio.Coroutine]] = []
    plan_warnings: list[str] = []

    if req.mode in (TravelMode.FLY, TravelMode.EITHER):
        airports = lookup_city(req.destination_city)
        if airports is None:
            # Autocomplete hands us full street addresses ("3411 S Las Vegas
            # Blvd, Las Vegas, NV 89109, United States") which never match
            # the curated metro table by name — geocode and fall back to the
            # nearest metro by distance.
            try:
                dest_point = await geocoder.geocode(req.destination_city)
            except GeoError:
                dest_point = None
            metro = _nearest_metro(dest_point) if dest_point else None
            airports = AIRPORTS.get(metro) if metro else None
        if airports:
            candidates = [airports["primary"], *airports["nearby"]]
            miles_map = airports["miles_to_city_center"]
        else:
            # Silent empty results are the worst outcome — say why.
            candidates, miles_map = [], {}
            plan_warnings.append(
                "flight options unavailable — "
                f"no airports on file near '{req.destination_city}'"
            )
        try:
            # One geocode for all fly branches — not one per airport.
            origin_airport, home_miles = await _resolve_origin(req, geocoder)
        except GeoError as exc:
            # No origin → no fly options, but drive options can still price.
            plan_warnings.append(f"flight options unavailable ({exc})")
            candidates = []
        for ap in candidates:
            branches.append((
                f"flight {origin_airport}→{ap}",
                _price_fly_option(req, ap, miles_map.get(ap, 20.0),
                                 origin_airport, home_miles,
                                 flights, ground, rental, days),
            ))

    if req.mode in (TravelMode.DRIVE, TravelMode.EITHER):
        branches.append((
            "drive (own car)",
            _price_drive_option(req, True, driving, fuel, rental, days),
        ))
        if not req.own_car:
            # User has no car: rental is the only drive option; still show both
            # so they can compare against borrowing/buying later. Keep both.
            pass
        branches.append((
            "drive (rental car)",
            _price_drive_option(req, False, driving, fuel, rental, days),
        ))

    results = await asyncio.gather(
        *(asyncio.wait_for(coro, timeout=_BRANCH_TIMEOUT_S) for _, coro in branches),
        return_exceptions=True,
    )
    options: list[TripOption] = []
    warnings: list[str] = plan_warnings
    for (label, _), r in zip(branches, results):
        if isinstance(r, TripOption):
            options.append(r)
        elif isinstance(r, Exception):
            # A failed branch drops out of the ranking instead of failing the
            # trip — logged for operators, and surfaced in plan warnings so
            # the user knows an option went missing and why.
            log.warning("dropped a trip branch: %r", r)
            warnings.append(
                f"{label} unavailable ({_error_summary(r)}) — excluded from results"
            )
    options.sort(key=lambda o: o.total_usd)
    return TripPlan(
        origin=req.origin,
        destination_city=req.destination_city,
        options=options,
        warnings=warnings,
    )
