"""Order-book liquidity checks (docs/liquidity-checks-plan.md)."""
from __future__ import annotations

import datetime
import time
from unittest.mock import MagicMock

import pytest

from icici_breeze_backend.app.services import breeze_websocket_manager as bwm
from icici_breeze_backend.app.services import ws_tick_pipeline
from icici_breeze_backend.app.services.liquidity import book as book_store
from icici_breeze_backend.app.services.liquidity import check
from icici_breeze_backend.app.services.liquidity import settings as liq_settings
from icici_breeze_backend.app.services.liquidity import subscriptions as depth_subs
from icici_breeze_backend.app.services.liquidity.estimate import (
    REASON_BEYOND_BOOK,
    REASON_NO_BOOK,
    REASON_STALE_LTP,
    REASON_THIN,
    SOURCE_DEPTH,
    SOURCE_TOP_OF_BOOK,
    Book,
    Level,
    evaluate,
    max_passing_quantity,
    walk,
)
from icici_breeze_backend.app.services.liquidity.settings import LiquiditySettings

TICK = 0.05
NOW = 1_780_000_000.0
DEFAULTS = LiquiditySettings()


def _book(bids, asks, *, ltp=100.0, source=SOURCE_DEPTH, last_trade_at=NOW - 5):
    return Book(
        bids=tuple(Level(p, q) for p, q in bids),
        asks=tuple(Level(p, q) for p, q in asks),
        source=source,
        ltp=ltp,
        last_trade_at=last_trade_at,
    )


DEEP = _book(
    bids=[(99.9, 650), (99.8, 650), (99.5, 1300), (99.0, 1300), (98.0, 2600)],
    asks=[(100.1, 650), (100.2, 650), (100.5, 1300), (101.0, 1300), (102.0, 2600)],
)


def _eval(book, side, qty, *, lot=65, settings=DEFAULTS):
    return evaluate(book, side, qty, lot_size=lot, settings=settings, tick=TICK, now=NOW)


# ---- the estimator ----------------------------------------------------------------------


def test_walk_averages_over_consumed_levels():
    avg, filled = walk(DEEP.bids, 1300)
    assert filled == 1300
    assert avg == pytest.approx((99.9 * 650 + 99.8 * 650) / 1300)


def test_small_order_on_deep_book_passes():
    v = _eval(DEEP, "Sell", 650)
    assert v.ok is True
    assert v.reasons == []
    assert v.est_avg_price == pytest.approx(99.9)
    assert v.max_quantity == 650


def test_order_beyond_visible_book_fails_and_names_it():
    v = _eval(DEEP, "Sell", 7150)
    assert v.ok is False
    assert REASON_BEYOND_BOOK in v.reasons
    assert v.visible_qty == 6500
    assert "Quantity is large enough that the fill could be very different from LTP" in v.as_dict()["message"]
    assert "visible book holds only 6,500" in v.as_dict()["message"]
    # The visible part is all within 10% of LTP, so a bot may still take it.
    assert v.max_quantity == 6500


def test_thin_book_fails_on_both_pct_and_ticks():
    thin = _book(bids=[(99.0, 65), (80.0, 65), (60.0, 650)], asks=[(101.0, 650)])
    v = _eval(thin, "Sell", 650)
    assert v.ok is False
    assert REASON_THIN in v.reasons
    assert v.deviation_pct > 10
    assert "below the LTP of ₹100.00" in v.as_dict()["message"]
    # Two lots (one at 99, one at 80) average 89.5: more than 10% below. One lot passes.
    assert v.max_quantity == 65


def test_tick_floor_keeps_cheap_options_quiet():
    # Rs 0.80 LTP, filling at 0.70: 12.5% away but only 2 ticks, under the 5-tick floor.
    cheap = _book(bids=[(0.70, 6500)], asks=[(0.85, 6500)], ltp=0.80)
    v = _eval(cheap, "Sell", 650)
    assert v.ok is True


def test_fill_better_than_ltp_never_fails():
    # Bids sit above a stale-but-recent LTP: selling there is a gift, not a risk.
    rich = _book(bids=[(130.0, 6500)], asks=[(131.0, 6500)], ltp=100.0)
    v = _eval(rich, "Sell", 650)
    assert REASON_THIN not in v.reasons


