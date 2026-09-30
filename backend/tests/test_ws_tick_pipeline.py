"""Tests for WS tick ingest/cache pipeline."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from icici_breeze_backend.app.services import ws_tick_pipeline as pipeline


def _raw_nifty_call_25000() -> dict:
    path = Path(__file__).resolve().parent / "fixtures" / "icici_ticks" / "nifty_call_25000_raw.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _raw_nifty_put_25000() -> dict:
    path = Path(__file__).resolve().parent / "fixtures" / "icici_ticks" / "nifty_put_25000_raw.json"
    return json.loads(path.read_text(encoding="utf-8"))


class _FakePipe:
    def __init__(self, redis: "_FakeRedis") -> None:
        self._redis = redis
        self._ops: list[tuple] = []

    def set(self, key, value, ex=None):
        self._ops.append(("set", key, value, ex))
        return self

    def publish(self, channel, message):
        self._ops.append(("publish", channel, message))
        return self

    def execute(self):
        self._redis.executes += 1
        if self._redis.failures:
            raise self._redis.failures.pop(0)
        for op in self._ops:
            if op[0] == "set":
                self._redis.sets[op[1]] = (json.loads(op[2]), op[3])
            else:
                self._redis.published.append(op[1:])
        return []


class _FakeRedis:
    """Records pipelined raw-tick writes; `failures` are raised by the next executes."""

    def __init__(self, failures: list[BaseException] | None = None) -> None:
        self.failures = list(failures or [])
        self.sets: dict[str, tuple[dict, int]] = {}
        self.published: list[tuple] = []
        self.executes = 0

    def pipeline(self, transaction=True):
        return _FakePipe(self)


@pytest.fixture
def fake_redis(monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr(pipeline, "get_redis", lambda: redis)
    monkeypatch.setattr(pipeline, "_record_snapshot_cells", lambda entries: None)
    monkeypatch.setattr(pipeline, "_CACHE_ERROR_BACKOFF_SECONDS", 0.01)
    pipeline.stop_tick_pipeline()
    yield redis
    pipeline.stop_tick_pipeline()


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_pipeline_writes_raw_ticks_not_normalized_cells(fake_redis):
    pipeline.start_tick_pipeline()
    pipeline.ingest_tick(_raw_nifty_call_25000())
    assert _wait_for(lambda: fake_redis.sets)
    (key, (payload, _ttl)), = fake_redis.sets.items()
    assert "quotes:ws:raw:NFO:" in key
    assert payload["raw"]["last"] == 1.4
    assert "ltp" not in payload


class TestCacheThreadSurvivesRedisErrors:
    """B-05: one Redis error used to end the `ws-tick-cache` thread for the life of the
    process, with `_started` still True so nothing ever restarted it."""

    def test_a_redis_error_does_not_end_the_thread(self, fake_redis):
        fake_redis.failures.append(TimeoutError("Timeout reading from socket"))
        pipeline.start_tick_pipeline()
        pipeline.ingest_tick(_raw_nifty_call_25000())
        assert _wait_for(lambda: pipeline.pipeline_stats()["cache_errors"] >= 1)
        assert pipeline.raw_cache_failing() is True

        pipeline.ingest_tick(_raw_nifty_put_25000())
        assert _wait_for(lambda: fake_redis.sets)
        stats = pipeline.pipeline_stats()
        assert stats["cache_alive"] is True
        assert "TimeoutError" in stats["last_cache_error"]
        assert pipeline.raw_cache_failing() is False

    def test_a_dead_thread_is_restarted_by_start(self, fake_redis, monkeypatch):
        """Both loops catch their own errors; this is the backstop for whatever gets
        past that. `reconnect_ws()` and the watchdog both reach it."""
        pipeline.start_tick_pipeline()
        # End the cache thread the way an uncaught error would, leaving `_started` set.
        dead = MagicMock()
        dead.is_alive.return_value = False
        real = pipeline._cache_thread
        monkeypatch.setattr(pipeline, "_cache_thread", dead)
        assert pipeline.raw_cache_failing() is True

        pipeline.start_tick_pipeline()
        assert pipeline._cache_thread is not dead
        assert pipeline._cache_thread is not real
        assert pipeline._cache_thread.is_alive()
        assert pipeline.raw_cache_failing() is False

    def test_revive_leaves_a_stopped_pipeline_alone(self, fake_redis):
        assert pipeline.revive_tick_pipeline() is False
        assert pipeline.pipeline_stats()["started"] is False


class TestRawTickTimestamps:
    """B-39: `received_at` is when the tick arrived, not when it was written."""

    def test_received_at_is_the_arrival_time(self, fake_redis):
        written = pipeline._write_raw_batch([("k", {"last": 1.0}, time.time() - 30.0)], 120)
        assert len(written) == 1
        payload, ttl = fake_redis.sets["k"]
        assert time.time() - payload["received_at"] == pytest.approx(30.0, abs=1.0)
        # Only the life the tick has left, so a late write cannot outlive the tick.
        assert 89 <= ttl <= 91

    def test_a_tick_past_its_life_is_not_written(self, fake_redis):
        written = pipeline._write_raw_batch([("k", {"last": 1.0}, time.time() - 500.0)], 120)
        assert written == []
        assert fake_redis.sets == {}
        assert fake_redis.executes == 0

    def test_one_round_trip_and_one_notice_per_batch(self, fake_redis):
        now = time.time()
        pipeline._write_raw_batch([(f"k{i}", {"last": i}, now) for i in range(50)], 120)
        assert fake_redis.executes == 1
        assert len(fake_redis.sets) == 50
        assert len(fake_redis.published) == 1

    def test_backlog_is_conflated_not_queued(self, fake_redis, monkeypatch):
        """With the cache thread behind, ticks wait in the coalesce map, where the newest
        one per contract wins, and nothing is dropped."""
        q = pipeline.queue.Queue(maxsize=1)
        q.put_nowait([])  # full: the cache thread is "behind"
        ingest = pipeline.queue.Queue()
        stop = pipeline.threading.Event()
        monkeypatch.setattr(pipeline, "_coalesce_seconds", lambda: 0.02)
        th = pipeline.threading.Thread(target=pipeline._drain_loop, args=(ingest, q, stop), daemon=True)
        th.start()
        try:
            raw = _raw_nifty_call_25000()
            ingest.put((100.0, dict(raw, last=1.0)))
            ingest.put((101.0, dict(raw, last=2.0)))
            assert _wait_for(lambda: len(pipeline._coalesce) == 1 and ingest.empty())
            time.sleep(0.1)
            assert q.qsize() == 1  # still only the blocker
            q.get_nowait()
            assert _wait_for(lambda: q.qsize() == 1)
            (_key, tick, received_at), = q.get_nowait()
            assert tick["last"] == 2.0
            assert received_at == 101.0
        finally:
            stop.set()
            th.join(timeout=1.0)


def test_ingest_drops_when_queue_full(monkeypatch):
    monkeypatch.setattr(pipeline, "_ingest_qsize", lambda: 1)
    pipeline.stop_tick_pipeline()
    pipeline.start_tick_pipeline()
    try:
        raw = _raw_nifty_call_25000()
        pipeline.ingest_tick(raw)
        pipeline.ingest_tick(dict(raw, last=9.9))
        stats = pipeline.pipeline_stats()
        assert stats["started"] is True
    finally:
        pipeline.stop_tick_pipeline()


class TestConflatedTickBuffer:
    def test_overwrites_intermediate_ticks_keeping_only_latest(self):
        buf = pipeline.ConflatedTickBuffer()
        buf.update("K", ltp=1.0, bid=0.9, ask=1.1, ts=1.0)
        buf.update("K", ltp=2.0, bid=1.9, ask=2.1, ts=2.0)
        buf.update("K", ltp=3.0, bid=2.9, ask=3.1, ts=3.0)

        assert len(buf) == 1
        drained = buf.drain()
        assert drained == {"K": {"ltp": 3.0, "bid": 2.9, "ask": 3.1, "timestamp": 3.0}}

    def test_drain_atomically_clears_the_buffer(self):
        buf = pipeline.ConflatedTickBuffer()
        buf.update("A", ltp=1.0, bid=None, ask=None, ts=1.0)
        buf.update("B", ltp=2.0, bid=None, ask=None, ts=2.0)

        first = buf.drain()
        assert set(first) == {"A", "B"}
        assert len(buf) == 0
        assert buf.drain() == {}

    def test_distinct_symbols_are_not_conflated_together(self):
        buf = pipeline.ConflatedTickBuffer()
        buf.update("A", ltp=1.0, bid=None, ask=None, ts=1.0)
        buf.update("B", ltp=2.0, bid=None, ask=None, ts=1.0)
        assert len(buf) == 2


class TestIngestTickStagesPnlBuffer:
    def setup_method(self):
        pipeline._pnl_quote_buffer.drain()

    def teardown_method(self):
        pipeline._pnl_quote_buffer.drain()

    def test_ingest_stages_a_scrip_keyed_entry(self):
        pipeline.ingest_tick(_raw_nifty_call_25000())
        staged = pipeline._pnl_quote_buffer.drain()
        assert len(staged) == 1
        key, fields = next(iter(staged.items()))
        assert key == "NFO|NIFTY|30-Jun-2026|25000|CE"
        assert fields["ltp"] == 1.4
        assert fields["bid"] == 1.45
        assert fields["ask"] == 1.5

    def test_repeated_ticks_for_the_same_contract_conflate_to_one_entry(self):
        raw = _raw_nifty_call_25000()
        pipeline.ingest_tick(raw)
        pipeline.ingest_tick(dict(raw, last=1.5, bPrice=1.48, sPrice=1.55))
        pipeline.ingest_tick(dict(raw, last=1.55, bPrice=1.5, sPrice=1.6))

        staged = pipeline._pnl_quote_buffer.drain()
        assert len(staged) == 1
        fields = next(iter(staged.values()))
        assert fields["ltp"] == 1.55
        assert fields["bid"] == 1.5
        assert fields["ask"] == 1.6

    def test_different_contracts_stage_independently(self):
        pipeline.ingest_tick(_raw_nifty_call_25000())
        pipeline.ingest_tick(_raw_nifty_put_25000())
        staged = pipeline._pnl_quote_buffer.drain()
        assert set(staged) == {
            "NFO|NIFTY|30-Jun-2026|25000|CE",
            "NFO|NIFTY|30-Jun-2026|25000|PE",
        }

    def test_ingest_updates_last_tick_monotonic(self):
        pipeline.ingest_tick(_raw_nifty_call_25000())
        assert pipeline.last_tick_monotonic() is not None
        age = pipeline.last_tick_age_seconds()
        assert age is not None
        assert age >= 0


class TestFlushPnlQuotes:
    def setup_method(self):
        pipeline._pnl_quote_buffer.drain()

    def teardown_method(self):
        pipeline._pnl_quote_buffer.drain()

    def test_empty_buffer_flushes_nothing_and_skips_redis(self, monkeypatch):
        mock_redis = MagicMock()
        monkeypatch.setattr(pipeline, "get_redis", lambda: mock_redis)
        assert pipeline.flush_pnl_quotes() == 0
        mock_redis.pipeline.assert_not_called()

    def test_flush_writes_one_pipelined_batch_not_sequential_calls(self, monkeypatch):
        pipeline._pnl_quote_buffer.update("A", ltp=1.4, bid=1.35, ask=1.45, ts=100.0)
        pipeline._pnl_quote_buffer.update("B", ltp=118.25, bid=118.0, ask=118.5, ts=101.0)

        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = [True, True, True, True]
        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        monkeypatch.setattr(pipeline, "get_redis", lambda: mock_redis)

        flushed = pipeline.flush_pnl_quotes()

        assert flushed == 2
        mock_redis.pipeline.assert_called_once_with(transaction=False)
        assert mock_pipe.hset.call_count == 2
        assert mock_pipe.expire.call_count == 2
        mock_pipe.execute.assert_called_once()
        # buffer was drained even though the write is mocked out
        assert len(pipeline._pnl_quote_buffer) == 0

        written_keys = {call.args[0] for call in mock_pipe.hset.call_args_list}
        assert written_keys == {"quotes:pnl:A", "quotes:pnl:B"}

    def test_flush_error_is_swallowed_and_counted(self, monkeypatch):
        pipeline._pnl_quote_buffer.update("A", ltp=1.0, bid=None, ask=None, ts=100.0)
        mock_pipe = MagicMock()
        mock_pipe.execute.side_effect = RuntimeError("boom")
        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        monkeypatch.setattr(pipeline, "get_redis", lambda: mock_redis)

        errors_before = pipeline._pnl_flush_stats["flush_errors"]
        flushed = pipeline.flush_pnl_quotes()
        assert flushed == 0
        assert pipeline._pnl_flush_stats["flush_errors"] == errors_before + 1


def test_run_pnl_quote_flush_loop_ticks_and_cancels_cleanly(monkeypatch):
    monkeypatch.setattr(pipeline, "_pnl_flush_interval_seconds", lambda: 0.01)
    calls = []
    monkeypatch.setattr(pipeline, "flush_pnl_quotes", lambda: calls.append(1) or 0)

    async def _drive():
        task = asyncio.create_task(pipeline.run_pnl_quote_flush_loop())
        await asyncio.sleep(0.08)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_drive())
    assert len(calls) >= 1


class TestPnlFlushIntervalSettingsBackedAndLive:
    def test_reads_from_persisted_settings_module(self, monkeypatch):
        from icici_breeze_backend.app.services import pnl_engine_settings

        monkeypatch.setattr(
            pnl_engine_settings,
            "load_pnl_engine_settings",
            lambda: {"quote_flush_interval_seconds": 1.7, "pnl_recompute_interval_seconds": 2.0},
        )
        assert pipeline._pnl_flush_interval_seconds() == 1.7

    def test_falls_back_to_env_default_when_settings_lookup_fails(self, monkeypatch):
        from icici_breeze_backend.app.services import pnl_engine_settings

        def _boom():
            raise RuntimeError("settings db unavailable")

        monkeypatch.setattr(pnl_engine_settings, "load_pnl_engine_settings", _boom)
        monkeypatch.setattr(pipeline.cfg, "PNL_QUOTE_FLUSH_INTERVAL_SECONDS", 1.9, raising=False)
        assert pipeline._pnl_flush_interval_seconds() == pytest.approx(1.9)

    def test_env_fallback_is_clamped_to_hard_bounds(self, monkeypatch):
        from icici_breeze_backend.app.services import pnl_engine_settings

        monkeypatch.setattr(
            pnl_engine_settings,
            "load_pnl_engine_settings",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(pipeline.cfg, "PNL_QUOTE_FLUSH_INTERVAL_SECONDS", 999.0, raising=False)
        assert pipeline._pnl_flush_interval_seconds() == 10.0

    def test_loop_re_reads_interval_every_iteration_not_just_once(self, monkeypatch):
        """Proves live-reload: changing the configured interval mid-flight (no
        restart) changes the sleep duration used on the *next* loop tick."""
        readings = iter([0.01, 0.01, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0])
        monkeypatch.setattr(pipeline, "_pnl_flush_interval_seconds", lambda: next(readings, 5.0))
        calls = []
        monkeypatch.setattr(pipeline, "flush_pnl_quotes", lambda: calls.append(1) or 0)

        async def _drive():
            task = asyncio.create_task(pipeline.run_pnl_quote_flush_loop())
            await asyncio.sleep(0.08)  # long enough for the fast 0.01s ticks, nowhere near the 5.0s ticks
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(_drive())
        # Only the first couple (fast) intervals should have fired within the window.
        assert 1 <= len(calls) <= 3


@pytest.fixture(autouse=True)
def _clear_next_open_cache():
    """The flush path memoizes the resolved next-session-open (it would otherwise
    re-read the exchange calendar every couple of seconds), so each test must start
    from a cold cache."""
    from icici_breeze_backend.app.services import ws_tick_pipeline as wtp

    wtp._next_open_cache = None
    yield
    wtp._next_open_cache = None


class TestSessionScopedQuoteRetention:
    """A quote must outlive the tick that wrote it, but never outlive its session.

    The old flat 30s TTL made the cache useless the moment ticks stopped: every leg
    then revalued at its own entry price, so a whole book read as exactly ₹0 after
    the close and through any outage longer than half a minute.
    """

    def test_ttl_runs_to_just_before_the_next_session_open(self, monkeypatch):
        from datetime import datetime, timedelta

        from icici_breeze_backend.app.core.timezone import IST
        from icici_breeze_backend.app.services import ws_tick_pipeline as wtp

        now = datetime(2026, 9, 8, 11, 0, tzinfo=IST)
        next_open = now + timedelta(hours=22, minutes=15)
        monkeypatch.setattr(
            "icici_breeze_backend.app.services.market_calendar.next_session_open",
            lambda _now=None: next_open,
        )
        monkeypatch.setattr(wtp, "datetime", _FrozenDatetime(now))

        ttl = wtp._pnl_quote_ttl_seconds()
        expected = int((next_open - now).total_seconds()) - wtp._QUOTE_RETENTION_MARGIN_SECONDS
        assert ttl == expected
        # Comfortably past the close, so a post-close read still finds a real price.
        assert ttl > 6 * 3600

    def test_ttl_falls_back_to_the_configured_floor_when_the_calendar_fails(self, monkeypatch):
        from icici_breeze_backend.app.services import ws_tick_pipeline as wtp

        def _boom(_now=None):
            raise RuntimeError("calendar unavailable")

        monkeypatch.setattr(
            "icici_breeze_backend.app.services.market_calendar.next_session_open", _boom
        )
        assert wtp._pnl_quote_ttl_seconds() == 30

    def test_ttl_is_capped_so_a_bad_calendar_cannot_pin_the_keyspace(self, monkeypatch):
        from datetime import datetime, timedelta

        from icici_breeze_backend.app.core.timezone import IST
        from icici_breeze_backend.app.services import ws_tick_pipeline as wtp

        now = datetime(2026, 9, 8, 11, 0, tzinfo=IST)
        monkeypatch.setattr(
            "icici_breeze_backend.app.services.market_calendar.next_session_open",
            lambda _now=None: now + timedelta(days=400),
        )
        monkeypatch.setattr(wtp, "datetime", _FrozenDatetime(now))
        assert wtp._pnl_quote_ttl_seconds() == wtp._QUOTE_RETENTION_MAX_SECONDS


class _FrozenDatetime:
    """Stand-in for the module's `datetime` so `datetime.now(IST)` is deterministic."""

    def __init__(self, now):
        self._now = now

    def now(self, tz=None):
        return self._now


