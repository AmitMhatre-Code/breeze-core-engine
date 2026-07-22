"""End-to-end tests for the local Breeze mock server against the *real* breeze_connect SDK.

Unlike `test_icici_mock_mode.py` (which exercises the duck-typed `MockBreezeSdk`
used by `ICICI_BROKER_MODE=mock`) and `test_ws_subscription_holders.py` (which
mocks `sdk.subscribe_feeds`/`unsubscribe_feeds` as bare `MagicMock`s), these
tests run the actual `breeze_connect.BreezeConnect` client -- real
`generate_session()`, real `socketio.Client()` WS handshake, real
`subscribe_feeds()` token resolution and `parse_data()` tick decoding -- against
`tests/mock_breeze_server.py`, so a real protocol/schema mismatch between our
code's assumptions and the SDK's actual wire behavior would surface here even
if every unit test mocking the SDK directly still passes.
"""
from __future__ import annotations

import time

from breeze_connect import BreezeConnect

from tests import breeze_mock_env
from icici_breeze_backend.app.services.ws_tick_normalize import normalize_icici_tick

NIFTY_25000_CALL = dict(exchange_code="NFO", stock_code="NIFTY", product_type="options",
                         expiry_date="30-Jun-2026", strike_price="25000", right="call")
NIFTY_25000_PUT = dict(exchange_code="NFO", stock_code="NIFTY", product_type="options",
                        expiry_date="30-Jun-2026", strike_price="25000", right="put")


def _connect_and_subscribe(server, *contracts):
    """Random dummy credentials -- the mock's permissive auth must accept them."""
    breeze = BreezeConnect(api_key="totally-random-dummy-api-key-000")
    breeze.generate_session(api_secret="totally-random-dummy-secret-111", session_token="dummy-session-222")
    breeze_mock_env.seed_security_master(breeze)

    ticks: list[dict] = []
    breeze.on_ticks = ticks.append

    breeze.ws_connect()
    for contract in contracts:
        result = breeze.subscribe_feeds(get_exchange_quotes=True, get_market_depth=False, **contract)
        assert "subscribed successfully" in str(result), f"subscribe_feeds failed: {result}"
    return breeze, ticks


def test_generate_session_accepts_any_dummy_credentials():
    with breeze_mock_env.run_mock_breeze_server(mode="LIVE") as server:
        with breeze_mock_env.patch_breeze_urls(server.base_url):
            breeze = BreezeConnect(api_key="random-key-abc")
            breeze.generate_session(api_secret="random-secret-xyz", session_token="random-session-789")

            assert breeze.session_key is not None
            assert breeze.user_id == "MOCKUSER1"

            customer = breeze.get_customer_details(api_session="random-session-789")
            assert customer.get("Status") == 200


def test_live_mode_streams_moving_ticks_matching_real_sdk_schema():
    with breeze_mock_env.run_mock_breeze_server(mode="LIVE") as server:
        with breeze_mock_env.patch_breeze_urls(server.base_url):
            breeze, ticks = _connect_and_subscribe(server, NIFTY_25000_CALL)
            try:
                deadline = time.time() + 8
                while time.time() < deadline and len(ticks) < 10:
                    time.sleep(0.2)

                assert len(ticks) >= 10, f"expected several ticks, got {len(ticks)}: {ticks}"

                first = ticks[0]
                for key in ("symbol", "open", "last", "high", "low", "change", "bPrice", "bQty",
                            "sPrice", "sQty", "ltq", "avgPrice", "OI", "CHNGOI", "ttq",
                            "totalBuyQt", "totalSellQ", "ltt", "close", "exchange"):
                    assert key in first, f"missing real-SDK tick key {key!r} in {first}"

                # Enrichment merged in from get_data_from_stock_token_value (seeded security master).
                assert first["stock_name"] == "NIFTY 50"
                assert first["product_type"] == "Options"
                assert first["expiry_date"] == "30-Jun-2026"
                assert first["strike_price"] == "25000"
                assert first["right"] == "Call"
                assert first["exchange"] == "NSE Futures & Options"

                last_values = {round(t["last"], 4) for t in ticks}
                assert len(last_values) > 1, "LIVE mode should random-walk `last`, not stay frozen"

                # The app's own tick normalizer must accept a mock-produced tick unmodified.
                parsed = normalize_icici_tick(first)
                assert parsed is not None
                parsed_tick, cell = parsed
                assert parsed_tick.stock_code == "NIFTY"
                assert parsed_tick.right == "call"
                assert cell["strike_price"] == 25000
            finally:
                breeze.unsubscribe_feeds(get_exchange_quotes=True, get_market_depth=False, **NIFTY_25000_CALL)
                breeze.ws_disconnect()