def test_buy_side_walks_asks():
    thin_asks = _book(bids=[(99.0, 6500)], asks=[(101.0, 65), (130.0, 6500)])
    v = _eval(thin_asks, "Buy", 650)
    assert REASON_THIN in v.reasons
    assert "above the LTP" in v.as_dict()["message"]


def test_no_bid_is_no_book():
    v = _eval(_book(bids=[], asks=[(101.0, 650)]), "Sell", 65)
    assert v.ok is False
    assert REASON_NO_BOOK in v.reasons
    assert v.max_quantity == 0
    assert "no bid to sell into" in v.as_dict()["message"]


def test_ltp_outside_book_is_stale_and_does_not_shrink():
    # The 2026-06-29 NIFTY capture: last 61.20 under a 61.40 bid.
    b = _book(bids=[(61.40, 6500)], asks=[(61.65, 6500)], ltp=61.20)
    v = _eval(b, "Sell", 650)
    assert v.ok is False
    assert v.reasons == [REASON_STALE_LTP]
    assert v.size_ok is True
    assert v.max_quantity == 650
    assert "outside the current bid/ask" in v.as_dict()["message"]


def test_old_last_trade_is_stale():
    b = _book(bids=[(99.9, 6500)], asks=[(100.1, 6500)], last_trade_at=NOW - 600)
    v = _eval(b, "Buy", 65)
    assert v.reasons == [REASON_STALE_LTP]
    assert "10 minutes old" in v.as_dict()["message"]


def test_no_ltp_measures_against_the_touch():
    b = _book(bids=[(10.0, 65), (5.0, 650)], asks=[(11.0, 650)], ltp=None)
    v = _eval(b, "Sell", 650)
    assert REASON_STALE_LTP in v.reasons
    assert REASON_THIN in v.reasons
    assert "no trade yet today" in v.as_dict()["message"]


def test_top_of_book_is_labelled():
    b = _book(bids=[(99.9, 65)], asks=[(100.1, 65)], source=SOURCE_TOP_OF_BOOK)
    v = _eval(b, "Sell", 650)
    assert REASON_BEYOND_BOOK in v.reasons
    assert "(estimate: top of book only)" in v.as_dict()["message"]


def test_max_passing_quantity_is_lot_aligned_boundary():
    levels = (Level(100.0, 100), Level(85.0, 100), Level(50.0, 1000))
    # LTP 100, 10% => average must stay >= 90. 130 units: (100*100 + 85*30)/130 = 96.5.
    q = max_passing_quantity(levels, "Sell", 1000, 65, 100.0, DEFAULTS, TICK)
    assert q % 65 == 0
    avg, _ = walk(levels, q)
    assert avg >= 90
    avg_next, _ = walk(levels, q + 65)
    assert avg_next < 90


# ---- the book store -----------------------------------------------------------------------


@pytest.fixture
def store(monkeypatch):
    book_store.reset_state_for_tests()
    monkeypatch.setattr(ws_tick_pipeline, "last_ingest_age_seconds", lambda: 1.0)
    yield
    book_store.reset_state_for_tests()


def _depth_payload(token=71472, prefix="4.2"):
    return {
        "symbol": f"{prefix}!{token}",
        "time": "Mon Jun 29 09:59:59 2026",
        "depth": [
            {"BestBuyRate-1": 61.4, "BestBuyQty-1": 65, "BuyNoOfOrders-1": 1, "BuyFlag-1": "",
             "BestSellRate-1": 61.65, "BestSellQty-1": 130, "SellNoOfOrders-1": 2, "SellFlag-1": ""},
            {"BestBuyRate-2": 61.3, "BestBuyQty-2": 650, "BuyNoOfOrders-2": 3, "BuyFlag-2": "",
             "BestSellRate-2": 61.7, "BestSellQty-2": 650, "SellNoOfOrders-2": 3, "SellFlag-2": ""},
        ],
        "quotes": "Market Depth",
        "product_type": "Options",
        "stock_name": "NIFTY 50",
    }


def _quote_payload(token=71472, ltt=None):
    return {
        "symbol": f"4.1!{token}", "last": 61.5, "bPrice": 61.4, "bQty": 65,
        "sPrice": 61.65, "sQty": 130, "quotes": "Quotes Data", "OI": 100,
        "ltt": ltt if ltt is not None else time.strftime("%c"), "product_type": "Options",
    }


