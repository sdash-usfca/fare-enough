"""State lookup + date validation — the inputs fuel and trip length depend on."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.data.states import state_from_place
from app.models import TripRequest


@pytest.mark.parametrize("place, want", [
    ("Auburn, WA 98092", "WA"),
    ("Ames, Iowa", "IA"),  # used to read as WA: "iowa" contains "wa"
    ("Denver, CO", "CO"),
    ("Auburn, King County, Washington, 98092, United States", "WA"),
    ("3411 South Las Vegas Boulevard, Las Vegas, NV 89109, United States", "NV"),
    ("Washington, DC", "DC"),
    ("Auburn WA 98092", "WA"),
    ("auburn, wa", "WA"),
    ("Boise", None),
    ("123 Or Street", None),
])
def test_state_from_place(place, want):
    assert state_from_place(place) == want


def _trip(**dates) -> TripRequest:
    return TripRequest(origin="Auburn, WA", destination_city="Los Angeles", **dates)


def test_return_before_depart_rejected():
    with pytest.raises(ValidationError, match="return_date can't be before depart_date"):
        _trip(depart_date=date(2026, 10, 19), return_date=date(2026, 10, 16))


def test_same_day_and_one_way_trips_still_valid():
    assert _trip(depart_date=date(2026, 10, 16), return_date=date(2026, 10, 16))
    assert _trip(depart_date=date(2026, 10, 16))


@pytest.mark.parametrize("travelers", [0, 10])
def test_travelers_bounded_one_to_nine(travelers):
    with pytest.raises(ValidationError):
        _trip(depart_date=date(2026, 10, 16), travelers=travelers)
