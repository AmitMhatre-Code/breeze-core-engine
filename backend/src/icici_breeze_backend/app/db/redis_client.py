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
_memory_hashes: dict[str, dict[str, str]] = {}
_memory_hash_expires: dict[str, float] = {}
_memory_lock = threading.RLock()


class _MemoryPipeline:
    def __init__(self, store: "_MemoryStore", transaction: bool = True) -> None:
        del transaction  # in-memory fallback has no atomicity distinction to make
        self._store = store
        self._ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def set(self, key: str, value: str, ex: int | None = None) -> "_MemoryPipeline":
        self._ops.append(("set", (key, value), {"ex": ex}))
        return self

    def delete(self, *keys: str) -> "_MemoryPipeline":
        self._ops.append(("delete", keys, {}))
        return self

    def hset(self, key: str, mapping: dict[str, Any] | None = None) -> "_MemoryPipeline":
        self._ops.append(("hset", (key,), {"mapping": mapping or {}}))
        return self

    def hgetall(self, key: str) -> "_MemoryPipeline":
        self._ops.append(("hgetall", (key,), {}))
        return self

    def expire(self, key: str, seconds: int) -> "_MemoryPipeline":
        self._ops.append(("expire", (key, seconds), {}))
        return self

    def execute(self) -> list[Any]:
        out: list[Any] = []
        for op, args, kw in self._ops:
            if op == "set":
                out.append(self._store.set(args[0], args[1], ex=kw.get("ex")))
            elif op == "delete":
                out.append(self._store.delete(*args))
            elif op == "hset":
                out.append(self._store.hset(args[0], mapping=kw.get("mapping")))
            elif op == "hgetall":
                out.append(self._store.hgetall(args[0]))
            elif op == "expire":
                out.append(self._store.expire(args[0], args[1]))
        self._ops = []
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

    def pipeline(self, transaction: bool = True) -> _MemoryPipeline:
        return _MemoryPipeline(self, transaction=transaction)

    def hset(self, key: str, mapping: dict[str, Any] | None = None) -> int:
        entries = {str(k): str(v) for k, v in (mapping or {}).items()}
        with _memory_lock:
            bucket = _memory_hashes.setdefault(key, {})
            added = sum(1 for k in entries if k not in bucket)
            bucket.update(entries)
        return added

    def hgetall(self, key: str) -> dict[str, str]:
        with _memory_lock:
            expires = _memory_hash_expires.get(key)
            if expires is not None and time.time() > expires:
                _memory_hashes.pop(key, None)
                _memory_hash_expires.pop(key, None)
                return {}
            return dict(_memory_hashes.get(key, {}))

    def hmget(self, key: str, fields: list[str]) -> list[str | None]:
        bucket = self.hgetall(key)
        return [bucket.get(f) for f in fields]

    def expire(self, key: str, seconds: int) -> bool:
        with _memory_lock:
            if key not in _memory_hashes and key not in _memory:
                return False
            _memory_hash_expires[key] = time.time() + seconds
            return True

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
            _memory_hashes.clear()
            _memory_hash_expires.clear()


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


def require_redis_connected() -> None:
    """Fail fast when production Redis is required but unreachable."""
    if not getattr(cfg, "REDIS_REQUIRE_CONNECTED", False):
        return
    _init_redis_client()
    if redis_using_memory_fallback():
        url = cfg.redis_connection_url()
        raise RuntimeError(
            "Redis is required but unavailable; refusing to start with in-memory cache fallback. "
            f"Ensure breeze-redis is running and REDIS_URL is reachable (configured: {url!r})."
        )


def redis_runtime_stats() -> dict[str, Any]:
    """Best-effort Redis memory/key stats for monitoring."""
    out: dict[str, Any] = {
        "redis_connected": redis_available(),
        "redis_memory_fallback": redis_using_memory_fallback(),
    }
    if not redis_available():
        return out
    try:
        client = get_redis()
        info = client.info("memory")
        out["used_memory"] = int(info.get("used_memory") or 0)
        out["used_memory_human"] = str(info.get("used_memory_human") or "")
        out["maxmemory"] = int(info.get("maxmemory") or 0)
        out["maxmemory_human"] = str(info.get("maxmemory_human") or "")
        out["dbsize"] = int(client.dbsize())
    except Exception as exc:
        out["error"] = str(exc)
    return out


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