def test_store_prefers_depth_and_keeps_quote_ltp(store):
    book_store.on_raw_tick(_quote_payload())
    book_store.on_raw_tick(_depth_payload())
    b = book_store.book_for("NFO", 71472)
    assert b.source == SOURCE_DEPTH
    assert b.bids == (Level(61.4, 65), Level(61.3, 650))
    assert b.asks == (Level(61.65, 130), Level(61.7, 650))
    assert b.ltp == 61.5
    assert b.last_trade_at == pytest.approx(time.time(), abs=5)


def test_store_falls_back_to_top_of_book(store):
    book_store.on_raw_tick(_quote_payload())
    b = book_store.book_for("NFO", 71472)
    assert b.source == SOURCE_TOP_OF_BOOK
    assert b.bids == (Level(61.4, 65),)


def test_store_ignores_cash_ticks_and_bfo_maps(store):
    cash = {"symbol": "4.1!2885", "last": 2900.0, "bPrice": 2899, "bQty": 10, "quotes": "Quotes Data"}
    book_store.on_raw_tick(cash)
    assert book_store.book_for("NFO", 2885) is None
    book_store.on_raw_tick(_depth_payload(token=820390, prefix="8.2"))
    assert book_store.book_for("BFO", 820390).source == SOURCE_DEPTH


def test_store_says_nothing_once_the_feed_is_quiet(store, monkeypatch):
    book_store.on_raw_tick(_quote_payload())
    monkeypatch.setattr(ws_tick_pipeline, "last_ingest_age_seconds", lambda: 300.0)
    assert book_store.book_for("NFO", 71472) is None


def test_store_forgets_yesterday(store):
    book_store.on_raw_tick(_quote_payload())
    tomorrow = time.time() + 86_400
    assert book_store.book_for("NFO", 71472, now=tomorrow) is None


def test_parse_last_trade_reads_both_shapes():
    assert book_store.parse_last_trade(1_780_000_000) == 1_780_000_000.0
    text = time.strftime("%c", time.localtime(1_780_000_000))
    assert book_store.parse_last_trade(text) == pytest.approx(1_780_000_000.0)
    assert book_store.parse_last_trade("garbage") is None


def test_depth_symbol():
    assert book_store.depth_symbol("4.1!123") == "4.2!123"
    assert book_store.depth_symbol("8.1!9") == "8.2!9"
    assert book_store.depth_symbol("4.2!123") is None


# ---- the tick pipeline must not read depth as a quote ----------------------------------------


def test_ingest_stops_depth_after_raw_listeners(monkeypatch):
    seen = []
    staged = []
    monkeypatch.setattr(ws_tick_pipeline, "_raw_listeners", [seen.append])
    monkeypatch.setattr(ws_tick_pipeline, "_stage_pnl_quote", staged.append)
    q = MagicMock()
    monkeypatch.setattr(ws_tick_pipeline, "_ingest_queue", q)
    payload = _depth_payload()
    ws_tick_pipeline.ingest_tick(payload)
    assert seen == [payload]
    assert staged == []
    q.put_nowait.assert_not_called()

    ws_tick_pipeline.ingest_tick(_quote_payload())
    assert len(staged) == 1
    q.put_nowait.assert_called_once()


# ---- settings ---------------------------------------------------------------------------------


@pytest.fixture
def settings_db(tmp_path, monkeypatch):
    path = str(tmp_path / "users.sqlite3")
    monkeypatch.setattr(liq_settings, "_db_path", lambda: path)
    return path


def test_settings_default_and_save(settings_db):
    s = liq_settings.load_liquidity_settings()
    assert (s.max_deviation_pct, s.min_deviation_ticks, s.ltp_stale_seconds) == (10.0, 5, 300)
    s = liq_settings.save_liquidity_settings(max_deviation_pct=7.5)
    assert s.max_deviation_pct == 7.5 and s.min_deviation_ticks == 5
    assert liq_settings.load_liquidity_settings().max_deviation_pct == 7.5


def test_settings_reject_out_of_bounds(settings_db):
    with pytest.raises(ValueError):
        liq_settings.save_liquidity_settings(min_deviation_ticks=-1)


# ---- the check and bot sizing -----------------------------------------------------------------


@pytest.fixture
def market(monkeypatch, store, settings_db):
    monkeypatch.setattr(check, "_market_open", lambda: True)
    monkeypatch.setattr(check, "resolve_token", lambda ex, stk, exp, strike, right: int(strike))
    check.reset_state_for_tests()
    yield


