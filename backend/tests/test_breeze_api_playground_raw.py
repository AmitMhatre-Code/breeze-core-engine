"""Breeze API Playground: raw ICICI passthrough (no synthesized invoke errors)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from icici_breeze_backend.app.domain.breeze_api_tester_catalog import (
    build_invoke_args_permissive,
    is_breeze_invoke_response_ok,
    parse_playground_literal,
    sdk_args_from_user_params,
)
from icici_breeze_backend.app.services.breeze_websocket_manager import (
    get_playground_event_log,
    playground_subscribe,
    release_holder,
    ws_connect_playground,
    ws_disconnect_playground,
    ws_release_playground,
)
import icici_breeze_backend.app.services.ws_tick_pipeline as ws_tick_pipeline


def _reset_bwm(monkeypatch) -> None:
    import icici_breeze_backend.app.services.breeze_websocket_manager as bwm

    monkeypatch.setattr(bwm, "_holders", {})
    monkeypatch.setattr(bwm, "_sub_holders", {})
    monkeypatch.setattr(bwm, "_sub_meta", {})
    monkeypatch.setattr(bwm, "_sdk", None)
    monkeypatch.setattr(bwm, "_sdk_user_id", None)
    monkeypatch.setattr(bwm, "_connected", False)
    monkeypatch.setattr(bwm, "_playground_events", [])
    monkeypatch.setattr(bwm, "_playground_event_seq", 0)
    monkeypatch.setattr(bwm, "_last_error", None)


def test_build_invoke_args_permissive_omits_empty_params():
    pos, kw = build_invoke_args_permissive(
        "subscribe_feeds",
        {
            "exchange_code": "NFO",
            "stock_code": "",
            "expiry_date": "",
            "strike_price": "",
            "right": "",
        },
    )
    assert pos == ()
    assert kw == {"exchange_code": "NFO"}
    assert "stock_code" not in kw
    assert "strike_price" not in kw


def test_build_invoke_args_permissive_allows_extra_keys():
    pos, kw = build_invoke_args_permissive(
        "get_quotes",
        {"stock_code": "NIFTY", "exchange_code": "NFO", "custom_flag": "yes"},
    )
    assert pos == ()
    assert kw["custom_flag"] == "yes"


def test_sdk_args_from_user_params_coerces_bools():
    args = sdk_args_from_user_params(
        {
            "exchange_code": "NFO",
            "get_exchange_quotes": "true",
            "get_market_depth": "false",
            "holder_id": "h1",
        }
    )
    assert args == {
        "exchange_code": "NFO",
        "get_exchange_quotes": True,
        "get_market_depth": False,
    }
    assert "holder_id" not in args


def test_parse_playground_literal_stock_token_list():
    assert parse_playground_literal("['4.1!44684','4.1!44734']") == [
        "4.1!44684",
        "4.1!44734",
    ]
    assert parse_playground_literal('["4.1!44684","4.1!44734"]') == [
        "4.1!44684",
        "4.1!44734",
    ]


def test_parse_playground_literal_plain_token():
    assert parse_playground_literal("4.1!2885") == "4.1!2885"
    assert parse_playground_literal("NIFTY") == "NIFTY"


def test_sdk_args_from_user_params_stock_token_list():
    args = sdk_args_from_user_params({"stock_token": "['4.1!44684','4.1!44734']"})
    assert args == {"stock_token": ["4.1!44684", "4.1!44734"]}


def test_playground_subscribe_stock_token_list(monkeypatch):
    sdk = MagicMock()
    sdk.subscribe_feeds.return_value = {
        "message": "Stock ['4.1!44684', '4.1!44734'] subscribed successfully"
    }
    proc = MagicMock()
    proc.get_session_breeze.return_value = sdk
    _reset_bwm(monkeypatch)
    import icici_breeze_backend.app.services.breeze_websocket_manager as bwm

    monkeypatch.setattr(bwm, "_sdk", sdk)
    monkeypatch.setattr(bwm, "_connected", True)
    monkeypatch.setattr(bwm, "_sdk_user_id", "u1")
    monkeypatch.setattr(ws_tick_pipeline, "start_tick_pipeline", lambda: None)

    out = playground_subscribe(
        proc,
        "u1",
        {"stock_token": "['4.1!44684','4.1!44734']", "holder_id": "pg1"},
    )

    assert out["ok"] is True
    sdk.subscribe_feeds.assert_called_once_with(
        stock_token=["4.1!44684", "4.1!44734"],
    )


def test_build_invoke_args_permissive_invalid_json_passes_raw_string():
    pos, kw = build_invoke_args_permissive(
        "margin_calculator",
        {"margin_list": "{not json", "exchange_code": "NFO"},
    )
    assert pos == ("{not json",)
    assert kw["exchange_code"] == "NFO"


def test_is_breeze_invoke_response_ok_string_exception():
    err = "Exception while subscribing to feeds Strike Price cannot be empty for Product-Type 'Options'."
    assert is_breeze_invoke_response_ok(err) is False
    assert is_breeze_invoke_response_ok({"message": "Stock NIFTY subscribed successfully"}) is True
    assert is_breeze_invoke_response_ok({"Status": 400, "Error": "bad"}) is False


def test_playground_subscribe_empty_strike_calls_sdk(monkeypatch):
    sdk = MagicMock()
    err = "Exception while subscribing to feeds Strike Price cannot be empty for Product-Type 'Options'."
    sdk.subscribe_feeds.return_value = err
    proc = MagicMock()
    proc.get_session_breeze.return_value = sdk
    _reset_bwm(monkeypatch)
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.ws_tick_pipeline.start_tick_pipeline",
        lambda: None,
    )
    import icici_breeze_backend.app.services.breeze_websocket_manager as bwm

    monkeypatch.setattr(bwm, "_sdk", sdk)
    monkeypatch.setattr(bwm, "_connected", True)
    monkeypatch.setattr(bwm, "_sdk_user_id", "u1")

    out = playground_subscribe(
        proc,
        "u1",
        {
            "exchange_code": "NFO",
            "stock_code": "NIFTY",
            "expiry_date": "30-Jun-2026",
            "strike_price": "",
            "right": "call",
            "holder_id": "h1",
        },
    )

    assert out["ok"] is True
    assert out["response"] == err
    sdk.subscribe_feeds.assert_called_once_with(
        exchange_code="NFO",
        stock_code="NIFTY",
        expiry_date="30-Jun-2026",
        right="call",
    )


def test_playground_subscribe_sdk_exception_in_response(monkeypatch):
    sdk = MagicMock()
    sdk.ws_connect.side_effect = RuntimeError("Could not authenticate credentials. Please check token and keys")

    proc = MagicMock()
    proc.get_session_breeze.return_value = sdk
    _reset_bwm(monkeypatch)

    out = playground_subscribe(
        proc,
        "u1",
        {
            "exchange_code": "NFO",
            "stock_code": "NIFTY",
            "expiry_date": "30-Jun-2026",
            "strike_price": "25000",
            "right": "call",
        },
    )

    assert out["ok"] is False
    assert out["response"] == "Could not authenticate credentials. Please check token and keys"
    assert out["icici_command"]["sdk_method"] == "subscribe_feeds"


def test_ws_connect_playground_includes_icici_command(monkeypatch):
    sdk = MagicMock()
    proc = MagicMock()
    proc.get_session_breeze.return_value = sdk
    _reset_bwm(monkeypatch)
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.ws_tick_pipeline.start_tick_pipeline",
        lambda: None,
    )

    out = ws_connect_playground(proc, "u1")

    assert out["ok"] is True
    assert out["icici_command"]["sdk_method"] == "ws_connect"
    assert "on_ticks" in " ".join(out["icici_command"].get("side_effects") or [])
    assert out["event_id"] is not None
    events = get_playground_event_log()
    assert events and events[0]["step"] == "ws_connect"


def test_playground_subscribe_success_tracks_active_subscriptions(monkeypatch):
    sdk = MagicMock()
    sdk.subscribe_feeds.return_value = {"message": "Stock NIFTY subscribed successfully"}

    proc = MagicMock()
    proc.get_session_breeze.return_value = sdk
    _reset_bwm(monkeypatch)
    monkeypatch.setattr(bwm := __import__(
        "icici_breeze_backend.app.services.breeze_websocket_manager",
        fromlist=["_sdk"],
    ), "_sdk", sdk)
    monkeypatch.setattr(bwm, "_connected", True)
    monkeypatch.setattr(bwm, "_sdk_user_id", "u1")
    monkeypatch.setattr(ws_tick_pipeline, "start_tick_pipeline", lambda: None)

    out = playground_subscribe(
        proc,
        "u1",
        {
            "exchange_code": "NFO",
            "stock_code": "NIFTY",
            "expiry_date": "30-Jun-2026",
            "strike_price": "25000",
            "right": "call",
            "holder_id": "pg1",
        },
    )

    assert out["ok"] is True
    assert out["response"] == {"message": "Stock NIFTY subscribed successfully"}
    assert out["active_subscriptions"] == 1
    sdk.subscribe_feeds.assert_called_once_with(
        exchange_code="NFO",
        stock_code="NIFTY",
        expiry_date="30-Jun-2026",
        strike_price="25000",
        right="call",
    )


def test_release_keeps_socket_ws_disconnect_closes(monkeypatch):
    sdk = MagicMock()
    sdk.subscribe_feeds.return_value = {"message": "ok"}
    proc = MagicMock()
    proc.get_session_breeze.return_value = sdk
    _reset_bwm(monkeypatch)
    import icici_breeze_backend.app.services.breeze_websocket_manager as bwm

    monkeypatch.setattr(ws_tick_pipeline, "start_tick_pipeline", lambda: None)
    monkeypatch.setattr(ws_tick_pipeline, "stop_tick_pipeline", lambda: None)
    monkeypatch.setattr(bwm, "_sdk", sdk)
    monkeypatch.setattr(bwm, "_connected", True)
    monkeypatch.setattr(bwm, "_sdk_user_id", "u1")
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.reference_data.ws_token_index.lookup_token_for_contract",
        lambda *_args, **_kwargs: 44684,
    )

    bwm.subscribe_option(proc, "u1", "NFO", "NIFTY", "30-Jun-2026", 25000.0, "call", holder_id="h1")
    out = ws_release_playground("h1")
    assert out["ok"] is True
    sdk.unsubscribe_feeds.assert_called_once()
    sdk.ws_disconnect.assert_not_called()

    ws_disconnect_playground()
    sdk.ws_disconnect.assert_called_once()


def test_sse_tick_queue_uses_threadsafe_put():
    async def _run() -> list[dict]:
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        received: list[dict] = []

        def _on_tick(payload: dict) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, payload)

        _on_tick({"raw": {"ltp": 1}, "normalized": {"ltp": 1}})
        item = await asyncio.wait_for(queue.get(), timeout=1.0)
        received.append(item)
        return received

    received = asyncio.run(_run())
    assert received == [{"raw": {"ltp": 1}, "normalized": {"ltp": 1}}]


BFO_RAW_TICK = {
    "symbol": "8.1!844663",
    "open": 96,
    "last": 98,
    "bPrice": 96.8,
    "sPrice": 97.25,
    "OI": 37020,
    "totalBuyQt": 30800,
    "totalSellQ": 12360,
    "close": 100.05,
}


def test_raw_bfo_tick_reaches_playground_listener(monkeypatch):
    from icici_breeze_backend.app.services import ws_tick_pipeline as pipeline

    received: list[dict] = []

    def _on_raw(payload: dict) -> None:
        received.append(payload)

    monkeypatch.setattr(pipeline, "_ingest_queue", __import__("queue").Queue())
    monkeypatch.setattr(pipeline, "_process_queue", __import__("queue").Queue())
    monkeypatch.setattr(pipeline, "_started", True)
    pipeline.register_raw_tick_listener(_on_raw)
    try:
        pipeline.ingest_tick(BFO_RAW_TICK)
    finally:
        pipeline.unregister_raw_tick_listener(_on_raw)

    assert len(received) == 1
    assert received[0] == BFO_RAW_TICK


def test_bfo_raw_tick_normalizes_with_token_index(monkeypatch, tmp_path):
    import sqlite3

    import icici_breeze_backend.app.core.config as cfg
    from icici_breeze_backend.app.services.reference_data.ws_token_index import clear_token_lookup_cache
    from icici_breeze_backend.app.services.ws_tick_normalize import normalize_icici_tick

    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
    with sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB, timeout=30) as conn:
        conn.execute(
            """
            CREATE TABLE ws_token_index (
                Token INTEGER PRIMARY KEY,
                SegmentCode TEXT,
                ShortName TEXT,
                ExpiryDate DATE,
                StrikePrice REAL,
                OptionType TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO ws_token_index (Token, SegmentCode, ShortName, ExpiryDate, StrikePrice, OptionType)
            VALUES (844663, 'BFO', 'BSESEN', '2026-07-02', 82000, 'CE')
            """
        )
    clear_token_lookup_cache()

    result = normalize_icici_tick(BFO_RAW_TICK)
    assert result is not None
    parsed, cell = result
    assert parsed.exchange_code == cfg.BFO
    assert parsed.stock_code == "BSESEN"
    assert cell["ltp"] == 98


def test_playground_listener_not_on_normalized_path(monkeypatch):
    from icici_breeze_backend.app.services import ws_tick_pipeline as pipeline

    raw_received: list[dict] = []
    norm_received: list[dict] = []

    def on_raw(payload: dict) -> None:
        raw_received.append(payload)

    def on_norm(payload: dict) -> None:
        norm_received.append(payload)

    pipeline.register_raw_tick_listener(on_raw)
    pipeline.register_tick_listener(on_norm)
    try:
        monkeypatch.setattr(pipeline, "_ingest_queue", None)
        pipeline.ingest_tick(BFO_RAW_TICK)
    finally:
        pipeline.unregister_raw_tick_listener(on_raw)
        pipeline.unregister_tick_listener(on_norm)

    assert len(raw_received) == 1
    assert norm_received == []
