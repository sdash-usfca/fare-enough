"""Orchestrator tests — stub providers make the engine fully testable
without network access (ARCHITECTURE.md decision 3 paying off)."""

from datetime import date

import pytest

from app.core.orchestrator import plan_trip
from app.models import TravelMode, TripRequest
from app.providers.stubs import (
    HeuristicGroundProvider,
    StubDrivingProvider,
    StubFlightProvider,
    StubFuelProvider,
    StubRentalCarProvider,
)


def _req(**overrides) -> TripRequest:
    base = dict(
        origin="Auburn, WA 98092",
        destination_city="Los Angeles",
        depart_date=date(2026, 10, 16),
        return_date=date(2026, 10, 19),
        mode=TravelMode.EITHER,
    )
    base.update(overrides)
    return TripRequest(**base)


def _providers():
    return dict(
        flights=StubFlightProvider(),
        driving=StubDrivingProvider(),
        ground=HeuristicGroundProvider(),
        rental=StubRentalCarProvider(),
        fuel=StubFuelProvider(),
    )


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
