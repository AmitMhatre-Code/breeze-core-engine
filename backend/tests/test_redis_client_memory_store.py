"""Tests for the in-memory Redis fallback's hash + pipeline support.

Exercises `_MemoryStore`/`_MemoryPipeline` directly (bypassing the module-level
`get_redis()` singleton) so behavior is deterministic regardless of whether a
real Redis server happens to be reachable in the test environment.
"""
from __future__ import annotations

import time

from icici_breeze_backend.app.db.redis_client import _MemoryStore


def test_hset_and_hgetall_round_trip():
    store = _MemoryStore()
    store.hset("quotes:pnl:NFO|NIFTY|30-Jun-2026|25000|CE", mapping={"ltp": 1.4, "bid": 1.35, "ask": 1.45})
    fields = store.hgetall("quotes:pnl:NFO|NIFTY|30-Jun-2026|25000|CE")
    assert fields == {"ltp": "1.4", "bid": "1.35", "ask": "1.45"}


def test_hgetall_missing_key_returns_empty_dict():
    store = _MemoryStore()
    assert store.hgetall("no-such-key") == {}


def test_hset_merges_into_existing_hash():
    store = _MemoryStore()
    store.hset("k", mapping={"a": 1})
    store.hset("k", mapping={"b": 2})
    assert store.hgetall("k") == {"a": "1", "b": "2"}


def test_expire_makes_hash_disappear_after_ttl():
    store = _MemoryStore()
    store.hset("k", mapping={"a": 1})
    store.expire("k", 0)
    time.sleep(0.01)
    assert store.hgetall("k") == {}


def test_pipeline_accepts_transaction_kwarg_and_batches_hash_writes():
    store = _MemoryStore()
    pipe = store.pipeline(transaction=False)
    pipe.hset("quotes:pnl:A", mapping={"ltp": 1.0})
    pipe.expire("quotes:pnl:A", 30)
    pipe.hset("quotes:pnl:B", mapping={"ltp": 2.0})
    pipe.expire("quotes:pnl:B", 30)
    results = pipe.execute()

    assert len(results) == 4
    assert store.hgetall("quotes:pnl:A") == {"ltp": "1.0"}
    assert store.hgetall("quotes:pnl:B") == {"ltp": "2.0"}


def test_pipeline_hgetall_reads_are_batched_in_execute_order():
    store = _MemoryStore()
    store.hset("quotes:pnl:A", mapping={"ltp": 1.0})
    store.hset("quotes:pnl:B", mapping={"ltp": 2.0})

    pipe = store.pipeline(transaction=False)
    pipe.hgetall("quotes:pnl:A")
    pipe.hgetall("quotes:pnl:B")
    pipe.hgetall("quotes:pnl:missing")
    results = pipe.execute()

    assert results == [{"ltp": "1.0"}, {"ltp": "2.0"}, {}]


def test_pipeline_is_reusable_after_execute():
    store = _MemoryStore()
    pipe = store.pipeline(transaction=False)
    pipe.hset("k", mapping={"a": 1})
    pipe.execute()
    # queued ops are cleared after execute(); a stale pipeline shouldn't replay them
    assert pipe.execute() == []


def test_hget_reads_a_single_field():
    """`dashboard_day_pnl_live` pipelines `hget` for its per-contract LTPs. Without
    it on the fallback, every read raised, was swallowed by that module's own
    except, and left the Day's P&L tile permanently unpriced wherever Redis is not
    reachable — silently, and regardless of market hours."""
    store = _MemoryStore()
    store.hset("quotes:pnl:x", mapping={"ltp": 2.5, "timestamp": 100.0})
    assert store.hget("quotes:pnl:x", "ltp") == "2.5"
    assert store.hget("quotes:pnl:x", "absent") is None
    assert store.hget("quotes:pnl:missing-key", "ltp") is None


def test_hget_respects_hash_expiry():
    store = _MemoryStore()
    store.hset("quotes:pnl:x", mapping={"ltp": 2.5})
    store.expire("quotes:pnl:x", 0)
    time.sleep(0.01)
    assert store.hget("quotes:pnl:x", "ltp") is None


def test_pipeline_hget_batches_in_execute_order():
    store = _MemoryStore()
    store.hset("a", mapping={"ltp": 1})
    store.hset("b", mapping={"ltp": 2})
    pipe = store.pipeline(transaction=False)
    pipe.hget("a", "ltp")
    pipe.hget("b", "ltp")
    pipe.hget("missing", "ltp")
    assert pipe.execute() == ["1", "2", None]


class _FakeRedis:
    def __init__(self, existing=None):
        self.strings = dict(existing or {})
        self.hashes = {}
        self.sets = {}
        self.ttls = {}

    def ping(self):
        return True

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        self.ttls[key] = ex
        return True

    def exists(self, key):
        return int(key in self.strings or key in self.hashes)

    def hset(self, key, mapping=None):
        self.hashes.setdefault(key, {}).update(mapping or {})

    def expire(self, key, seconds):
        self.ttls[key] = seconds

    def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)


def test_a_process_on_the_fallback_moves_to_redis_when_it_returns(monkeypatch):
    """B-32: the fallback was for the life of the process, so the API and the chain builder
    could split between memory and Redis until a restart."""
    import icici_breeze_backend.app.db.redis_client as rc

    store = _MemoryStore()
    monkeypatch.setattr(rc, "_memory", {})
    monkeypatch.setattr(rc, "_memory_hashes", {})
    monkeypatch.setattr(rc, "_memory_hash_expires", {})
    monkeypatch.setattr(rc, "_memory_sets", {})
    monkeypatch.setattr(rc, "_redis", store)
    monkeypatch.setattr(rc, "_use_memory", True)
    store.set("tick:a", "mine", ex=60)
    store.set("refdata:current_version", "7")
    store.hset("quotes:pnl:A", mapping={"ltp": "1.5"})
    store.sadd("chains:active", "NFO|NIFTY|X")

    real = _FakeRedis(existing={"refdata:current_version": "8"})
    monkeypatch.setattr(rc, "_connect_real", lambda: real)
    rc._probe_and_switch()

    assert rc._redis is real and rc._use_memory is False
    assert real.strings["tick:a"] == "mine" and real.ttls["tick:a"] <= 60
    assert real.strings["refdata:current_version"] == "8", "never over a key Redis holds"
    assert real.hashes["quotes:pnl:A"] == {"ltp": "1.5"}
    assert real.sets["chains:active"] == {"NFO|NIFTY|X"}
    rc._redis, rc._use_memory = store, True  # other fixtures' teardown still reads the store


def test_the_probe_is_spaced_and_a_failed_probe_stays_on_memory(monkeypatch):
    import icici_breeze_backend.app.db.redis_client as rc

    store = _MemoryStore()
    monkeypatch.setattr(rc, "_redis", store)
    monkeypatch.setattr(rc, "_use_memory", True)

    def down():
        raise ConnectionError("refused")

    monkeypatch.setattr(rc, "_connect_real", down)
    rc._probe_and_switch()
    assert rc._redis is store and rc._use_memory is True

    started = []
    monkeypatch.setattr(rc.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: started.append(1)})())
    monkeypatch.setattr(rc, "_probe_state", {"last": rc.time.monotonic(), "running": False})
    rc._maybe_probe_for_redis()
    assert started == [], "inside 30 s of the last probe nothing is started"
    monkeypatch.setattr(rc, "_probe_state", {"last": 0.0, "running": False})
    rc._maybe_probe_for_redis()
    assert started == [1]
