"""Bot 4's LIVE path -- the four-leg entry, its unwind paths, and the exit.

`place_and_confirm` is already covered by `test_scalping_live.py`, so it is stubbed here and
these tests are about the thing only this module decides: **what order the legs go in, and
what happens to the ones that already filled when a later one does not.**

Every test below is a state that would be expensive to reach in production:

* wings sold before they were bought (a naked short for the life of one order),
* an unbalanced structure adopted as if it were a fly,
* a failed unwind quietly closed as though flat,
* a partial fill treated as a smaller fly rather than as no fly at all.
"""
from __future__ import annotations

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.bots_migrate import BOT_IRON_FLY_SCALPER, ensure_bots_tables
from icici_breeze_backend.app.domain.bots import IronFlyScalperConfig, ReasonCode
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots import charges as charges_mod
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.bots.scalping import iron_fly_bot as fly
from icici_breeze_backend.app.services.bots.scalping import live, runtime, spreads
from icici_breeze_backend.app.services.bots.scalping.momentum_bot import Quote

USER = "u1"
BOT = BOT_IRON_FLY_SCALPER
ATM = 24_000.0
LOT = 75
CHARGES = ChargesModel()


class FakeProc:
    def fetch_stock_codes(self, exchange_code):
        return [{"stock_code": "NIFTY", "expiry_dates": ["2026-09-10T06:00:00.000Z"]}]

    def fetch_lot_size(self, stock_code, expiry_display, exchange_code=None):
        return LOT

    def get_session_breeze(self, user_id):
        return self

    def margin_calculator(self, payload, exchange_code=""):
        lots = max(1, int(payload[0]["quantity"]) // LOT)
        return {"Status": 200, "Success": {"span_margin_required": 40_000.0 * lots}}


def _price(strike):
    return max(2.0, 120.0 - abs(strike - ATM) * 0.5)


def _rows(right):
    return [
        {
            "strike_price": float(s), "spot_price": 24_010.0,
            "best_bid_price": round(_price(s) - 0.5, 2),
            "best_offer_price": round(_price(s) + 0.5, 2),
            "ltp": _price(s),
        }
        for s in (23_700, 23_800, 23_850, 23_900, 24_000, 24_100, 24_150, 24_200, 24_300)
    ]


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    monkeypatch.setattr(charges_mod, "_db_path", lambda: path)
    monkeypatch.setattr(spreads, "_db_path", lambda: path)
    ensure_bots_tables(path)
    spreads.reset_throttle_for_tests()
    runtime.reset_state_for_tests()
    live.reset_state_for_tests()
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.quote_source_router.fetch_chain_side_icici_response",
        lambda p, u, s, e, exp, right, **k: {"Status": 200, "Success": _rows(right)},
    )
    monkeypatch.setattr(
        fly, "live_quote",
        lambda proc, uid, expiry, strike, right: Quote(
            bid=round(_price(strike) - 0.5, 2), ask=round(_price(strike) + 0.5, 2),
            ltp=_price(strike),
        ),
    )
    # Never let a test reach Telegram or the real disarm write path unnoticed.
    monkeypatch.setattr(fly, "_alert_stuck", lambda *a, **k: None)
    return FakeProc()


class Dispatcher:
    """Scripted `place_and_confirm`. Records (action, right, strike, quantity) in order."""

    def __init__(self, outcomes=None):
        self.calls: list[tuple[str, str, float, int]] = []
        self._outcomes = list(outcomes or [])
        self._n = 0

    def __call__(self, proc, user_id, leg, *, price_for_attempt, timeout_seconds, attempts=2):
        self.calls.append((leg.action, leg.right, leg.strike_price, leg.quantity))
        spec = self._outcomes[self._n] if self._n < len(self._outcomes) else {}
        self._n += 1
        qty = int(leg.quantity)
        filled = spec.get("filled", qty)
        return live.FillResult(
            order_id=spec.get("order_id", f"OID{self._n}"),
            requested_quantity=qty,
            filled_quantity=filled,
            average_price=spec.get("price", 100.0),
            error=spec.get("error") if filled < qty else None,
            cancel_failed=spec.get("cancel_failed", False),
        )

    @property
    def actions(self):
        return [(a, r, s) for a, r, s, _ in self.calls]


