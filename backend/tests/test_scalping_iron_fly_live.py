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
        # ~7,500 a lot: what a hedged NIFTY fly needed on the 10-11 Sep 2026 paper days.
        return {"Status": 200, "Success": {"span_margin_required": 7_500.0 * lots}}


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
    # The fake scrip master lists a 10-Sep-2026 expiry. Pin the expiry picker's clock to the
    # date these tests are written for, or they rot the day that expiry passes.
    import datetime

    monkeypatch.setattr(
        "icici_breeze_backend.app.services.bots.scalping.momentum_bot.now_ist",
        lambda: datetime.datetime(2026, 9, 8, 10, 0),
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.quote_source_router.fetch_chain_side_icici_response",
        lambda p, u, s, e, exp, right, **k: {
            "Status": 200, "Success": _rows(right), "quote_source": "websocket",
        },
    )
    monkeypatch.setattr(
        fly, "live_quote",
        lambda proc, uid, expiry, strike, right: Quote(
            bid=round(_price(strike) - 0.5, 2), ask=round(_price(strike) + 0.5, 2),
            ltp=_price(strike), source="websocket",
        ),
    )
    monkeypatch.setattr(fly, "live_index_spot", lambda: 24_010.0)
    # Never let a test reach Telegram or the real disarm write path unnoticed.
    monkeypatch.setattr(fly, "_alert_stuck", lambda *a, **k: None)
    return FakeProc()


class Dispatcher:
    """Scripted `place_and_confirm`. Records (action, right, strike, quantity) in order."""

    def __init__(self, outcomes=None):
        self.calls: list[tuple[str, str, float, int]] = []
        self._outcomes = list(outcomes or [])
        self._n = 0

    def __call__(self, proc, user_id, leg, *, price_for_attempt, timeout_seconds, attempts=2,
                 journal=None, **_kw):
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
            outcome_unknown=spec.get("outcome_unknown", False),
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


def _alerts(monkeypatch):
    sent: list = []
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.telegram_alerts._notify",
        lambda user_id, text, *, kind: sent.append((kind, text)),
    )
    return sent


@pytest.mark.parametrize("unaccounted", [
    {"cancel_failed": True, "error": "cancel failed"},
    {"outcome_unknown": True, "error": "answer lost"},  # B-21
])
def test_an_unaccounted_order_unwinds_nothing_and_leaves_the_row_pending(
    env, monkeypatch, unaccounted,
):
    """The one failure that must NOT unwind (B-02).

    An order that may still fill -- one that could not be cancelled, or whose answer was lost
    -- would turn an unwind into a position built out of a guess. The row stays PENDING, so
    no exit can trade the planned legs, and the resolver settles it from the broker. The bot
    is not switched off yet: that happens only if the broker still cannot answer after the
    retry budget.
    """
    disarmed: list = []
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.bots.scalping.guards.switch_off",
        lambda uid, bt, why: disarmed.append(why),
    )
    sent = _alerts(monkeypatch)
    d, cycles = _enter(env, monkeypatch, outcomes=[{}, {}, {"filled": 0, **unaccounted}])
    assert len(d.calls) == 3, "no unwind orders may be sent"
    assert disarmed == []
    row = cycles[0]
    assert row.is_open and row.detail["pending"] is True
    assert unaccounted["error"] in row.detail["resolve_problem"]
    assert row.detail.get("cancel_failed", False) is unaccounted.get("cancel_failed", False)
    assert [k for k, _ in sent] == ["scalping_orphan"]
    # And the exit path cannot see it as a fly.
    assert fly.inspect_position(env, USER, IronFlyScalperConfig(mode="live"), BOT) is None


def test_a_complete_live_fly_records_its_entry_value(env, monkeypatch):
    """B-52: the daily stop reads `entry_value`. NULL made a live fly's whole buy-back cost
    read as a loss, so the bot stood down on its first pass."""
    _, cycles = _enter(env, monkeypatch, outcomes=[
        {"price": 10.0}, {"price": 12.0}, {"price": 100.0}, {"price": 105.0},
    ])
    row = cycles[0]
    q = int(row.legs[0]["quantity"])
    assert row.entry_value == pytest.approx(183.0 * q)
    # Marked at a close cost of 152/unit, the stop sees the real +31/unit, not -152.
    from icici_breeze_backend.app.services.bots.scalping import guards

    assert guards.unrealized_pnl(row, 152.0 * q) == pytest.approx(31.0 * q)


def test_an_unwind_that_sticks_leaves_the_cycle_open_and_disarms(env, monkeypatch):
    """The worst reachable state, and it must never be tidied away as flat."""
    disarmed: list = []
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.bots.scalping.guards.switch_off",
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
        "icici_breeze_backend.app.services.bots.scalping.guards.switch_off",
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


