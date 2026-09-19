"""Bot 4, the ATM Iron Fly scalper (services.bots.scalping.iron_fly_bot).

Three things here would be quietly catastrophic if wrong, and each has its own test:
the leg ORDER (wings before shorts, and the reverse on the way out), the margin call
carrying BUY actions on the wings, and the unwind paths when a leg cannot be priced.
"""
from __future__ import annotations

import datetime

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.bots_migrate import (
    BOT_IRON_FLY_SCALPER,
    ensure_bots_tables,
)
from icici_breeze_backend.app.domain.bots import (
    IronFlyExitConfig,
    IronFlyScalperConfig,
    ReasonCode,
)
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots import charges as charges_mod
from icici_breeze_backend.app.services.bots.scalping import iron_fly_bot as fly
from icici_breeze_backend.app.services.bots.scalping import runtime, spreads
from icici_breeze_backend.app.services.bots.scalping.candles import Candle
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.bots.scalping.decide import FeedHealth
from icici_breeze_backend.app.services.bots.scalping.momentum_bot import Quote

USER = "u1"
EXPIRY = "10-Sep-2026"
ATM = 24_000.0
LOT = 75
CHARGES = ChargesModel()


class FakeProc:
    """Prices every strike off a crude but monotonic curve, and records margin calls."""

    # ~7,500 a lot is what a hedged NIFTY fly actually needed on the 10-11 Sep 2026 paper days.
    def __init__(self, *, margin_per_lot=7_500.0, unpriceable=()):
        self.margin_calls: list[list[dict]] = []
        self._margin_per_lot = margin_per_lot
        self._unpriceable = set(unpriceable)

    def fetch_stock_codes(self, exchange_code):
        return [{"stock_code": "NIFTY", "expiry_dates": ["2026-09-10T06:00:00.000Z"]}]

    def fetch_lot_size(self, stock_code, expiry_display, exchange_code=None):
        return LOT

    def get_session_breeze(self, user_id):
        return self

    def margin_calculator(self, payload, exchange_code=""):
        self.margin_calls.append(payload)
        lots = max(1, int(payload[0]["quantity"]) // LOT)
        return {"Status": 200, "Success": {"span_margin_required": self._margin_per_lot * lots}}


def _price(strike, right):
    """Nearer the money is dearer; wings are cheap but non-zero."""
    distance = abs(strike - ATM)
    return max(2.0, 120.0 - distance * 0.5)


def _rows(right, proc):
    out = []
    for strike in (23_700, 23_800, 23_850, 23_900, 24_000, 24_100, 24_150, 24_200, 24_300):
        p = _price(strike, right)
        unpriceable = (strike, right) in proc._unpriceable
        out.append({
            "strike_price": float(strike),
            "spot_price": 24_010.0,
            "best_bid_price": 0.0 if unpriceable else round(p - 0.5, 2),
            "best_offer_price": 0.0 if unpriceable else round(p + 0.5, 2),
            "ltp": p,
        })
    return out


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    monkeypatch.setattr(charges_mod, "_db_path", lambda: path)
    monkeypatch.setattr(spreads, "_db_path", lambda: path)
    ensure_bots_tables(path)
    spreads.reset_throttle_for_tests()
    runtime.reset_state_for_tests()
    proc = FakeProc()
    # The fake scrip master lists a 10-Sep-2026 expiry. Pin the expiry picker's clock to the
    # date these tests are written for, or they rot the day that expiry passes.
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.bots.scalping.momentum_bot.now_ist",
        lambda: datetime.datetime(2026, 9, 8, 10, 0),
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.quote_source_router.fetch_chain_side_icici_response",
        lambda p, u, s, e, exp, right, **k: {"Status": 200, "Success": _rows(right, proc)},
    )
    return proc


# --- structure ------------------------------------------------------------------------


def test_wings_snap_outward_never_inward(env):
    """Inward would collect less credit AND need more margin -- worse on both axes."""
    rows = _rows("call", env)
    assert fly.pick_wing(rows, 24_120.0, outward_up=True)["strike_price"] == 24_150.0
    rows_p = _rows("put", env)
    assert fly.pick_wing(rows_p, 23_880.0, outward_up=False)["strike_price"] == 23_850.0


