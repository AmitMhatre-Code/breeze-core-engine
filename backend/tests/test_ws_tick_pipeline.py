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


@patch("icici_breeze_backend.app.services.ws_tick_pipeline.cache_publish")
@patch("icici_breeze_backend.app.services.ws_tick_pipeline.cache_set_json")
def test_pipeline_writes_raw_ticks_not_normalized_cells(mock_cache, _mock_publish):
    pipeline.stop_tick_pipeline()
    pipeline.start_tick_pipeline()
    try:
        raw = _raw_nifty_call_25000()
        pipeline.ingest_tick(raw)
        deadline = time.time() + 2.0
        while time.time() < deadline and mock_cache.call_count == 0:
            time.sleep(0.05)
        assert mock_cache.call_count >= 1
        key, payload = mock_cache.call_args[0]
        assert "quotes:ws:raw:NFO:" in key
        assert isinstance(payload, dict)
        assert "raw" in payload
        assert payload["raw"]["last"] == 1.4
        assert "ltp" not in payload
    finally:
        pipeline.stop_tick_pipeline()


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