class TestQuoteRetentionHousekeeping:
    def test_next_session_open_is_resolved_once_not_per_flush(self, monkeypatch):
        """The flush worker asks for a TTL every couple of seconds, and each miss is a
        SQLite read of the exchange calendar."""
        from datetime import datetime, timedelta

        from icici_breeze_backend.app.core.timezone import IST
        from icici_breeze_backend.app.services import ws_tick_pipeline as wtp

        now = datetime(2026, 9, 8, 11, 0, tzinfo=IST)
        calls = []

        def _resolve(_now=None):
            calls.append(1)
            return now + timedelta(hours=22)

        monkeypatch.setattr(
            "icici_breeze_backend.app.services.market_calendar.next_session_open", _resolve
        )
        monkeypatch.setattr(wtp, "datetime", _FrozenDatetime(now))

        for _ in range(20):
            wtp._pnl_quote_ttl_seconds()
        assert len(calls) == 1

    def test_cache_is_re_resolved_once_the_session_boundary_passes(self, monkeypatch):
        from datetime import datetime, timedelta

        from icici_breeze_backend.app.core.timezone import IST
        from icici_breeze_backend.app.services import ws_tick_pipeline as wtp

        first = datetime(2026, 9, 8, 11, 0, tzinfo=IST)
        calls = []

        def _resolve(now=None):
            calls.append(now)
            return now + timedelta(hours=1)

        monkeypatch.setattr(
            "icici_breeze_backend.app.services.market_calendar.next_session_open", _resolve
        )
        monkeypatch.setattr(wtp, "datetime", _FrozenDatetime(first))
        wtp._pnl_quote_ttl_seconds()
        # Jump past the cached target: the answer is no longer in the future.
        monkeypatch.setattr(wtp, "datetime", _FrozenDatetime(first + timedelta(hours=2)))
        wtp._pnl_quote_ttl_seconds()
        assert len(calls) == 2

    def test_clear_retained_quotes_deletes_only_quote_keys(self):
        from icici_breeze_backend.app.services import ws_tick_pipeline as wtp

        store = MagicMock()
        store.scan_iter.return_value = iter(["quotes:pnl:a", "quotes:pnl:b"])
        store.delete.return_value = 2
        with patch.object(wtp, "get_redis", return_value=store):
            assert wtp.clear_retained_pnl_quotes() == 2
        assert store.scan_iter.call_args.kwargs["match"] == "quotes:pnl:*"
        store.delete.assert_called_once_with("quotes:pnl:a", "quotes:pnl:b")

    def test_clear_retained_quotes_survives_a_redis_failure(self):
        from icici_breeze_backend.app.services import ws_tick_pipeline as wtp

        store = MagicMock()
        store.scan_iter.side_effect = RuntimeError("redis down")
        with patch.object(wtp, "get_redis", return_value=store):
            assert wtp.clear_retained_pnl_quotes() == 0