def test_the_structure_is_two_atm_shorts_and_two_wings(env):
    structure, problem = fly.build_structure(env, USER, IronFlyScalperConfig(), None)
    assert problem is None
    expiry, atm, width, legs = structure
    assert atm == ATM and width == 150.0
    shorts = [l for l in legs if l.is_short]
    longs = [l for l in legs if not l.is_short]
    assert {l.strike for l in shorts} == {ATM}
    assert {l.right for l in shorts} == {"call", "put"}
    assert sorted(l.strike for l in longs) == [23_850.0, 24_150.0]


def test_an_unquoted_wing_skips_the_cycle(env, monkeypatch):
    """A fly missing its hedge is a short straddle in disguise."""
    env._unpriceable = {(24_150.0, "call")}
    structure, problem = fly.build_structure(env, USER, IronFlyScalperConfig(), None)
    assert structure is None
    assert problem[0] == ReasonCode.QUOTE_UNAVAILABLE
    assert "long CE" in problem[1]


def test_the_vix_rule_widens_only_when_switched_on_and_breached():
    off = IronFlyScalperConfig()
    assert fly.wing_width_for(off, 25.0) == 150.0  # rule off: VIX is irrelevant

    on = IronFlyScalperConfig(
        structure={"wing_width_points": 150.0, "widen_above_vix": 18.0,
                   "widened_wing_width_points": 200.0}
    )
    assert fly.wing_width_for(on, 12.0) == 150.0
    assert fly.wing_width_for(on, 22.0) == 200.0
    # An unavailable reading must not widen: widening answers *known* high volatility.
    assert fly.wing_width_for(on, None) == 150.0


# --- sizing ---------------------------------------------------------------------------


def test_margin_is_priced_with_all_four_legs_in_one_call(env):
    """Separately-priced legs discard the netting the hedge exists to earn."""
    plan, problem = fly.plan_entry(env, USER, IronFlyScalperConfig())
    assert problem is None
    first = env.margin_calls[0]
    assert len(first) == 4


def test_the_wings_are_sent_as_buys_not_sells(env):
    """Bot 2's helper hardcodes SELL; sending wings that way inflates margin instead of
    reducing it, and would refuse every lot count against the ceiling."""
    fly.plan_entry(env, USER, IronFlyScalperConfig())
    actions = [leg["action"] for leg in env.margin_calls[0]]
    assert actions.count(cfg.BUY) == 2 and actions.count(cfg.SELL) == 2


def test_lots_are_the_most_that_fit_under_the_ceiling(env):
    # 7,500 per lot against the default 25,000 ceiling -> 3 lots, the size the stops assume.
    plan, _ = fly.plan_entry(env, USER, IronFlyScalperConfig())
    assert plan.lots == 3
    assert plan.margin_required <= 25_000.0


def test_a_ceiling_too_small_for_one_lot_skips_rather_than_partial_funding(env):
    plan, problem = fly.plan_entry(env, USER, IronFlyScalperConfig(margin_ceiling_inr=5_000.0))
    assert plan is None and problem[0] == ReasonCode.MARGIN_CAP_TOO_SMALL


def test_an_unpriceable_margin_is_never_treated_as_zero(env, monkeypatch):
    """A zero would size an unlimited position against the ceiling."""
    monkeypatch.setattr(env, "margin_calculator", lambda *a, **k: {"Status": 500})
    plan, problem = fly.plan_entry(env, USER, IronFlyScalperConfig())
    assert plan is None and problem[0] == ReasonCode.MARGIN_LOOKUP_FAILED


# --- sequencing -----------------------------------------------------------------------


def test_entry_buys_the_wings_before_selling_the_shorts(env):
    """Selling first draws a margin rejection before the hedge exists. Non-negotiable."""
    plan, _ = fly.plan_entry(env, USER, IronFlyScalperConfig())
    order = plan.entry_sequence()
    assert [l.is_short for l in order] == [False, False, True, True]


def test_exit_reverses_it_buying_back_shorts_first(env):
    """Selling hedges while shorts are open inverts the margin mid-unwind."""
    plan, _ = fly.plan_entry(env, USER, IronFlyScalperConfig())
    order = plan.exit_sequence()
    assert [l.is_short for l in order] == [True, True, False, False]


