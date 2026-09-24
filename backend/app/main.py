"""App entrypoint — assembles the service (ARCHITECTURE.md file tour)."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.trips import router as trips_router
from app.config import settings

app = FastAPI(title=settings.app_name)

# Web UI runs on :5173 in dev; tighten this list in prod.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(trips_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "app": settings.app_name}
