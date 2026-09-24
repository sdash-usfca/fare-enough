# Architecture decisions

This file is the "why" behind the codebase. Each decision is recorded with the
reasoning and the tradeoff, so the repo reads as design thinking, not just code.

## 1. Modular monolith, not microservices

**Decision:** one deployable backend service, organized into modules with
strict boundaries (`api/`, `core/`, `providers/`, `data/`).

**Why:** there is no scaling reason to split yet — one service handles this
workload easily. Microservices would add service discovery, distributed
tracing, and network failure modes before the first user arrives. The module
boundaries *are* the future service boundaries: if flight pricing ever needs
to scale independently, `providers/flights_duffel.py` becomes its own service
with almost no rework.

**Interview line:** "Monolith first, extract services when a scaling reason
appears — premature distribution is the most expensive kind of tech debt."

## 2. Python + FastAPI for the backend

**Why:** the heart of this product is the pricing *engine* — enumerating an
option tree, fanning out to price providers in parallel, ranking results.
That is algorithmic work where Python is strongest, and FastAPI's native
`async` support makes parallel provider calls trivial (`asyncio.gather`).
Pydantic gives us request validation and schemas for free.

## 3. Provider interface pattern

**Decision:** every price source implements an abstract interface in
`backend/app/providers/base.py` (`FlightProvider`, `DrivingProvider`,
`GroundTransportProvider`, `RentalCarProvider`, `ParkingProvider`,
`FuelPriceProvider`). The orchestrator only talks to interfaces.

**Why:** adding a real Duffel flight search later means writing one new class,
not touching the engine. Tests inject stub providers. This is the single most
important structural decision in the codebase — it is what lets "Phase 1 with
stubs" and "Phase 2 with real APIs" be the same program.

## 4. Every quote carries its confidence

**Decision:** each `Quote` has `confidence: live | estimated` and a `source`
label, surfaced all the way to the UI.

**Why:** some legs have real APIs (flights via Duffel), some never will
(Uber/Lyft killed public fare APIs — rideshare is always a heuristic). Honest
UX shows which numbers are real and which are estimates instead of blending
them silently.

## 5. Trip search is a job, not a request/response

**Decision:** `POST /trips` returns `202 Accepted` with a `job_id`;
`GET /trips/{job_id}` polls for completion. (SSE streaming of partial results
is the planned upgrade.)

**Why:** pricing every branch of the option tree takes 10–30 seconds of
fan-out calls. Holding an HTTP connection open that long is fragile; a job
model is resumable, pollable, and matches how the UI wants to render
("results appearing as they're computed"). Phase 1 keeps jobs in memory with
a comment marking where Redis takes over.

## 6. Fan-out with graceful degradation

**Decision:** the orchestrator fires all provider calls concurrently with
per-provider timeouts; a failed provider removes its branches, never the
whole trip.

**Why:** partial results beat total failure. If the rental-car API is down,
you still see flight options. Each option lists which legs are missing so the
user knows what the total excludes.

## 7. Postgres + Redis (in compose, wired in Phase 1b)

**Why:** trips, users, and saved searches are relational (Postgres). Flight
prices are volatile and fetched under rate limits — they want a TTL cache,
which is Redis's job. Both run in `docker-compose.yml` today so the dev
environment matches prod.

## 8. One API, many frontends

**Decision:** React web now; React Native (Expo) iOS app later against the
same API; Android free after that.

**Why:** the pricing engine is the product — frontends are thin. Building the
API first means the second client costs a fraction of the first.

## 9. Environment-based config (12-factor)

**Decision:** all secrets and tunables live in environment variables via
`backend/app/config.py` (pydantic-settings). No keys in code, ever.

**Why:** the same container runs in dev, CI, and prod with different env.
API keys (Duffel etc.) slot in without code changes.

## File tour

| Path | What it is | Why it exists |
|---|---|---|
| `backend/app/main.py` | App entrypoint: creates the FastAPI app, mounts routes | One place where the service is assembled |
| `backend/app/config.py` | Settings from env vars | 12-factor config; secrets never in code |
| `backend/app/models.py` | Pydantic request/response schemas | API contract, shared by backend and (later) generated clients |
| `backend/app/api/trips.py` | `POST /trips`, `GET /trips/{id}` | Job-model endpoints (decision 5) |
| `backend/app/core/orchestrator.py` | Builds the option tree, fans out, ranks | The product's brain — decision 2 and 6 live here |
| `backend/app/providers/base.py` | Provider ABCs + `Quote` | Decision 3 and 4 |
| `backend/app/providers/*.py` | Stub/real price sources | One file per source; swap without touching the engine |
| `backend/app/data/airports.py` | Metro → airports mapping | Nearby-airport logic needs curated data, not an API |
| `docker-compose.yml` | api + postgres + redis | Dev matches prod (decision 7) |
| `frontend/` | Vite + React UI | Thin client over the API (decision 8) |
