"""Tests for the real-time portfolio P&L recalculation engine.

Covers: position-row -> leg conversion, vectorized P&L math (matching the
BUY/SELL sign convention already used by `processor.get_positions`), a single
pipelined Redis round trip for quote reads regardless of leg count, target/
stop-loss rule dispatch, and the stale-stream circuit breaker.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from icici_breeze_backend.app.services import portfolio_pnl_engine as engine


@pytest.fixture(autouse=True)
def _reset_engine_state():
    engine._legs_by_user.clear()
    engine._portfolio_rules.clear()
    engine._rule_hit_listeners.clear()
    engine._latest_snapshots.clear()
    yield
    engine._legs_by_user.clear()
    engine._portfolio_rules.clear()
    engine._rule_hit_listeners.clear()
    engine._latest_snapshots.clear()


def _row(
    *,
    stock_code="NIFTY",
    exchange_code="NFO",
    expiry_date="2026-06-30T06:00:00.000Z",
    strike_price="25000",
    right="Call",
    quantity="50",
    average_price="100",
    action="Buy",
) -> dict:
    return {
        "stock_code": stock_code,
        "exchange_code": exchange_code,
        "expiry_date": expiry_date,
        "strike_price": strike_price,
        "right": right,
        "quantity": quantity,
        "average_price": average_price,
        "action": action,
    }


class TestLegFromPositionRow:
    def test_converts_a_well_formed_buy_row(self):
        leg = engine.leg_from_position_row("u1", _row())
        assert leg is not None
        assert leg.stock_code == "NIFTY"
        assert leg.exchange_code == "NFO"
        assert leg.expiry_display == "30-Jun-2026"
        assert leg.strike == 25000.0
        assert leg.right == "call"
        assert leg.quantity == 50
        assert leg.average_price == 100.0
        assert leg.action == "Buy"
        assert leg.scrip_key == "NFO|NIFTY|30-Jun-2026|25000|CE"

    def test_zero_quantity_row_is_skipped(self):
        assert engine.leg_from_position_row("u1", _row(quantity="0")) is None

    def test_missing_strike_row_is_skipped(self):
        assert engine.leg_from_position_row("u1", _row(strike_price="")) is None

    def test_put_right_normalizes_to_put(self):
        leg = engine.leg_from_position_row("u1", _row(right="Put"))
        assert leg.right == "put"
        assert leg.scrip_key.endswith("|PE")


class TestRegisterAndSync:
    def test_sync_from_response_registers_tracked_legs(self):
        response = {"Status": 200, "Success": [_row()]}
        count = engine.sync_positions_from_response("u1", response)
        assert count == 1
        assert engine.tracked_leg_count() == 1

    def test_sync_from_ui_shaped_response_dict_of_positions(self):
        response = {"Status": 200, "Success": {"positions": [_row()]}}
        count = engine.sync_positions_from_response("u1", response)
        assert count == 1

    def test_non_200_response_clears_tracking(self):
        engine.sync_positions_from_response("u1", {"Status": 200, "Success": [_row()]})
        assert engine.tracked_leg_count() == 1
        engine.sync_positions_from_response("u1", {"Status": 400, "Error": "boom"})
        assert engine.tracked_leg_count() == 0

    def test_re_registering_carries_forward_previously_set_rule(self):
        engine.sync_positions_from_response("u1", {"Status": 200, "Success": [_row()]})
        scrip_key = next(iter(engine._legs_by_user["u1"]))
        assert engine.set_leg_rule("u1", scrip_key, target_pnl=500.0) is True

        # A later poll re-registers the same leg from a fresh broker response.
        engine.sync_positions_from_response("u1", {"Status": 200, "Success": [_row()]})
        leg = engine._legs_by_user["u1"][scrip_key]
        assert leg.target_pnl == 500.0

    def test_malformed_row_among_valid_rows_is_skipped_not_raised(self):
        rows = [_row(), {"stock_code": "NIFTY"}, "not-a-dict"]
        count = engine.sync_positions_from_response("u1", {"Status": 200, "Success": rows})
        assert count == 1


class TestVectorizedPnl:
    def test_buy_leg_profit_matches_manual_formula(self, monkeypatch):
        row = _row(action="Buy", quantity="50", average_price="100")
        engine.register_positions("u1", [engine.leg_from_position_row("u1", row)])
        leg = next(iter(engine._legs_by_user["u1"].values()))

        monkeypatch.setattr(engine, "_fetch_quotes", lambda keys: {leg.scrip_key: {"ltp": "110", "timestamp": str(time.time())}})
        monkeypatch.setattr(engine, "is_tick_stream_stale", lambda: False)

        engine.run_pnl_tick()
        snapshot = engine.latest_snapshot("u1")
        assert snapshot["total_pnl"] == pytest.approx((110 - 100) * 50)
        assert snapshot["stream_stale"] is False
        assert snapshot["legs"][0]["has_live_quote"] is True

    def test_sell_leg_uses_flipped_sign(self, monkeypatch):
        row = _row(action="Sell", quantity="50", average_price="200")
        engine.register_positions("u1", [engine.leg_from_position_row("u1", row)])
        leg = next(iter(engine._legs_by_user["u1"].values()))

        monkeypatch.setattr(engine, "_fetch_quotes", lambda keys: {leg.scrip_key: {"ltp": "180", "timestamp": str(time.time())}})
        monkeypatch.setattr(engine, "is_tick_stream_stale", lambda: False)

        engine.run_pnl_tick()
        snapshot = engine.latest_snapshot("u1")
        # SELL: (avg - ltp) * qty, matching processor.get_positions' current_profit formula
        assert snapshot["total_pnl"] == pytest.approx((200 - 180) * 50)

    def test_missing_quote_falls_back_to_average_price_flat_pnl(self, monkeypatch):
        row = _row(action="Buy", quantity="50", average_price="100")
        engine.register_positions("u1", [engine.leg_from_position_row("u1", row)])

        monkeypatch.setattr(engine, "_fetch_quotes", lambda keys: {})
        monkeypatch.setattr(engine, "is_tick_stream_stale", lambda: True)

        engine.run_pnl_tick()
        snapshot = engine.latest_snapshot("u1")
        assert snapshot["total_pnl"] == pytest.approx(0.0)
        assert snapshot["stream_stale"] is True
        assert snapshot["legs"][0]["has_live_quote"] is False

    def test_multi_user_multi_leg_totals_are_independent(self, monkeypatch):
        buy_row = _row(action="Buy", quantity="50", average_price="100", strike_price="25000")
        sell_row = _row(action="Sell", quantity="25", average_price="50", strike_price="24000", right="Put")
        engine.register_positions("u1", [engine.leg_from_position_row("u1", buy_row)])
        engine.register_positions("u2", [engine.leg_from_position_row("u2", sell_row)])

        quotes = {
            engine.contract_index_key("NFO", "NIFTY", "30-Jun-2026", 25000.0, "call"): {"ltp": "120"},
            engine.contract_index_key("NFO", "NIFTY", "30-Jun-2026", 24000.0, "put"): {"ltp": "40"},
        }
        monkeypatch.setattr(engine, "_fetch_quotes", lambda keys: quotes)
        monkeypatch.setattr(engine, "is_tick_stream_stale", lambda: False)

        engine.run_pnl_tick()
        assert engine.latest_snapshot("u1")["total_pnl"] == pytest.approx((120 - 100) * 50)
        assert engine.latest_snapshot("u2")["total_pnl"] == pytest.approx((50 - 40) * 25)


class TestSingleRoundTripRead:
    def test_run_pnl_tick_reads_redis_in_one_pipelined_round_trip(self, monkeypatch):
        legs = []
        for i in range(5):
            row = _row(strike_price=str(25000 + i * 100), average_price="100")
            legs.append(engine.leg_from_position_row("u1", row))
        engine.register_positions("u1", legs)

        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = [{"ltp": "100"} for _ in legs]
        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        monkeypatch.setattr(engine, "get_redis", lambda: mock_redis)
        monkeypatch.setattr(engine, "is_tick_stream_stale", lambda: False)

        engine.run_pnl_tick()

        mock_redis.pipeline.assert_called_once_with(transaction=False)
        assert mock_pipe.hgetall.call_count == len(legs)
        mock_pipe.execute.assert_called_once()

    def test_duplicate_scrip_keys_across_users_are_fetched_once(self, monkeypatch):
        row = _row()
        engine.register_positions("u1", [engine.leg_from_position_row("u1", row)])
        engine.register_positions("u2", [engine.leg_from_position_row("u2", row)])

        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = [{"ltp": "100"}]
        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        monkeypatch.setattr(engine, "get_redis", lambda: mock_redis)
        monkeypatch.setattr(engine, "is_tick_stream_stale", lambda: False)

        engine.run_pnl_tick()

        assert mock_pipe.hgetall.call_count == 1


class TestRuleEvaluation:
    def test_target_hit_dispatches_squareoff_payload(self, monkeypatch):
        row = _row(action="Buy", quantity="50", average_price="100")
        leg = engine.leg_from_position_row("u1", row)
        engine.register_positions("u1", [leg])
        engine.set_leg_rule("u1", leg.scrip_key, target_pnl=400.0)

        monkeypatch.setattr(engine, "_fetch_quotes", lambda keys: {leg.scrip_key: {"ltp": "110"}})
        monkeypatch.setattr(engine, "is_tick_stream_stale", lambda: False)

        hits = []
        engine.register_rule_hit_listener(hits.append)
        try:
            engine.run_pnl_tick()
        finally:
            engine.unregister_rule_hit_listener(hits.append)

        assert len(hits) == 1
        assert hits[0]["reason"] == "target_hit"
        assert hits[0]["action"] == "SquareOff"
        assert hits[0]["stock_code"] == "NIFTY"

    def test_stop_loss_hit_dispatches_squareoff_payload(self, monkeypatch):
        row = _row(action="Sell", quantity="50", average_price="100")
        leg = engine.leg_from_position_row("u1", row)
        engine.register_positions("u1", [leg])
        engine.set_leg_rule("u1", leg.scrip_key, stop_loss_pnl=1000.0)

        # SELL leg, ltp way above average -> big loss: (100-150)*50 = -2500
        monkeypatch.setattr(engine, "_fetch_quotes", lambda keys: {leg.scrip_key: {"ltp": "150"}})
        monkeypatch.setattr(engine, "is_tick_stream_stale", lambda: False)

        hits = []
        engine.register_rule_hit_listener(hits.append)
        try:
            engine.run_pnl_tick()
        finally:
            engine.unregister_rule_hit_listener(hits.append)

        assert len(hits) == 1
        assert hits[0]["reason"] == "stop_loss_hit"

    def test_no_rule_no_dispatch(self, monkeypatch):
        row = _row(action="Buy", quantity="50", average_price="100")
        leg = engine.leg_from_position_row("u1", row)
        engine.register_positions("u1", [leg])

        monkeypatch.setattr(engine, "_fetch_quotes", lambda keys: {leg.scrip_key: {"ltp": "110"}})
        monkeypatch.setattr(engine, "is_tick_stream_stale", lambda: False)

        hits = []
        engine.register_rule_hit_listener(hits.append)
        try:
            engine.run_pnl_tick()
        finally:
            engine.unregister_rule_hit_listener(hits.append)

        assert hits == []

    def test_portfolio_level_target_rule_fires_on_total_pnl(self, monkeypatch):
        row = _row(action="Buy", quantity="50", average_price="100")
        leg = engine.leg_from_position_row("u1", row)
        engine.register_positions("u1", [leg])
        engine.set_portfolio_rule("u1", target_pnl=400.0)

        monkeypatch.setattr(engine, "_fetch_quotes", lambda keys: {leg.scrip_key: {"ltp": "110"}})
        monkeypatch.setattr(engine, "is_tick_stream_stale", lambda: False)

        hits = []
        engine.register_rule_hit_listener(hits.append)
        try:
            engine.run_pnl_tick()
        finally:
            engine.unregister_rule_hit_listener(hits.append)

        assert len(hits) == 1
        assert hits[0]["reason"] == "portfolio_target_hit"


class TestStaleStreamCircuitBreaker:
    def test_is_tick_stream_stale_true_when_no_tick_ever_seen(self, monkeypatch):
        monkeypatch.setattr(engine.ws_tick_pipeline, "last_tick_age_seconds", lambda: None)
        assert engine.is_tick_stream_stale() is True

    def test_is_tick_stream_stale_false_when_recent(self, monkeypatch):
        monkeypatch.setattr(engine.ws_tick_pipeline, "last_tick_age_seconds", lambda: 0.5)
        assert engine.is_tick_stream_stale() is False

    def test_is_tick_stream_stale_true_when_old(self, monkeypatch):
        monkeypatch.setattr(engine.ws_tick_pipeline, "last_tick_age_seconds", lambda: 999.0)
        assert engine.is_tick_stream_stale() is True

    def test_stale_stream_never_triggers_a_rest_call(self, monkeypatch):
        """No REST fallback exists to call — assert the engine only ever reads
        Redis (via _fetch_quotes) even while the stream is flagged stale."""
        row = _row(action="Buy", quantity="50", average_price="100")
        leg = engine.leg_from_position_row("u1", row)
        engine.register_positions("u1", [leg])

        fetch_calls = []
        monkeypatch.setattr(
            engine,
            "_fetch_quotes",
            lambda keys: fetch_calls.append(keys) or {leg.scrip_key: {"ltp": "105"}},
        )
        monkeypatch.setattr(engine, "is_tick_stream_stale", lambda: True)

        engine.run_pnl_tick()

        assert len(fetch_calls) == 1
        snapshot = engine.latest_snapshot("u1")
        assert snapshot["stream_stale"] is True
        assert snapshot["legs"][0]["has_live_quote"] is True
        assert snapshot["total_pnl"] == pytest.approx((105 - 100) * 50)


class TestPnlRecomputeIntervalSettingsBackedAndLive:
    def test_reads_from_persisted_settings_module(self, monkeypatch):
        from icici_breeze_backend.app.services import pnl_engine_settings

        monkeypatch.setattr(
            pnl_engine_settings,
            "load_pnl_engine_settings",
            lambda: {"quote_flush_interval_seconds": 2.0, "pnl_recompute_interval_seconds": 4.5},
        )
        assert engine._pnl_engine_interval_seconds() == 4.5

    def test_falls_back_to_env_default_when_settings_lookup_fails(self, monkeypatch):
        from icici_breeze_backend.app.services import pnl_engine_settings

        monkeypatch.setattr(
            pnl_engine_settings,
            "load_pnl_engine_settings",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(engine.cfg, "PNL_ENGINE_INTERVAL_SECONDS", 3.5, raising=False)
        assert engine._pnl_engine_interval_seconds() == pytest.approx(3.5)

    def test_env_fallback_is_clamped_to_hard_bounds(self, monkeypatch):
        from icici_breeze_backend.app.services import pnl_engine_settings

        monkeypatch.setattr(
            pnl_engine_settings,
            "load_pnl_engine_settings",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(engine.cfg, "PNL_ENGINE_INTERVAL_SECONDS", 0.01, raising=False)
        assert engine._pnl_engine_interval_seconds() == 1.0

    def test_loop_re_reads_interval_every_iteration_not_just_once(self, monkeypatch):
        """Proves live-reload: changing the configured interval mid-flight (no
        restart) changes the sleep duration used on the *next* loop tick."""
        readings = iter([0.01, 0.01, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0])
        monkeypatch.setattr(engine, "_pnl_engine_interval_seconds", lambda: next(readings, 5.0))
        calls = []
        monkeypatch.setattr(engine, "run_pnl_tick", lambda: calls.append(1))

        async def _drive():
            task = asyncio.create_task(engine.run_pnl_loop())
            await asyncio.sleep(0.08)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(_drive())
        assert 1 <= len(calls) <= 3