# --- B-01: a close that sticks leaves the cycle holding only what is really open ----------
#
# Before this, `cycle.legs` stayed the planned fly after a close or unwind stuck, and the next
# exit re-ran the close over all four legs: buying back shorts already bought back (new longs)
# and selling wings already sold or never bought (new naked shorts).

from icici_breeze_backend.app.services import portfolio_margin_netting as pmn
from icici_breeze_backend.app.services import telegram_alerts
from icici_breeze_backend.app.services.bots.scalping import held_legs

# Captured at import, before the `env` fixture stubs it out, so the Telegram tests below can
# put the real alert back and see exactly what reaches the user.
_REAL_ALERT_STUCK = fly._alert_stuck


class _Remainder:
    reason_code = ReasonCode.CLOSING_REMAINDER
    reason_text = "Retrying the close."


def _no_disarm(monkeypatch):
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.bots.scalping.guards.switch_off",
        lambda *a, **k: None,
    )


def _positions(monkeypatch, rows=None, *, available=True):
    """The broker's positions, as `held_legs.broker_holdings` reads them."""
    monkeypatch.setattr(
        pmn, "positions_for_underlying",
        lambda proc, uid, stock, exchange: pmn.PositionSet(
            rows=list(rows or []), available=available,
            error=None if available else "positions down",
        ),
    )


def _row(leg, quantity=None):
    return {
        "stock_code": "NIFTY", "exchange_code": "NFO",
        "action": "Sell" if leg["action"] == cfg.SELL else "Buy",
        "quantity": str(leg["quantity"] if quantity is None else quantity),
        "right": leg["right"].capitalize(),
        "strike_price": str(int(leg["strike_price"])),
        "expiry_date": leg["expiry_display"],
    }


def _make_due():
    cycle = repo.open_cycles(USER, BOT)[0]
    detail = dict(cycle.detail)
    detail["unwind_next_at"] = 0
    repo.update_cycle_detail(cycle.id, detail)


def _retry(env, monkeypatch, outcomes=None):
    context = fly.inspect_position(env, USER, IronFlyScalperConfig(mode="live"), BOT)
    d = _dispatch(monkeypatch, outcomes)
    fly._close_live(env, USER, IronFlyScalperConfig(mode="live"), context, _Remainder(), CHARGES)
    return context, d


def _stick_exit(env, monkeypatch, second=None, first=None):
    """Open a fly (every entry fill at 100) and close it with the second short refused."""
    _no_disarm(monkeypatch)
    cycle, context = _open_position(env, monkeypatch)
    d = _dispatch(monkeypatch, outcomes=[
        first or {"price": 40.0},
        second or {"filled": 0, "error": "no fill"},
        {"price": 5.0}, {"price": 5.0},
    ])
    fly._close_live(env, USER, IronFlyScalperConfig(mode="live"), context, _Decision(), CHARGES)
    return cycle, d, d.calls[1][1]  # the right of the short that stuck


def test_a_stuck_exit_keeps_only_the_legs_still_held(env, monkeypatch):
    cycle, d, right = _stick_exit(env, monkeypatch)
    q = int(cycle.legs[0]["quantity"])
    # Both shorts tried; only the wing whose short is gone is sold. The other wing still
    # covers the short that would not buy back.
    assert [a for a, _, _ in d.actions] == [cfg.BUY, cfg.BUY, cfg.SELL]
    assert d.actions[2][1] != right
    left = repo.open_cycles(USER, BOT)[0]
    assert sorted((l["right"], l["action"], l["quantity"]) for l in left.legs) == sorted(
        [(right, cfg.SELL, q), (right, cfg.BUY, q)]
    )
    assert left.detail["unwinding"] is True
    # Banked: one short bought back at 40 and one wing sold at 5, both opened at 100.
    assert left.detail["realized_gross"] == pytest.approx((100 - 40) * q + (5 - 100) * q)


def test_the_retry_closes_only_the_remainder_and_books_the_whole_fly(env, monkeypatch):
    cycle, _, right = _stick_exit(env, monkeypatch)
    q = int(cycle.legs[0]["quantity"])
    credit = float(cycle.detail["net_credit_per_unit"])
    _positions(monkeypatch, [_row(l) for l in repo.open_cycles(USER, BOT)[0].legs])
    _make_due()

    context, d = _retry(env, monkeypatch, outcomes=[{"price": 40.0}, {"price": 5.0}])

    assert context.verdict[0] == ReasonCode.CLOSING_REMAINDER
    assert [(a, r) for a, r, _ in d.actions] == [(cfg.BUY, right), (cfg.SELL, right)]
    closed = repo.list_cycles(USER, bot_type=BOT)[0]
    assert not closed.is_open
    # Booked as the exit that started it, and at the same P&L as a one-pass close.
    assert closed.exit_reason_code == ReasonCode.CREDIT_DECAY_TARGET
    assert closed.gross_pnl == pytest.approx((credit - 70.0) * q)
    assert closed.exit_value == pytest.approx(70.0 * q)


