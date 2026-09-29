"""JobStore tests — SQLite file per test, same SQLAlchemy code path as prod.

Why SQLite and not a Postgres testcontainer: the store speaks SQLAlchemy,
not Postgres-isms (JSON columns, server defaults — nothing dialect-specific
beyond asyncpg vs aiosqlite drivers). SQLite keeps the suite hermetic: no
docker, no network, CI-friendly. Postgres parity is covered by compose for
manual verification.
"""

from datetime import date

import pytest
import pytest_asyncio

from app.db.store import JobStore, configure_job_store, get_job_store
from app.models import (
    JobStatus,
    LegQuote,
    QuoteConfidence,
    TravelMode,
    TripJob,
    TripOption,
    TripPlan,
    TripRequest,
)


def _req() -> TripRequest:
    return TripRequest(
        origin="Auburn, WA 98092",
        destination_city="Los Angeles",
        depart_date=date(2026, 10, 16),
        return_date=date(2026, 10, 19),
        mode=TravelMode.EITHER,
    )


def _plan() -> TripPlan:
    return TripPlan(
        origin="Auburn, WA 98092",
        destination_city="Los Angeles",
        options=[
            TripOption(
                id="drive-own-abc123",
                mode=TravelMode.DRIVE,
                summary="Drive your own car (2231 mi)",
                legs=[LegQuote(label="Fuel", amount_usd=100.0,
                               confidence=QuoteConfidence.ESTIMATED,
                               source="stub")],
                total_usd=100.0,
            )
        ],
        warnings=["demo warning"],
    )


@pytest_asyncio.fixture
async def store(tmp_path):
    s = configure_job_store(f"sqlite+aiosqlite:///{tmp_path}/jobs.db")
    await s.init_models()
    yield s
    await s.close()


async def test_create_starts_pending_and_round_trips_request(store: JobStore):
    job = await store.create_job("abc123", _req())
    assert job.job_id == "abc123"
    assert job.status == JobStatus.PENDING
    assert job.plan is None

    fetched = await store.get_job("abc123")
    assert fetched is not None
    assert fetched.status == JobStatus.PENDING
    # The request survives the JSON round trip intact.
    req = TripRequest(**(await _raw_request(store, "abc123")))
    assert req.origin == "Auburn, WA 98092"
    assert req.return_date == date(2026, 10, 19)


async def test_full_lifecycle_pending_running_complete(store: JobStore):
    await store.create_job("j1", _req())
    await store.mark_running("j1")
    assert (await store.get_job("j1")).status == JobStatus.RUNNING

    await store.mark_complete("j1", _plan())
    done = await store.get_job("j1")
    assert done.status == JobStatus.COMPLETE
    assert done.plan is not None
    assert done.plan.options[0].total_usd == 100.0
    assert done.plan.warnings == ["demo warning"]
    assert done.error is None


async def test_failed_job_carries_error(store: JobStore):
    await store.create_job("j2", _req())
    await store.mark_failed("j2", "Duffel exploded")
    failed = await store.get_job("j2")
    assert failed.status == JobStatus.FAILED
    assert failed.error == "Duffel exploded"
    assert failed.plan is None


async def test_unknown_job_returns_none(store: JobStore):
    assert await store.get_job("nope") is None


async def test_duplicate_job_id_rejected(store: JobStore):
    await store.create_job("dup", _req())
    with pytest.raises(ValueError, match="already exists"):
        await store.create_job("dup", _req())


async def test_marking_unknown_job_raises_keyerror(store: JobStore):
    with pytest.raises(KeyError):
        await store.mark_complete("ghost", _plan())
    with pytest.raises(KeyError):
        await store.mark_failed("ghost", "boom")


async def test_recent_lists_newest_first(store: JobStore):
    await store.create_job("old", _req())
    await store.create_job("new", _req())
    jobs = await store.recent(limit=10)
    assert [j.job_id for j in jobs] == ["new", "old"]


async def test_get_job_store_raises_before_configure():
    import app.db.store as store_mod

    prev, store_mod._store = store_mod._store, None
    try:
        with pytest.raises(RuntimeError, match="not configured"):
            get_job_store()
    finally:
        store_mod._store = prev


async def _raw_request(store: JobStore, job_id: str) -> dict:
    async with store._sessions() as s:
        from app.db.models import JobRow

        row = await s.get(JobRow, job_id)
        assert row is not None
        return row.request_json
