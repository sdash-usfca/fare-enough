"""Orchestrator tests — stub providers make the engine fully testable
without network access (ARCHITECTURE.md decision 3 paying off)."""

from datetime import date

from app.core.orchestrator import plan_trip
from app.models import QuoteConfidence, TravelMode, TripRequest
from app.providers.base import FlightQuote
from app.providers.geo import StubGeocoder
from app.providers.osrm import FallbackDrivingProvider
from app.providers.stubs import (
    HeuristicGroundProvider,
    StubDrivingProvider,
    StubFlightProvider,
    StubFuelProvider,
    StubRentalCarProvider,
)


def _req(**overrides) -> TripRequest:
    base = {
        "origin": "Auburn, WA 98092",
        "destination_city": "Los Angeles",
        "depart_date": date(2026, 10, 16),
        "return_date": date(2026, 10, 19),
        "mode": TravelMode.EITHER,
    }
    base.update(overrides)
    return TripRequest(**base)


def _providers():
    return {
        "flights": StubFlightProvider(),
        # fallback wrapper around the stub: same shape as prod wiring,
        # no network in tests
        "driving": FallbackDrivingProvider([StubDrivingProvider()]),
        "ground": HeuristicGroundProvider(),
        "rental": StubRentalCarProvider(),
        "fuel": StubFuelProvider(),
        "geocoder": StubGeocoder(),
    }


async def test_either_mode_returns_fly_and_drive_options():
    plan = await plan_trip(_req(), **_providers())
    modes = {o.mode for o in plan.options}
    assert TravelMode.FLY in modes
    assert TravelMode.DRIVE in modes
    # one fly option per candidate airport: LAX + BUR/LGB/SNA/ONT = 5
    assert sum(1 for o in plan.options if o.mode == TravelMode.FLY) == 5


async def test_options_ranked_cheapest_first():
    plan = await plan_trip(_req(), **_providers())
    totals = [o.total_usd for o in plan.options]
    assert totals == sorted(totals)


async def test_drive_only_mode_has_no_fly_options():
    plan = await plan_trip(_req(mode=TravelMode.DRIVE), **_providers())
    assert plan.options
    assert all(o.mode == TravelMode.DRIVE for o in plan.options)


async def test_every_leg_names_its_source_and_confidence():
    plan = await plan_trip(_req(), **_providers())
    for option in plan.options:
        assert option.legs, "option with no priced legs"
        for leg in option.legs:
            assert leg.source  # honest UX: every number says where it came from
            assert leg.confidence


class _ExplodingFlightProvider(StubFlightProvider):
    async def search(self, *args, **kwargs):
        raise RuntimeError("Duffel exploded")


async def test_failed_branch_becomes_plan_warning_not_silent_drop():
    providers = _providers()
    providers["flights"] = _ExplodingFlightProvider()
    plan = await plan_trip(_req(), **providers)
    # drive options survive the flight outage
    assert plan.options
    assert all(o.mode == TravelMode.DRIVE for o in plan.options)
    # every failed fly branch is reported to the user, naming the branch
    assert len(plan.warnings) == 5  # LAX + 4 nearby airports
    assert all(w.startswith("flight SEA→") for w in plan.warnings)
    assert all("Duffel exploded" in w for w in plan.warnings)


class _SlowFlightProvider(StubFlightProvider):
    async def search(self, *args, **kwargs):
        raise TimeoutError()


async def test_timed_out_branch_reports_timeout():
    providers = _providers()
    providers["flights"] = _SlowFlightProvider()
    plan = await plan_trip(_req(), **providers)
    assert plan.warnings
    assert all("timed out" in w for w in plan.warnings)


async def test_healthy_run_has_no_warnings():
    plan = await plan_trip(_req(), **_providers())
    assert plan.warnings == []


class _FixedFlightProvider(StubFlightProvider):
    """Returns a fixed one-way/round-trip fare so the orchestrator's math
    can be checked exactly."""

    async def search(self, origin_airport, dest_airport, depart, prefs,
                     return_date=None, passengers=1):
        amount = (200.0 if return_date else 100.0) * passengers
        return [FlightQuote(amount, QuoteConfidence.ESTIMATED, "fixed",
                            "Fixed Air 1, nonstop")]


