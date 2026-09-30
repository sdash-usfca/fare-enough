# Phase 2a notes — rental-car provider: researched, honestly deferred

## What changed
Almost no code — one docstring in `backend/app/providers/stubs.py` and a new
ARCHITECTURE.md decision (#11). The `StubRentalCarProvider` stays the active
rental-car source, still `ESTIMATED` / `source="stub-rental"`.

## Why so little code
The research came back negative, and negative results are still results:
- Amadeus for Developers (the original "real impl" named in the stub
  docstring) **shut down its self-service portal in July 2026** — enterprise
  contracts only, no signup path. The old docstring was factually wrong; fixed.
- Every other car-search API (Avis/Budget dev suite, Discover Cars affiliate
  API, CarTrawler, BCD Travel) requires a business partnership.
- There is no free, key-based, verifiable rental-car price API — unlike
  flights (Duffel test mode) or routes (OSRM).

Options were: (a) ship an untested integration against a partner API with no
credentials to verify it, or (b) keep the labeled estimate and document the
landscape. (a) is fake progress — it would look live without ever returning
a real price — so (b) won. The provider interface is the deliverable: when
partner credentials exist, the live class slots into the single factory in
`api/trips.py::_providers()` with zero engine changes.

## What's next
- Phase 2b: real fuel prices via the EIA weekly retail gasoline API (free key,
  unlike rental cars — the API actually exists).

---

# Phase 1b notes — Postgres-backed jobs + Redis provider cache

## What changed
`POST /trips` → `GET /trips/{job_id}` keeps the exact same contract, but jobs
now persist in a `jobs` table (Postgres in compose/prod, SQLite file for
zero-infra local dev) instead of the `_JOBS` in-memory dict. OSRM route
results and geocode results are cached (Redis when `REDIS_URL` is set,
in-memory otherwise). 59 tests green (40 pre-existing + 19 new).

## Decisions

### 1. Postgres for jobs, Redis for cache — never the reverse
Jobs are the system of record: they must survive restarts, outlive any TTL,
and be queryable ("show me recent failed jobs"). Redis is volatile by design
(eviction policies, memory limits) — perfect for a cache, wrong for the
record of what you sold the user. The old comment in `trips.py` said "Redis
takes over" for jobs; that was the wrong call and this phase corrects it.

### 2. `create_all` on startup, not Alembic
One table, zero production rows to migrate — Alembic would be ceremony.
The trigger for adopting it is explicit: the day a schema change must
preserve existing jobs, add Alembic then. Premature migration tooling is
the same kind of debt as premature microservices (ARCHITECTURE.md #1).

### 3. SQLite as the default `DATABASE_URL`, Postgres in compose
`sqlite+aiosqlite:///./fare_enough.db` when `DATABASE_URL` is unset means
`uvicorn` works with zero infrastructure — important for a portfolio project
a reviewer clones cold. Same SQLAlchemy 2.x async code path for both; the
only Postgres-ism is the driver (`asyncpg` vs `aiosqlite`), selected by URL
scheme. Tests use per-test SQLite files: hermetic, no docker, no network.

### 4. `JobStore` seam; sessions per method, not per request
The routes speak Pydantic and never import an ORM model — storage can evolve
(SQLite → Postgres → separate worker pool) without touching the API. Each
store method opens and closes its own async session, which is what makes it
safe to call from FastAPI `BackgroundTasks`: no request-scoped session leaks
across the background boundary.

### 5. `pending → running → complete | failed`
New jobs are born `pending`; the background task marks `running` before
pricing. With in-memory jobs the distinction was meaningless; with a
persistent store (and a future worker pool) it's the real lifecycle, and it
lets an operator tell "queued" from "stuck".

### 6. Redis caches provider results, not jobs — and only where it pays
- OSRM routes (7-day TTL): the slowest call in the drive branches; road
  distances barely change.
- Geocodes (30-day TTL): Nominatim's usage policy asks for ~1 req/s
  politeness; coordinates effectively never change.
- Deliberately NOT cached: flight offers (volatile, rate-limited — that's the
  next cache to build when Duffel goes live) and jobs (see #1).
- Only `LIVE`/`SANDBOX` results are cached. Caching a fallback `ESTIMATED`
  heuristic would keep serving stale numbers after the real provider
  recovers — the codebase's own confidence vocabulary enforces the rule.
- Redis is optional at runtime: unset `REDIS_URL` → in-memory cache; dead
  Redis → misses, never broken requests. A cache must never take down the
  app it accelerates.

## What's next
- Alembic when the jobs table has data worth migrating (see #2).
- Duffel fare cache with TTL before live flight pricing (ARCHITECTURE.md #10).
- `GET /trips/recent` operator view — `JobStore.recent()` already exists.
- Separate worker pool consuming `pending` jobs (the lifecycle in #5 is ready).
