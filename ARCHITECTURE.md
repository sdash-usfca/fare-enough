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
("results appearing as they're computed"). Phase 1b persists jobs in
Postgres (SQLite for zero-infra local dev) via `app/db/store.py` — a restart
no longer loses in-flight searches, and the `pending → running → complete |
failed` lifecycle is ready for a separate worker pool later. (SSE streaming
of partial results is the planned upgrade.)

## 6. Fan-out with graceful degradation

**Decision:** the orchestrator fires all provider calls concurrently with
per-provider timeouts; a failed provider removes its branches, never the
whole trip.

**Why:** partial results beat total failure. If the rental-car API is down,
you still see flight options. Each option lists which legs are missing so the
user knows what the total excludes.

## 7. Postgres for jobs, Redis for cache (wired in Phase 1b)

**Why:** trips, users, and saved searches are relational (Postgres) — jobs
are the system of record and must survive restarts, so they live in a
`jobs` table, never in a TTL store. Flight prices are volatile and fetched
under rate limits — they want a TTL cache, which is Redis's job. Phase 1b
wires both: `app/db/store.py` persists every job (Postgres in compose, a
SQLite file for zero-infra local dev), and `app/core/cache.py` caches
provider results — OSRM routes (7-day TTL) and geocodes (30-day TTL, keeping
us polite to Nominatim's ~1 req/s policy). Redis is optional at runtime: no
`REDIS_URL` means an in-memory cache, and a dead Redis degrades to misses,
never to broken requests. Both run in `docker-compose.yml` so the dev
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

## 10. Duffel for live flight prices

**Decision:** the first real provider is Duffel (offer-requests API), chosen
over scraping or GDS-direct integration.

**Why:** one REST API returns bookable fares across 300+ airlines — no
per-airline deals, no screen-scraping. Its free **test mode** returns
realistic sandbox fares, so the integration is verifiable without spending a
dollar or booking anything real. The adapter maps Duffel offers to our
`FlightQuote` interface; time-window preferences are pushed server-side via
Duffel's `departure_time` filter, while the red-eye preference stays
client-side (Duffel has no such flag). The swap happens in exactly one place
(`api/trips.py::_providers`): set `DUFFEL_API_KEY` and flights go live, with
the orchestrator untouched — the payoff of decision 3.

**Tradeoff:** Duffel is a middleman — fares can differ slightly from what an
airline sells directly, and offer requests cost per call in live mode, which
is why the Redis fare cache matters before going live. Built 2026-09-30:
`CachedFlightProvider` (`providers/cached.py`) wraps the Duffel provider
only — the stub stays unwrapped — caching quote lists for `FARE_TTL_S`
(4h, `core/cache.py`). The key covers airports, dates, and all of
`FlightPrefs` including the client-side red-eye filter; only non-empty
LIVE/SANDBOX results are cached, never estimates or empty offer lists.

## 11. No live rental-car prices — researched, not skipped

**Decision:** the rental-car leg stays an honestly-labeled `ESTIMATED` quote;
no live provider is wired.

**Why:** there is no Duffel-equivalent for rental cars. Amadeus shut down its
entire self-service developer portal in July 2026 (enterprise contracts
only), and every remaining car-search API — Avis/Budget's dev suite, the
Discover Cars affiliate API, CarTrawler, BCD Travel — requires a business
partnership, not a signup. A portfolio project cannot conjure partner
credentials, and shipping an *untested* integration against one of those APIs
would be fake progress: it would look live while never having returned a
real price. So the `RentalCarProvider` interface (decision 3) stands ready —
a partner-backed provider slots into the one factory in
`api/trips.py::_providers()` the day credentials exist — and until then the
leg is priced by the stub with `confidence=ESTIMATED` and `source="stub-rental"`,
surfaced to the UI exactly like every other estimate.

**Interview line:** "I researched the supplier landscape, found no self-serve
API, and chose an honest labeled estimate over an unverifiable integration.
The seam for the real provider is already in the codebase."

## 12. EIA for live fuel prices

**Decision:** the second real provider is the EIA (U.S. Energy Information
Administration) weekly retail gasoline API, in `backend/app/providers/eia.py`.

**Why:** unlike rental cars, this data source actually exists for developers:
a free key (emailed instantly, no partnership), ~9,000 req/hr, one endpoint
covering every state (`petroleum/pri/gnd`, `duoarea=S{STATE}`). It's the
federal weekly pump-price survey, so it's the closest thing to ground truth
for "what does gas cost in California this week." The adapter follows the
Duffel pattern exactly — credential-gated in the one factory
(`api/trips.py::_providers()` sets `EIA_API_KEY` → live), injectable
`httpx.AsyncClient` so tests run on `MockTransport` with no network.

**Tradeoffs (documented in the module docstring, not hidden):**
- Weekly granularity: the price can lag the pump by up to ~7 days, longer
  across holiday weeks. The quote detail names the survey week
  ("week of 2026-09-21") so the staleness is visible, not silent.
- The dataset's gasoline series (`EPM0`) is the all-grades average, not
  regular-grade specifically — the detail says "all grades" instead of
  inheriting the stub's "regular" label. Close enough for trip math, but
  labeled for what it is.
- Failure mode is the same Chain of Responsibility as driving (decision 6):
  `FallbackFuelProvider([EIA, stub])` — EIA hiccups degrade to the labeled
  `ESTIMATED` stub instead of killing both drive options.

**Interview line:** "I picked the data source a developer can actually get —
free government API, no partnership — and labeled its two honest limitations
right in the quote instead of rounding them away."

## 13. Traveler-supplied fuel price beats every average

**Decision:** `TripRequest.fuel_price_per_gal` (optional) feeds a
`UserFuelProvider` (`backend/app/providers/user.py`) that sits first in the
fuel chain: user price → EIA (if keyed) → labeled-ESTIMATED stub.

**Why:** this started as a constraint — no .gov, and third-party fuel APIs
turned out to be $10k/year enterprise products (Zyla) or 10-req/month toys
(RapidAPI) — but it's the better product decision anyway. EIA tells you what
Washington averaged last week; the traveler knows what the Costco in Auburn
charged this morning. The pump you actually use is the ground truth, and it
needs no key, no network, and nothing that can break. New `USER` confidence
level keeps it distinct from `LIVE` (API data) in the quote.

**Tradeoffs (documented, not hidden):**
- One price applies to the whole trip — a documented simplification. Most
  people fill up near home; per-state prices would be over-engineering v1.
- Trusts the traveler's input; pydantic bounds (`0 < price ≤ 30`) catch
  $479 typos, not dishonesty — it's their own trip math.
- The chain order means a stale user price silently beats a fresh EIA one.
  Acceptable: if you bothered to type it, it's probably today's price.

**Interview line:** "The best data source turned out to be the user — their
actual pump price beats any state average, and it works with zero
infrastructure. Constraints made the design better."

## File tour

| Path | What it is | Why it exists |
|---|---|---|
| `backend/app/main.py` | App entrypoint: creates the FastAPI app, mounts routes | One place where the service is assembled |
| `backend/app/config.py` | Settings from env vars | 12-factor config; secrets never in code |
| `backend/app/models.py` | Pydantic request/response schemas | API contract, shared by backend and (later) generated clients |
| `backend/app/api/trips.py` | `POST /trips`, `GET /trips/{id}` | Job-model endpoints (decision 5) |
| `backend/app/db/store.py` | `JobStore`: Postgres/SQLite job persistence | Decision 7 — the jobs table is the system of record |
| `backend/app/core/cache.py` | Redis (or in-memory) provider-result cache | Decision 7 — OSRM routes + geocodes, never jobs |
| `backend/app/providers/cached.py` | Cache decorators for driving/geocoding | Keeps Nominatim polite and OSRM fast |
| `backend/app/core/orchestrator.py` | Builds the option tree, fans out, ranks | The product's brain — decision 2 and 6 live here |
| `backend/app/providers/base.py` | Provider ABCs + `Quote` | Decision 3 and 4 |
| `backend/app/providers/duffel.py` | Live flight prices (Duffel) | First real provider; active when `DUFFEL_API_KEY` is set |
| `backend/app/providers/eia.py` | Live fuel prices (EIA) + fallback | Second real provider; active when `EIA_API_KEY` is set (decision 12) |
| `backend/app/providers/user.py` | Traveler-supplied fuel price | Top of the fuel chain when `fuel_price_per_gal` is set (decision 13) |
| `backend/app/providers/*.py` | Stub/real price sources | One file per source; swap without touching the engine |
| `backend/app/data/airports.py` | Metro → airports mapping | Nearby-airport logic needs curated data, not an API || `docker-compose.yml` | api + postgres + redis | Dev matches prod (decision 7) |
| `frontend/` | Vite + React UI | Thin client over the API (decision 8) |
