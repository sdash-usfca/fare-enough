"""JobStore — the only thing the API layer knows about persistence.

Why a store class instead of raw SQLAlchemy in the routes: the routes speak
in Pydantic (TripJob/TripRequest/TripPlan) and never import an ORM model.
That seam is what keeps the API contract stable while the storage evolves
(SQLite today, Postgres in compose, a separate worker pool tomorrow).

Each method opens and closes its own session, so this is safe to call from
FastAPI BackgroundTasks — no request-scoped session leaks across the
background boundary.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Base, JobRow
from app.models import JobStatus, RecentTrip, TripJob, TripPlan, TripRequest

log = logging.getLogger(__name__)


def _normalize_url(url: str) -> str:
    """Accept plain `postgresql://` URLs; SQLAlchemy async needs a driver."""
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


class JobStore:
    def __init__(self, database_url: str):
        self._url = _normalize_url(database_url)
        # Async engine everywhere: asyncpg for Postgres, aiosqlite for the
        # local-dev SQLite default. Same code path, different driver.
        self._engine = create_async_engine(self._url)
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)

    async def init_models(self) -> None:
        """Create tables if missing.

        Deliberately create_all, not Alembic (see PHASE_NOTES.md): one table,
        zero production rows to migrate. The day a schema change must preserve
        existing jobs is the day Alembic earns its place.
        """
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def create_job(self, job_id: str, req: TripRequest) -> TripJob:
        """Insert a pending job. Raises on duplicate job_id (DB constraint)."""
        now = datetime.now(timezone.utc)
        async with self._sessions() as s:
            s.add(JobRow(job_id=job_id, status=JobStatus.PENDING.value,
                         request_json=req.model_dump(mode="json"),
                         created_at=now, updated_at=now))
            try:
                await s.commit()
            except IntegrityError as exc:
                await s.rollback()
                raise ValueError(f"job {job_id} already exists") from exc
        log.info("job %s created (pending)", job_id)
        return TripJob(job_id=job_id, status=JobStatus.PENDING)

    async def get_job(self, job_id: str) -> TripJob | None:
        async with self._sessions() as s:
            row = await s.get(JobRow, job_id)
            return _to_trip_job(row) if row else None

    async def mark_running(self, job_id: str) -> None:
        await self._set_status(job_id, JobStatus.RUNNING)

    async def mark_complete(self, job_id: str, plan: TripPlan) -> None:
        async with self._sessions() as s:
            row = await s.get(JobRow, job_id)
            if row is None:
                raise KeyError(f"unknown job_id {job_id}")
            row.status = JobStatus.COMPLETE.value
            row.plan_json = plan.model_dump(mode="json")
            row.updated_at = datetime.now(timezone.utc)
            await s.commit()
        log.info("job %s complete (%d options)", job_id, len(plan.options))

    async def mark_failed(self, job_id: str, error: str) -> None:
        async with self._sessions() as s:
            row = await s.get(JobRow, job_id)
            if row is None:
                raise KeyError(f"unknown job_id {job_id}")
            row.status = JobStatus.FAILED.value
            row.error = error[:2000]
            row.updated_at = datetime.now(timezone.utc)
            await s.commit()
        log.warning("job %s failed: %s", job_id, error[:120])

    async def _set_status(self, job_id: str, status: JobStatus) -> None:
        async with self._sessions() as s:
            row = await s.get(JobRow, job_id)
            if row is None:
                raise KeyError(f"unknown job_id {job_id}")
            row.status = status.value
            row.updated_at = datetime.now(timezone.utc)
            await s.commit()

    async def recent(self, limit: int = 50) -> list[TripJob]:
        """Newest jobs first — the seed of an operator/debugging view."""
        async with self._sessions() as s:
            rows = (await s.execute(
                select(JobRow).order_by(JobRow.created_at.desc()).limit(limit)
            )).scalars().all()
            return [_to_trip_job(r) for r in rows]

    async def recent_detailed(self, limit: int = 20) -> list[RecentTrip]:
        """Newest jobs first, each with its request summary and outcome —
        what GET /trips/recent serves."""
        async with self._sessions() as s:
            rows = (await s.execute(
                select(JobRow).order_by(JobRow.created_at.desc()).limit(limit)
            )).scalars().all()
            return [_to_recent_trip(r) for r in rows]

    async def claim_oldest_pending(self) -> tuple[str, TripRequest] | None:
        """Atomically move the oldest pending job to running.

        The conditional UPDATE (status='pending' in the WHERE clause) is the
        whole concurrency story: N workers can race, but only the winner's
        rowcount is 1 — losers get 0 and move on. Portable, unlike
        FOR UPDATE SKIP LOCKED, so SQLite and Postgres share the code path.
        """
        async with self._sessions() as s:
            row = (await s.execute(
                select(JobRow)
                .where(JobRow.status == JobStatus.PENDING.value)
                .order_by(JobRow.created_at)
                .limit(1)
            )).scalars().first()
            if row is None:
                return None
            job_id, req = row.job_id, TripRequest(**row.request_json)
            updated = await s.execute(
                update(JobRow)
                .where(JobRow.job_id == job_id,
                       JobRow.status == JobStatus.PENDING.value)
                .values(status=JobStatus.RUNNING.value,
                        updated_at=datetime.now(timezone.utc))
            )
            await s.commit()
            if (updated.rowcount or 0) != 1:
                return None  # lost the race to another worker
            log.info("job %s claimed by worker", job_id)
            return job_id, req

    async def reset_stuck_running(self, older_than_s: int) -> int:
        """Requeue jobs stuck in running — their worker died mid-pricing.

        Called once at worker startup. Without it, a crash would leave jobs
        in running forever: invisible to claim_oldest_pending, never retried.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_s)
        async with self._sessions() as s:
            updated = await s.execute(
                update(JobRow)
                .where(JobRow.status == JobStatus.RUNNING.value,
                       JobRow.updated_at < cutoff)
                .values(status=JobStatus.PENDING.value,
                        updated_at=datetime.now(timezone.utc))
            )
            await s.commit()
            n = updated.rowcount or 0
            if n:
                log.warning("requeued %d stuck running job(s)", n)
            return n

    async def close(self) -> None:
        await self._engine.dispose()


