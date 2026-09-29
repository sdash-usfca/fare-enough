"""Provider-result cache (ARCHITECTURE.md decision 7 — the Redis half).

What gets cached and why:
- OSRM route results (origin → destination miles/hours): the slowest call in
  the drive branches, and road distances barely change — 7-day TTL.
- Geocode results (place → lat/lon): Nominatim's usage policy asks for
  ~1 req/s politeness; coordinates effectively never change — 30-day TTL.

What does NOT get cached: flight offers (volatile prices under rate limits —
  that's the next cache to build, deliberately, when Duffel goes live) and
  jobs (the system of record — Postgres, not a TTL store).

If REDIS_URL is unset, everything falls back to a process-local MemoryCache
so tests and stub-mode dev need no infrastructure. A dead Redis degrades to
cache misses, never to broken requests.
"""

import json
import logging
import time
from typing import Any, Protocol

log = logging.getLogger(__name__)

ROUTE_TTL_S = 7 * 24 * 3600
GEOCODE_TTL_S = 30 * 24 * 3600


class Cache(Protocol):
    async def get_json(self, key: str) -> Any | None: ...
    async def set_json(self, key: str, value: Any, ttl_s: int) -> None: ...


class MemoryCache:
    """Process-local TTL cache — the no-Redis default."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}

    async def get_json(self, key: str) -> Any | None:
        hit = self._data.get(key)
        if hit is None:
            return None
        expires_at, value = hit
        if time.monotonic() >= expires_at:
            del self._data[key]
            return None
        return value

    async def set_json(self, key: str, value: Any, ttl_s: int) -> None:
        self._data[key] = (time.monotonic() + ttl_s, value)


class RedisCache:
    """Redis-backed cache. Failures become misses — never exceptions."""

    def __init__(self, redis_url: str):
        from redis.asyncio import from_url
        self._client = from_url(redis_url, decode_responses=True)

    async def get_json(self, key: str) -> Any | None:
        try:
            raw = await self._client.get(key)
        except Exception as exc:  # noqa: BLE001 — cache must not break requests
            log.warning("redis get failed (%r); treating as miss", exc)
            return None
        return json.loads(raw) if raw is not None else None

    async def set_json(self, key: str, value: Any, ttl_s: int) -> None:
        try:
            await self._client.setex(key, ttl_s, json.dumps(value))
        except Exception as exc:  # noqa: BLE001 — see above
            log.warning("redis set failed (%r); skipping cache write", exc)

    async def close(self) -> None:
        await self._client.aclose()


def build_cache(redis_url: str | None = None) -> Cache:
    """Redis when configured, MemoryCache otherwise."""
    if redis_url:
        log.info("provider cache: redis")
        return RedisCache(redis_url)
    log.info("provider cache: in-memory (set REDIS_URL for shared caching)")
    return MemoryCache()
