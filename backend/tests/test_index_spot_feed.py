"""Tests for the live NIFTY/SENSEX index-tick feed backing the navbar ticker
and quote_source_router's chain-completeness spot cache."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.redis_client import cache_delete_pattern, cache_get_json
from icici_breeze_backend.app.services import index_spot_feed as isf
from icici_breeze_backend.app.services.reference_data.keys import index_spot_key


def _clear_index_spot_cache() -> None:
    cache_delete_pattern(index_spot_key("nifty"))
    cache_delete_pattern(index_spot_key("sensex"))


@pytest.fixture(autouse=True)
def _reset():
    isf.reset_state_for_tests()
    _clear_index_spot_cache()
    yield
    _clear_index_spot_cache()
    isf.reset_state_for_tests()


def test_on_raw_tick_ignores_unknown_symbol():
    isf._on_raw_tick({"symbol": "4.1!999999", "last": "24800.5"})
    assert cache_get_json(index_spot_key("nifty")) is None


def test_on_raw_tick_updates_cache_and_seeds_chain_spot(monkeypatch):
    isf._symbol_to_label["4.1!4963"] = "nifty"
    isf._previous_close["nifty"] = 24700.0

    seen = {}

    def _fake_remember(exchange_code, stock_code, spot):
        seen["args"] = (exchange_code, stock_code, spot)

    monkeypatch.setattr(
        "icici_breeze_backend.app.services.quote_source_router.remember_chain_spot",
        _fake_remember,
    )

    isf._on_raw_tick({"symbol": "4.1!4963", "last": "24800.5"})

    cached = cache_get_json(index_spot_key("nifty"))
    assert cached is not None
    assert cached["ltp"] == 24800.5
    assert cached["previous_close"] == 24700.0
    assert cached["change"] == pytest.approx(100.5)
    assert cached["change_pct"] == pytest.approx(100.5 / 24700.0 * 100.0)

    assert seen["args"] == (cfg.NFO, "NIFTY", 24800.5)


def test_on_raw_tick_handles_missing_previous_close():
    isf._symbol_to_label["8.1!5678"] = "sensex"
    isf._on_raw_tick({"symbol": "8.1!5678", "last": "83000"})
    cached = cache_get_json(index_spot_key("sensex"))
    assert cached["ltp"] == 83000.0
    assert cached["previous_close"] is None
    assert cached["change"] is None
    assert cached["change_pct"] is None


def test_sync_index_spot_subscriptions_idempotent_same_day(monkeypatch):
    fake_sdk = MagicMock()
    fake_sdk.get_stock_token_value.side_effect = [
        ("4.1!4963", False),
        ("1.1!1", False),
    ]
    fake_sdk.get_quotes.return_value = {
        "Status": 200,
        "Success": [{"previous_close": "24700.0"}],
    }
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.breeze_websocket_manager._ensure_ws",
        lambda proc, user_id: fake_sdk,
    )

    proc = MagicMock()
    isf.sync_index_spot_subscriptions(proc, "u1")
    assert fake_sdk.subscribe_feeds.call_count == 2
    assert isf._symbol_to_label["4.1!4963"] == "nifty"
    assert isf._previous_close["nifty"] == 24700.0

    # Second call same day: no-op, no additional subscribe_feeds calls.
    isf.sync_index_spot_subscriptions(proc, "u1")
    assert fake_sdk.subscribe_feeds.call_count == 2


def test_sync_index_spot_subscriptions_noop_without_session(monkeypatch):
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.breeze_websocket_manager._ensure_ws",
        lambda proc, user_id: None,
    )
    proc = MagicMock()
    isf.sync_index_spot_subscriptions(proc, "u1")
    assert isf._symbol_to_label == {}


def test_get_index_quotes_status_reads_cache():
    from icici_breeze_backend.app.db.redis_client import cache_set_json

    cache_set_json(index_spot_key("nifty"), {"ltp": 24800.5}, ex=15)
    status = isf.get_index_quotes_status()
    assert status["quotes"]["nifty"] == {"ltp": 24800.5}
    assert status["quotes"]["sensex"] is None