def _put(token, bids, asks, ltp):
    book_store.put_for_tests(
        "NFO", token,
        ltp=ltp, quote_at=time.time(), last_trade_raw=time.time(),
        best_bid=bids[0][0] if bids else None, best_ask=asks[0][0] if asks else None,
        bids=tuple(Level(p, q) for p, q in bids), asks=tuple(Level(p, q) for p, q in asks),
        depth_at=time.time(),
    )


def test_market_closed_is_unknown(monkeypatch, store):
    monkeypatch.setattr(check, "_market_open", lambda: False)
    v = check.check_contract("NFO", "NIFTY", "07-Oct-2026", 25000, "call", "Sell", 650, lot_size=65)
    assert v.ok is None
    assert v.as_dict()["message"] == ""


def test_check_legs_sums_the_same_contract(market):
    _put(25000, bids=[(100.0, 650)], asks=[(100.5, 650)], ltp=100.0)
    out = check.check_legs(
        "NFO", "NIFTY", "07-Oct-2026",
        [
            check.LegQuery("a", 25000, "Call", "Sell", 390),
            check.LegQuery("b", 25000, "Call", "Sell", 390),
        ],
        lot_size=65,
    )
    # 390 alone fits in 650; together they need 780.
    assert out["a"]["Sell"]["ok"] is False
    assert out["a"]["Sell"]["quantity"] == 780
    assert REASON_BEYOND_BOOK in out["b"]["Sell"]["reasons"]


def test_check_legs_without_a_side_judges_both(market):
    _put(25000, bids=[(100.0, 6500)], asks=[(100.5, 65)], ltp=100.0)
    out = check.check_legs(
        "NFO", "NIFTY", "07-Oct-2026", [check.LegQuery("a", 25000, "Call", None, 650)], lot_size=65
    )
    assert out["a"]["Sell"]["ok"] is True
    assert out["a"]["Buy"]["ok"] is False


def test_fit_lots_shrinks_to_the_thinnest_leg(market):
    _put(25000, bids=[(100.0, 6500)], asks=[(100.5, 6500)], ltp=100.0)
    _put(25500, bids=[(50.0, 195)], asks=[(50.5, 6500)], ltp=50.0)
    legs = [
        check.SizedLeg("NFO", "NIFTY", "07-Oct-2026", 25000, "call", "Sell"),
        check.SizedLeg("NFO", "NIFTY", "07-Oct-2026", 25500, "call", "Sell"),
    ]
    fit = check.fit_lots(legs, 10, 65)
    assert fit.judged and fit.lots == 3 and fit.shrunk


def test_fit_lots_refuses_below_min_and_on_stale_ltp(market):
    _put(25000, bids=[(100.0, 65)], asks=[(100.5, 6500)], ltp=100.0)
    legs = [check.SizedLeg("NFO", "NIFTY", "07-Oct-2026", 25000, "call", "Sell")]
    assert check.fit_lots(legs, 5, 65, min_lots=2).refused

    _put(25000, bids=[(100.0, 6500)], asks=[(100.5, 6500)], ltp=90.0)  # LTP under the bid
    fit = check.fit_lots(legs, 5, 65)
    assert fit.refused
    # The Strategy Builder leaves staleness to the ticket's warning.
    assert check.fit_lots(legs, 5, 65, treat_stale_as_fail=False).lots == 5


def test_fit_lots_leaves_size_alone_when_nothing_is_known(market):
    legs = [check.SizedLeg("NFO", "NIFTY", "07-Oct-2026", 26000, "call", "Sell")]
    fit = check.fit_lots(legs, 5, 65)
    assert not fit.judged and fit.lots == 5


def test_bot_note_sends_one_telegram_per_day(market, monkeypatch):
    sent = []
    from icici_breeze_backend.app.services import telegram_alerts

    monkeypatch.setattr(telegram_alerts, "notify_bot_liquidity", lambda u, b, t: sent.append(t))
    fit = check.Fit(requested=5, lots=0, judged=True)
    assert check.note_bot_fit("u1", "iron_fly", "NIFTY fly", fit).startswith("Skipped NIFTY fly")
    check.note_bot_fit("u1", "iron_fly", "NIFTY fly", fit)
    assert len(sent) == 1
    assert check.note_bot_fit("u1", "iron_fly", "x", check.Fit(requested=5, lots=5, judged=True)) is None


# ---- depth subscriptions ----------------------------------------------------------------------


