"""CAS Bingo (docs/bots-cas-bingo-plan.md).

Each test pins a decision the user made while the requirements were validated, so a later
"fix" that quietly reverses one fails here first:

* live state flips, not bars; a flip out of `unavailable` is not a flip;
* debit = strong flip then sustain; credit = move from the OPEN, then ANY flip against it;
* credit strikes from the open (an ITM short is allowed and noted), debit/strangle from spot;
* buys before sells, and a failed sell unwinds the buy;
* liquidation: most-captured first, capped at the minimum-% price, just enough lots,
  and no margin_calculator call;
* Autonomous spreads need readiness == ready.
"""
from __future__ import annotations

import datetime

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.db.bots_migrate import BOT_CAS_BINGO, ensure_bots_tables
from icici_breeze_backend.app.domain.bots import CasBingoConfig, ReasonCode
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots import charges as charges_mod
from icici_breeze_backend.app.services.bots.cas_bingo import execution, liquidation, plan, runtime, triggers
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.bots.scalping import live
from icici_breeze_backend.app.services.bots.scalping.momentum_bot import Quote

DAY = datetime.date(2026, 9, 15)
WINDOWS = [("14:30", "15:15"), ("15:15", "15:29")]
USER = "u1"
LOT = 75
EXPIRY = "15-Sep-2026"


def ts(hhmm: str) -> float:
    """HH:MM or HH:MM:SS on the test day, IST."""
    parts = [int(x) for x in hhmm.split(":")] + [0]
    return datetime.datetime(DAY.year, DAY.month, DAY.day, parts[0], parts[1], parts[2], tzinfo=IST).timestamp()


def row(kind, at, state, signal=None, spot=None):
    return {"kind": kind, "ts": ts(at), "state": state, "signal": signal, "spot": spot}


# --------------------------------------------------------------------------------------
# Triggers
# --------------------------------------------------------------------------------------


class TestDebitTrigger:
    def _eval(self, rows, now="15:10", live_state="bullish", live_signal=0.4, strong=0.5, sustain=180):
        return triggers.evaluate_debit(
            rows, live_state=live_state, live_signal=live_signal, now_ts=ts(now),
            windows=WINDOWS, strong_threshold=strong, sustain_seconds=sustain,
        )

    def test_strong_flip_held_for_the_sustain_period_buys_a_call_spread(self):
        rows = [
            row("sample", "15:00", "neutral", 0.1),
            row("transition", "15:05", "bullish", 0.32, 24000),
            row("sample", "15:06", "bullish", 0.55),
        ]
        verdict = self._eval(rows, now="15:08:30")
        assert verdict.trigger is not None and verdict.trigger.right == "call"

    def test_bearish_goes_on_the_put_side(self):
        rows = [row("sample", "15:00", "neutral"), row("transition", "15:05", "bearish", -0.6)]
        verdict = self._eval(rows, now="15:09", live_state="bearish", live_signal=-0.6)
        assert verdict.trigger.right == "put"

    def test_a_flip_that_never_got_strong_does_not_fire(self):
        rows = [row("sample", "15:00", "neutral"), row("transition", "15:05", "bullish", 0.32)]
        verdict = self._eval(rows, now="15:12", live_signal=0.35)
        assert verdict.trigger is None and "strong" in verdict.reason

    def test_not_yet_sustained(self):
        rows = [row("sample", "15:00", "neutral"), row("transition", "15:05", "bullish", 0.7)]
        assert self._eval(rows, now="15:06").trigger is None

    def test_waking_up_from_unavailable_is_not_a_flip(self):
        # 15:15-15:20 the constituent books are empty; the first reading after is the signal
        # waking up, which the readiness verdict never scored.
        rows = [row("sample", "15:16", "unavailable"), row("transition", "15:21", "bullish", 0.8)]
        verdict = self._eval(rows, now="15:26")
        assert verdict.trigger is None and "woke up" in verdict.reason

    def test_a_flip_before_the_windows_does_not_count(self):
        rows = [row("sample", "14:00", "neutral"), row("transition", "14:10", "bullish", 0.8)]
        assert self._eval(rows, now="14:40").trigger is None

    def test_only_the_latest_episode_counts(self):
        rows = [
            row("sample", "14:40", "neutral"),
            row("transition", "14:45", "bullish", 0.8),
            row("transition", "14:50", "neutral", 0.1),
        ]
        assert self._eval(rows, now="15:00", live_state="neutral").trigger is None