def _to_trip_job(row: JobRow) -> TripJob:
    return TripJob(
        job_id=row.job_id,
        status=JobStatus(row.status),
        plan=TripPlan(**row.plan_json) if row.plan_json else None,
        error=row.error,
    )


def _to_recent_trip(row: JobRow) -> RecentTrip:
    req = TripRequest(**row.request_json)
    plan = TripPlan(**row.plan_json) if row.plan_json else None
    return RecentTrip(
        job_id=row.job_id,
        status=JobStatus(row.status),
        created_at=row.created_at,
        origin=req.origin,
        destination_city=req.destination_city,
        depart_date=req.depart_date,
        return_date=req.return_date,
        option_count=len(plan.options) if plan else 0,
        cheapest_usd=(plan.options[0].total_usd
                      if plan and plan.options else None),
        error=row.error,
        mode=req.mode,
    )


_store: JobStore | None = None


def configure_job_store(database_url: str | None = None) -> JobStore:
    """Configure the process-wide store.

    An explicit URL always (re)configures — that's how tests hermetically
    isolate themselves. With no URL, settings are used but only if no store
    exists yet, so app startup is idempotent.
    """
    global _store
    if database_url is not None or _store is None:
        _store = JobStore(database_url or settings.database_url)
    return _store


def get_job_store() -> JobStore:
    if _store is None:
        raise RuntimeError(
            "job store not configured — the app lifespan must call "
            "configure_job_store() before serving requests")
    return _store


async def init_db() -> None:
    """Create tables. Called once at app startup."""
    await configure_job_store().init_models()