def test_a_retry_waits_for_its_back_off(env, monkeypatch):
    _stick_exit(env, monkeypatch)
    _positions(monkeypatch, [_row(l) for l in repo.open_cycles(USER, BOT)[0].legs])
    _, d = _retry(env, monkeypatch)
    assert d.calls == []
    assert repo.open_cycles(USER, BOT)[0].is_open


def test_a_leg_closed_by_hand_is_not_closed_again(env, monkeypatch):
    _stick_exit(env, monkeypatch)
    left = repo.open_cycles(USER, BOT)[0].legs
    wing = next(l for l in left if l["action"] == cfg.BUY)
    _positions(monkeypatch, [_row(wing)])  # the user bought the short back themselves
    _make_due()
    _, d = _retry(env, monkeypatch)
    assert [a for a, _, _ in d.actions] == [cfg.SELL]


def test_everything_closed_by_hand_closes_the_cycle_without_an_order(env, monkeypatch):
    _stick_exit(env, monkeypatch)
    banked = repo.open_cycles(USER, BOT)[0].detail["realized_gross"]
    _positions(monkeypatch, [])
    _make_due()
    _, d = _retry(env, monkeypatch)
    assert d.calls == []
    closed = repo.list_cycles(USER, bot_type=BOT)[0]
    assert not closed.is_open
    assert closed.exit_reason_code == ReasonCode.CLOSED_OUTSIDE_BOT
    assert closed.gross_pnl == pytest.approx(banked)


def test_unreadable_positions_send_nothing(env, monkeypatch):
    """Unreadable is not flat (B-11), and it is not "still held" either."""
    _stick_exit(env, monkeypatch)
    _positions(monkeypatch, available=False)
    _make_due()
    _, d = _retry(env, monkeypatch)
    assert d.calls == []
    left = repo.open_cycles(USER, BOT)[0]
    assert left.is_open and left.detail["unwind_next_at"] > 0


def test_a_partly_filled_close_leaves_only_the_unfilled_units(env, monkeypatch):
    _no_disarm(monkeypatch)
    cycle, context = _open_position(env, monkeypatch)
    q = int(cycle.legs[0]["quantity"])
    d = _dispatch(monkeypatch, outcomes=[
        {"filled": q - 25, "error": "partial"}, {}, {}, {},
    ])
    fly._close_live(env, USER, IronFlyScalperConfig(mode="live"), context, _Decision(), CHARGES)
    right = d.calls[0][1]
    left = repo.open_cycles(USER, BOT)[0].legs
    # 25 units of the short, and its wing held back in full to cover them.
    assert sorted((l["right"], l["action"], l["quantity"]) for l in left) == sorted(
        [(right, cfg.SELL, 25), (right, cfg.BUY, q)]
    )


def test_an_aborted_entry_keeps_only_the_legs_that_filled(env, monkeypatch):
    """The planned second short never filled; it must never appear in the cycle's legs."""
    _no_disarm(monkeypatch)
    d, _ = _enter(env, monkeypatch, outcomes=[
        {}, {},                                    # wings on
        {},                                        # short 1 on
        {"filled": 0, "error": "no fill"},         # short 2 fails -> unwind
        {"filled": 0, "error": "cannot buy back"}, # short 1 will not buy back
        {},                                        # the other wing sells
    ])
    short_right = d.calls[2][1]
    never_filled = (d.calls[3][1], d.calls[3][2])
    left = repo.open_cycles(USER, BOT)[0]
    assert sorted((l["right"], l["action"]) for l in left.legs) == sorted(
        [(short_right, cfg.SELL), (short_right, cfg.BUY)]
    )
    assert never_filled not in {(l["right"], l["strike_price"]) for l in left.legs}
    assert "pending" not in left.detail and left.detail["unwinding"] is True


def test_retries_stop_at_the_limit_and_then_only_watch(env, monkeypatch):
    _stick_exit(env, monkeypatch)
    cycle = repo.open_cycles(USER, BOT)[0]
    detail = dict(cycle.detail)
    detail["unwind_attempts"] = held_legs.MAX_CLOSE_RETRIES
    repo.update_cycle_detail(cycle.id, detail)
    _positions(monkeypatch, [_row(l) for l in cycle.legs])
    _make_due()
    _retry(env, monkeypatch, outcomes=[{"filled": 0, "error": "no fill"}])
    assert repo.open_cycles(USER, BOT)[0].detail["unwind_orders_halted"] == "attempts"

    _make_due()
    _, d = _retry(env, monkeypatch)
    assert d.calls == [], "past the limit the bot only watches"
    assert repo.open_cycles(USER, BOT)[0].is_open


