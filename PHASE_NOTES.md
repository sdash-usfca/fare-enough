<!-- Newest first. Each entry's "What's next" is a snapshot from that phase;
     the live roadmap is in README → Project status. -->

# Phase 3f notes — docs refresh

- README "Project status" rewritten: what's live vs estimated per leg, the
  optional API keys, and a current roadmap (it still said "Phase 1 (now)").
- `backend/.env.example` lists every key; `.gitignore` lets it through.
- ARCHITECTURE decisions 17–18, file tour fixed (two rows were fused) and
  updated (states.py, worker, smoke test, CI).

---

# Phase 3e notes — long drives

## What changed
- Drives past 10h/day add hotel nights each way ($160.54, CoStar 2025 ADR),
  one room per 4 travelers; a warning when the drive doesn't fit the trip.
- `travel_hours` / `travel_note` on every option (door to door, one way);
  Duffel slice durations parsed and cached; UI shows "⏱ …".
- 160 tests green (145 + 15).
- End to end, Auburn → LA, 4-day trip: drives $1,211–1,223 with 4 hotel
  nights and a "takes 3 days each way" warning; flights from $381, ~5h.

## Decisions
- Price the nights, show the hours (ARCHITECTURE.md decision 18).

---

# Phase 3d notes — fly-option pricing gaps

## What changed
- Drive-and-park includes the drive; calendar-day parking; no parking on
  one-way trips; road miles (×1.2) to the airport; destination rental pays
  fuel; Duffel non-USD fares rejected; one fuel lookup per trip.
- 145 tests green (140 + 7 new, 4 reworked to the new math).

## Decisions
- One rule for all four: an option's total carries every cost it implies
  (ARCHITECTURE.md decision 17).
- Tests reworked, not loosened: the old 1-day-Auburn case now correctly
  picks the rideshare, so parking tests moved to a home where parking wins.

---

# Phase 3c notes — own-car wear & tear

## What changed
- `config.py`: `own_car_wear_per_mile_usd = 0.1104` (AAA 2025).
- `core/orchestrator.py`: own-car drives add a "Wear & tear" leg (miles ×
  rate × cars); rental drives don't.
- 140 tests green (137 + 3 new).
- End to end (real OSRM), solo, Auburn → LA round trip (2,231 mi): own car
  $580.95 (fuel $334.65 + wear $246.30) now ranks just **behind** a rental at
  $568.65. Auburn → Portland (307 mi): own car still wins easily, $80.05.

## Decisions

### 1. Marginal cost, not cost of ownership
AAA's maintenance-repairs-tires figure, not the IRS rate. See ARCHITECTURE.md
decision 16.

### 2. The long-road-trip flip is real, not a bug
Past ~2,000 mi, wear plus fuel can exceed a few days' rental plus fuel. Real
road-trippers rent for exactly this reason. Still, the rental side is a stub
estimate ($58/day + $20 insurance), so the margin is only as good as that
estimate (decision 11).

## What's next
- "Drive + park at the airport" still omits the fuel and wear of the drive to
  the airport (small for a ~11 mi trip, but not zero).
