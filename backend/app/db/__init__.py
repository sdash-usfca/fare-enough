"""Persistent storage: Postgres in prod/compose, SQLite for zero-friction local dev.

The jobs table is the system of record for trip searches (ARCHITECTURE.md
decision 7) — it must survive restarts and be queryable, which is why jobs
live here and not in Redis.
"""

from app.db.models import Base, JobRow
from app.db.store import JobStore, configure_job_store, get_job_store, init_db

__all__ = [
    "Base",
    "JobRow",
    "JobStore",
    "configure_job_store",
    "get_job_store",
    "init_db",
]
