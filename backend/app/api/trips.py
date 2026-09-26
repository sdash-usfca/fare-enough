"""Trip endpoints — the job model (ARCHITECTURE.md decision 5).

POST /trips → 202 + job_id (search runs in the background)
GET  /trips/{job_id} → status + ranked plan when complete

Phase 1 keeps jobs in memory (see _JOBS); the comment marks where Redis
takes over in Phase 1b.
"""

import asyncio
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.config import settings
from app.core.orchestrator import plan_trip
from app.models import JobStatus, TripJob, TripRequest
from app.providers.duffel import DuffelFlightProvider
from app.providers.geo import FallbackGeocoder, NominatimGeocoder, StubGeocoder
from app.providers.stubs import (
    HeuristicGroundProvider,
    StubDrivingProvider,
    StubFlightProvider,
    StubFuelProvider,
    StubRentalCarProvider,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/trips", tags=["trips"])

# Phase 1: in-memory job store. Phase 1b: Redis hash keyed by job_id.
_JOBS: dict[str, TripJob] = {}


def _providers():
    # One place where concrete providers are chosen — swap stubs for real
    # implementations here without touching the orchestrator or routes.
    # Duffel takes over flights the moment DUFFEL_API_KEY is set; everything
    # else stays on stubs until its real provider lands.
    if settings.duffel_api_key:
        log.info("flights: Duffel (live prices)")
        flights = DuffelFlightProvider(settings.duffel_api_key)
    else:
        log.info("flights: stub (set DUFFEL_API_KEY for live prices)")
        flights = StubFlightProvider()
    return {
        "flights": flights,
        "driving": StubDrivingProvider(),
        "ground": HeuristicGroundProvider(),
        "rental": StubRentalCarProvider(),
        "fuel": StubFuelProvider(),
        # Real geocoding needs no API key (Nominatim); the curated stub
        # covers the demo corridor if Nominatim is unreachable.
        "geocoder": FallbackGeocoder([NominatimGeocoder(), StubGeocoder()]),
    }


async def _run_job(job_id: str, req: TripRequest) -> None:
    try:
        plan = await plan_trip(req, **_providers())
        _JOBS[job_id].status = JobStatus.COMPLETE
        _JOBS[job_id].plan = plan
    except Exception as exc:  # noqa: BLE001 — surfaced to the client as failed
        _JOBS[job_id].status = JobStatus.FAILED
        _JOBS[job_id].error = str(exc)


@router.post("", status_code=202, response_model=TripJob)
async def create_trip(req: TripRequest, background: BackgroundTasks) -> TripJob:
    job_id = uuid.uuid4().hex[:12]
    _JOBS[job_id] = TripJob(job_id=job_id, status=JobStatus.RUNNING)
    background.add_task(_run_job, job_id, req)
    return _JOBS[job_id]


@router.get("/{job_id}", response_model=TripJob)
async def get_trip(job_id: str) -> TripJob:
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job_id")
    # Nudge: if the background task hasn't been awaited yet in tests, run it.
    if job.status == JobStatus.RUNNING:
        await asyncio.sleep(0)
    return job