async def test_roundtrip_does_not_double_flight_leg():
    providers = _providers()
    providers["flights"] = _FixedFlightProvider()
    plan = await plan_trip(_req(), **providers)  # _req has a return_date
    fly_options = [o for o in plan.options if o.mode == TravelMode.FLY]
    assert fly_options
    for option in fly_options:
        flight_legs = [l for l in option.legs if l.label.startswith("Flight ")]
        assert len(flight_legs) == 1
        # provider already priced the round trip: orchestrator must not 2x it
        assert flight_legs[0].amount_usd == 200.0
    assert not any("2x one-way" in w
                   for o in plan.options for w in o.warnings)


async def test_oneway_leaves_amounts_undoubled():
    providers = _providers()
    providers["flights"] = _FixedFlightProvider()
    plan = await plan_trip(_req(return_date=None), **providers)
    fly_options = [o for o in plan.options if o.mode == TravelMode.FLY]
    assert fly_options
    for option in fly_options:
        flight_legs = [l for l in option.legs if l.label.startswith("Flight ")]
        assert flight_legs[0].amount_usd == 100.0


class _NullGeocoder(StubGeocoder):
    async def geocode(self, place: str):
        return None


async def test_unresolvable_origin_warns_but_drive_options_survive():
    providers = _providers()
    providers["geocoder"] = _NullGeocoder()
    plan = await plan_trip(_req(), **providers)
    assert any("flight options unavailable" in w for w in plan.warnings)
    assert any("could not locate origin" in w for w in plan.warnings)
    # drive options don't need an airport, so they still price
    assert plan.options
    assert all(o.mode == TravelMode.DRIVE for o in plan.options)


async def test_home_airport_miles_come_from_geocoding():
    # Auburn → SEA is ~11 mi straight-line (the old code hardcoded 18.0),
    # ~13 mi by road after the ×1.2 circuity factor.
    plan = await plan_trip(_req(), **_providers())
    fly = next(o for o in plan.options if o.mode == TravelMode.FLY)
    home_leg = next(l for l in fly.legs if l.label.startswith("Rideshare home"))
    assert "~13 mi" in home_leg.detail
    assert "18 mi" not in home_leg.detail


class _AddressGeocoder(StubGeocoder):
    """Resolves full street addresses the way Nominatim would in prod."""

    async def geocode(self, place: str):
        low = place.lower()
        if "garden grove" in low:
            from app.providers.geo import GeoPoint
            return GeoPoint(33.7743, -117.9381)  # Garden Grove, CA
        if "las vegas" in low and "boulevard" in low:
            from app.providers.geo import GeoPoint
            return GeoPoint(36.1121, -115.1729)  # the Strip
        return await super().geocode(place)


async def test_full_street_address_resolves_to_nearest_metro():
    # Regression: autocomplete hands us "Garden Grove, California, United
    # States" — lookup_city can't match that by name, so fly mode used to
    # silently return zero options.
    providers = _providers()
    providers["geocoder"] = _AddressGeocoder()
    req = _req(destination_city="Garden Grove, California, United States",
               mode=TravelMode.FLY)
    plan = await plan_trip(req, **providers)
    fly_options = [o for o in plan.options if o.mode == TravelMode.FLY]
    # LAX + BUR/LGB/SNA/ONT, same as a bare "Los Angeles"
    assert len(fly_options) == 5


async def test_vegas_strip_address_resolves_to_las():
    providers = _providers()
    providers["geocoder"] = _AddressGeocoder()
    req = _req(destination_city="3411 South Las Vegas Boulevard, Las Vegas, NV 89109, United States",
               mode=TravelMode.FLY)
    plan = await plan_trip(req, **providers)
    airports = {l.label.split("→")[1].strip()
                for o in plan.options if o.mode == TravelMode.FLY
                for l in o.legs if l.label.startswith("Flight ")}
    assert airports == {"LAS"}


async def test_unresolvable_destination_warns_instead_of_silent_empty():
    providers = _providers()
    providers["geocoder"] = _NullGeocoder()
    req = _req(destination_city="Nowhere, XX", mode=TravelMode.FLY)
    plan = await plan_trip(req, **providers)
    assert plan.options == []
    assert any("no airports on file near 'Nowhere, XX'" in w
               for w in plan.warnings)


# --- Regressions for the cost-comparison and origin bugs -------------------

def _leg_labels(option) -> list[str]:
    return [l.label for l in option.legs]


class _FarFromSeaGeocoder(StubGeocoder):
    """Home ~20 mi straight-line south of SEA (~24 road mi)."""

    async def geocode(self, place: str):
        from app.providers.geo import GeoPoint
        return GeoPoint(47.16, -122.31)


def _far_providers():
    providers = _providers()
    providers["geocoder"] = _FarFromSeaGeocoder()
    return providers