def test_a_complete_entry_collects_a_net_credit(env):
    plan, _ = fly.plan_entry(env, USER, IronFlyScalperConfig())
    fills = fly.simulate_entry(plan, CHARGES)
    assert fills.complete
    # Shorts are ATM and dear, wings are far and cheap, so the structure is a net credit.
    assert fills.net_credit_per_unit > 0


def test_a_failed_wing_unwinds_with_nothing_sold(env):
    """The bounded, harmless case: report it as such rather than as a live position."""
    plan, _ = fly.plan_entry(env, USER, IronFlyScalperConfig())
    wing = next(l for l in plan.legs if not l.is_short)
    legs = tuple(
        fly.FlyLeg(l.right, l.strike, l.action, Quote(None, None, None)) if l is wing else l
        for l in plan.legs
    )
    fills = fly.simulate_entry(
        fly.FlyPlan(EXPIRY, ATM, 150.0, legs, LOT, 1, 40_000.0), CHARGES
    )
    assert not fills.complete
    code, text = fly.unwind_reason(fills)
    assert code == ReasonCode.ENTRY_UNFILLED and "nothing was sold" in text


def test_a_failed_short_after_the_wings_reports_a_different_state(env):
    """One short against both wings is a real position -- bounded risk, wrong trade."""
    plan, _ = fly.plan_entry(env, USER, IronFlyScalperConfig())
    shorts = [l for l in plan.legs if l.is_short]
    legs = tuple(
        fly.FlyLeg(l.right, l.strike, l.action, Quote(None, None, None)) if l is shorts[-1] else l
        for l in plan.legs
    )
    fills = fly.simulate_entry(
        fly.FlyPlan(EXPIRY, ATM, 150.0, legs, LOT, 1, 40_000.0), CHARGES
    )
    assert not fills.complete
    code, _ = fly.unwind_reason(fills)
    assert code == ReasonCode.ENTRY_PARTIAL_UNWOUND


# --- exits ----------------------------------------------------------------------------


def _legs():
    return [
        {"right": "call", "strike_price": ATM, "action": cfg.SELL, "quantity": LOT,
         "expiry_display": EXPIRY},
        {"right": "put", "strike_price": ATM, "action": cfg.SELL, "quantity": LOT,
         "expiry_display": EXPIRY},
        {"right": "call", "strike_price": 24_150.0, "action": cfg.BUY, "quantity": LOT,
         "expiry_display": EXPIRY},
        {"right": "put", "strike_price": 23_850.0, "action": cfg.BUY, "quantity": LOT,
         "expiry_display": EXPIRY},
    ]


def test_close_cost_uses_the_ask_for_shorts_and_the_bid_for_longs():
    """Mid-priced decay books a profit the exit then fails to realise."""
    quotes = {
        "call|24000.0": Quote(bid=50.0, ask=52.0, ltp=51.0),
        "put|24000.0": Quote(bid=48.0, ask=50.0, ltp=49.0),
        "call|24150.0": Quote(bid=8.0, ask=10.0, ltp=9.0),
        "put|23850.0": Quote(bid=7.0, ask=9.0, ltp=8.0),
    }
    # buy back shorts at ask (52 + 50), sell wings at bid (8 + 7)
    assert fly.cost_to_close(_legs(), quotes) == pytest.approx(52 + 50 - 8 - 7)


def test_an_unpriceable_leg_makes_the_unwind_cost_unknown():
    quotes = {"call|24000.0": Quote(None, None, None)}
    assert fly.cost_to_close(_legs(), quotes) is None


def test_the_decay_target_books_the_position():
    cfg_ = IronFlyScalperConfig()
    verdict = fly.evaluate_exit(
        cfg_, net_credit_per_unit=100.0, close_cost_per_unit=84.0, quantity=LOT,
        spot=24_000.0, atm_strike_price=ATM,
    )
    assert verdict[0] == ReasonCode.CREDIT_DECAY_TARGET


def test_the_decay_target_is_not_reached_early():
    cfg_ = IronFlyScalperConfig()
    assert fly.evaluate_exit(
        cfg_, net_credit_per_unit=100.0, close_cost_per_unit=90.0, quantity=LOT,
        spot=24_000.0, atm_strike_price=ATM,
    ) is None


