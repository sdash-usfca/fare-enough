"""API contract tests — POST /trips → 202 + job_id, GET polls to completion.

The app's real _providers() hits the network (Nominatim/OSRM), so these
tests swap in the stub stack. The JobStore points at a per-test SQLite file,
so no Postgres is needed and tests stay hermetic.

Since Phase 2f the API only enqueues — workers price jobs — so the tests
drive the worker inline (see _wait_for): each poll lets the worker claim
and price one job before checking status.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

import app.api.trips as trips_mod
from app import worker as worker_mod
from app.core import orchestrator as orch_mod
from app.db import get_job_store
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


def _stub_providers(req):
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
    async def _poll():
        store = get_job_store()
        for _ in range(tries):
            body = client.get(f"/trips/{job_id}").json()
            if body["status"] == want:
                return body
            # The API only enqueues — act as the worker for one iteration.
            await worker_mod.run_once(store)
            await asyncio.sleep(0.01)
        raise AssertionError(f"job {job_id} never reached {want}: {body}")

    return asyncio.run(_poll())


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

    monkeypatch.setattr(orch_mod, "plan_trip", _boom)
    job_id = client.post("/trips", json=BODY).json()["job_id"]
    failed = _wait_for(client, job_id, want="failed")
    assert failed["error"] == "provider meltdown"
    assert failed["plan"] is None


def test_recent_lists_newest_first_with_summaries(client):
    id1 = client.post("/trips", json=BODY).json()["job_id"]
    _wait_for(client, id1)
    nyc = dict(BODY, destination_city="New York",
               depart_date="2026-11-01", return_date="2026-11-04")
    id2 = client.post("/trips", json=nyc).json()["job_id"]
    _wait_for(client, id2)

    r = client.get("/trips/recent")
    assert r.status_code == 200
    items = r.json()
    assert [i["job_id"] for i in items] == [id2, id1]  # newest first
    first = items[0]
    assert first["origin"] == "Auburn, WA 98092"
    assert first["destination_city"] == "New York"
    assert first["depart_date"] == "2026-11-01"
    assert first["status"] == "complete"
    assert first["option_count"] > 0
    assert first["cheapest_usd"] is not None
    assert first["error"] is None


def test_recent_limit_is_respected(client):
    ids = [client.post("/trips", json=BODY).json()["job_id"] for _ in range(3)]
    for jid in ids:
        _wait_for(client, jid)
    assert len(client.get("/trips/recent?limit=2").json()) == 2


def test_recent_not_swallowed_by_job_id_route(client):
    # /recent must be declared before /{job_id}; otherwise this 404s as
    # "unknown job_id".
    r = client.get("/trips/recent")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
