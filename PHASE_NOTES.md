# Phase 2c notes — traveler fuel-price override

## What changed
- `TripRequest.fuel_price_per_gal` (optional, `0 < price ≤ 30`).
- New `backend/app/providers/user.py`: `UserFuelProvider` quotes the
  traveler's price verbatim with the new `USER` confidence level.
- `api/trips.py::_providers(req)` now takes the request; `_fuel_provider()`
  builds the chain user → EIA (if keyed) → stub inside one
  `FallbackFuelProvider`.
- 74 tests green (66 pre-existing + 8 new).

## Decisions

### 1. The constraint became the design
No .gov (her call), and third-party fuel APIs priced themselves out
(Zyla $10k/yr, RapidAPI 10 req/mo free). But the traveler's pump price is
more accurate than any state average anyway — EIA says what Washington
averaged last week, she knows what Auburn Costco charged this morning.
Zero keys, zero network, zero breakage.

### 2. One price for the whole trip (documented simplification)
Per-state prices would be over-engineering v1; most people fill up near
home. Noted in the model field comment and decision 13.

### 3. USER confidence, not LIVE
`LIVE` means "priced from a real API just now." A typed-in price is
neither — it gets its own level so the quote stays honest about origin.

### 4. Bounds catch typos, not dishonesty
`le=30` rejects $479 fat-fingers (US record is under $8); it doesn't try
to police what she types for her own trip.

## What's next
- Frontend: surface a fuel-price input on the trip form (defaults empty →
  estimate; typed → override).
- EIA remains a keyed optional upgrade if she ever wants it.

---

# Phase 2b notes — real fuel prices via EIA

## What changed
New `backend/app/providers/eia.py`: `EIAFuelProvider` queries the EIA weekly
retail gasoline survey (`api.eia.gov/v2/petroleum/pri/gnd/data/`,
`duoarea=S{STATE}`, latest week only) and maps it to a `MoneyQuote` with
`confidence=LIVE`, `source="eia"`. Wired in `api/trips.py::_providers()`:
`EIA_API_KEY` set → `FallbackFuelProvider([EIA, stub])`; unset → stub as
before. `eia_api_key` added to `backend/app/config.py`. 66 tests green
(59 pre-existing + 7 new, all on `httpx.MockTransport` — no network, no key).

## Decisions

### 1. EIA because the key actually exists
Free, instant, no partnership — the opposite of the rental-car situation
(Phase 2a). One endpoint covers all 50 states; ~9k req/hr means no cache
needed at this call volume (1–2 calls per trip).

### 2. Two honest limitations, labeled in the quote
- Weekly data can lag the pump ~7 days → the detail names the survey week.
- `EPM0` is the all-grades average, not regular → the detail says "all
  grades" instead of keeping the stub's "regular" label.

### 3. Same fallback shape as driving
`FallbackFuelProvider` mirrors `FallbackDrivingProvider`: EIA first, the
stub as a labeled-`ESTIMATED` safety net. A dead EIA degrades fuel legs, it
never kills the drive options.

## What's next
- To go live: grab a free key at eia.gov/opendata/register.php and set
  `EIA_API_KEY` in `backend/.env` (same move as the Duffel test key).
- Rental cars stay estimated until partner credentials exist (Phase 2a).

---

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