def _mock_ws(monkeypatch):
    sdk = MagicMock()
    sdk.unsubscribe_feeds.return_value = {"message": "ok"}
    monkeypatch.setattr(bwm, "_holders", {})
    monkeypatch.setattr(bwm, "_sub_holders", {})
    monkeypatch.setattr(bwm, "_sub_meta", {})
    monkeypatch.setattr(bwm, "_sdk", sdk)
    monkeypatch.setattr(bwm, "_connected", True)
    monkeypatch.setattr(bwm, "_sdk_user_id", "u1")
    monkeypatch.setattr(bwm, "_last_error", None)
    depth_subs.reset_state_for_tests()
    proc = MagicMock()
    proc.get_session_breeze.return_value = sdk
    return sdk, proc


def test_refused_depth_caps_to_the_near_money_band(monkeypatch):
    sdk, proc = _mock_ws(monkeypatch)
    strikes = [24000.0 + 100 * i for i in range(30)]
    quotes = [f"4.1!{i}" for i in range(30)]
    monkeypatch.setattr(bwm, "list_ws_stock_tokens_for_liquid_contracts", lambda *a: list(quotes))
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.reference_data.ws_token_index.list_ws_tokens_with_strikes",
        lambda *a: list(zip(strikes, quotes)),
    )
    monkeypatch.setattr(depth_subs, "_spot", lambda ex, stk: 25000.0)  # strike index 10

    def subscribe(stock_token=None, **_):
        if any(t.startswith("4.2!") for t in stock_token):
            return "Limit exceeded"
        return {"message": "ok"}

    sdk.subscribe_feeds.side_effect = subscribe
    monkeypatch.setattr(bwm, "_subscribe_batch_size", lambda: 100)
    ok = bwm.sync_holder_chain_subscriptions(proc, "u1", "h1", "NIFTY", "NFO", "07-Oct-2026")
    assert ok is True  # quotes went through; depth's refusal is not a dead chain
    assert depth_subs.is_capped()
    band = depth_subs.depth_tokens_for_chain("NFO", "NIFTY", "07-Oct-2026", quotes)
    assert band == sorted(f"4.2!{i}" for i in range(0, 21))


def test_quote_failure_is_not_read_as_a_cap(monkeypatch):
    sdk, proc = _mock_ws(monkeypatch)
    monkeypatch.setattr(bwm, "list_ws_stock_tokens_for_liquid_contracts", lambda *a: ["4.1!1"])
    sdk.subscribe_feeds.return_value = "Failed to connect to live stream"
    ok = bwm.sync_holder_chain_subscriptions(proc, "u1", "h1", "NIFTY", "NFO", "07-Oct-2026")
    assert ok is False
    assert not depth_subs.is_capped()
    # Depth is not even tried on a socket that refused the quotes.
    assert sdk.subscribe_feeds.call_count == 1


# ---- bots shrink, or skip --------------------------------------------------------------------


def test_bot2_strangle_shrinks_both_legs_together(market, monkeypatch):
    from icici_breeze_backend.app.services.bots import expiry_index_writer as bot2

    monkeypatch.setattr(check, "note_bot_fit", lambda *a: "noted")
    _put(25000, bids=[(100.0, 6500)], asks=[(101.0, 6500)], ltp=100.0)
    _put(24000, bids=[(80.0, 195)], asks=[(81.0, 6500)], ltp=80.0)
    r = bot2.FireResult(index_code="NIFTY", exchange_code="NFO", expiry_display="07-Oct-2026", right="both")
    r.lots, r.quantity, r.premium_total, r.margin_total = 10, 650, 117_000.0, 1_000_000.0
    r.legs = [
        {"right": "call", "strike_price": 25000, "bid": 100.0, "quantity": 650},
        {"right": "put", "strike_price": 24000, "bid": 80.0, "quantity": 650},
    ]
    assert bot2._fit_to_book("u1", r) is True
    assert (r.lots, r.quantity) == (3, 195)
    assert [l["quantity"] for l in r.legs] == [195, 195]
    assert r.legs[1]["premium_total"] == pytest.approx(80.0 * 195)
    assert r.margin_total == pytest.approx(300_000.0)
    assert r.liquidity_note == "noted"