def _dispatch(monkeypatch, outcomes=None):
    d = Dispatcher(outcomes)
    monkeypatch.setattr(live, "place_and_confirm", d)
    return d


def _enter(env, monkeypatch, outcomes=None, config=None):
    d = _dispatch(monkeypatch, outcomes)
    run_id = repo.open_session_run(USER, BOT)
    plan, problem = fly.plan_entry(env, USER, config or IronFlyScalperConfig(mode="live"), None)
    assert problem is None, problem
    fly._open_live(env, USER, BOT, config or IronFlyScalperConfig(mode="live"),
                   run_id, plan, CHARGES)
    return d, repo.list_cycles(USER, bot_type=BOT)


# --- entry sequencing ------------------------------------------------------------------


def test_wings_are_bought_before_the_shorts_are_sold(env, monkeypatch):
    """Non-negotiable (plan section 4.3). Selling first presents the broker with two naked
    legs and draws a margin rejection before the hedge exists."""
    d, cycles = _enter(env, monkeypatch)
    assert [a for a, _, _ in d.actions] == [cfg.BUY, cfg.BUY, cfg.SELL, cfg.SELL]
    assert len(cycles) == 1 and cycles[0].is_open


def test_the_four_legs_go_out_one_at_a_time(env, monkeypatch):
    """Design decision #24: serialization is what makes a throttle an unambiguous refusal."""
    d, cycles = _enter(env, monkeypatch)
    assert len(d.calls) == 4
    # All four legs carry the SAME size. An unbalanced fly is not a fly, and sizing is a
    # whole-structure decision -- the four quantities can never legitimately differ.
    sizes = {qty for _, _, _, qty in d.calls}
    assert len(sizes) == 1 and sizes.pop() % LOT == 0


def test_a_complete_fly_records_the_credit_it_actually_filled_at(env, monkeypatch):
    """Credit comes from the fills, not from the quotes the plan was built on."""
    d, cycles = _enter(env, monkeypatch, outcomes=[
        {"price": 10.0}, {"price": 12.0},    # wings bought
        {"price": 100.0}, {"price": 105.0},  # shorts sold
    ])
    detail = cycles[0].detail
    assert detail["net_credit_per_unit"] == pytest.approx(100.0 + 105.0 - 10.0 - 12.0)
    # `mark_cycle_placed` removes the key outright; that is what stops the row being a
    # reconciliation question.
    assert "pending" not in detail
    assert len(detail["entry_fills"]) == 4


# --- entry failure: the unwind paths ---------------------------------------------------


def test_a_failed_wing_unwinds_the_filled_wing_and_sells_nothing(env, monkeypatch):
    """No short ever went out, so the leftover is a bounded, harmless long."""
    d, cycles = _enter(env, monkeypatch, outcomes=[
        {},                                  # wing 1 fills
        {"filled": 0, "error": "no fill"},   # wing 2 fails
    ])
    assert cycles[0].exit_reason_code == ReasonCode.ENTRY_UNFILLED
    assert not cycles[0].is_open
    # Two entry attempts, then one unwind sell of the wing that did fill.
    assert len(d.calls) == 3
    assert d.calls[2][0] == cfg.SELL


def test_a_failed_short_leaves_a_long_strangle_and_closes_it_at_once(env, monkeypatch):
    """One short against both wings is a real position -- bounded risk, wrong trade."""
    d, cycles = _enter(env, monkeypatch, outcomes=[
        {}, {},                              # both wings on
        {},                                  # first short sold
        {"filled": 0, "error": "no fill"},   # second short fails
    ])
    assert cycles[0].exit_reason_code == ReasonCode.ENTRY_PARTIAL_UNWOUND
    assert not cycles[0].is_open


