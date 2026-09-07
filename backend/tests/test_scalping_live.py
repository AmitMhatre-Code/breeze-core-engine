"""Live order dispatch (services.bots.scalping.live, .guards reconciliation).

Every test here is about a state that only exists once real orders are involved: a limit
resting at the exchange, a cancel that fails, a crash between placing and recording. Paper
mode has none of them, so none of this is covered anywhere else.
"""
from __future__ import annotations

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.bots_migrate import (
    BOT_MOMENTUM_LONG_SCALPER,
    ensure_bots_tables,
)
from icici_breeze_backend.app.domain.bots import ReasonCode
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots.scalping import guards, live

USER = "u1"
LEG = live.LegOrder(
    stock_code="NIFTY", exchange_code=cfg.NFO, right="call", strike_price=24_000.0,
    expiry_display="10-Sep-2026", action=cfg.BUY, quantity=75,
)


class FakeBroker:
    """A broker that answers exactly as scripted, and records what it was asked."""

    def __init__(self, *, place=None, fills=None, cancels=None):
        self.placed: list[dict] = []
        self.cancelled: list[str] = []
        self._place = list(place or [{"ok": True, "order_id": "OID1"}])
        self._fills = dict(fills or {})
        self._cancels = list(cancels or [])

    def place_order(self, user_id, product, stock, action, strike, right, price, expiry, qty, **kw):
        self.placed.append({"action": action, "price": float(price), "qty": qty})
        spec = self._place[min(len(self.placed) - 1, len(self._place) - 1)]
        if not spec.get("ok"):
            return {"Status": 500, "Error": spec.get("error", "rejected")}
        return {"Status": 200, "Success": {"order_id": spec["order_id"]}}

    def cancel_order_single(self, user_id, order_ref):
        self.cancelled.append(str(order_ref))
        ok = self._cancels[min(len(self.cancelled) - 1, len(self._cancels) - 1)] if self._cancels else True
        return {"success": ok}

    def get_session_breeze(self, user_id):
        return self

    def get_order_detail(self, exchange_code="", order_id=""):
        state = self._fills.get(str(order_id))
        if state is None:
            return {"Status": 200, "Success": []}
        return {"Status": 200, "Success": [state]}


@pytest.fixture(autouse=True)
def clean():
    live.reset_state_for_tests()
    yield
    live.reset_state_for_tests()


def _no_sleep(_seconds):
    return None


def _clock():
    """A monotonic clock that always reports the deadline as passed."""
    ticks = iter([0.0] + [999.0] * 200)
    return lambda: next(ticks)


# --- the gate --------------------------------------------------------------------------


def test_live_dispatch_ships_switched_off():
    """Step 9's precondition is paper evidence on production, which no code can assert."""
    assert live.LIVE_DISPATCH_ENABLED is False


# --- placing ---------------------------------------------------------------------------


def test_a_filled_order_reports_its_fill():
    broker = FakeBroker(fills={"OID1": {"quantity_executed": 75, "status": "Executed",
                                        "average_price": 101.5}})
    result = live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=lambda a: 101.5,
        timeout_seconds=0, now=_clock(), sleep=_no_sleep,
    )
    assert result.ok and result.filled_quantity == 75
    assert result.average_price == pytest.approx(101.5)
    assert len(broker.placed) == 1 and broker.cancelled == []


def test_an_unfilled_limit_is_cancelled_then_repriced_once_then_abandoned():
    """The whole point of the live entry path: never walk away from a resting order."""
    broker = FakeBroker(fills={})  # nothing ever fills
    result = live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=lambda a: 101.0 + a,
        timeout_seconds=0, attempts=2, now=_clock(), sleep=_no_sleep,
    )
    assert not result.ok
    assert len(broker.placed) == 2, "one re-price, then stop"
    assert len(broker.cancelled) == 2, "every resting order is cancelled"
    assert result.cancelled is True
    assert "cancelled" in (result.error or "")


def test_a_reprice_uses_a_fresh_price():
    broker = FakeBroker(fills={})
    live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=lambda a: 101.0 + a,
        timeout_seconds=0, attempts=2, now=_clock(), sleep=_no_sleep,
    )
    assert [p["price"] for p in broker.placed] == [101.0, 102.0]


def test_a_failed_cancel_stops_everything_immediately():
    """An order believed dead that is not will fill into a position nothing manages.

    So: no second order, and the caller is told to stand the bot down.
    """
    broker = FakeBroker(fills={}, cancels=[False])
    result = live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=lambda a: 101.0,
        timeout_seconds=0, attempts=3, now=_clock(), sleep=_no_sleep,
    )
    assert result.cancel_failed is True
    assert len(broker.placed) == 1, "must not place again while an order is unaccounted for"
    assert "standing down" in (result.error or "")


def test_a_rejected_order_is_not_cancelled_but_is_retried():
    """Nothing rests after a rejection, so a re-price is safe and a cancel is pointless."""
    broker = FakeBroker(
        place=[{"ok": False, "error": "margin shortfall"}],
    )
    result = live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=lambda a: 101.0,
        timeout_seconds=0, attempts=2, now=_clock(), sleep=_no_sleep,
    )
    assert not result.ok and broker.cancelled == []
    assert "margin shortfall" in (result.error or "")


def test_a_partial_fill_is_reported_rather_than_retried():
    """A cancelled partial is a real, small position. The caller decides, not the dispatcher."""
    broker = FakeBroker(fills={"OID1": {"quantity_executed": 25, "status": "Ordered"}})
    result = live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=lambda a: 101.0,
        timeout_seconds=0, attempts=3, now=_clock(), sleep=_no_sleep,
    )
    assert result.filled_quantity == 25 and not result.ok
    assert len(broker.placed) == 1, "a partial is not something to place more on top of"


