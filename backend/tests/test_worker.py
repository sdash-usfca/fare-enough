"""Worker-pool tests — claiming, stuck-job reaping, and full pricing runs.

All hermetic: per-test SQLite file, stub providers. The two-workers-one-job
race is covered by the conditional UPDATE's contract (losers get rowcount 0);
deterministic coverage here is oldest-first order, no double-claim, and
empty-queue behavior.
"""

from datetime import date

import pytest
import pytest_asyncio

import app.api.trips as trips_mod
from app import worker as worker_mod
from app.core import orchestrator as orch_mod
from app.db.store import JobStore, configure_job_store
from app.models import JobStatus, TravelMode, TripRequest
from app.providers.geo import StubGeocoder
from app.providers.osrm import FallbackDrivingProvider
from app.providers.stubs import (
    HeuristicGroundProvider,
    StubDrivingProvider,
    StubFlightProvider,
    StubFuelProvider,
    StubRentalCarProvider,
)


def _req() -> TripRequest:
    return TripRequest(
        origin="Auburn, WA 98092",
        destination_city="Los Angeles",
        depart_date=date(2026, 10, 16),
        return_date=date(2026, 10, 19),
        mode=TravelMode.EITHER,
    )


def _stub_providers(req):
    return {
        "flights": StubFlightProvider(),
        "driving": FallbackDrivingProvider([StubDrivingProvider()]),
        "ground": HeuristicGroundProvider(),
        "rental": StubRentalCarProvider(),
        "fuel": StubFuelProvider(),
        "geocoder": StubGeocoder(),
    }


@pytest_asyncio.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setattr(trips_mod, "_providers", _stub_providers)
    s = configure_job_store(f"sqlite+aiosqlite:///{tmp_path}/worker.db")
    await s.init_models()
    yield s
    await s.close()


async def test_claim_oldest_pending_first(store: JobStore):
    await store.create_job("aaa", _req())
    await store.create_job("bbb", _req())
    claimed = await store.claim_oldest_pending()
    assert claimed is not None and claimed[0] == "aaa"
    assert (await store.get_job("aaa")).status == JobStatus.RUNNING
    # Second claim gets the other job — never the same one twice.
    claimed2 = await store.claim_oldest_pending()
    assert claimed2 is not None and claimed2[0] == "bbb"
    assert await store.claim_oldest_pending() is None  # queue empty


async def test_reset_stuck_running_requeues(store: JobStore):
    await store.create_job("aaa", _req())
    await store.mark_running("aaa")
    # older_than_s=0: anything updated before this call counts as stuck.
    assert await store.reset_stuck_running(older_than_s=0) == 1
    assert (await store.get_job("aaa")).status == JobStatus.PENDING
    # Nothing stuck → 0 requeued; pending jobs are untouched.
    assert await store.reset_stuck_running(older_than_s=0) == 0


async def test_run_once_prices_job_to_complete(store: JobStore):
    await store.create_job("aaa", _req())
    assert await worker_mod.run_once(store) is True
    job = await store.get_job("aaa")
    assert job.status == JobStatus.COMPLETE
    assert job.plan is not None and job.plan.options
    assert await worker_mod.run_once(store) is False  # queue drained


async def test_run_once_marks_failed_on_provider_meltdown(
    store: JobStore, monkeypatch
):
    async def _boom(*args, **kwargs):
        raise RuntimeError("provider meltdown")

    monkeypatch.setattr(orch_mod, "plan_trip", _boom)
    await store.create_job("aaa", _req())
    assert await worker_mod.run_once(store) is True
    job = await store.get_job("aaa")
    assert job.status == JobStatus.FAILED
    assert job.error == "provider meltdown"
