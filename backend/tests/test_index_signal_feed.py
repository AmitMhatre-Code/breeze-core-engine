"""Tests for the index signal's live plumbing: depth feed, tick-pipeline short-circuit, publisher,
reader, the price-feed watchdog's depth target, and the mock broker's depth rooms."""
from __future__ import annotations

import queue
from unittest.mock import MagicMock

import pytest

from icici_breeze_backend.app.db.redis_client import cache_delete_pattern
from icici_breeze_backend.app.services import breeze_websocket_manager as bwm
from icici_breeze_backend.app.services import ws_price_feed_watchdog as wd
from icici_breeze_backend.app.services import ws_tick_pipeline as pipeline
from icici_breeze_backend.app.services.index_signal import (
    depth_feed,
    publisher,
    reader,
    shadow_log,
    weights,
)
from icici_breeze_backend.app.services.index_signal.engine import Constituent


def _nse_depth(bids, asks) -> list[dict]:
    """Rows in breeze_connect's NSE layout (`parse_market_depth`, exchange '4')."""
    rows = []
    for k, (b, a) in enumerate(zip(bids, asks), start=1):
        rows.append(
            {
                f"BestBuyRate-{k}": "1000.00",
                f"BestBuyQty-{k}": str(b),
                f"BuyNoOfOrders-{k}": "3",
                f"BuyFlag-{k}": "N",
                f"BestSellRate-{k}": "1000.05",
                f"BestSellQty-{k}": str(a),
                f"SellNoOfOrders-{k}": "2",
                f"SellFlag-{k}": "N",
            }
        )
    return rows


def _bse_depth(bids, asks) -> list[dict]:
    """Rows in breeze_connect's BSE layout (exchange '1'): no order counts or flags."""
    return [
        {f"BestBuyRate-{k}": 1000.0, f"BestBuyQty-{k}": b, f"BestSellRate-{k}": 1000.05, f"BestSellQty-{k}": a}
        for k, (b, a) in enumerate(zip(bids, asks), start=1)
    ]


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    monkeypatch.setattr(publisher, "index_signal_enabled", lambda: True)
    monkeypatch.setattr(shadow_log, "_db_path", lambda: str(tmp_path / "users_test.sqlite3"))
    for mod in (depth_feed, publisher, shadow_log, weights):
        mod.reset_state_for_tests()
    cache_delete_pattern("signal:index:*")
    yield
    for mod in (depth_feed, publisher, shadow_log, weights):
        mod.reset_state_for_tests()
    cache_delete_pattern("signal:index:*")


class TestDepthParsing:
    def test_nse_rows_sum_the_top_five_levels(self):
        depth = _nse_depth([100, 200, 300, 400, 500, 9999], [50, 50, 50, 50, 50, 9999])
        assert depth_feed.depth_sums(depth, 5) == (1500.0, 250.0)

    def test_bse_rows(self):
        assert depth_feed.depth_sums(_bse_depth([10, 20], [30, 40]), 5) == (30.0, 70.0)

    def test_flattened_single_dict(self):
        flat = {"BestBuyQty-1": "5", "BestSellQty-1": "7", "BestBuyQty-6": "1000"}
        assert depth_feed.depth_sums(flat, 5) == (5.0, 7.0)

    def test_fewer_levels_when_configured(self):
        depth = _nse_depth([100, 200, 300], [50, 50, 50])
        assert depth_feed.depth_sums(depth, 2) == (300.0, 100.0)

    def test_empty_book_is_zero_but_malformed_is_none(self):
        assert depth_feed.depth_sums(_bse_depth([0], [0]), 5) == (0.0, 0.0)
        assert depth_feed.depth_sums([{"foo": 1}], 5) is None
        assert depth_feed.depth_sums("garbage", 5) is None

    def test_is_depth_payload(self):
        assert depth_feed.is_depth_payload({"symbol": "4.2!1333", "quotes": "Market Depth"})
        assert depth_feed.is_depth_payload({"symbol": "1.2!500180", "depth": []})
        assert not depth_feed.is_depth_payload({"symbol": "4.1!44684", "last": 10})
        assert not depth_feed.is_depth_payload(None)


@pytest.fixture
def fake_sdk(monkeypatch):
    tokens = {
        ("NSE", "HDFBAN"): "4.2!1333",
        ("NSE", "RELIND"): "4.2!2885",
        ("BSE", "HDFBAN"): "1.2!500180",
    }
    sdk = MagicMock()

    def _token_value(exchange_code, stock_code, get_exchange_quotes, get_market_depth, **_kw):
        token = tokens.get((exchange_code, stock_code))
        # The real SDK *returns* its exception for an unknown scrip.
        return (False, token) if token else ValueError("Stock not found")

    sdk.get_stock_token_value.side_effect = _token_value
    sdk.subscribe_feeds.return_value = {"message": "Stock subscribed successfully"}
    monkeypatch.setattr(bwm, "_ensure_ws", lambda proc, user_id: sdk)
    monkeypatch.setattr(bwm, "_reset_stale_auth_latch", lambda s: None)
    monkeypatch.setattr(pipeline, "register_raw_tick_listener", lambda cb: None)
    return sdk


