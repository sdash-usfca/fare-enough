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
                     return_date=None):
        amount = 200.0 if return_date else 100.0
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
    # Auburn → SEA is ~11 mi straight-line; the old code hardcoded 18.0.
    plan = await plan_trip(_req(), **_providers())
    fly = next(o for o in plan.options if o.mode == TravelMode.FLY)
    home_leg = next(l for l in fly.legs if l.label.startswith("Rideshare home"))
    assert "~11 mi" in home_leg.detail
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


async def test_round_trip_rideshare_compared_against_parking():
    # 1-day Auburn trip: rideshare is ~$23 each way (~$46 round trip),
    # parking at SEA is $30. Comparing the one-way fare used to pick the
    # $46 rideshare; the cheaper $30 parking must win.
    plan = await plan_trip(_req(return_date=date(2026, 10, 17)), **_providers())
    fly = next(o for o in plan.options if o.mode == TravelMode.FLY)
    assert _leg_labels(fly)[0] == "Drive + park at SEA (1d)"
    assert fly.legs[0].amount_usd == 30.0


async def test_round_trip_rideshare_compared_against_rental():
    # SNA is 35 mi out: ~$69 each way (~$139 round trip) vs a $78 1-day
    # rental. The rental is cheaper for the whole trip and must win.
    plan = await plan_trip(_req(return_date=date(2026, 10, 17), mode=TravelMode.FLY),
                           **_providers())
    sna = next(o for o in plan.options if "→ SNA" in o.summary)
    assert _leg_labels(sna)[-1] == "Rental car at SNA (1d)"
    assert sna.legs[-1].amount_usd == 78.0


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
