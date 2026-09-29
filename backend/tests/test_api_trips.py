"""API contract tests — POST /trips → 202 + job_id, GET polls to completion.

The app's real _providers() hits the network (Nominatim/OSRM), so these
tests swap in the stub stack. The JobStore points at a per-test SQLite file,
so no Postgres is needed and tests stay hermetic.
"""

import time

import pytest
from fastapi.testclient import TestClient

import app.api.trips as trips_mod
from app.db.store import configure_job_store
from app.main import app
from app.providers.geo import StubGeocoder
from app.providers.osrm import FallbackDrivingProvider
from app.providers.stubs import (
    HeuristicGroundProvider,
    StubDrivingProvider,
    StubFlightProvider,
    StubFuelProvider,
    StubRentalCarProvider,
)

BODY = {
    "origin": "Auburn, WA 98092",
    "destination_city": "Los Angeles",
    "depart_date": "2026-10-16",
    "return_date": "2026-10-19",
    "mode": "either",
}


def _stub_providers():
    return {
        "flights": StubFlightProvider(),
        "driving": FallbackDrivingProvider([StubDrivingProvider()]),
        "ground": HeuristicGroundProvider(),
        "rental": StubRentalCarProvider(),
        "fuel": StubFuelProvider(),
        "geocoder": StubGeocoder(),
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Per-test SQLite file; the app lifespan runs init_db() (create_all),
    # so tables exist before the first request.
    configure_job_store(f"sqlite+aiosqlite:///{tmp_path}/api.db")
    monkeypatch.setattr(trips_mod, "_providers", _stub_providers)
    with TestClient(app) as c:
        yield c


def _wait_for(client, job_id, want="complete", tries=100):
    for _ in range(tries):
        body = client.get(f"/trips/{job_id}").json()
        if body["status"] == want:
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached {want}: {body}")


def test_post_returns_202_with_job_id(client):
    r = client.post("/trips", json=BODY)
    assert r.status_code == 202
    body = r.json()
    assert body["job_id"]
    assert body["status"] in ("pending", "running", "complete")
    assert body["plan"] is None  # not done yet at creation time


def test_poll_to_complete_keeps_response_shape(client):
    job_id = client.post("/trips", json=BODY).json()["job_id"]
    done = _wait_for(client, job_id)
    assert done["job_id"] == job_id
    assert done["error"] is None
    plan = done["plan"]
    assert plan["origin"] == "Auburn, WA 98092"
    assert plan["destination_city"] == "Los Angeles"
    assert plan["options"], "expected priced options"
    totals = [o["total_usd"] for o in plan["options"]]
    assert totals == sorted(totals)


def test_unknown_job_id_404s(client):
    r = client.get("/trips/doesnotexist")
    assert r.status_code == 404


def test_failed_job_reports_error(client, monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("provider meltdown")

    monkeypatch.setattr(trips_mod, "plan_trip", _boom)
    job_id = client.post("/trips", json=BODY).json()["job_id"]
    failed = _wait_for(client, job_id, want="failed")
    assert failed["error"] == "provider meltdown"
    assert failed["plan"] is None