async def test_round_trip_rideshare_compared_against_parking():
    # 1-day trip from ~24 road mi out: the rideshare is ~$48 one way but ~$97
    # round trip; driving + 2 calendar days of parking is ~$73. Comparing the
    # one-way fare used to pick the rideshare; the cheaper parking must win.
    plan = await plan_trip(_req(return_date=date(2026, 10, 17), mode=TravelMode.FLY),
                           **_far_providers())
    fly = plan.options[0]
    assert _leg_labels(fly)[:2] == ["Park at SEA (2 days)",
                                    "Drive home → SEA and back (48 mi)"]
    park_total = fly.legs[0].amount_usd + fly.legs[1].amount_usd
    assert fly.legs[0].amount_usd == 60.0  # $30/day × 2 calendar days
    assert 48 < park_total < 97


async def test_round_trip_rideshare_compared_against_rental():
    # SNA is 35 mi out: ~$69 each way (~$139 round trip) vs a $78 1-day
    # rental. The rental is cheaper for the whole trip and must win.
    plan = await plan_trip(_req(return_date=date(2026, 10, 17), mode=TravelMode.FLY),
                           **_providers())
    sna = next(o for o in plan.options if "→ SNA" in o.summary)
    assert _leg_labels(sna)[-2:] == ["Rental car at SNA (1d)",
                                     "Rental fuel: 70 mi airport ↔ city"]
    assert sna.legs[-2].amount_usd == 78.0
    assert sna.summary == "Fly SEA → SNA, then rental car to Los Angeles"


class _DenverGeocoder(StubGeocoder):
    async def geocode(self, place: str):
        if "denver" in place.lower():
            from app.providers.geo import GeoPoint
            return GeoPoint(39.7392, -104.9903)
        return await super().geocode(place)


async def test_origin_far_from_every_airport_gets_no_fly_options():
    # Regression: Denver resolved to SJC (~950 mi) and priced "drive + park
    # at SJC" for $75 with the 950-mile drive missing from the total.
    providers = _providers()
    providers["geocoder"] = _DenverGeocoder()
    plan = await plan_trip(_req(origin="Denver, CO"), **providers)
    assert all(o.mode == TravelMode.DRIVE for o in plan.options)
    assert any("no departure airport on file within 100 mi" in w
               and "SJC" in w for w in plan.warnings)


async def test_fuel_priced_in_origin_state():
    providers = _providers()
    providers["geocoder"] = _DenverGeocoder()
    plan = await plan_trip(_req(origin="Denver, CO", mode=TravelMode.DRIVE),
                           **providers)
    fuel = next(l for l in plan.options[0].legs if l.label.startswith("Fuel"))
    assert "@ $3.55/gal" in fuel.label  # Colorado stub price, not California's
    assert "CO avg" in fuel.detail


async def test_unknown_origin_state_uses_national_fallback():
    plan = await plan_trip(_req(origin="Auburn", mode=TravelMode.DRIVE), **_providers())
    fuel = next(l for l in plan.options[0].legs if l.label.startswith("Fuel"))
    assert "US avg" in fuel.detail


async def test_no_own_car_drops_own_car_option():
    plan = await plan_trip(_req(mode=TravelMode.DRIVE, own_car=False), **_providers())
    assert [o.summary.split(" (")[0] for o in plan.options] == ["Drive a rental car"]


async def test_own_car_offers_both_drive_options():
    plan = await plan_trip(_req(mode=TravelMode.DRIVE), **_providers())
    assert len(plan.options) == 2


# --- Party size -------------------------------------------------------------

def _leg(option, prefix):
    return next(l for l in option.legs if l.label.startswith(prefix))


async def test_flight_priced_for_whole_party_once():
    providers = _providers()
    providers["flights"] = _FixedFlightProvider()
    plan = await plan_trip(_req(travelers=3, mode=TravelMode.FLY), **providers)
    for option in plan.options:
        flight = _leg(option, "Flight ")
        assert flight.amount_usd == 600.0  # 200 round trip × 3, not × 9
        assert "(3 travelers)" in flight.label


async def test_party_of_four_is_one_car_four_fares():
    one = await plan_trip(_req(mode=TravelMode.DRIVE), **_providers())
    four = await plan_trip(_req(mode=TravelMode.DRIVE, travelers=4), **_providers())
    # Same car, same fuel: driving costs the same total for 1 or 4 people...
    assert [o.total_usd for o in four.options] == [o.total_usd for o in one.options]
    # ...so per person it's a quarter.
    for o in four.options:
        assert o.per_person_usd == round(o.total_usd / 4, 2)
    assert four.travelers == 4