def test_the_rest_backstop_catches_a_fill_the_feed_missed():
    """The lesson Strategy Groups learned: a completion path that only listens gets stuck."""
    broker = FakeBroker(fills={"OID1": {"quantity_executed": 75, "status": "Executed",
                                        "average_price": 100.0}})
    result = live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=lambda a: 101.0,
        timeout_seconds=0, now=_clock(), sleep=_no_sleep,
    )
    # No WS notification was ever delivered; only the REST check knew.
    assert result.ok and result.filled_quantity == 75


def test_a_broker_exception_does_not_escape():
    class Boom(FakeBroker):
        def place_order(self, *a, **k):
            raise RuntimeError("socket died")

    result = live.place_and_confirm(
        Boom(), USER, LEG, price_for_attempt=lambda a: 101.0,
        timeout_seconds=0, now=_clock(), sleep=_no_sleep,
    )
    assert not result.ok and "socket died" in (result.error or "")


# --- price ladders ---------------------------------------------------------------------


def test_an_entry_does_not_chase_further_on_retry():
    """The re-price is against a fresh touch, not a progressively worse price."""
    ladder = live.entry_price_ladder(100.0, tolerance_pct=1.0)
    assert ladder(0) == pytest.approx(101.0)
    assert ladder(1) == pytest.approx(101.0)


def test_an_exit_widens_progressively():
    """An exit must complete -- there is a live position with no stop behind it."""
    ladder = live.exit_price_ladder(100.0, band_pct=1.0)
    assert ladder(0) == pytest.approx(99.0)
    assert ladder(1) < ladder(0)
    assert ladder(2) < ladder(1)


def test_an_exit_limit_never_goes_to_zero():
    ladder = live.exit_price_ladder(0.10, band_pct=20.0)
    assert all(ladder(i) >= 0.05 for i in range(6))


# --- crash reconciliation --------------------------------------------------------------


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_bots_tables(path)
    return path


def _intent(run_id, order_ids=("OID1",)):
    return repo.open_cycle(
        USER, BOT_MOMENTUM_LONG_SCALPER, run_id, structure="long_ce",
        legs=[{"right": "call", "strike_price": 24_000.0, "quantity": 75}], lots=1,
        paper=False, detail={"pending": True, "order_ids": list(order_ids)},
    )


def test_an_intent_row_blocks_new_cycles(db):
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    assert guards.has_unresolved_intent(USER, BOT_MOMENTUM_LONG_SCALPER) is False
    _intent(run_id)
    assert guards.has_unresolved_intent(USER, BOT_MOMENTUM_LONG_SCALPER) is True


def test_a_filled_order_is_adopted_after_a_restart(db):
    """The position is real, so the exit loop must take it over rather than ignore it."""
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    _intent(run_id)
    broker = FakeBroker(fills={"OID1": {"quantity_executed": 75, "status": "Executed"}})

    assert guards.reconcile_pending_cycles(broker, USER, BOT_MOMENTUM_LONG_SCALPER) == 1

    assert guards.has_unresolved_intent(USER, BOT_MOMENTUM_LONG_SCALPER) is False
    open_now = repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)
    assert len(open_now) == 1 and open_now[0].detail["reconciled"] is True


def test_an_unfilled_order_is_abandoned_without_counting_as_a_loss(db):
    """Nothing was traded, so it must not read as a loss or trip the cooldown."""
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    _intent(run_id)
    broker = FakeBroker(fills={"OID1": {"quantity_executed": 0, "status": "Cancelled"}})

    guards.reconcile_pending_cycles(broker, USER, BOT_MOMENTUM_LONG_SCALPER)

    cycles = repo.list_cycles(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)
    assert cycles[0].closed_at is not None
    assert cycles[0].exit_reason_code == ReasonCode.ENTRY_UNFILLED
    assert cycles[0].is_loss is False
    totals = repo.scalper_day_totals(USER, BOT_MOMENTUM_LONG_SCALPER)
    assert totals.consecutive_losses == 0


def test_an_unanswerable_order_is_escalated_not_guessed(db, monkeypatch):
    """Assuming 'no fill' would strand a live position with no stop behind it."""
    sent = []
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.telegram_alerts._notify",
        lambda user_id, text, *, kind: sent.append(kind),
    )
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    _intent(run_id)
    broker = FakeBroker(fills={})  # broker answers for nothing

    guards.reconcile_pending_cycles(broker, USER, BOT_MOMENTUM_LONG_SCALPER)

    assert sent == ["scalping_orphan"]
    # Left open on purpose: an unresolved intent is a fact for a human, and it keeps
    # blocking new cycles until dealt with.
    assert guards.has_unresolved_intent(USER, BOT_MOMENTUM_LONG_SCALPER) is True


def test_a_row_with_no_order_id_is_escalated(db, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.telegram_alerts._notify",
        lambda user_id, text, *, kind: sent.append(kind),
    )
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    _intent(run_id, order_ids=())
    guards.reconcile_pending_cycles(FakeBroker(), USER, BOT_MOMENTUM_LONG_SCALPER)
    assert sent == ["scalping_orphan"]


def test_reconciliation_is_a_no_op_when_nothing_is_pending(db):
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    repo.open_cycle(USER, BOT_MOMENTUM_LONG_SCALPER, run_id, structure="long_ce",
                    legs=[], lots=1, paper=True)
    assert guards.reconcile_pending_cycles(FakeBroker(), USER, BOT_MOMENTUM_LONG_SCALPER) == 0
