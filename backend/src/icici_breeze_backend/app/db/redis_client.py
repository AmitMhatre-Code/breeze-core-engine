"""Redis client with in-process fallback when Redis is unavailable."""
from __future__ import annotations

import fnmatch
import json
import logging
import threading
import time
from typing import Any, Optional

from icici_breeze_backend.core import config as cfg

_logger = logging.getLogger(__name__)

_redis: Any = None
_use_memory = False
_memory: dict[str, tuple[str, float | None]] = {}
_memory_sets: dict[str, set[str]] = {}
_memory_lock = threading.RLock()


class _MemoryPipeline:
    def __init__(self, store: "_MemoryStore") -> None:
        self._store = store
        self._ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def set(self, key: str, value: str, ex: int | None = None) -> "_MemoryPipeline":
        self._ops.append(("set", (key, value), {"ex": ex}))
        return self

    def delete(self, *keys: str) -> "_MemoryPipeline":
        self._ops.append(("delete", keys, {}))
        return self

    def execute(self) -> list[Any]:
        out: list[Any] = []
        for op, args, kw in self._ops:
            if op == "set":
                out.append(self._store.set(args[0], args[1], ex=kw.get("ex")))
            elif op == "delete":
                out.append(self._store.delete(*args))
        return out


class _MemoryStore:
    def get(self, key: str) -> str | None:
        with _memory_lock:
            entry = _memory.get(key)
            if entry is None:
                return None
            val, expires = entry
            if expires is not None and time.time() > expires:
                del _memory[key]
                return None
            return val

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        with _memory_lock:
            expires = (time.time() + ex) if ex else None
            _memory[key] = (value, expires)
            return True

    def setex(self, key: str, seconds: int, value: str) -> bool:
        return self.set(key, value, ex=seconds)

    def delete(self, *keys: str) -> int:
        removed = 0
        with _memory_lock:
            for key in keys:
                if key in _memory:
                    del _memory[key]
                    removed += 1
        return removed

    def keys(self, pattern: str = "*") -> list[str]:
        with _memory_lock:
            now = time.time()
            alive = []
            for k, (_, exp) in list(_memory.items()):
                if exp is not None and now > exp:
                    del _memory[k]
                else:
                    alive.append(k)
            return [k for k in alive if fnmatch.fnmatch(k, pattern.replace("?", "?"))]

    def scan_iter(self, match: str = "*", count: int = 100):
        for k in self.keys(match):
            yield k

    def ping(self) -> bool:
        return True

    def pipeline(self) -> _MemoryPipeline:
        return _MemoryPipeline(self)

    def sadd(self, key: str, *values: str) -> int:
        with _memory_lock:
            bucket = _memory_sets.setdefault(key, set())
            added = 0
            for value in values:
                if value not in bucket:
                    bucket.add(value)
                    added += 1
            return added

    def srem(self, key: str, *values: str) -> int:
        with _memory_lock:
            bucket = _memory_sets.get(key)
            if not bucket:
                return 0
            removed = 0
            for value in values:
                if value in bucket:
                    bucket.remove(value)
                    removed += 1
            if not bucket:
                _memory_sets.pop(key, None)
            return removed

    def smembers(self, key: str) -> set[str]:
        with _memory_lock:
            return set(_memory_sets.get(key, set()))

    def publish(self, channel: str, message: str) -> int:
        return 0

    def pubsub(self, **kwargs: Any) -> Any:
        class _NoopPubSub:
            def subscribe(self, *_a: Any, **_k: Any) -> None:
                pass

            def get_message(self, **_k: Any) -> None:
                return None

        return _NoopPubSub()

    def close(self) -> None:
        with _memory_lock:
            _memory.clear()
            _memory_sets.clear()


def _init_redis_client() -> Any:
    global _redis, _use_memory
    if _redis is not None:
        return _redis
    try:
        import redis

        client = redis.Redis.from_url(
            cfg.redis_connection_url(),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        _redis = client
        _use_memory = False
        _logger.info("Redis connected at %s", cfg.redis_connection_url())
        return _redis
    except Exception as exc:
        _logger.warning("Redis unavailable (%s); using in-memory cache fallback.", exc)
        _redis = _MemoryStore()
        _use_memory = True
        return _redis


def get_redis() -> Any:
    return _init_redis_client()


def redis_available() -> bool:
    """True when a real Redis server is connected (not in-memory fallback)."""
    _init_redis_client()
    return not _use_memory


def redis_using_memory_fallback() -> bool:
    _init_redis_client()
    return _use_memory


def close_redis() -> None:
    global _redis, _use_memory
    if _redis is not None:
        try:
            _redis.close()
        except Exception:
            pass
    _redis = None
    _use_memory = False


def cache_get_json(key: str) -> Any | None:
    raw = get_redis().get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


def cache_set_json(key: str, value: Any, *, ex: int | None = None) -> None:
    payload = json.dumps(value, separators=(",", ":"), default=str)
    if ex:
        get_redis().setex(key, ex, payload)
    else:
        get_redis().set(key, payload)


def cache_delete_pattern(pattern: str) -> int:
    r = get_redis()
    removed = 0
    if hasattr(r, "scan_iter"):
        keys = list(r.scan_iter(match=pattern))
        if keys:
            removed = int(r.delete(*keys))
    else:
        keys = r.keys(pattern)
        if keys:
            removed = int(r.delete(*keys))
    return removed


def cache_publish(channel: str, message: str) -> None:
    """Publish to Redis pub/sub; no-op when using in-memory fallback."""
    if redis_using_memory_fallback():
        return
    try:
        get_redis().publish(channel, message)
    except Exception:
        _logger.debug("cache_publish failed channel=%s", channel, exc_info=True)