def test_bot2_skips_when_a_leg_has_no_bid(market, monkeypatch):
    from icici_breeze_backend.app.domain.bots import ReasonCode
    from icici_breeze_backend.app.services.bots import expiry_index_writer as bot2

    _put(25000, bids=[], asks=[(101.0, 6500)], ltp=100.0)
    r = bot2.FireResult(index_code="NIFTY", exchange_code="NFO", expiry_display="07-Oct-2026", right="call")
    r.lots, r.quantity = 2, 130
    r.legs = [{"right": "call", "strike_price": 25000, "bid": 100.0, "quantity": 130}]
    assert bot2._fit_to_book("u1", r) is False
    assert r.reason_code == ReasonCode.LIQUIDITY_THIN
    assert "no bid to sell into" in r.error


def test_bot1_fits_each_holding_on_its_own(market):
    from icici_breeze_backend.app.domain.bots import ProposalLeg
    from icici_breeze_backend.app.services.bots import placement

    _put(1500, bids=[(10.0, 1000)], asks=[(10.5, 6500)], ltp=10.0)
    _put(1600, bids=[], asks=[(5.5, 6500)], ltp=5.0)

    def leg(strike, lots):
        return ProposalLeg(
            stock_code="INFY", right="call", expiry_display="28-Oct-2026", strike_price=strike,
            lots=lots, lot_size=400, quantity=lots * 400, premium_per_share=10.0,
            premium_total=lots * 400 * 10.0, span_margin=100_000.0,
        )

    out, notes = placement.fit_legs_to_book("u1", "holdings_writer", [leg(1500, 5), leg(1600, 2)])
    assert [(l.strike_price, l.lots, l.quantity) for l in out] == [(1500, 2, 800)]
    assert out[0].span_margin == pytest.approx(40_000.0)
    assert len(notes) == 2


def test_iron_fly_respects_min_lots_and_reasks_margin(market, monkeypatch):
    from types import SimpleNamespace

    import icici_breeze_backend.app.core.config as cfg
    from icici_breeze_backend.app.domain.bots import ReasonCode
    from icici_breeze_backend.app.services.bots.scalping import iron_fly_bot as fly
    from icici_breeze_backend.app.services.bots.scalping.momentum_bot import Quote

    asked = []
    monkeypatch.setattr(fly, "margin_for_mixed_legs", lambda *a, **k: asked.append(k["legs"]) or 55_000.0)
    for strike in (24800, 25000, 25200):
        _put(strike, bids=[(100.0, 6500)], asks=[(100.5, 6500)], ltp=100.0)
    _put(25200, bids=[(100.0, 6500)], asks=[(100.5, 260)], ltp=100.0)  # the call wing
    q = Quote(100.0, 100.5, 100.0, "live")
    legs = (
        fly.FlyLeg("put", 24800, cfg.BUY, q),
        fly.FlyLeg("call", 25200, cfg.BUY, q),
        fly.FlyLeg("put", 25000, cfg.SELL, q),
        fly.FlyLeg("call", 25000, cfg.SELL, q),
    )
    lots, margin, problem = fly._fit_fly_to_book(
        None, "u1", SimpleNamespace(min_lots=1), "07-Oct-2026", legs, 65, 10, 200_000.0
    )
    assert (lots, margin, problem) == (4, 55_000.0, None)
    assert asked and asked[0][0][2] == 260

    lots, _m, problem = fly._fit_fly_to_book(
        None, "u1", SimpleNamespace(min_lots=5), "07-Oct-2026", legs, 65, 10, 200_000.0
    )
    assert lots == 0 and problem[0] == ReasonCode.LIQUIDITY_THIN


def test_cas_bingo_fits_before_liquidation(market, monkeypatch):
    import icici_breeze_backend.app.core.config as cfg
    from icici_breeze_backend.app.domain.bots import ReasonCode
    from icici_breeze_backend.app.services.bots.cas_bingo import execution
    from icici_breeze_backend.app.services.bots.cas_bingo.plan import Plan, PlanLeg
    from icici_breeze_backend.app.services.bots.scalping.momentum_bot import Quote

    q = Quote(100.0, 100.5, 100.0, "live")
    plan = Plan(
        index_code="NIFTY", expiry_display="07-Oct-2026", structure="bull_put_credit",
        legs=(PlanLeg("put", 24800, cfg.SELL, q), PlanLeg("put", 24600, cfg.BUY, q)),
        lot_size=65, lots=8, reference=25000.0, reference_kind="open", spot=25000.0,
        net_premium_per_unit=20.0, margin_required=80_000.0,
    )
    _put(24800, bids=[(100.0, 390)], asks=[(100.5, 6500)], ltp=100.0)
    _put(24600, bids=[(50.0, 6500)], asks=[(50.5, 6500)], ltp=50.0)
    fitted, note = execution._fit_to_book("u1", plan)
    assert fitted.lots == 6 and fitted.margin_required == pytest.approx(60_000.0)
    assert note

    # Refused: nothing downstream runs -- no margin read, no liquidation.
    _put(24800, bids=[], asks=[(100.5, 6500)], ltp=100.0)
    monkeypatch.setattr(execution, "_available", lambda *a: pytest.fail("margin was read"))
    out = execution._enter(
        None, "u1", None, "run", plan, live=True, charges=None, extra={}
    )
    assert out.opened is False and out.reason_code == ReasonCode.LIQUIDITY_THIN
    assert out.terminal is False


