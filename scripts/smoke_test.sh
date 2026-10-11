#!/usr/bin/env bash
# End-to-end smoke test against a running stack (API + worker + Postgres +
# Redis), e.g. `docker compose up -d --wait && scripts/smoke_test.sh`.
#
# Checks the wiring, not the prices — exact numbers are the unit tests' job.
# Real geocoding/routing (Nominatim, OSRM) may be slow or blocked from CI
# runners; the app's fallbacks still price the trip, so only structure is
# asserted: the job completes, options come back ranked, every leg is labeled.
#
# Usage: scripts/smoke_test.sh [base_url]   (default http://localhost:8000)
# Env:   SMOKE_RESTART_CMD — optional command that restarts the API; when
#        set, the job must still be readable afterwards (Postgres persistence).
#        SMOKE_JOB_TIMEOUT_S — how long to wait for the worker (default 180).
set -euo pipefail

BASE="${1:-http://localhost:8000}"
PY="${PYTHON:-python3}"
fail() { echo "FAIL: $*" >&2; exit 1; }
json() { "$PY" -c "import sys, json; d = json.load(sys.stdin); print($1)"; }

wait_healthy() {
  for _ in $(seq 1 60); do
    curl -fsS "$BASE/health" >/dev/null 2>&1 && return 0
    sleep 1
  done
  fail "API not healthy at $BASE/health after 60s"
}

echo "1. API is up"
wait_healthy

DEPART=$("$PY" -c "import datetime as d; print(d.date.today() + d.timedelta(days=21))")
RETURN=$("$PY" -c "import datetime as d; print(d.date.today() + d.timedelta(days=24))")
BODY="{\"origin\":\"Auburn, WA 98092\",\"destination_city\":\"Los Angeles\",\
\"depart_date\":\"$DEPART\",\"return_date\":\"$RETURN\",\"mode\":\"either\"}"

echo "2. POST /trips enqueues (202 + job_id)"
RESP=$(curl -sS -w '\n%{http_code}' -X POST "$BASE/trips" \
  -H 'Content-Type: application/json' -d "$BODY")
CODE=$(tail -n1 <<<"$RESP")
[ "$CODE" = "202" ] || fail "POST /trips returned $CODE: $(head -n1 <<<"$RESP")"
JOB=$(head -n1 <<<"$RESP" | json 'd["job_id"]')
echo "   job $JOB"

echo "3. A worker claims and prices it"
STATUS=pending
for _ in $(seq 1 $(( ${SMOKE_JOB_TIMEOUT_S:-180} / 2 ))); do
  JOB_JSON=$(curl -fsS "$BASE/trips/$JOB")
  STATUS=$(json 'd["status"]' <<<"$JOB_JSON")
  case "$STATUS" in complete|failed) break ;; esac
  sleep 2
done
[ "$STATUS" = "complete" ] || fail "job ended '$STATUS': $JOB_JSON"

echo "4. Plan is ranked cheapest-first, every leg labeled"
"$PY" - "$JOB_JSON" <<'PYEOF'
import json, sys
plan = json.loads(sys.argv[1])["plan"]
opts = plan["options"]
assert opts, f"no options; warnings: {plan['warnings']}"
totals = [o["total_usd"] for o in opts]
assert totals == sorted(totals), f"not ranked: {totals}"
assert {o["mode"] for o in opts} >= {"drive"}, "no drive options"
for o in opts:
    assert o["legs"], f"option without legs: {o['summary']}"
    for leg in o["legs"]:
        assert leg["confidence"] and leg["source"], f"unlabeled leg: {leg}"
print(f"   {len(opts)} options, cheapest ${totals[0]:.2f}: {opts[0]['summary']}")
for w in plan["warnings"]:
    print(f"   (plan warning) {w}")
PYEOF

echo "5. Bad input is rejected at the edge (return before depart → 422)"
BAD="{\"origin\":\"a\",\"destination_city\":\"b\",\
\"depart_date\":\"$RETURN\",\"return_date\":\"$DEPART\"}"
CODE=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$BASE/trips" \
  -H 'Content-Type: application/json' -d "$BAD")
[ "$CODE" = "422" ] || fail "reversed dates returned $CODE, want 422"

echo "6. GET /trips/recent lists the job"
curl -fsS "$BASE/trips/recent" | json "'$JOB' in [r['job_id'] for r in d]" \
  | grep -qx True || fail "job $JOB missing from /trips/recent"

if [ -n "${SMOKE_RESTART_CMD:-}" ]; then
  echo "7. Job survives an API restart (persisted, not in memory)"
  eval "$SMOKE_RESTART_CMD"
  wait_healthy
  STATUS=$(curl -fsS "$BASE/trips/$JOB" | json 'd["status"]')
  [ "$STATUS" = "complete" ] || fail "after restart job is '$STATUS'"
fi

echo "PASS: end-to-end smoke test"