class TestCreditTrigger:
    def _eval(self, rows, live_state, day_open=24000.0, move=0.5):
        return triggers.evaluate_credit(
            rows, live_state=live_state, now_ts=ts("15:20"), windows=WINDOWS,
            day_open=day_open, move_trigger_pct=move,
        )

    def test_rise_then_bearish_flip_sells_a_ce(self):
        rows = [row("sample", "15:00", "neutral"), row("transition", "15:02", "bearish", -0.31, 24200)]
        verdict = self._eval(rows, "bearish")
        assert verdict.trigger.right == "call"

    def test_drop_then_bullish_flip_sells_a_pe(self):
        rows = [row("sample", "15:00", "neutral"), row("transition", "15:02", "bullish", 0.31, 23800)]
        assert self._eval(rows, "bullish").trigger.right == "put"

    def test_a_flip_with_the_move_is_not_a_reversal(self):
        rows = [row("sample", "15:00", "neutral"), row("transition", "15:02", "bullish", 0.31, 24200)]
        assert self._eval(rows, "bullish").trigger is None

    def test_move_below_the_trigger(self):
        rows = [row("sample", "15:00", "neutral"), row("transition", "15:02", "bearish", -0.31, 24050)]
        assert self._eval(rows, "bearish").trigger is None

    def test_no_day_open_never_falls_back(self):
        rows = [row("sample", "15:00", "neutral"), row("transition", "15:02", "bearish", -0.31, 24200)]
        assert self._eval(rows, "bearish", day_open=None).trigger is None


def test_strangle_fires_from_its_time_until_its_window_closes():
    assert triggers.strangle_due(ts("15:14"), "15:15", WINDOWS).trigger is None
    assert triggers.strangle_due(ts("15:15"), "15:15", WINDOWS).trigger is not None
    assert triggers.strangle_due(ts("15:29"), "15:15", WINDOWS).trigger is None


# --------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------


def test_outer_leg_must_sit_beyond_inner():
    with pytest.raises(ValueError):
        CasBingoConfig(credit={"inner_pct": 1.0, "outer_pct": 0.5})


def test_strangle_time_outside_the_windows_is_refused():
    with pytest.raises(ValueError):
        CasBingoConfig(strangle={"entry_time_ist": "13:00"})


def test_windows_may_not_overlap():
    with pytest.raises(ValueError):
        CasBingoConfig(pre_cas_window={"start": "14:30", "end": "15:20"})


# --------------------------------------------------------------------------------------
# Plans
# --------------------------------------------------------------------------------------


def _price(strike, spot=24000.0, right="call"):
    intrinsic = max(0.0, spot - strike) if right == "call" else max(0.0, strike - spot)
    return round(intrinsic + max(2.0, 60.0 - abs(strike - spot) * 0.2), 2)


def _chain(right, spot=24000.0):
    return [
        {
            "strike_price": float(s),
            "spot_price": spot,
            "best_bid_price": _price(s, spot, right) - 0.5,
            "best_offer_price": _price(s, spot, right) + 0.5,
        }
        for s in range(23500, 24550, 50)
    ]


class FakeProc:
    def __init__(self, margin_per_lot=30_000.0):
        self.margin_calls = 0
        self.margin_per_lot = margin_per_lot

    def fetch_lot_size(self, stock_code, expiry_display, exchange_code=None):
        return LOT

    def get_session_breeze(self, user_id):
        return self

    def margin_calculator(self, payload, exchange_code=""):
        self.margin_calls += 1
        lots = int(payload[0]["quantity"]) // LOT
        return {"Status": 200, "Success": {"span_margin_required": self.margin_per_lot * lots}}


