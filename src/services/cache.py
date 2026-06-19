"""In-process TTL cache shared across services.

A plain dict is fine for a single-process server. If you ever run multiple
workers (e.g. uvicorn --workers 4), swap this for Redis or a shared sidecar.
"""
import time
from typing import Optional

_cache: dict = {
    "sched": None, "ts": 0.0,
    "well": None, "well_ts": 0.0,
    "wx": None, "wx_ts": 0.0,
    "fitform": None, "fitform_ts": 0.0,
    "other": None, "other_ts": 0.0,
}


def get(key: str):
    return _cache.get(key)


def set(key: str, value, ts_key: Optional[str] = None):  # noqa: A001
    _cache[key] = value
    if ts_key:
        _cache[ts_key] = time.time()


def fresh(ts_key: str, ttl: float) -> bool:
    """True if the cached value is younger than `ttl` seconds."""
    return _cache.get(ts_key, 0.0) > time.time() - ttl