def test_drift_outranks_the_pnl_stops():
    """By the time the centre has moved this far, gamma arrives faster than an exit can."""
    cfg_ = IronFlyScalperConfig()
    verdict = fly.evaluate_exit(
        cfg_, net_credit_per_unit=100.0, close_cost_per_unit=84.0, quantity=LOT,
        spot=24_200.0, atm_strike_price=ATM,  # 0.83% drift, limit 0.35%
    )
    assert verdict[0] == ReasonCode.DRIFT_STOP


def test_the_tighter_of_the_two_loss_stops_binds():
    """Both are live by design; honouring only the looser would retire the stricter."""
    # 1 lot, credit 7,500: a 1,500 flat stop against 5% of credit (375).
    tight_pct = IronFlyScalperConfig(
        exits={"stop_loss_credit_pct": 5.0, "hard_stop_loss_inr": 1500.0}
    )
    verdict = fly.evaluate_exit(
        tight_pct.model_copy(), net_credit_per_unit=100.0, close_cost_per_unit=106.0,
        quantity=LOT, spot=24_000.0, atm_strike_price=ATM,
    )
    assert verdict[0] == ReasonCode.STOP_LOSS  # -450 breaches 5% of 7,500 = 375


def test_both_stops_can_be_switched_off():
    e = IronFlyExitConfig(hard_stop_loss_inr=None, stop_loss_credit_pct=None)
    assert e.loss_limit_inr(10_000.0) is None
    cfg_ = IronFlyScalperConfig(exits={"hard_stop_loss_inr": None, "stop_loss_credit_pct": None})
    assert fly.evaluate_exit(
        cfg_, net_credit_per_unit=100.0, close_cost_per_unit=500.0, quantity=LOT,
        spot=24_000.0, atm_strike_price=ATM,
    ) is None  # deeply underwater, but the user disabled both stops


# --- re-entry gate --------------------------------------------------------------------


def _candles(rng_pts: float, n=12):
    base = 24_000.0
    return [
        Candle(i * 60, base, base + (rng_pts if i == n - 1 else 0), base, base, 1000, None, 1)
        for i in range(n)
    ]


def test_reentry_needs_both_the_cooldown_and_a_settled_range():
    cfg_ = IronFlyScalperConfig()
    now = datetime.datetime(2026, 9, 8, 12, 0)

    # Cooldown not yet served.
    blocked = fly.reentry_blocked(
        cfg_, now=now, last_closed_at="2026-09-08 11:55:00", candles=_candles(5.0)
    )
    assert blocked[0] == ReasonCode.REENTRY_GATE_CLOSED and "cooldown" in blocked[1]

    # Cooldown served, but spot is still moving (100 points on 24,000 is 0.42% > 0.15%).
    blocked = fly.reentry_blocked(
        cfg_, now=now, last_closed_at="2026-09-08 11:30:00", candles=_candles(100.0)
    )
    assert blocked[0] == ReasonCode.REENTRY_GATE_CLOSED and "ranged" in blocked[1]

    # Both clear.
    assert fly.reentry_blocked(
        cfg_, now=now, last_closed_at="2026-09-08 11:30:00", candles=_candles(5.0)
    ) is None


def test_the_first_fly_of_the_day_has_no_cooldown_to_serve():
    cfg_ = IronFlyScalperConfig()
    assert fly.reentry_blocked(
        cfg_, now=datetime.datetime(2026, 9, 8, 11, 35), last_closed_at=None,
        candles=_candles(5.0),
    ) is None


def test_no_candle_history_holds_re_entry():
    """Cannot judge whether spot has settled, so do not claim that it has."""
    blocked = fly.reentry_blocked(
        IronFlyScalperConfig(), now=datetime.datetime(2026, 9, 8, 12, 0),
        last_closed_at=None, candles=[],
    )
    assert blocked[0] == ReasonCode.NOT_WARM


# --- end to end through the driver ----------------------------------------------------