def _plan(structure, config=None, day_open=24000.0, spot=24000.0, proc=None):
    return plan.build_plan(
        proc or FakeProc(), USER, config or CasBingoConfig(),
        index_code="NIFTY", expiry_display=EXPIRY, structure=structure,
        day_open=day_open, spot=spot, calls=_chain("call", spot), puts=_chain("put", spot),
    )


def test_credit_strikes_come_from_the_open_and_the_hedge_is_bought_first():
    p, problem = _plan("bear_call_credit", day_open=24000.0, spot=24300.0)
    assert problem is None
    buy, sell = p.entry_sequence()
    assert buy.action == cfg.BUY and sell.action == cfg.SELL
    assert sell.strike == 24150.0  # open x 1.005 = 24120, snapped away -> 24150
    assert buy.strike == 24250.0  # open x 1.010 = 24240 -> 24250
    # Sold below spot: in the money, allowed, and said so.
    assert p.notes and "in the money" in p.notes[0]


def test_credit_sizing_fits_the_margin_budget_in_two_calls():
    proc = FakeProc(margin_per_lot=30_000.0)
    p, _ = _plan("bull_put_credit", config=CasBingoConfig(credit={"margin_lakhs": 1.0}), proc=proc)
    assert p.lots == 3 and p.margin_required == 90_000.0
    assert proc.margin_calls <= 2


def test_credit_spread_without_the_open_is_refused():
    p, problem = _plan("bull_put_credit", day_open=None)
    assert p is None and problem[0] == ReasonCode.DAY_OPEN_UNAVAILABLE


