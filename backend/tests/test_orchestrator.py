"""Orchestrator tests — stub providers make the engine fully testable
without network access (ARCHITECTURE.md decision 3 paying off)."""

from datetime import date

from app.core.orchestrator import plan_trip
from app.models import QuoteConfidence, TravelMode, TripRequest
from app.providers.base import FlightQuote
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
        "driving": StubDrivingProvider(),
        "ground": HeuristicGroundProvider(),
        "rental": StubRentalCarProvider(),
        "fuel": StubFuelProvider(),
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