async def test_six_travelers_need_two_cars_and_two_rideshares():
    one = await plan_trip(_req(), **_providers())
    six = await plan_trip(_req(travelers=6), **_providers())

    rental1 = next(o for o in one.options if o.summary.startswith("Drive a rental"))
    rental6 = next(o for o in six.options if o.summary.startswith("Drive a rental"))
    assert _leg(rental6, "Fuel").amount_usd == round(_leg(rental1, "Fuel").amount_usd * 2, 2)
    assert _leg(rental6, "Rental car").amount_usd == _leg(rental1, "Rental car").amount_usd * 2
    assert ", 2 cars)" in _leg(rental6, "Rental car").label

    own6 = next(o for o in six.options if o.summary.startswith("Drive your own"))
    assert any("assumes 2 cars of your own for 6 travelers" in w for w in own6.warnings)

    lax1 = next(o for o in one.options if "→ LAX" in o.summary)
    lax6 = next(o for o in six.options if "→ LAX" in o.summary)
    home1, home6 = lax1.legs[0], lax6.legs[0]
    assert home6.label.startswith("Rideshare home") and home6.label.endswith("(2 cars)")
    assert home6.amount_usd == round(home1.amount_usd * 2, 2)


async def test_solo_traveler_labels_unchanged():
    plan = await plan_trip(_req(), **_providers())
    for o in plan.options:
        assert o.per_person_usd == o.total_usd
        assert not any("cars" in l.label or "travelers" in l.label for l in o.legs)


async def test_no_car_never_parks_at_the_airport():
    # Parking beats the round-trip rideshare from ~24 road mi out — but only
    # for someone with a car to park.
    with_car = await plan_trip(_req(return_date=date(2026, 10, 17), mode=TravelMode.FLY),
                               **_far_providers())
    no_car = await plan_trip(_req(return_date=date(2026, 10, 17), mode=TravelMode.FLY,
                                  own_car=False), **_far_providers())
    assert with_car.options[0].legs[0].label.startswith("Park at")
    assert all(o.legs[0].label.startswith("Rideshare home") for o in no_car.options)


# --- Own-car wear & tear ----------------------------------------------------

async def test_own_car_includes_wear_and_tear_per_mile():
    plan = await plan_trip(_req(mode=TravelMode.DRIVE), **_providers())
    own = next(o for o in plan.options if o.summary.startswith("Drive your own"))
    route = await StubDrivingProvider().route("Auburn, WA 98092", "Los Angeles")
    miles = route.miles * 2  # _req is a round trip
    wear = _leg(own, "Wear & tear")
    assert wear.amount_usd == round(miles * 0.1104, 2)
    assert wear.confidence == QuoteConfidence.ESTIMATED
    assert wear.source == "aaa-driving-costs"
    # Wear is a real line in the total, not a footnote.
    assert own.total_usd == round(sum(l.amount_usd for l in own.legs), 2)


async def test_rental_drive_has_no_wear_leg():
    # The rental rate already covers the car's wear — never charge it twice.
    plan = await plan_trip(_req(mode=TravelMode.DRIVE), **_providers())
    rental = next(o for o in plan.options if o.summary.startswith("Drive a rental"))
    assert not any(l.label.startswith("Wear") for l in rental.legs)


async def test_wear_scales_per_car_and_follows_setting(monkeypatch):
    from app.core import orchestrator as orch
    monkeypatch.setattr(orch.settings, "own_car_wear_per_mile_usd", 0.20)
    one = await plan_trip(_req(mode=TravelMode.DRIVE), **_providers())
    six = await plan_trip(_req(mode=TravelMode.DRIVE, travelers=6), **_providers())
    w1 = _leg(next(o for o in one.options if "own car" in o.summary), "Wear")
    w6 = _leg(next(o for o in six.options if "own car" in o.summary), "Wear")
    assert "@ $0.20/mi" in w1.label
    assert w6.amount_usd == round(w1.amount_usd * 2, 2)
    assert w6.label.endswith("× 2 cars")


# --- Pricing gaps: calendar-day parking, drive to airport, rental fuel ------

async def test_one_way_trip_never_parks():
    # One-way, the car would be stranded in the lot — rideshare only, even
    # when parking for "1 day" would look cheaper.
    plan = await plan_trip(_req(return_date=None, mode=TravelMode.FLY), **_far_providers())
    assert all(o.legs[0].label.startswith("Rideshare home") for o in plan.options)