class TestSubscriptions:
    def test_subscribes_depth_only_and_latches_for_the_day(self, fake_sdk):
        targets = [("NSE", "HDFBAN"), ("BSE", "HDFBAN")]
        assert depth_feed.sync_depth_subscriptions(MagicMock(), "u1", targets) is True
        assert fake_sdk.subscribe_feeds.call_count == 2
        for call in fake_sdk.subscribe_feeds.call_args_list:
            assert call.kwargs["get_market_depth"] is True
            assert call.kwargs["get_exchange_quotes"] is False
            # Passing interval would flip BFO token resolution for the whole process.
            assert "interval" not in call.kwargs
        assert depth_feed.is_synced(targets)

        assert depth_feed.sync_depth_subscriptions(MagicMock(), "u1", targets) is True
        assert fake_sdk.subscribe_feeds.call_count == 2

    def test_names_leaving_the_basket_are_unsubscribed(self, fake_sdk):
        depth_feed.sync_depth_subscriptions(MagicMock(), "u1", [("NSE", "HDFBAN"), ("BSE", "HDFBAN")])
        assert depth_feed.sync_depth_subscriptions(
            MagicMock(), "u1", [("NSE", "HDFBAN"), ("NSE", "RELIND")]
        ) is True
        unsubscribed = [c.kwargs["exchange_code"] for c in fake_sdk.unsubscribe_feeds.call_args_list]
        assert unsubscribed == ["BSE"]
        assert depth_feed.status()["subscribed"] == ["NSE/HDFBAN", "NSE/RELIND"]

    def test_unsubscribe_all_drops_every_room(self, fake_sdk, monkeypatch):
        depth_feed.sync_depth_subscriptions(MagicMock(), "u1", [("NSE", "HDFBAN"), ("BSE", "HDFBAN")])
        monkeypatch.setattr(bwm, "current_ws_user_id", lambda: "u1")
        monkeypatch.setattr("icici_breeze_backend.app.services.processor.processor", MagicMock())
        depth_feed.unsubscribe_all()
        assert fake_sdk.unsubscribe_feeds.call_count == 2
        assert not depth_feed.has_subscriptions()
        assert not depth_feed.is_synced([("NSE", "HDFBAN"), ("BSE", "HDFBAN")])
        # Ticks for a dropped room are no longer routed.
        seen = []
        depth_feed.set_book_listener(lambda *args: seen.append(args))
        depth_feed._on_raw_tick({"symbol": "4.2!1333", "depth": _nse_depth([1], [1])})
        assert seen == []

    def test_unsubscribe_all_without_a_socket_only_clears_bookkeeping(self, fake_sdk, monkeypatch):
        depth_feed.sync_depth_subscriptions(MagicMock(), "u1", [("NSE", "HDFBAN")])
        monkeypatch.setattr(bwm, "current_ws_user_id", lambda: None)
        depth_feed.unsubscribe_all()
        assert fake_sdk.unsubscribe_feeds.call_count == 0
        assert not depth_feed.has_subscriptions()

    def test_a_rejected_subscribe_is_not_latched(self, fake_sdk):
        fake_sdk.subscribe_feeds.return_value = "Exception while subscribing to feeds boom"
        assert depth_feed.sync_depth_subscriptions(MagicMock(), "u1", [("NSE", "HDFBAN")]) is False
        assert not depth_feed.is_synced([("NSE", "HDFBAN")])
        assert "boom" in depth_feed.status()["last_error"]

    def test_an_unresolvable_scrip_fails_the_pass(self, fake_sdk):
        assert depth_feed.sync_depth_subscriptions(MagicMock(), "u1", [("NSE", "NOPE")]) is False
        assert depth_feed.status()["unresolved"] == ["NSE/NOPE"]

    def test_no_session_is_not_success(self, monkeypatch):
        monkeypatch.setattr(bwm, "_ensure_ws", lambda proc, user_id: None)
        assert depth_feed.sync_depth_subscriptions(MagicMock(), "u1", [("NSE", "HDFBAN")]) is False

    def test_depth_ticks_reach_the_book_listener(self, fake_sdk):
        depth_feed.sync_depth_subscriptions(MagicMock(), "u1", [("NSE", "HDFBAN")])
        seen = []
        depth_feed.set_book_listener(lambda *args: seen.append(args))
        depth_feed._on_raw_tick(
            {"symbol": "4.2!1333", "depth": _nse_depth([100, 100], [50, 50]), "quotes": "Market Depth"}
        )
        depth_feed._on_raw_tick({"symbol": "4.1!1333", "last": 700.0})  # the quote room: not ours
        assert len(seen) == 1
        exchange, short_name, bid, ask, _ts = seen[0]
        assert (exchange, short_name, bid, ask) == ("NSE", "HDFBAN", 200.0, 100.0)
        assert depth_feed.last_tick_age_seconds() is not None


