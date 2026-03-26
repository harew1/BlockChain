"""
cache.py — Two-tier cache: Redis (if available) → in-memory fallback.
Provides get / set / delete / flush for any serializable value.
"""

import json
import time
import logging
from typing import Any, Optional

from config import USE_REDIS, REDIS_URL, CACHE_TTL_SECS

logger = logging.getLogger("cache")

# ─── In-memory store ──────────────────────────────────────────────────────────
_store: dict[str, tuple[Any, float]] = {}   # key → (value, expires_at)


def _mem_get(key: str) -> Optional[Any]:
    entry = _store.get(key)
    if entry is None:
        return None
    value, expires_at = entry
    if time.time() > expires_at:
        del _store[key]
        return None
    return value


def _mem_set(key: str, value: Any, ttl: int) -> None:
    _store[key] = (value, time.time() + ttl)


def _mem_delete(key: str) -> None:
    _store.pop(key, None)


def _mem_flush() -> None:
    _store.clear()


# ─── Redis client (optional) ──────────────────────────────────────────────────
_redis_client = None

if USE_REDIS:
    try:
        import redis
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        _redis_client.ping()
        logger.info("[Cache] Redis connected: %s", REDIS_URL)
    except Exception as exc:
        logger.warning("[Cache] Redis unavailable (%s), falling back to in-memory.", exc)
        _redis_client = None


# ─── Public API ───────────────────────────────────────────────────────────────
def get(key: str) -> Optional[Any]:
    """Retrieve a cached value, or None if expired / missing."""
    if _redis_client:
        raw = _redis_client.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return _mem_get(key)


def set(key: str, value: Any, ttl: int = CACHE_TTL_SECS) -> None:
    """Store a value with TTL (seconds)."""
    if _redis_client:
        _redis_client.setex(key, ttl, json.dumps(value, default=str))
    else:
        _mem_set(key, value, ttl)


def delete(key: str) -> None:
    """Remove a key."""
    if _redis_client:
        _redis_client.delete(key)
    else:
        _mem_delete(key)


def flush() -> None:
    """Clear entire cache (use with care)."""
    if _redis_client:
        _redis_client.flushdb()
    else:
        _mem_flush()


def cached(key_fn, ttl: int = CACHE_TTL_SECS):
    """
    Decorator factory.
    Usage:
        @cached(lambda symbol: f"ticker:{symbol}", ttl=10)
        async def get_ticker(symbol): ...
    """
    import functools
    import asyncio

    def decorator(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            k = key_fn(*args, **kwargs)
            hit = get(k)
            if hit is not None:
                return hit
            result = await func(*args, **kwargs)
            if result is not None:
                set(k, result, ttl)
            return result

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            k = key_fn(*args, **kwargs)
            hit = get(k)
            if hit is not None:
                return hit
            result = func(*args, **kwargs)
            if result is not None:
                set(k, result, ttl)
            return result

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper
    return decorator