def test_debit_strikes_come_from_spot_and_size_by_premium_budget():
    p, problem = _plan("bull_call_debit", config=CasBingoConfig(debit={"premium_budget_inr": 10_000}))
    assert problem is None
    buy, sell = p.entry_sequence()
    assert buy.action == cfg.BUY and buy.strike == 24000.0 and sell.strike == 24150.0
    debit = -p.net_premium_per_unit
    assert p.lots == int(10_000 // (debit * LOT)) and p.margin_required <= 10_000


def test_strangle_buys_both_sides_ce_first():
    p, _ = _plan("long_strangle")
    legs = p.entry_sequence()
    assert [l.right for l in legs] == ["call", "put"] and all(l.action == cfg.BUY for l in legs)


def test_structure_for_maps_the_trigger_side():
    assert plan.structure_for("credit_spread", "call") == "bear_call_credit"
    assert plan.structure_for("debit_spread", "put") == "bear_put_debit"


# --------------------------------------------------------------------------------------
# Liquidation
# --------------------------------------------------------------------------------------


def _book():
    return [
        liquidation.BookLeg("call", 24500.0, 150, cfg.SELL, 100.0),  # 90% captured at ask 10
        liquidation.BookLeg("put", 23500.0, 150, cfg.SELL, 100.0),  # 50% captured at ask 50
        liquidation.BookLeg("put", 23000.0, 75, cfg.BUY, 20.0),
    ]


def _span(legs):
    # 10,000 per short lot; longs free. Enough to reason about "just enough lots".
    return sum(10_000.0 * (int(l["quantity"]) // LOT) for l in legs if l["action"] == cfg.SELL)


def _shorts(book, asks):
    return [
        liquidation.ShortPosition(l.right, l.strike, l.quantity, l.average_price, asks[(l.right, l.strike)])
        for l in book if l.action == cfg.SELL
    ]


def test_buys_back_the_most_captured_short_first_and_only_enough_lots():
    book = _book()
    shorts = _shorts(book, {("call", 24500.0): 10.0, ("put", 23500.0): 15.0})
    p = liquidation.plan_liquidation(
        book, shorts, shortfall=5_000, min_captured_pct=80, safety_buffer_pct=10,
        lot_size=LOT, spot=24000.0, span_fn=_span,
    )
    assert p.covered
    assert len(p.buybacks) == 1
    assert p.buybacks[0].right == "call" and p.buybacks[0].quantity == LOT  # one lot, not both


def test_a_short_that_has_not_captured_enough_is_never_bought_back():
    book = _book()
    shorts = _shorts(book, {("call", 24500.0): 30.0, ("put", 23500.0): 50.0})
    p = liquidation.plan_liquidation(
        book, shorts, shortfall=5_000, min_captured_pct=80, safety_buffer_pct=10,
        lot_size=LOT, spot=24000.0, span_fn=_span,
    )
    assert not p.covered and not p.buybacks and len(p.ineligible) == 2


def test_cap_price_is_rounded_down_to_the_tick():
    s = liquidation.ShortPosition("call", 24500.0, 75, 101.3, 5.0)
    assert s.cap_price(80) == pytest.approx(20.25)


def test_insufficient_when_every_eligible_short_is_not_enough():
    book = _book()
    shorts = _shorts(book, {("call", 24500.0): 10.0, ("put", 23500.0): 50.0})
    p = liquidation.plan_liquidation(
        book, shorts, shortfall=1_000_000, min_captured_pct=80, safety_buffer_pct=0,
        lot_size=LOT, spot=24000.0, span_fn=_span,
    )
    assert not p.covered and p.buybacks and p.note


def test_span_legs_use_the_brokers_spelling():
    # The SPAN engine reads "Call"/"Put"; a lower-case "call" would be priced as a put.
    leg = liquidation.BookLeg("call", 24500.0, 75, cfg.SELL).as_span_leg()
    assert leg["right"] == cfg.CALL and leg["action"] == cfg.SELL


# --------------------------------------------------------------------------------------
# Exits and settlement
# --------------------------------------------------------------------------------------


class _Cycle:
    def __init__(self, family, net, legs, paper=True):
        self.id = "c1"
        self.paper = paper
        self.bot_type = BOT_CAS_BINGO
        self.legs = legs
        self.detail = {"family": family, "net_entry_per_unit": net, "entry_charges": 0.0}


def _legs(*spec):
    return [
        {"right": r, "strike_price": k, "action": a, "quantity": LOT, "stock_code": "NIFTY",
         "exchange_code": cfg.NFO, "expiry_display": EXPIRY}
        for r, k, a in spec
    ]


def test_credit_target_is_a_share_of_the_credit_captured():
    config = CasBingoConfig(credit={"target_pct": 80, "stop_loss_pct": 100})
    cycle = _Cycle("credit", 20.0, _legs(("call", 24300.0, cfg.BUY), ("call", 24150.0, cfg.SELL)))
    assert execution.evaluate_exit(config, cycle, -3.0)[0] == ReasonCode.TARGET_REACHED  # kept 17 of 20
    assert execution.evaluate_exit(config, cycle, -10.0) is None
    assert execution.evaluate_exit(config, cycle, -40.0)[0] == ReasonCode.STOP_LOSS  # lost 20


def test_debit_stop_is_a_share_of_the_debit():
    config = CasBingoConfig(debit={"target_pct": 100, "stop_loss_pct": 50})
    cycle = _Cycle("debit", -40.0, _legs(("call", 24000.0, cfg.BUY), ("call", 24150.0, cfg.SELL)))
    assert execution.evaluate_exit(config, cycle, 19.0)[0] == ReasonCode.STOP_LOSS
    assert execution.evaluate_exit(config, cycle, 81.0)[0] == ReasonCode.TARGET_REACHED


def test_close_value_uses_bid_for_longs_and_ask_for_shorts():
    legs = _legs(("call", 24300.0, cfg.BUY), ("call", 24150.0, cfg.SELL))
    quotes = {("call", 24300.0): Quote(4.0, 5.0, 4.5), ("call", 24150.0): Quote(9.0, 10.0, 9.5)}
    assert execution.close_value_per_unit(legs, quotes) == -6.0


def test_settlement_is_intrinsic_at_the_last_level():
    legs = _legs(("call", 24300.0, cfg.BUY), ("call", 24150.0, cfg.SELL))
    assert execution.settle_value_per_unit(legs, 24200.0) == -50.0
    assert execution.settle_value_per_unit(legs, 24500.0) == -150.0
    assert execution.settle_value_per_unit(legs, 24000.0) == 0.0


# --------------------------------------------------------------------------------------
# Entries against the repository
# --------------------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    monkeypatch.setattr(charges_mod, "_db_path", lambda: path)
    ensure_bots_tables(path)
    runtime.reset_state_for_tests()
    live.reset_state_for_tests()
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.telegram_alerts._notify", lambda *a, **k: None
    )
    return path


def test_paper_entry_records_the_net_premium(db):
    p, _ = _plan("bull_call_debit")
    run_id = repo.open_session_run(USER, BOT_CAS_BINGO)
    outcome = execution.open_paper(USER, run_id, p, ChargesModel(), {})
    assert outcome.opened
    cycle = repo.open_cycles(USER, BOT_CAS_BINGO)[0]
    assert cycle.paper and cycle.detail["net_entry_per_unit"] < 0
    assert cycle.detail["index_code"] == "NIFTY"
    assert runtime.entered_today(USER, "NIFTY", datetime.date.fromisoformat(cycle.opened_at[:10]))


def test_live_sell_failure_unwinds_the_buy(db, monkeypatch):
    p, _ = _plan("bear_call_credit")
    placed = []

    def fake_place(proc, user_id, leg, **kwargs):
        placed.append((leg.action, leg.strike_price))
        if len(placed) == 2:  # the sell leg
            return live.FillResult(order_id="o2", requested_quantity=leg.quantity, error="rejected")
        return live.FillResult(
            order_id=f"o{len(placed)}", filled_quantity=leg.quantity,
            requested_quantity=leg.quantity, average_price=5.0,
        )

    monkeypatch.setattr(live, "place_and_confirm", fake_place)
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.bots.cas_bingo.market.live_quote",
        lambda *a, **k: Quote(4.0, 5.0, 4.5),
    )
    run_id = repo.open_session_run(USER, BOT_CAS_BINGO)
    outcome = execution.open_live(FakeProc(), USER, run_id, p, CasBingoConfig(), ChargesModel(), {})
    assert not outcome.opened and outcome.reason_code == ReasonCode.ENTRY_PARTIAL_UNWOUND
    # Buy first, then the sell that failed, then the buy sold back out.
    assert [a for a, _ in placed] == [cfg.BUY, cfg.SELL, cfg.SELL]
    assert not repo.open_cycles(USER, BOT_CAS_BINGO)


def test_short_margin_with_liquidation_off_is_refused(db, monkeypatch):
    p, _ = _plan("bull_call_debit")
    monkeypatch.setattr(execution, "_available", lambda proc, user: 1.0)
    outcome = execution.enter(
        FakeProc(), USER, CasBingoConfig(liquidation={"enabled": False}), "r", p,
        live=False, charges=ChargesModel(), extra={},
    )
    assert not outcome.opened and outcome.reason_code == ReasonCode.MARGIN_INSUFFICIENT


def test_autonomous_spread_waits_for_a_ready_signal(db, monkeypatch):
    monkeypatch.setattr(runtime, "readiness_status", lambda label: "too_early")
    monkeypatch.setattr(runtime, "sg_conflict", lambda *a: False)
    now = datetime.datetime(2026, 9, 15, 15, 0, tzinfo=IST)
    code, _text = runtime._entry_for_index(
        FakeProc(), USER, CasBingoConfig(mode="live"), "r", "NIFTY", EXPIRY, now
    )
    assert code == ReasonCode.SIGNAL_NOT_READY


def test_a_live_pbsl_rule_on_the_expiry_blocks_entry(db, monkeypatch):
    monkeypatch.setattr(runtime, "sg_conflict", lambda *a: True)
    now = datetime.datetime(2026, 9, 15, 15, 0, tzinfo=IST)
    code, _text = runtime._entry_for_index(
        FakeProc(), USER, CasBingoConfig(), "r", "NIFTY", EXPIRY, now
    )
    assert code == ReasonCode.SG_RULE_CONFLICT


def test_day_open_is_captured_from_the_tick_once_per_day():
    from icici_breeze_backend.app.services import index_spot_feed

    index_spot_feed.reset_state_for_tests()
    index_spot_feed._remember_day_open("nifty", "24012.5")
    index_spot_feed._remember_day_open("nifty", "24100")  # later ticks restate, never replace
    assert index_spot_feed.day_open("nifty") == 24012.5
    assert index_spot_feed.day_open("sensex") is None