def test_ingest_tick_keeps_depth_out_of_the_pnl_buffer_and_chain_queue(monkeypatch):
    heard, staged = [], []
    ingest_queue: queue.Queue = queue.Queue()
    monkeypatch.setattr(pipeline, "_raw_listeners", [heard.append])
    monkeypatch.setattr(pipeline, "_stage_pnl_quote", staged.append)
    monkeypatch.setattr(pipeline, "_ingest_queue", ingest_queue)

    pipeline.ingest_tick({"symbol": "4.2!1333", "depth": _nse_depth([1], [1]), "quotes": "Market Depth"})
    assert [p["symbol"] for p in heard] == ["4.2!1333"]
    assert staged == []
    assert ingest_queue.empty()

    pipeline.ingest_tick({"symbol": "4.1!44684", "last": 10.0})
    assert len(staged) == 1
    assert ingest_queue.qsize() == 1


_BASKETS = {
    "nifty": (Constituent("HDFCBANK", "HDFBAN", 60.0), Constituent("RELIANCE", "RELIND", 40.0)),
    "sensex": (Constituent("HDFCBANK", "HDFBAN", 55.0), Constituent("ICICIBANK", "ICIBAN", 45.0)),
}


@pytest.fixture
def baskets(monkeypatch):
    monkeypatch.setattr(
        weights,
        "tracked_constituents",
        lambda label, top_n, **_kw: (
            _BASKETS[label],
            {"source": "test", "as_of": "2026-09-11", "fetched_at": None, "unresolved": []},
        ),
    )


class TestPublisherAndReader:
    def test_end_to_end_publish_and_read(self, baskets):
        publisher.apply_weights_if_needed(force=True)
        assert publisher.depth_targets() == [
            ("NSE", "HDFBAN"),
            ("NSE", "RELIND"),
            ("BSE", "HDFBAN"),
            ("BSE", "ICIBAN"),
        ]

        t0 = 1_000_000.0
        for short_name in ("HDFBAN", "RELIND"):
            publisher._on_book("NSE", short_name, 1500.0, 500.0, t0)  # OBI +0.5
        first = publisher.publish_once(now=t0 + 1, interval=2.0, session_open=True)
        assert (first["nifty"]["state"], first["nifty"]["reason"]) == ("unavailable", "warming_up")
        # NSE books never drive SENSEX: it has no BSE books yet.
        assert first["sensex"]["reason"] == "low_coverage"

        for short_name in ("HDFBAN", "RELIND"):
            publisher._on_book("NSE", short_name, 1500.0, 500.0, t0 + 8)
        second = publisher.publish_once(now=t0 + 8, interval=2.0, session_open=True)
        assert second["nifty"]["state"] == "bullish"

        read = reader.get_index_signal("nifty", now=t0 + 9)
        assert read["state"] == "bullish"
        assert read["weights"]["source"] == "test"
        assert read["valid_until"] == pytest.approx(t0 + 8 + 10.0)
        assert reader.index_signal_state("NIFTY", now=t0 + 9) == "bullish"

        view = reader.navbar_view(now=t0 + 9)["nifty"]
        assert view["state"] == "bullish"
        assert view["weights_source"] == "test"
        assert "constituents" not in view

        stale = reader.get_index_signal("nifty", now=t0 + 8 + 11)
        assert (stale["state"], stale["reason"]) == ("unavailable", "stale")

    def test_challengers_are_shadow_logged_beside_the_incumbent(self, baskets):
        """Logged as `<index>:flow`, fed by BSE tops and NIFTY futures quotes, never published."""
        publisher.apply_weights_if_needed(force=True)
        t0 = 1_000_000.0
        for k in range(0, 80, 5):
            for short_name in ("HDFBAN", "ICIBAN"):
                publisher._on_top("BSE", short_name, 1000.0, 100.0 + k, 1000.05, 100.0, t0 + k)
            publisher._on_futures_quote(
                {"bPrice": 23500.0, "bQty": 100 + k, "sPrice": 23500.5, "sQty": 100,
                 "last": 23500.5, "ttq": 1000 + k},
                t0 + k,
            )
            publisher.publish_once(now=t0 + k, interval=2.0, session_open=True)
        sensex = shadow_log.load_rows("sensex:flow", 0.0)
        nifty = shadow_log.load_rows("nifty:flow", 0.0)
        assert sensex and nifty
        assert sensex[-1]["state"] == "bullish" and nifty[-1]["state"] == "bullish"
        # Readers still see only the incumbent.
        assert "challenger" not in reader.get_index_signal("sensex", now=t0 + 76)

    def test_validity_scales_with_the_publish_interval(self, baskets):
        publisher.apply_weights_if_needed(force=True)
        out = publisher.publish_once(now=5_000.0, interval=20.0, session_open=True)
        assert out["nifty"]["valid_until"] == pytest.approx(5_000.0 + 60.0)

    def test_reader_before_anything_is_published(self):
        assert reader.get_index_signal("sensex") == {
            "label": "sensex",
            "state": "unavailable",
            "reason": "not_published",
            "signal": None,
        }

    def test_reader_rejects_unknown_labels(self):
        with pytest.raises(ValueError):
            reader.get_index_signal("banknifty")

    def test_ensure_depth_feed_is_a_noop_when_disabled(self, monkeypatch):
        monkeypatch.setattr(publisher, "index_signal_enabled", lambda: False)
        called = []
        monkeypatch.setattr(depth_feed, "sync_depth_subscriptions", lambda *a, **k: called.append(1))
        assert publisher.ensure_depth_feed(MagicMock(), "u1") is True
        assert called == []

    def test_ensure_depth_feed_subscribes_both_exchanges(self, baskets, monkeypatch):
        seen = {}

        def _sync(proc, user_id, targets, *, force=False):
            seen["targets"] = list(targets)
            return True

        monkeypatch.setattr(depth_feed, "sync_depth_subscriptions", _sync)
        assert publisher.ensure_depth_feed(MagicMock(), "u1") is True
        assert ("BSE", "ICIBAN") in seen["targets"]
        assert ("NSE", "RELIND") in seen["targets"]


