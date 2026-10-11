"""Trip orchestrator: builds the option tree, prices every branch, ranks cheapest-first.

The tree (see README/ARCHITECTURE):
  fly   = home→origin airport (rideshare vs parking, cheaper wins)
          + flight(origin → each candidate dest airport)
          + dest airport→city (rideshare vs rental car, cheaper wins)
  drive = own car (fuel + wear)  vs  rental car (rate + fuel + insurance)

All provider calls fan out concurrently with per-branch timeouts; a failed
branch is dropped from the ranking but reported in the plan's warnings so
the user sees what went missing instead of silently fewer options
(ARCHITECTURE.md decision 6).
"""

import asyncio
import logging
import math
import uuid

from app.config import settings
from app.data.airports import AIRPORTS, METRO_CENTERS, ORIGIN_AIRPORTS, lookup_city
from app.data.states import state_from_place
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
from app.providers.geo import (
    GeocodingProvider,
    GeoError,
    GeoPoint,
    haversine_miles,
)

_BRANCH_TIMEOUT_S = 20
# Farthest we'll send a traveler to a departure airport. The home→airport
# leg is priced as a rideshare or parking only — there is no drive leg —
# so without a cap a Denver origin "parked at SJC" 950 mi away for $75.
_MAX_HOME_AIRPORT_MILES = 100.0
# Party size → vehicles. Flights are per seat (the provider prices the whole
# party), but ground legs are per vehicle: a standard rideshare seats 4, a
# car (own, rental, or parked) seats 5. Six travelers = 2 rideshares or 2 cars.
_RIDESHARE_SEATS = 4
_CAR_SEATS = 5
# Geocoding gives straight-line miles; roads are longer. ~1.2 is the
# commonly cited US road "circuity factor" (Ballou et al., 2002). Applied to
# home → airport, the one distance we only know as the crow flies.
_ROAD_FACTOR = 1.2
# Overnight stops on long drives: guests per hotel room (two queens).
_ROOM_GUESTS = 4
# Door-to-door time for fly options — rough, labeled "~", shown not priced.
_AIRPORT_ARRIVE_EARLY_H = 1.5  # check-in + security before departure
_DEPLANE_H = 0.5  # deplane + bags at the other end
_LOCAL_MPH = 30.0  # city driving to/from airports
_CRUISE_MPH = 450.0  # block-time estimate when the fare has no duration
_TAXI_H = 0.5  # taxi, climb, approach on top of cruise

log = logging.getLogger(__name__)


def _trip_days(req: TripRequest) -> int:
    if req.return_date:
        return max((req.return_date - req.depart_date).days, 1)
    return 1


def _roundtrip(req: TripRequest) -> bool:
    return req.return_date is not None