# ---- the Strategy Builder -------------------------------------------------------------------


def test_strategy_builder_excludes_and_caps(market):
    from types import SimpleNamespace

    from icici_breeze_backend.app.services.options_strategy_engine import book_liquidity
    from icici_breeze_backend.app.services.options_strategy_engine.helpers import quote_from_api
    from icici_breeze_backend.app.services.options_strategy_engine.types import (
        StrategyResult,
        TradeLeg,
    )

    ctx = SimpleNamespace(exchange_code="NFO", stock_code="NIFTY", expiry_display="07-Oct-2026", lot_size=65)
    _put(25000, bids=[(100.0, 6500)], asks=[(100.5, 6500)], ltp=100.0)
    _put(25500, bids=[], asks=[(40.5, 6500)], ltp=40.0)  # nothing to sell into
    _put(24500, bids=[(60.0, 260)], asks=[(60.5, 6500)], ltp=60.0)

    rows = {"total_buy_qty": 10, "total_sell_qty": 10, "ltp": 1}
    liquid, dead = quote_from_api(25000, "Call", rows), quote_from_api(25500, "Call", rows)
    book_liquidity.apply_book_check(ctx, [liquid, dead])
    assert liquid.liquid is True
    assert dead.liquid is False and dead.book_sell_ok is False

    r = StrategyResult("short_strangle", "Short Strangle", status="ok", max_loss=None)
    r.legs = [
        TradeLeg("Call", "Sell", 25000, 650, 100.0),
        TradeLeg("Put", "Sell", 24500, 650, 60.0),
    ]
    book_liquidity.cap_results_to_book(ctx, [r])
    assert [l.quantity for l in r.legs] == [260, 260]
    assert book_liquidity.BOOK_CAPPED_BADGE in r.badges


# ---- the route ----------------------------------------------------------------------------------


def _ctx():
    from icici_breeze_backend.app.auth.context import RequestContext

    return RequestContext(user_id="uid1", username="uid1", roles=["trader"], is_authenticated=True, broker_token="tok")


def test_route_checks_and_pins_depth(market, monkeypatch):
    from fastapi import HTTPException

    from icici_breeze_backend.app.api.v1 import route_liquidity as route

    pinned = []
    monkeypatch.setattr(check, "pin_depth", lambda proc, uid, ex, stk, exp, sr: pinned.extend(sr))
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.processor.processor", lambda: MagicMock()
    )
    _put(25000, bids=[(100.0, 65)], asks=[(100.5, 6500)], ltp=100.0)
    body = route.CheckBody(
        exchange_code="NFO", stock_code="NIFTY", expiry_display="07-Oct-2026", lot_size=65,
        legs=[
            route.CheckLeg(ref="l1", strike=25000, right="Call", side=None, quantity=650),
            route.CheckLeg(ref="l2", strike=25000, right="Call", side="Buy", quantity=0),
        ],
    )
    out = route.liquidity_check_legs(body, _ctx())
    assert set(out["legs"]) == {"l1"}  # a zero quantity is not asked about
    assert out["legs"]["l1"]["Sell"]["ok"] is False
    assert out["legs"]["l1"]["Buy"]["ok"] is True
    assert pinned == [(25000.0, "Call")]
    assert out["settings"]["max_deviation_pct"] == 10.0

    with pytest.raises(HTTPException) as e:
        route.liquidity_settings_put(route.SettingsBody(max_deviation_pct=0), _ctx())
    assert e.value.status_code == 422
    assert route.liquidity_settings_put(route.SettingsBody(min_deviation_ticks=3), _ctx())["min_deviation_ticks"] == 3