def test_the_unwind_buys_shorts_back_before_it_sells_the_wings(env, monkeypatch):
    """The whole reason `_unwind_legs` re-orders rather than walking the fill list.

    Selling a wing while its short is still open leaves a naked short for the life of one
    order -- exactly what wings-first entry exists to prevent, arrived at from the other
    direction.
    """
    d, _ = _enter(env, monkeypatch, outcomes=[
        {}, {},                              # wings on
        {},                                  # short 1 on
        {"filled": 0, "error": "no fill"},   # short 2 fails
    ])
    unwind = d.actions[4:]
    assert unwind[0][0] == cfg.BUY, "the short must be bought back first"
    assert [a for a, _, _ in unwind[1:]] == [cfg.SELL, cfg.SELL], "then the wings"


def test_a_partial_fill_is_unwound_not_adopted(env, monkeypatch):
    """Bot 3 adopts a partial and manages the smaller position; Bot 4 must not.

    A wing filled at 75 and a short at 50 is not a fly -- the wing no longer covers the short
    it was bought to cover, and no exit rule in this module describes the result.
    """
    d, cycles = _enter(env, monkeypatch, outcomes=[
        {}, {},
        {"filled": 50, "error": "partial"},  # short partially filled, order cancelled
    ])
    assert not cycles[0].is_open
    assert cycles[0].exit_reason_code == ReasonCode.ENTRY_PARTIAL_UNWOUND
    # The 50 units that did fill are bought back, at their real size.
    buybacks = [c for c in d.calls[3:] if c[0] == cfg.BUY]
    assert buybacks and buybacks[0][3] == 50


def test_a_cancel_failure_unwinds_nothing_and_stands_the_bot_down(env, monkeypatch):
    """The one failure that must NOT unwind.

    An order believed dead but still live will fill later. Unwinding around it would build a
    position out of a guess, so everything freezes and a human is told.
    """
    disarmed: list = []
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.bots.scalping.guards.disarm_bot",
        lambda uid, bt, why: disarmed.append(why),
    )
    d, cycles = _enter(env, monkeypatch, outcomes=[
        {}, {},
        {"filled": 0, "cancel_failed": True, "error": "cancel failed"},
    ])
    assert len(d.calls) == 3, "no unwind orders may be sent"
    assert disarmed, "the bot must be disarmed"
    assert cycles[0].is_open, "the row stays open -- the position is unaccounted for"
    assert cycles[0].detail["cancel_failed"] is True


def test_an_unwind_that_sticks_leaves_the_cycle_open_and_disarms(env, monkeypatch):
    """The worst reachable state, and it must never be tidied away as flat."""
    disarmed: list = []
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.bots.scalping.guards.disarm_bot",
        lambda uid, bt, why: disarmed.append(why),
    )
    d, cycles = _enter(env, monkeypatch, outcomes=[
        {}, {},                                    # wings on
        {},                                        # short on
        {"filled": 0, "error": "no fill"},         # short 2 fails -> unwind
        {"filled": 0, "error": "cannot buy back"}, # the unwind's buyback ALSO fails
    ])
    assert cycles[0].is_open, "a stuck leg must not close the cycle"
    assert disarmed
    assert cycles[0].detail["stuck_legs"]


# --- the exit ---------------------------------------------------------------------------


def _open_position(env, monkeypatch):
    d, cycles = _enter(env, monkeypatch)
    cycle = repo.open_cycles(USER, BOT)[0]
    quotes = fly.fetch_leg_quotes(env, USER, cycle.legs)
    return cycle, fly.FlyContext(
        cycle=cycle, quotes=quotes,
        close_cost=fly.cost_to_close(cycle.legs, quotes), verdict=None, spot=24_010.0,
    )


class _Decision:
    reason_code = ReasonCode.CREDIT_DECAY_TARGET
    reason_text = "Credit decayed to target."