def _drive(monkeypatch, proc, now=datetime.datetime(2026, 9, 8, 12, 0)):
    """Point the driver at the fake broker with all shared gates open."""
    monkeypatch.setattr(runtime, "_trading_allowed", lambda: True)
    monkeypatch.setattr(runtime, "_api_calls_remaining", lambda uid: 90)
    monkeypatch.setattr(runtime, "_is_expiry_day", lambda cfg: False)
    monkeypatch.setattr(runtime, "_current_vix", lambda p, u, c: None)
    monkeypatch.setattr(
        runtime, "_feed_health", lambda bot_type, cfg: FeedHealth(warm=True, stale=False, stale_seconds=0.0)
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.market_calendar.is_trading_day", lambda now=None: True
    )
    monkeypatch.setattr(runtime, "now_ist", lambda: now)
    monkeypatch.setattr("icici_breeze_backend.app.services.processor.processor", lambda: proc)

    def _quote(p_, u_, stock, exch, expiry, right, strike, **k):
        # Price each leg off the same curve the chain uses, so the unwind cost tracks the
        # credit collected. A flat quote for every leg makes the fly look instantly
        # profitable and books it on the next pass.
        price = _price(float(strike), right)
        return {"Status": 200, "Success": [{
            "best_bid_price": round(price - 0.5, 2),
            "best_offer_price": round(price + 0.5, 2),
            "ltp": price,
        }]}

    monkeypatch.setattr(
        "icici_breeze_backend.app.services.quote_source_router.fetch_quote_icici_response", _quote
    )

    class Builder:
        candles = _candles(5.0)
        session_vwap = 24_000.0

    class Feed:
        builder = Builder()

    monkeypatch.setattr(runtime.futures_feed, "get_feed", lambda index="nifty": Feed())


def test_the_driver_opens_a_paper_fly_inside_the_window(env, monkeypatch):
    _drive(monkeypatch, env)
    runtime.tick_bot(USER, BOT_IRON_FLY_SCALPER, IronFlyScalperConfig())

    cycles = repo.list_cycles(USER, bot_type=BOT_IRON_FLY_SCALPER)
    assert len(cycles) == 1
    c = cycles[0]
    assert c.structure == "iron_fly" and c.paper is True and len(c.legs) == 4
    assert c.detail["net_credit_per_unit"] > 0
    assert c.detail["atm_strike"] == ATM
    assert c.detail["margin_required"] > 0
    assert c.detail["loss_limit_inr"] is not None


def test_the_driver_does_not_open_a_second_fly_while_one_is_live(env, monkeypatch):
    _drive(monkeypatch, env)
    cfg_ = IronFlyScalperConfig()
    for _ in range(3):
        runtime.tick_bot(USER, BOT_IRON_FLY_SCALPER, cfg_)
    assert len(repo.list_cycles(USER, bot_type=BOT_IRON_FLY_SCALPER)) == 1


def test_the_window_closing_flattens_the_fly(env, monkeypatch):
    """Bot 4 exits at window end; Bot 3 deliberately does not."""
    _drive(monkeypatch, env)
    cfg_ = IronFlyScalperConfig()
    runtime.tick_bot(USER, BOT_IRON_FLY_SCALPER, cfg_)
    assert repo.open_cycles(USER, BOT_IRON_FLY_SCALPER)

    monkeypatch.setattr(runtime, "now_ist", lambda: datetime.datetime(2026, 9, 8, 13, 45))
    runtime.tick_bot(USER, BOT_IRON_FLY_SCALPER, cfg_)

    closed = repo.list_cycles(USER, bot_type=BOT_IRON_FLY_SCALPER)[0]
    assert not closed.is_open
    assert closed.exit_reason_code == ReasonCode.SQUARE_OFF
    assert closed.friction > 0


def test_a_live_mode_fly_dispatches_real_orders_not_simulated_fills(env, monkeypatch):
    """Bot 4's live path exists now, and paper must never stand in for it.

    Supersedes the earlier `test_a_live_mode_fly_places_nothing`, which pinned the guard that
    returned before placing anything. That guard was the honest state while the path was
    unwritten; leaving it in place now would mean a bot set Live quietly simulating -- the
    one behaviour the mode switch exists to make impossible. `test_scalping_iron_fly_live.py`
    covers the sequencing and unwind paths in full.
    """
    _drive(monkeypatch, env)
    simulated: list = []
    monkeypatch.setattr(fly, "simulate_entry", lambda *a, **k: simulated.append(1))
    dispatched: list = []
    monkeypatch.setattr(fly, "_open_live", lambda *a, **k: dispatched.append(1))

    runtime.tick_bot(USER, BOT_IRON_FLY_SCALPER, IronFlyScalperConfig(mode="live"))

    assert dispatched == [1], "live mode must take the live path"
    assert simulated == [], "live mode must never fall through to paper fills"
