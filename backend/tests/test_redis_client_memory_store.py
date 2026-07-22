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
