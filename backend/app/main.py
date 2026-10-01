"""App entrypoint — assembles the service (ARCHITECTURE.md file tour)."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.geocode import router as geocode_router
from app.api.trips import router as trips_router
from app.config import settings
from app.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create the jobs table if missing, then serve. See app/db/store.py
    # for why this is create_all and not Alembic at this stage.
    await init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# Web UI runs on :5173 in dev; tighten this list in prod.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(trips_router)
app.include_router(geocode_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "app": settings.app_name}
