"""Background worker pool — the execution half of the job model.

POST /trips only enqueues (a pending row in the jobs table) and returns
202; workers do the pricing. Run one for dev, scale horizontally when the
queue grows (`docker compose up --scale worker=3`) — N workers never
double-run a job because claiming is atomic
(see JobStore.claim_oldest_pending).

Run:  python -m app.worker   (compose runs this as the `worker` service)
"""

import asyncio
import logging
import signal

import app.api.trips as trips_mod
from app.core import orchestrator as orch_mod
from app.db import configure_job_store, get_job_store, init_db

log = logging.getLogger(__name__)

# Idle cadence when the queue is empty. After running a job the worker
# re-polls immediately (burst mode) instead of sleeping.
POLL_INTERVAL_S = 2.0
# A job still marked running this long after startup is presumed orphaned
# (its worker died mid-pricing) and goes back to pending. Pricing takes
# seconds, so ten minutes is generous.
STUCK_AFTER_S = 10 * 60


async def run_once(store=None) -> bool:
    """Claim the oldest pending job and price it. True if one ran.

    Provider selection resolves through app.api.trips at call time (not
    import time) so tests can swap the stub stack via monkeypatch.
    """
    store = store or get_job_store()
    claimed = await store.claim_oldest_pending()
    if claimed is None:
        return False
    job_id, req = claimed
    try:
        plan = await orch_mod.plan_trip(req, **trips_mod._providers(req))
    except Exception as exc:  # noqa: BLE001 — surfaced on the job, not raised
        await store.mark_failed(job_id, str(exc))
        return True
    await store.mark_complete(job_id, plan)
    return True


async def run_forever() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await init_db()
    store = get_job_store()
    await store.reset_stuck_running(STUCK_AFTER_S)
    log.info("worker started (idle poll %.1fs)", POLL_INTERVAL_S)
    while not stop.is_set():
        try:
            if await run_once(store):
                continue  # burst mode: more work may be waiting
        except Exception:  # noqa: BLE001 — the loop must never die
            log.exception("worker iteration failed; continuing")
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL_S)
        except asyncio.TimeoutError:
            pass
    log.info("worker stopped")


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    configure_job_store()  # settings.database_url; idempotent without a URL
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