def test_off_market_mode_streams_frozen_last_close_snapshot():
    with breeze_mock_env.run_mock_breeze_server(mode="OFF_MARKET") as server:
        with breeze_mock_env.patch_breeze_urls(server.base_url):
            breeze, ticks = _connect_and_subscribe(server, NIFTY_25000_PUT)
            try:
                deadline = time.time() + 5
                while time.time() < deadline and len(ticks) < 3:
                    time.sleep(0.2)

                assert len(ticks) >= 3, f"expected several frozen snapshots, got {len(ticks)}: {ticks}"

                # Frozen snapshot: every broadcast carries identical LTP/high/low/close.
                distinct_last = {t["last"] for t in ticks}
                distinct_high = {t["high"] for t in ticks}
                distinct_low = {t["low"] for t in ticks}
                assert distinct_last == {118.25}
                assert distinct_high == {125.0}
                assert distinct_low == {115.0}

                last_tick = ticks[-1]
                assert last_tick["close"] == last_tick["last"], "off-market close should equal frozen LTP"
                assert last_tick["right"] == "Put"
                assert last_tick["strike_price"] == "25000"
            finally:
                breeze.unsubscribe_feeds(get_exchange_quotes=True, get_market_depth=False, **NIFTY_25000_PUT)
                breeze.ws_disconnect()


def test_market_mode_switch_via_control_endpoint_takes_effect_live():
    """A running server's mode can flip mid-session (e.g. simulating a market-close transition)."""
    with breeze_mock_env.run_mock_breeze_server(mode="LIVE") as server:
        with breeze_mock_env.patch_breeze_urls(server.base_url):
            breeze, ticks = _connect_and_subscribe(server, NIFTY_25000_CALL)
            try:
                deadline = time.time() + 5
                while time.time() < deadline and len(ticks) < 2:
                    time.sleep(0.2)
                assert len(ticks) >= 2

                server.set_mode("OFF_MARKET")
                ticks.clear()

                deadline = time.time() + 5
                while time.time() < deadline and len(ticks) < 3:
                    time.sleep(0.2)
                assert len(ticks) >= 3
                assert {t["last"] for t in ticks} == {1.4}
            finally:
                breeze.unsubscribe_feeds(get_exchange_quotes=True, get_market_depth=False, **NIFTY_25000_CALL)
                breeze.ws_disconnect()


def test_market_mode_query_param_overrides_server_default():
    """`?market_mode=` on the Socket.IO connection URL overrides the server's default mode."""
    with breeze_mock_env.run_mock_breeze_server(mode="LIVE") as server:
        with breeze_mock_env.patch_breeze_urls(server.base_url, ws_market_mode="OFF_MARKET"):
            breeze, ticks = _connect_and_subscribe(server, NIFTY_25000_CALL)
            try:
                deadline = time.time() + 5
                while time.time() < deadline and len(ticks) < 3:
                    time.sleep(0.2)

                assert server.state.mode == "OFF_MARKET"
                assert len(ticks) >= 3
                assert {t["last"] for t in ticks} == {1.4}
            finally:
                breeze.unsubscribe_feeds(get_exchange_quotes=True, get_market_depth=False, **NIFTY_25000_CALL)
                breeze.ws_disconnect()