class _SixtyMilesFromSeaGeocoder(StubGeocoder):
    """~60 mi straight-line south of SEA: a $280 round-trip rideshare, so
    driving + parking always wins and its math is always checked."""

    async def geocode(self, place: str):
        from app.providers.geo import GeoPoint
        return GeoPoint(46.58, -122.31)


async def test_parking_charges_calendar_days():
    providers = _providers()
    providers["geocoder"] = _SixtyMilesFromSeaGeocoder()
    plan = await plan_trip(_req(mode=TravelMode.FLY), **providers)
    fly = plan.options[0]
    # Fri 16 → Mon 19 is 3 nights but 4 days in the lot: $30 × 4.
    assert fly.legs[0].label == "Park at SEA (4 days)"
    assert fly.legs[0].amount_usd == 120.0
    drive = fly.legs[1]
    assert drive.label.startswith("Drive home → SEA and back")
    assert "fuel $" in drive.detail and "wear $" in drive.detail
    assert drive.amount_usd > 0


async def test_fuel_looked_up_once_per_trip():
    class _CountingFuel(StubFuelProvider):
        calls = 0

        async def price_per_gallon(self, state):
            type(self).calls += 1
            return await super().price_per_gallon(state)

    providers = _providers()
    providers["fuel"] = _CountingFuel()
    await plan_trip(_req(), **providers)  # 5 fly + 2 drive branches
    assert _CountingFuel.calls == 1


# --- Long drives: overnight stops, feasibility, travel time -------------------

def _option(plan, prefix):
    return next(o for o in plan.options if o.summary.startswith(prefix))


async def test_long_drive_adds_overnight_stops_both_ways():
    # Stub Auburn → LA is ~18h each way: 2 days on the road each way at
    # ≤10h/day → 1 night out + 1 night back.
    plan = await plan_trip(_req(mode=TravelMode.DRIVE), **_providers())
    for o in plan.options:  # own car and rental both sleep somewhere
        stay = _leg(o, "Overnight stops")
        assert stay.label == "Overnight stops: 2 nights @ $161"
        assert stay.amount_usd == round(2 * 160.54, 2)
        assert stay.source == "costar-adr-2025"
        assert o.travel_note == "~18h driving each way, 1 night on the road"


async def test_short_drive_has_no_overnight_and_one_way_counts_once():
    short = await plan_trip(_req(destination_city="Portland", mode=TravelMode.DRIVE),
                            **_providers())
    assert not any(l.label.startswith("Overnight") for o in short.options for l in o.legs)
    one_way = await plan_trip(_req(return_date=None, mode=TravelMode.DRIVE), **_providers())
    assert _leg(one_way.options[0], "Overnight stops").label.startswith("Overnight stops: 1 night ")


async def test_big_party_books_extra_rooms():
    plan = await plan_trip(_req(mode=TravelMode.DRIVE, travelers=6), **_providers())
    stay = _leg(_option(plan, "Drive a rental"), "Overnight stops")
    assert "× 2 rooms" in stay.label
    assert stay.amount_usd == round(2 * 2 * 160.54, 2)


async def test_drive_longer_than_trip_warns():
    # Fri → Sat is 2 days in total; 2 days on the road each way can't fit.
    plan = await plan_trip(_req(return_date=date(2026, 10, 17), mode=TravelMode.DRIVE),
                           **_providers())
    assert any("the drive takes 2 days each way — longer than your 2-day trip"
               in w for w in _option(plan, "Drive your own").warnings)
    roomy = await plan_trip(_req(return_date=date(2026, 10, 26), mode=TravelMode.DRIVE),
                            **_providers())
    assert not _option(roomy, "Drive your own").warnings


async def test_fly_options_show_door_to_door_time():
    plan = await plan_trip(_req(mode=TravelMode.FLY), **_providers())
    for o in plan.options:
        # SEA → LA ≈ 2.7h block + ~0.4h to SEA + 1.5h early + 0.5h deplane + transfer
        assert 4.5 < o.travel_hours < 7.5, o.travel_hours
        assert o.travel_note.endswith("door to door each way")


async def test_fare_duration_beats_the_estimate():
    class _TimedFlight(StubFlightProvider):
        async def search(self, *args, **kwargs):
            quotes = await super().search(*args, **kwargs)
            for q in quotes:
                q.hours = 10.0  # e.g. a long connection
            return quotes

    providers = _providers()
    providers["flights"] = _TimedFlight()
    plan = await plan_trip(_req(mode=TravelMode.FLY), **providers)
    assert all(o.travel_hours > 12 for o in plan.options)
