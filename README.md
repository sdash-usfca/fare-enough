# Fare Enough ✈️🚗

*"Fair enough."* — the cheapest way to get there, computed.

Tell Fare Enough where you want to go. It prices **every reasonable way to get
there** — fly direct, fly to a nearby airport + rental car, drive your own car,
drive a rental — and ranks them cheapest-first with a per-leg cost breakdown.

## Quickstart

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload          # API on http://localhost:8000
python -m app.worker                   # worker, in a second terminal: prices jobs

# Frontend
cd frontend
npm install && npm run dev             # UI on http://localhost:5173
```

Try it:

```bash
curl -X POST http://localhost:8000/trips \
  -H 'Content-Type: application/json' \
  -d '{"origin":"Auburn, WA 98092","destination_city":"Los Angeles",
       "depart_date":"2026-10-16","return_date":"2026-10-19","mode":"either"}'
# → 202 {"job_id":"...","status":"pending"}  (the worker picks it up)
curl http://localhost:8000/trips/<job_id>   # poll until "complete"
```

Or with Docker:

```bash
docker compose up --build
```

## Tests

```bash
cd backend && pytest tests -q          # unit + in-process integration (no network)
scripts/smoke_test.sh                  # end-to-end against a running stack
```

CI runs three jobs on every PR: **backend** (ruff + pytest), **frontend**
(production build), and **e2e** (`docker compose up` with API, worker,
Postgres and Redis, then `scripts/smoke_test.sh`, including an API restart
to prove jobs persist).

## Project status

The pricing engine, job pipeline, web UI and CI are done. Each price comes
from a real source when one is configured, and from an honest, labeled
estimate otherwise. Every leg in a result says which (`live`, `sandbox`,
`user` or `estimated`, plus its source).

| Leg | Source today | Live when |
|---|---|---|
| Flights | Estimate (deterministic placeholder fare) | `DUFFEL_API_KEY` set: real fares and flight times |
| Fuel price | Your typed price → Google Places → estimate | `GOOGLE_PLACES_API_KEY` set: median of nearby stations |
| Driving distance and time | **Live** (OSRM), estimate as fallback | Always: no key needed |
| Address lookup | **Live** (Nominatim), curated fallback | Always: no key needed |
| Rental cars | Estimate ($58/day + $20 insurance) | No self-serve API exists ([why](ARCHITECTURE.md#11-no-live-rental-car-prices--researched-not-skipped)) |
| Rideshare, parking | Estimate | No public fare APIs exist |
| Wear & tear, hotels | Estimate (AAA 2025, CoStar 2025 averages) | Tunable per deploy |

**What a result accounts for:** group size (seats vs vehicles), round trips,
drive-and-park including the drive, calendar-day parking, rental fuel,
overnight stops on long drives, wear on your own car, and door-to-door
travel time (shown, not priced).

### API keys (all optional)

Copy `backend/.env.example` to `backend/.env` and fill in what you have.
Nothing is required: without keys the app runs end to end on estimates.

- `DUFFEL_API_KEY`: [duffel.com](https://duffel.com) → dashboard → **test mode**
  key (free, realistic sample fares, books nothing). Tracked in #7.
- `GOOGLE_PLACES_API_KEY`: a Google Cloud key with **Places API (New)**
  enabled. About 1,000 lookups a month are free; results are cached 6 hours.
  Tracked in #2.
- `EIA_API_KEY`: optional U.S. government weekly averages, ranked below
  Google. Off unless set.

### Roadmap

- Verify Duffel and Google Places against the live APIs (#7, #2)
- Stream results as each option finishes (SSE) instead of polling
- More destinations and departure airports beyond the West Coast
- Deploy (CORS currently allows only `localhost:5173`)
- React Native iOS app on the same API

## Layout

```
backend/          FastAPI service: API routes, pricing engine, price providers
frontend/         React web UI (Vite)
scripts/          smoke_test.sh: end-to-end check against a running stack
ARCHITECTURE.md   Every significant decision and why — start here to learn the codebase
PHASE_NOTES.md    What changed in each phase, newest first
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design decisions behind this layout.