class TestZeroLtpIsNotAPrice:
    """B-15: a `last` of 0 is an untraded contract. Valued at 0 it tripped group rules."""

    def test_a_traded_price_is_used_as_is(self):
        assert pipeline._pnl_mark(12.5, 12.0, 13.0) == 12.5

    def test_zero_last_with_a_two_sided_book_is_valued_at_the_mid(self):
        assert pipeline._pnl_mark(0.0, 4.0, 5.0) == 4.5

    def test_zero_last_with_a_one_sided_or_empty_book_is_unpriced(self):
        assert pipeline._pnl_mark(0.0, 0.0, 5.0) == ""
        assert pipeline._pnl_mark(0.0, None, None) == ""

    def test_a_tick_without_a_last_field_leaves_the_stored_price_alone(self):
        assert pipeline._pnl_mark(None, 4.0, 5.0) is None

    def test_the_engine_reads_an_unpriced_mark_and_a_stored_zero_as_no_quote(self):
        from icici_breeze_backend.app.services import portfolio_pnl_engine as engine

        assert engine._parse_quote_fields({"ltp": "", "timestamp": "100.0"}) == (None, 100.0)
        assert engine._parse_quote_fields({"ltp": "0.0", "timestamp": "100.0"}) == (None, 100.0)
        assert engine._parse_quote_fields({"ltp": "4.5", "timestamp": "100.0"}) == (4.5, 100.0)
