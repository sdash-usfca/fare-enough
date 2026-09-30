"""Trip endpoints — the job model (ARCHITECTURE.md decision 5).

POST /trips → 202 + job_id (search runs in the background)
GET  /trips/{job_id} → status + ranked plan when complete

Jobs persist in the JobStore (Postgres in compose/prod, SQLite for
zero-infra local dev) so a restart no longer loses in-flight searches.
Provider results (OSRM routes, geocodes) are cached via Redis with an
in-memory fallback — see app/core/cache.py for the what and why.
"""

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.config import settings
from app.core.cache import build_cache
from app.core.orchestrator import plan_trip
from app.db import get_job_store
from app.models import TripJob, TripRequest
from app.providers.base import FuelPriceProvider
from app.providers.cached import CachedDrivingProvider, CachedGeocoder
from app.providers.duffel import DuffelFlightProvider
from app.providers.eia import EIAFuelProvider, FallbackFuelProvider
from app.providers.geo import FallbackGeocoder, NominatimGeocoder, StubGeocoder
from app.providers.osrm import FallbackDrivingProvider, OSRMDrivingProvider
from app.providers.stubs import (
    HeuristicGroundProvider,
    StubDrivingProvider,
    StubFlightProvider,
    StubFuelProvider,
    StubRentalCarProvider,
)
from app.providers.user import UserFuelProvider

log = logging.getLogger(__name__)

router = APIRouter(prefix="/trips", tags=["trips"])

# Built once per process: the redis client connects lazily and every cache
# op degrades to a miss on failure, so this is safe even if Redis is down.
_cache = build_cache(settings.redis_url)


def _fuel_provider(req: TripRequest) -> FuelPriceProvider:
    # Fuel chain, highest priority first. The traveler's own price beats
    # any average (their pump > a state survey); EIA beats the stub; the
    # stub keeps the lights on so fuel never kills the drive options.
    providers: list[FuelPriceProvider] = []
    if req.fuel_price_per_gal is not None:
        log.info("fuel: user price $%.2f/gal", req.fuel_price_per_gal)
        providers.append(UserFuelProvider(req.fuel_price_per_gal))
    if settings.eia_api_key:
        log.info("fuel: EIA (live prices)")
        providers.append(EIAFuelProvider(settings.eia_api_key))
    else:
        log.info("fuel: stub (enter fuel_price_per_gal or set "
                 "EIA_API_KEY for live prices)")
    providers.append(StubFuelProvider())
    return FallbackFuelProvider(providers)


def _providers(req: TripRequest):
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
    # Real geocoding needs no API key (Nominatim); the curated stub
    # covers the demo corridor if Nominatim is unreachable. Results are
    # cached across trips — Nominatim's usage policy asks for ~1 req/s
    # politeness, so we don't re-ask for the same place twice.
    geocoder = CachedGeocoder(
        FallbackGeocoder([NominatimGeocoder(), StubGeocoder()]), _cache
    )
    return {
        "flights": flights,
        # Real road distances via OSRM; the stub survives as a labeled
        # ESTIMATED fallback so a demo-server hiccup doesn't kill driving.
        # Route results are cached — road distances barely change, and OSRM
        # is the slowest call in the drive branches.
        "driving": CachedDrivingProvider(
            FallbackDrivingProvider(
                [OSRMDrivingProvider(geocoder), StubDrivingProvider()]),
            _cache,
        ),
        "ground": HeuristicGroundProvider(),
        "rental": StubRentalCarProvider(),
        # EIA takes over fuel the moment EIA_API_KEY is set; the traveler's
        # own fuel_price_per_gal beats both; the stub survives inside the
        # fallback as a labeled ESTIMATED safety net, same Chain of
        # Responsibility as the driving providers above.
        "fuel": _fuel_provider(req),
        "geocoder": geocoder,
    }


async def _run_job(job_id: str, req: TripRequest) -> None:
    store = get_job_store()
    await store.mark_running(job_id)
    try:
        plan = await plan_trip(req, **_providers(req))
        await store.mark_complete(job_id, plan)
    except Exception as exc:  # noqa: BLE001 — surfaced to the client as failed
        await store.mark_failed(job_id, str(exc))


@router.post("", status_code=202, response_model=TripJob)
async def create_trip(req: TripRequest, background: BackgroundTasks) -> TripJob:
    job_id = uuid.uuid4().hex[:12]
    job = await get_job_store().create_job(job_id, req)
    background.add_task(_run_job, job_id, req)
    return job


@router.get("/{job_id}", response_model=TripJob)
async def get_trip(job_id: str) -> TripJob:
    job = await get_job_store().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job_id")
    return job