def _vehicles(travelers: int, seats: int) -> int:
    return -(-travelers // seats)  # ceiling division


def _cars_note(cars: int) -> str:
    """Label suffix for multi-vehicle legs, e.g. ", 2 cars"."""
    return f", {cars} cars" if cars > 1 else ""


def _times_cars(detail: str, cars: int) -> str:
    return detail + (f" — ×{cars} cars" if cars > 1 else "")


def _per_person(total: float, travelers: int) -> float:
    return round(total / travelers, 2)


def _parking_days(req: TripRequest) -> int:
    """Calendar days the car sits in the lot: Fri→Mon is 4 days, not 3.

    Rentals stay on 24h periods (_trip_days) — that's how they're sold."""
    return (req.return_date - req.depart_date).days + 1 if req.return_date else 1


def _drive_days(hours: float) -> int:
    """Days on the road for one direction of a drive (≥ 1)."""
    return max(1, math.ceil(hours / settings.max_drive_hours_per_day))


def _fmt_hours(h: float) -> str:
    return f"~{h:.0f}h" if h >= 3 else f"~{h:.1f}h"


def _fuel_state(req: TripRequest) -> str:
    # Fuel is priced in the origin's state (most people fill up near home).
    # Unknown state → "US": the stub serves its national default and EIA
    # misses into the same fallback, instead of guessing a wrong state.
    return state_from_place(req.origin) or "US"


class _OncePerTrip(FuelPriceProvider):
    """Every branch of one trip shares one fuel lookup.

    Fly branches price the drive to the airport and rental fuel, drive
    branches price the road trip — up to 7 concurrent asks for the same
    number. One call keeps paid sources (Google Places) and rate limits
    (EIA) honest."""

    def __init__(self, inner: FuelPriceProvider):
        self._inner = inner
        self._tasks: dict[str, asyncio.Task] = {}

    async def price_per_gallon(self, state: str):
        if state not in self._tasks:
            self._tasks[state] = asyncio.ensure_future(self._inner.price_per_gallon(state))
        return await self._tasks[state]


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

    Raises GeoError if the origin can't be located, or if no departure
    airport on file is within _MAX_HOME_AIRPORT_MILES — the caller turns
    that into a user-visible warning and still prices drive options.
    """
    point = await geocoder.geocode(req.origin)
    if point is None:
        raise GeoError(f"could not locate origin {req.origin!r}")
    code, miles = _nearest_origin_airport(point)
    if miles > _MAX_HOME_AIRPORT_MILES:
        raise GeoError(
            f"no departure airport on file within {_MAX_HOME_AIRPORT_MILES:.0f} mi "
            f"of {req.origin!r} — nearest is {code}, {miles:.0f} mi away")
    return code, miles


async def _price_fly_option(
    req: TripRequest,
    dest_airport: str,
    airport_miles: float,
    origin_airport: str,
    home_miles: float,
    flights: FlightProvider,
    ground: GroundTransportProvider,
    rental: RentalCarProvider,
    fuel: FuelPriceProvider,
    days: int,
    dest_center: GeoPoint | None = None,
) -> TripOption | None:
    """Price one fly branch: home → origin airport → dest airport → city."""
    legs: list[LegQuote] = []
    warnings: list[str] = []
    roundtrip = _roundtrip(req)
    road_miles = round(home_miles * _ROAD_FACTOR, 1)
    park_days = _parking_days(req)

    # Home → origin airport: rideshare vs drive-and-park, cheaper wins.
    ride, park, gas = await asyncio.gather(
        ground.rideshare_estimate(road_miles),
        ground.airport_parking(origin_airport, park_days),
        fuel.price_per_gallon(_fuel_state(req)),
    )
    rides = _vehicles(req.travelers, _RIDESHARE_SEATS)
    cars = _vehicles(req.travelers, _CAR_SEATS)
    # Compare like with like: the rideshare quote is one-way, parking already
    # covers the whole stay — so double the rideshare *before* comparing.
    # Both scale per vehicle, not per traveler.
    ride_leg = _roundtrip_leg(f"Rideshare home → {origin_airport}", ride,
                              roundtrip, vehicles=rides)
    # Driving yourself costs the lot *and* the drive there and back.
    drive_miles = road_miles * 2 * cars
    drive_fuel = drive_miles / req.mpg * gas.amount_usd
    drive_wear = drive_miles * settings.own_car_wear_per_mile_usd
    park_legs = [
        LegQuote(label=f"Park at {origin_airport} ({park_days} days{_cars_note(cars)})",
                 amount_usd=round(park.amount_usd * cars, 2),
                 confidence=park.confidence, source=park.source,
                 detail=_times_cars(park.detail, cars)),
        LegQuote(label=(f"Drive home → {origin_airport} and back "
                        f"({road_miles * 2:.0f} mi{_cars_note(cars)})"),
                 amount_usd=round(drive_fuel + drive_wear, 2),
                 confidence=gas.confidence, source=gas.source,
                 detail=(f"fuel ${drive_fuel:.2f} @ ${gas.amount_usd:.2f}/gal + "
                         f"wear ${drive_wear:.2f}; ~road miles (straight-line × "
                         f"{_ROAD_FACTOR})")),
    ]
    # Parking only makes sense with a car to park and a trip back to it:
    # one-way, the car would be stranded in the lot.
    can_park = req.own_car and roundtrip
    if not can_park or ride_leg.amount_usd <= sum(l.amount_usd for l in park_legs):
        legs.append(ride_leg)
    else:
        legs.extend(park_legs)

    # Flight: cheapest offer for this airport pair. The provider prices the
    # full round trip when return_date is set — never double it here.
    # Quotes cover the whole party too — never multiply by travelers here.
    offers = await flights.search(origin_airport, dest_airport, req.depart_date,
                                  req.flight_prefs, req.return_date,
                                  passengers=req.travelers)
    if not offers:
        return None
    best = offers[0]
    party = f" ({req.travelers} travelers)" if req.travelers > 1 else ""
    legs.append(LegQuote(label=f"Flight {origin_airport} → {dest_airport}{party}",
                         amount_usd=best.amount_usd, confidence=best.confidence,
                         source=best.source, detail=best.detail))

    # Dest airport → city: rideshare vs rental car, cheaper wins.
    ride_out, rental_q = await asyncio.gather(
        ground.rideshare_estimate(airport_miles),
        rental.quote(days, dest_airport),
    )
    # Same rule as above: the rental is priced for the whole stay.
    ride_out_leg = _roundtrip_leg(
        f"Rideshare {dest_airport} → {req.destination_city}", ride_out,
        roundtrip, vehicles=rides)
    rental_usd = round(rental_q.amount_usd * cars, 2)
    # The rental burns fuel on the same airport ↔ city trips the rideshare
    # would make — price that, or the rental always looks cheaper than it is.
    rental_miles = airport_miles * (2 if roundtrip else 1)
    rental_fuel = round(rental_miles / settings.default_mpg * gas.amount_usd * cars, 2)
    if ride_out_leg.amount_usd <= rental_usd + rental_fuel:
        legs.append(ride_out_leg)
    else:
        legs.append(LegQuote(
            label=f"Rental car at {dest_airport} ({days}d{_cars_note(cars)})",
            amount_usd=rental_usd, confidence=rental_q.confidence,
            source=rental_q.source, detail=_times_cars(rental_q.detail, cars)))
        legs.append(LegQuote(
            label=(f"Rental fuel: {rental_miles:.0f} mi airport ↔ city"
                   + (f" × {cars} cars" if cars > 1 else "")),
            amount_usd=rental_fuel, confidence=gas.confidence, source=gas.source,
            detail=f"{settings.default_mpg:.0f} mpg @ ${gas.amount_usd:.2f}/gal"))

    # Door to door, one way: get to the airport, arrive early, fly, deplane,
    # get into town. The fare's own duration when the provider has one.
    flight_h = best.hours
    if flight_h is None and dest_center is not None:
        lat, lon = ORIGIN_AIRPORTS[origin_airport]
        flight_h = haversine_miles(GeoPoint(lat, lon), dest_center) / _CRUISE_MPH + _TAXI_H
    travel_h = None
    if flight_h is not None:
        travel_h = round(road_miles / _LOCAL_MPH + _AIRPORT_ARRIVE_EARLY_H + flight_h
                         + _DEPLANE_H + airport_miles / _LOCAL_MPH, 1)

    total = round(sum(l.amount_usd for l in legs), 2)
    return TripOption(
        id=f"fly-{dest_airport.lower()}-{uuid.uuid4().hex[:6]}",
        mode=TravelMode.FLY,
        summary=(f"Fly {origin_airport} → {dest_airport}, then "
                 f"{'rental car' if legs[-1].label.startswith('Rental fuel') else 'rideshare'}"
                 f" to {req.destination_city}"),
        legs=legs,
        total_usd=total,
        per_person_usd=_per_person(total, req.travelers),
        travel_hours=travel_h,
        travel_note=(f"{_fmt_hours(travel_h)} door to door"
                     + (" each way" if roundtrip else "")) if travel_h else "",
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
    gas = await fuel.price_per_gallon(_fuel_state(req))
    # One car per 5 travelers: each burns its own fuel and needs its own rental.
    cars = _vehicles(req.travelers, _CAR_SEATS)
    fuel_cost = round(miles / req.mpg * gas.amount_usd * cars, 2)
    warnings: list[str] = []
    if own_car and cars > 1:
        warnings.append(f"assumes {cars} cars of your own for {req.travelers} "
                        f"travelers ({_CAR_SEATS} seats each)")

    legs = [
        LegQuote(label=drive_label,
                 amount_usd=0, confidence=route.confidence, source=route.source,
                 detail="distance/time only — no cost"),
        LegQuote(label=(f"Fuel: {miles:.0f} mi / {req.mpg} mpg @ "
                        f"${gas.amount_usd:.2f}/gal" + (f" × {cars} cars" if cars > 1 else "")),
                 amount_usd=fuel_cost, confidence=gas.confidence, source=gas.source,
                 detail=gas.detail),
    ]
    if own_car:
        # Fuel isn't the whole cost of driving your own car: every mile wears
        # tires, brakes and oil too. A rental's rate already covers that.
        rate = settings.own_car_wear_per_mile_usd
        legs.append(LegQuote(
            label=(f"Wear & tear: {miles:.0f} mi @ ${rate:.2f}/mi"
                   + (f" × {cars} cars" if cars > 1 else "")),
            amount_usd=round(miles * rate * cars, 2),
            confidence=QuoteConfidence.ESTIMATED, source="aaa-driving-costs",
            detail="maintenance, repairs & tires per AAA Your Driving Costs 2025"))
    else:
        rq = await rental.quote(days, pickup_airport="home")
        legs.append(LegQuote(
            label=f"Rental car ({days}d, picked up near home{_cars_note(cars)})",
            amount_usd=round(rq.amount_usd * cars, 2), confidence=rq.confidence,
            source=rq.source, detail=_times_cars(rq.detail, cars)))

    # Long drives sleep somewhere: one night per extra day on the road.
    drive_days = _drive_days(route.hours)
    directions = 2 if _roundtrip(req) else 1
    nights = (drive_days - 1) * directions
    if nights:
        rooms = _vehicles(req.travelers, _ROOM_GUESTS)
        rate = settings.lodging_per_night_usd
        legs.append(LegQuote(
            label=(f"Overnight stops: {nights} night{'s' if nights > 1 else ''}"
                   + (f" × {rooms} rooms" if rooms > 1 else "") + f" @ ${rate:.0f}"),
            amount_usd=round(nights * rooms * rate, 2),
            confidence=QuoteConfidence.ESTIMATED, source="costar-adr-2025",
            detail=(f"{route.hours:.1f}h each way at ≤{settings.max_drive_hours_per_day:.0f}h"
                    f"/day = {drive_days} days on the road each way; "
                    "US average hotel rate 2025 (CoStar/STR)")))
    # A drive that eats the whole trip is technically priced but useless.
    if _roundtrip(req) and drive_days * 2 > _parking_days(req):
        warnings.append(
            f"the drive takes {drive_days} days each way — longer than your "
            f"{_parking_days(req)}-day trip allows")
    travel_note = f"{_fmt_hours(route.hours)} driving" + (" each way" if _roundtrip(req) else "")
    if drive_days > 1:
        travel_note += f", {drive_days - 1} night{'s' if drive_days > 2 else ''} on the road"
    total = round(sum(l.amount_usd for l in legs), 2)
    return TripOption(
        id=f"drive-{'own' if own_car else 'rental'}-{uuid.uuid4().hex[:6]}",
        mode=TravelMode.DRIVE,
        summary=f"Drive {'your own car' if own_car else 'a rental car'} ({miles:.0f} mi)",
        legs=legs,
        total_usd=total,
        per_person_usd=_per_person(total, req.travelers),
        travel_hours=route.hours,
        travel_note=travel_note,
        warnings=warnings,
    )


def _error_summary(exc: BaseException) -> str:
    """Short human-readable cause for a dropped branch (no tracebacks)."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timed out"
    msg = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return msg[:120] if msg else type(exc).__name__


def _roundtrip_leg(label: str, quote, roundtrip: bool, vehicles: int = 1) -> LegQuote:
    """Build a leg from a one-way, one-vehicle MoneyQuote, doubling it for
    round trips and multiplying by vehicles for big parties.

    Only per-direction legs (rideshares) go through here — duration-priced
    legs like parking and rentals are already round-trip-aware."""
    amount = quote.amount_usd * (2 if roundtrip else 1) * vehicles
    detail = quote.detail + (" — ×2 covers the return trip" if roundtrip else "")
    if vehicles > 1:
        label += f" ({vehicles} cars)"
        detail += f" — ×{vehicles} cars"
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
    fuel = _OncePerTrip(fuel)
    branches: list[tuple[str, asyncio.Coroutine]] = []
    plan_warnings: list[str] = []

    if req.mode in (TravelMode.FLY, TravelMode.EITHER):
        airports = lookup_city(req.destination_city)
        metro = req.destination_city.strip().lower() if airports else None
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
        dest_center = GeoPoint(*METRO_CENTERS[metro]) if metro else None
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
                                 flights, ground, rental, fuel, days, dest_center),
            ))

    if req.mode in (TravelMode.DRIVE, TravelMode.EITHER):
        # No car → no own-car option: it has no rental fee, so it would otherwise
        # rank as the cheapest way to go for someone who can't take it.
        if req.own_car:
            branches.append((
                "drive (own car)",
                _price_drive_option(req, True, driving, fuel, rental, days),
            ))
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
        travelers=req.travelers,
        options=options,
        warnings=warnings,
    )
