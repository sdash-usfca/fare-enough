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

**Phase 1 (now):** FastAPI backend + trip-pricing engine + minimal React web UI.
Price providers run as **stubs** returning deterministic estimates, so the app
works end-to-end with no API keys. Each stub is marked `# STUB` with a note on
what the real implementation needs.

**Live flight prices:** set `DUFFEL_API_KEY` in `backend/.env` to swap the
flight stub for real Duffel fares (test mode is free — get a key at
https://duffel.com, dashboard → test mode). Everything else stays on stubs
until its provider lands.

**Live fuel prices:** set `GOOGLE_PLACES_API_KEY` in `backend/.env` (a Google
Cloud key with **Places API (New)** enabled) to price fuel from real gas
stations near your origin. About 1,000 lookups a month are free; results
are cached for 6 hours. A price typed into the form always wins.

**Roadmap:** real providers (Duffel for flights, OSRM for driving) →
Postgres/Redis wiring → React Native iOS app on the same API → App Store.

## Layout

```
backend/          FastAPI service: API routes, pricing engine, price providers
frontend/         React web UI (Vite)
ARCHITECTURE.md   Every significant decision and why — start here to learn the codebase
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design decisions behind this layout.