def test_the_exit_buys_shorts_back_before_selling_the_wings(env, monkeypatch):
    """Selling the hedges while the shorts are open inverts the margin mid-unwind."""
    cycle, context = _open_position(env, monkeypatch)
    d = _dispatch(monkeypatch)
    fly._close_live(env, USER, IronFlyScalperConfig(mode="live"), context, _Decision(), CHARGES)
    assert [a for a, _, _ in d.actions[:2]] == [cfg.BUY, cfg.BUY]
    assert [a for a, _, _ in d.actions[2:]] == [cfg.SELL, cfg.SELL]
    assert not repo.list_cycles(USER, bot_type=BOT)[0].is_open


def test_an_exit_leg_that_will_not_fill_keeps_the_cycle_open(env, monkeypatch):
    """A position that is still partly on is not closed. The exit loop retries next pass."""
    disarmed: list = []
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.bots.scalping.guards.disarm_bot",
        lambda uid, bt, why: disarmed.append(why),
    )
    cycle, context = _open_position(env, monkeypatch)
    _dispatch(monkeypatch, outcomes=[{}, {"filled": 0, "error": "no fill"}, {}, {}])
    fly._close_live(env, USER, IronFlyScalperConfig(mode="live"), context, _Decision(), CHARGES)
    reread = repo.list_cycles(USER, bot_type=BOT)[0]
    assert reread.is_open, "a fly with a leg still on must not be booked as closed"
    assert reread.detail["exit_partial"]["stuck"]
    assert disarmed


def test_a_closed_fly_prices_gross_off_credit_minus_buyback(env, monkeypatch):
    cycle, context = _open_position(env, monkeypatch)
    credit = float(cycle.detail["net_credit_per_unit"])
    quantity = int(cycle.legs[0]["quantity"])
    _dispatch(monkeypatch, outcomes=[{"price": 40.0}, {"price": 40.0},
                                     {"price": 5.0}, {"price": 5.0}])
    fly._close_live(env, USER, IronFlyScalperConfig(mode="live"), context, _Decision(), CHARGES)
    closed = repo.list_cycles(USER, bot_type=BOT)[0]
    # Buy back two shorts at 40 each, sell two wings at 5 each -> cost to close 70/unit.
    assert closed.detail["exit"]["close_cost_per_unit"] == pytest.approx(70.0)
    assert closed.gross_pnl == pytest.approx((credit - 70.0) * quantity)


# --- the bot must not enter on top of an unresolved intent -------------------------------


def test_an_unreconciled_order_blocks_a_new_fly(env, monkeypatch):
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.bots.scalping.guards.has_unresolved_intent",
        lambda uid, bt: True,
    )
    d = _dispatch(monkeypatch)
    run_id = repo.open_session_run(USER, BOT)
    plan, _ = fly.plan_entry(env, USER, IronFlyScalperConfig(mode="live"), None)
    fly._open_live(env, USER, BOT, IronFlyScalperConfig(mode="live"), run_id, plan, CHARGES)
    assert d.calls == []
    assert repo.list_cycles(USER, bot_type=BOT) == []


# --- routing ------------------------------------------------------------------------------


def test_a_real_fly_is_never_unwound_at_simulated_prices(env, monkeypatch):
    """The same invariant `momentum_bot` documents: an open position is managed the way it
    was OPENED. A user stepping the bot back to Paper must not close four real legs in a
    simulation."""
    cycle, context = _open_position(env, monkeypatch)
    d = _dispatch(monkeypatch)
    paper_closes: list = []
    monkeypatch.setattr(fly, "_close", lambda *a, **k: paper_closes.append(1))

    class _Exit:
        action = "exit"
        reason_code = ReasonCode.SQUARE_OFF
        reason_text = "Hard square-off."

    fly.execute(
        env, USER, BOT, IronFlyScalperConfig(mode="paper"), cycle.run_id, _Exit(),
        context, CHARGES, [], __import__("datetime").datetime(2026, 9, 8, 15, 20), None,
    )
    assert paper_closes == [], "a live cycle must never take the paper close path"
    assert d.calls, "it must dispatch real unwind orders instead"