class TestWatchdogDepthTarget:
    def test_nothing_subscribed_counts_as_healthy(self):
        assert wd._index_depth_ticking() is True

    def test_silent_depth_feed_is_rearmed_but_never_escalates(self, monkeypatch):
        wd.reset_state_for_tests()
        forced, outcomes = [], []
        monkeypatch.setattr(wd, "_watchdog_chain_targets", lambda: [])
        monkeypatch.setattr(wd, "_index_spot_ticking", lambda: True)
        monkeypatch.setattr(wd, "_index_depth_ticking", lambda: False)
        monkeypatch.setattr(wd, "_force_index_depth", lambda: forced.append(1) or False)
        monkeypatch.setattr(wd, "_note_pass_outcome", lambda results, now: outcomes.append(list(results)))

        wd._check_silent_feeds(100.0)
        assert forced == []  # starts the silence clock
        wd._check_silent_feeds(100.0 + wd._SILENCE_SECONDS + 1)
        assert forced == [1]
        # A failed depth re-arm must not push the socket towards a rebuild.
        assert outcomes == [[], []]
        wd.reset_state_for_tests()


class TestMockBroker:
    def test_cash_depth_rooms_resolve_while_index_spot_stays_cold(self):
        from icici_breeze_backend.dev.mock_broker import MockBreezeSdk

        sdk = MockBreezeSdk()
        assert sdk.get_stock_token_value(
            exchange_code="NSE", stock_code="NIFTY", get_exchange_quotes=True, get_market_depth=False
        ) == (False, False)
        quotes, nse_room = sdk.get_stock_token_value(
            exchange_code="NSE", stock_code="HDFBAN", get_exchange_quotes=False, get_market_depth=True
        )
        _q, bse_room = sdk.get_stock_token_value(
            exchange_code="BSE", stock_code="HDFBAN", get_exchange_quotes=False, get_market_depth=True
        )
        assert quotes is False
        assert nse_room.startswith("4.2!")
        assert bse_room.startswith("1.2!")

        sdk.subscribe_feeds(exchange_code="NSE", stock_code="HDFBAN", get_exchange_quotes=False, get_market_depth=True)
        assert nse_room in sdk._ws_tokens
        tick = sdk._mock_depth_tick(nse_room, {})
        assert depth_feed.is_depth_payload(tick)
        bid, ask = depth_feed.depth_sums(tick["depth"], 5)
        assert bid > 0 and ask > 0

        sdk.unsubscribe_feeds(exchange_code="NSE", stock_code="HDFBAN", get_exchange_quotes=False, get_market_depth=True)
        assert nse_room not in sdk._ws_tokens