- Live Google Places check, blocked on a key (issue #2).

---

# Phase 3b notes — party size (travelers 1–9)

## What changed
- `models.py`: `TripRequest.travelers` (1–9); `TripOption.per_person_usd`;
  `TripPlan.travelers`. The new response fields have defaults, so plans
  stored earlier still load.
- `FlightProvider.search(..., passengers=1)`: Duffel sends one adult per
  traveler (its `total_amount` covers the party), the stub scales per seat,
  and the fare cache key includes party size.
- `core/orchestrator.py`: rideshares × ceil(n/4); fuel, rentals and parking
  × ceil(n/5); own-car drives warn when they assume a second car. Fly options
  no longer offer "drive + park" when `own_car=False`.
- `frontend/src/App.jsx`: Travelers field (clamped 1–9); "$X per person"
  under each total for parties of 2+.
- 137 tests green (129 + 8 new). End-to-end with a real API and worker, 6
  travelers, Auburn → LA: drive own car $669 ($112/pp) vs cheapest flight
  $1,849 ($308/pp).

## Decisions

### 1. The provider prices the party, the orchestrator never multiplies fares
Same rule as round trips: Duffel already returns a party total, so a second
multiplication would be silent overcharging. One place owns each unit.

### 2. Vehicles, not people, for ground legs
`ceil(n / seats)`: 4 is a standard rideshare, 5 a standard car. Simple and
explainable on the receipt ("2 cars").

## What's next
- Wear-and-tear per mile for own-car trips (fuel-only still flatters driving).
- Live Google Places check, blocked on a key (issue #2).

---

# Phase 3a notes — fuel prices from Google Places

## What changed
- New `providers/google_places.py`: `GooglePlacesFuelProvider` geocodes the
  origin, runs Nearby Search for `gas_station` (20 nearest, ~5 mi), and
  quotes the median fresh regular-unleaded price as `LIVE` /
  `source="google-places"`.
- `api/trips.py::_fuel_provider(req, geocoder)`: chain is user → Google
  (if `GOOGLE_PLACES_API_KEY`) → EIA (if `EIA_API_KEY`) → stub.
- `core/cache.py`: `FUEL_TTL_S = 6h`; `config.py`: `google_places_api_key`.
- 129 tests green (114 + 15 new, all on `httpx.MockTransport`).

## Decisions

### 1. Median, not cheapest
One station's stale or mistyped price shouldn't set the trip cost. The
median of the nearby stations is robust to one outlier either way.

### 2. Reject what we can't verify the unit of
Google doesn't document $/gal vs $/liter. USD-only plus a $1.50–$15 band
keeps a liter price (~$1.10) from quartering the fuel leg.

### 3. Cost is a design constraint
1,000 free calls/month at the Enterprise + Atmosphere tier. One fetch per
trip (shared across drive branches), a 6h cache on a ~1 km grid, and a
`places.fuelOptions`-only field mask. Failures are never cached.

### 4. EIA stays, but optional and ranked below Google
No key → it's simply absent from the chain, so "no government data" is
the default; nothing to delete.

## What's next
- Verify against the live API with a real key (needs an explicit go-ahead: calls past the free tier are billed).
- Passenger count; wear-and-tear per mile for own-car trips.

---

# Phase 2f notes — separate worker pool for jobs

## What changed
- `db/store.py`: `claim_oldest_pending()` (atomic: SELECT oldest pending +
  conditional UPDATE, losers get rowcount 0 — portable, no FOR UPDATE SKIP
  LOCKED) and `reset_stuck_running()` (requeues jobs orphaned by a crashed
  worker).
- New `app/worker.py`: `run_once()` claims and prices one job (complete /
  failed with error); `run_forever()` loops with burst mode (re-poll
  immediately after work) and a 2s idle cadence; SIGTERM/SIGINT shuts down
  gracefully. Entry point: `python -m app.worker`.
- `api/trips.py`: `POST /trips` is now enqueue-only — no `BackgroundTasks`,
  no in-process pricing. The API is a thin edge; workers do the work.
- `docker-compose.yml`: new `worker` service (`python -m app.worker`);
  scale with `docker compose up --scale worker=3`.
- `README.md`: quickstart now runs the worker in a second terminal.
- 84 tests green (80 + 4 new worker tests); smoke-tested across two real
  processes (API enqueues → pending; worker claims → prices → marks done).

## Decisions

### 1. No in-process fallback — the API never prices
Keeping BackgroundTasks as a fallback would mean two execution paths and a
double-run hazard. One path is honest: if no worker runs, jobs sit pending
and the UI shows it. Dev runs `python -m app.worker` next to uvicorn;
compose runs the worker service.

### 2. Atomic claim via conditional UPDATE, not SELECT FOR UPDATE
`UPDATE jobs SET status='running' WHERE job_id=? AND status='pending'`
makes the race safe on both SQLite and Postgres. `SKIP LOCKED` would be
Postgres-only; the store's whole point is one code path for both.

### 3. Stuck-job reaping at startup, not a separate reaper
A crashed worker leaves `running` rows nobody will touch. Resetting them to
pending once at startup (older than 10 min) is enough at this scale — no
background sweeper process to operate.

## What's next
- EIA live verification still parked (needs her explicit go-ahead).
- SSE streaming of partial results (the planned upgrade in decision 5).
- Rental-car live API when a self-serve option exists.

# Phase 2e notes — GET /trips/recent operator view

## What changed
- `models.py`: new `RecentTrip` (job_id, status, created_at, request
  summary, option_count, cheapest_usd, error) — the full plan stays behind
  `GET /trips/{job_id}` so the listing stays light.
- `db/store.py`: `recent_detailed(limit)` builds it from the stored
  request_json + plan_json.
- `api/trips.py`: `GET /trips/recent?limit=` (default 20, max 100), declared
  before `/{job_id}` so "recent" isn't swallowed as a job id.
- `frontend/src/App.jsx`: "Recent searches" section — loads on mount,
  refreshes after each search; clicking a completed row loads its plan.
- 80 tests green (77 + 3 new: newest-first summaries, limit respected,
  /recent not swallowed by /{job_id}); `vite build` passes.

## Decisions

### 1. A separate light model, not TripJob with extras
TripJob is the job-lifecycle contract (polling). RecentTrip is a read model
for the listing — request summary plus outcome, no plan payload. The
listing stays fast and the polling contract stays untouched.

### 2. Route order is the API contract
FastAPI matches routes in definition order, so /recent must precede
/{job_id}. Covered by an explicit regression test — this is exactly the
kind of thing that breaks silently in a future reorder.

## What's next
- Separate worker pool consuming `pending` jobs (the lifecycle is ready).
- EIA remains a keyed optional upgrade (her call — no .gov for now).

---

# Phase 2d notes — Duffel fare cache with TTL

## What changed
- `core/cache.py`: new `FARE_TTL_S = 4h` (deliberately the shortest TTL —
  fares move intraday) and an updated module docstring.
- `providers/cached.py`: new `CachedFlightProvider`, same wrapper shape as
  the driving/geocode caches. Key = airports + depart/return dates + all of
  `FlightPrefs` (including `red_eye_ok`, which filters client-side *after*
  the fetch, so it needs its own cache entries).
- `api/trips.py::_providers`: wraps the Duffel provider only; the stub
  stays unwrapped per the honesty rule.
- 77 tests green (74 pre-existing + 3 new: live results cached, estimates
  never cached, empty offer lists never cached).

## Decisions

### 1. Four hours, not four days
Routes get 7 days (roads don't move) and geocodes 30 (coordinates never
move). Fares move intraday, so 4h: long enough that tweaking a trip and
re-searching doesn't re-pay the 15s airline fan-out, short enough to trust.
Tunable in one constant.

### 2. The key includes the client-side filter
`red_eye_ok` never reaches Duffel — it's applied after the fetch. Caching
the *filtered* quotes without it in the key would serve red-eyes to someone
who asked for none. Two searches differing only there get two entries;
slightly fewer hits, always correct.

### 3. Empty results are misses, not data
Like the geocoder's uncached misses: a transient "no offers" cached for 4h
would hide recovery. Duffel failures raise (never cached); empty lists
aren't written.

## What's next
- EIA remains a keyed optional upgrade (her call — no .gov for now).
- `GET /trips/recent` operator view — `JobStore.recent()` already exists.
- Separate worker pool consuming `pending` jobs.

---

# Phase 2c notes — frontend: fuel-price input on the trip form

## What changed
- `frontend/src/App.jsx`: optional "Fuel price ($/gal)" number input
  (`min 0.01, max 30`, placeholder `e.g. 4.29`).
- Blank (or non-numeric) → the key is omitted from `POST /trips`, so the
  backend falls back to EIA/estimate exactly as before. Typed → parsed to
  float and sent as `fuel_price_per_gal`, which tops the fuel chain via
  `UserFuelProvider`.
- No result-display changes needed: the fuel leg already renders
  `[confidence · source]`, so an override shows up as `[USER · user]` with
  "price you entered: $X.XX/gal".
- `vite build` passes.

## Decisions
- Omit-on-blank, don't send `null`: keeps the "empty = existing fallback
  behavior" contract from Phase 2c — the backend never sees a difference
  between "no frontend" and "frontend left it blank".
- Client-side `min`/`max` mirrors the backend's `0 < price ≤ 30`; the
  backend validation remains the real guard.

## What's next
- EIA remains a keyed optional upgrade if she ever wants it.
- Duffel fare cache with TTL before live flight pricing (ARCHITECTURE.md #10).

---

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