def test_a_close_whose_cancel_failed_is_never_retried_by_order(env, monkeypatch):
    """An order that may still fill must not have a second close placed beside it."""
    _stick_exit(env, monkeypatch, second={"filled": 0, "cancel_failed": True, "error": "x"})
    cycle = repo.open_cycles(USER, BOT)[0]
    assert cycle.detail["unwind_orders_halted"] == "cancel_failed"
    _positions(monkeypatch, [_row(l) for l in cycle.legs])
    _make_due()
    _, d = _retry(env, monkeypatch)
    assert d.calls == []


def test_rows_stuck_by_an_older_build_are_rebuilt_to_their_leftover_legs(env, monkeypatch):
    cycle, _ = _open_position(env, monkeypatch)
    short = next(l for l in cycle.legs if l["action"] == cfg.SELL)
    detail = dict(cycle.detail)
    detail["exit_partial"] = {
        "closed": [],
        "stuck": [{"right": short["right"], "strike": short["strike_price"],
                   "quantity": short["quantity"], "error": "no fill"}],
    }
    repo.update_cycle_detail(cycle.id, detail)  # the old shape: planned legs, stuck in detail

    assert held_legs.repair_legacy_rows() == 1
    left = repo.open_cycles(USER, BOT)[0]
    assert [(l["right"], l["action"]) for l in left.legs] == [(short["right"], cfg.SELL)]
    assert left.detail["unwinding"] is True
    assert held_legs.repair_legacy_rows() == 0, "idempotent"


def _telegram(monkeypatch):
    """Every message the user would get on Telegram. Nobody watches the bot live, so this is
    the only channel that reaches them."""
    sent: list[str] = []
    monkeypatch.setattr(fly, "_alert_stuck", _REAL_ALERT_STUCK)
    monkeypatch.setattr(telegram_alerts, "_notify", lambda uid, text, *, kind: sent.append(text))
    return sent


def test_every_step_of_a_stuck_close_reaches_telegram(env, monkeypatch):
    sent = _telegram(monkeypatch)
    _stick_exit(env, monkeypatch)
    assert len(sent) == 1
    assert "needs checking" in sent[0] and "still open" in sent[0]
    assert "checks your positions before each try" in sent[0], "the retry plan is spelled out"

    _positions(monkeypatch, [_row(l) for l in repo.open_cycles(USER, BOT)[0].legs])
    _make_due()
    _retry(env, monkeypatch, outcomes=[{"price": 40.0}, {"price": 5.0}])
    assert len(sent) == 2 and "leftover legs closed" in sent[1]


def test_legs_closed_by_hand_are_confirmed_on_telegram(env, monkeypatch):
    sent = _telegram(monkeypatch)
    _stick_exit(env, monkeypatch)
    _positions(monkeypatch, [])
    _make_due()
    _retry(env, monkeypatch)
    assert len(sent) == 2
    assert "leftover legs closed" in sent[1] and "no longer show in your positions" in sent[1]


def test_giving_up_is_announced_on_telegram(env, monkeypatch):
    sent = _telegram(monkeypatch)
    _stick_exit(env, monkeypatch)
    cycle = repo.open_cycles(USER, BOT)[0]
    repo.update_cycle_detail(
        cycle.id, {**cycle.detail, "unwind_attempts": held_legs.MAX_CLOSE_RETRIES}
    )
    _positions(monkeypatch, [_row(l) for l in cycle.legs])
    _make_due()
    _retry(env, monkeypatch, outcomes=[{"filled": 0, "error": "no fill"}])
    assert len(sent) == 2
    assert "still could not be closed" in sent[1]
    assert "will try" not in sent[1], "no retry promise once the bot has stopped sending orders"


def test_a_stuck_close_switches_the_bot_off_with_one_accurate_message(env, monkeypatch):
    """The switch-off itself is real here, not stubbed. It used to go through `disarm_bot`,
    whose second message told the user the fly "hit its cumulative daily loss limit"."""
    sent = _telegram(monkeypatch)
    repo.get_or_create_bot(USER, BOT)
    repo.update_bot(USER, BOT, enabled=True)
    _, context = _open_position(env, monkeypatch)
    _dispatch(monkeypatch, outcomes=[
        {"price": 40.0}, {"filled": 0, "error": "no fill"}, {"price": 5.0}, {"price": 5.0},
    ])
    fly._close_live(env, USER, IronFlyScalperConfig(mode="live"), context, _Decision(), CHARGES)
    assert repo.get_or_create_bot(USER, BOT).enabled is False
    assert len(sent) == 1 and "needs checking" in sent[0]
    assert not any("daily loss limit" in text for text in sent)
