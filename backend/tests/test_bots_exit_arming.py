"""Arming a bot position's stop off the WS order feed (services.bots.exit_arming).

Two rules carry the whole design, and most of these tests pin one or the other:

* the feed decides when to arm -- the last fill arms the stop, with no polling before it;
* REST is touched only when the feed looks broken (an order never acknowledged, a dead
  socket, a restart), and then at most once per backstop interval.
"""
from __future__ import annotations

import datetime
import time
from types import SimpleNamespace

import pytest

from icici_breeze_backend.app.db.bots_migrate import (
    BOT_EXPIRY_INDEX_WRITER,
    ensure_bots_tables,
)
from icici_breeze_backend.app.domain.bots import ReasonCode
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots import exit_arming

EXPIRY = "15-Sep-2026"
MORNING = datetime.datetime(2026, 9, 15, 10, 0)
TERMS = {
    "premium_collected": 22328.0,
    "loss_limit": 66984.0,
    "loss_multiple": 3.0,
    "target_option_price": None,
}
GRACE = exit_arming.ACK_GRACE_SECONDS
BACKSTOP = exit_arming.REST_BACKSTOP_SECONDS


class FakeProc:
    """Only the order book: the arm guard's one broker call, counted."""

    def __init__(self, orders=()):
        self.orders = list(orders)
        self.reads = 0
        self.exchanges = []

    def get_orders(self, user_id, start, end, *, exchange_codes=None):
        self.reads += 1
        self.exchanges.append(exchange_codes)
        return {"Status": 200, "Error": None, "Success": list(self.orders)}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "bots.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_bots_tables(path)
    exit_arming.reset_state_for_tests()
    clock = SimpleNamespace(now=MORNING)
    monkeypatch.setattr(exit_arming, "now_ist", lambda: clock.now)

    sent: list[str] = []
    monkeypatch.setattr(exit_arming, "_notify", lambda user_id, text: sent.append(text))
    armed: list[dict] = []

    def fake_arm(user_id, **kw):
        armed.append(kw)
        return SimpleNamespace(
            id="rule-1",
            profit_target_pnl=kw["profit_target_pnl"],
            loss_limit_pnl=kw["loss_limit_pnl"],
            target_premium_pct=kw["target_premium_pct"],
            stop_loss_premium_pct=kw["stop_loss_premium_pct"],
            target_option_price=kw["target_option_price"],
        )

    monkeypatch.setattr(
        "icici_breeze_backend.app.repositories.squareoff_rules.arm_rule", fake_arm
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.portfolio_pnl_engine.set_group_rule",
        lambda *a, **k: None,
    )
    # A healthy feed unless a test says otherwise.
    health = SimpleNamespace(ws_user="u1", tick_age=1.0)
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.breeze_websocket_manager.current_ws_user_id",
        lambda: health.ws_user,
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.ws_tick_pipeline.last_tick_age_seconds",
        lambda: health.tick_age,
    )
    yield SimpleNamespace(sent=sent, armed=armed, clock=clock, health=health)
    exit_arming.reset_state_for_tests()


def _wait(order_ids=("O1", "O2"), run_id=None):
    return exit_arming.wait_then_arm(
        user_id="u1",
        bot_type=BOT_EXPIRY_INDEX_WRITER,
        run_id=run_id,
        stock_code="NIFTY",
        exchange_code="NFO",
        expiry_display=EXPIRY,
        order_ids=list(order_ids),
        terms=TERMS,
    )


def _feed(order_id, status, executed=0, total=14885):
    exit_arming.record_order_state(
        order_id,
        status=status,
        executed=executed,
        total=total,
        stock_code="NIFTY",
        expiry_display=EXPIRY,
    )


# --- the feed decides --------------------------------------------------------------------


def test_the_fill_that_completes_the_position_arms_the_stop(env):
    proc = FakeProc()
    pending = _wait()
    t0 = time.monotonic()
    _feed("O1", "ordered")
    _feed("O2", "ordered")

    exit_arming.evaluate(proc, now=t0 + 1)
    _feed("O1", "executed", executed=14885)
    exit_arming.evaluate(proc, now=t0 + 2)
    assert env.armed == [] and proc.reads == 0, "one leg still working: nothing to do yet"

    _feed("O2", "executed", executed=14885)
    exit_arming.evaluate(proc, now=t0 + 3)

    assert len(env.armed) == 1
    assert env.armed[0]["loss_limit_pnl"] == TERMS["loss_limit"]
    assert proc.reads == 1, "one read: the arm guard's own"
    assert exit_arming.current_status(pending) == "armed"
    assert any("Stop armed" in text for text in env.sent)


def test_a_rest_read_is_one_exchange_order_book_not_per_order_lookups(env):
    """The backstop reads the day's order book (`get_order_list`) for the position's own
    exchange only -- one call that covers every order, instead of one per order id, and
    not the other exchange's book, which can never hold an NFO contract."""
    proc = FakeProc()
    _wait()  # nothing ever heard on the feed, so the grace expiry forces one REST read
    t0 = time.monotonic()

    exit_arming.evaluate(proc, now=t0 + GRACE + 1)

    assert proc.reads == 1
    assert proc.exchanges == [["NFO"]]


def test_events_that_arrive_before_the_stop_is_registered_are_not_lost(env):
    """Orders go out before the pending exit exists; a quick fill lands in that gap."""
    _feed("O1", "executed", executed=14885)
    _feed("O2", "executed", executed=14885)
    _wait()

    exit_arming.evaluate(FakeProc())

    assert len(env.armed) == 1


def test_the_listener_reads_parsed_order_notifications(env):
    _wait(order_ids=("O1",))
    note = SimpleNamespace(
        order_id="O1", status="executed", executed_quantity=75, total_quantity=75,
        stock_code="NIFTY", expiry_display=EXPIRY,
    )
    exit_arming._on_notification(note)

    exit_arming.evaluate(FakeProc())

    assert len(env.armed) == 1


def test_a_resting_order_on_a_healthy_feed_costs_no_broker_calls(env):
    """Silence after an acknowledgement is the order resting -- not a reason to poll."""
    proc = FakeProc()
    _wait()
    t0 = time.monotonic()
    _feed("O1", "ordered")
    _feed("O2", "partially executed", executed=4500)

    for minutes in (1, 5, 30, 120):
        exit_arming.evaluate(proc, now=t0 + minutes * 60)

    assert proc.reads == 0
    assert env.armed == []


def test_nothing_filled_means_no_stop_and_no_broker_call(env):
    proc = FakeProc()
    pending = _wait()
    _feed("O1", "cancelled")
    _feed("O2", "rejected")

    exit_arming.evaluate(proc)

    assert env.armed == [] and proc.reads == 0
    assert exit_arming.current_status(pending) == "nothing_filled"
    assert any("No stop needed" in text for text in env.sent)


# --- REST only when the feed looks broken ------------------------------------------------


def test_an_order_the_feed_never_acknowledged_gets_one_rest_check_after_the_grace(env):
    proc = FakeProc()
    _wait()
    t0 = time.monotonic()
    _feed("O1", "executed", executed=14885)  # O2 is never heard from

    exit_arming.evaluate(proc, now=t0 + 30)
    assert proc.reads == 0, "inside the grace an unacknowledged order is just latency"

    exit_arming.evaluate(proc, now=t0 + GRACE + 1)

    assert proc.reads == 1
    assert len(env.armed) == 1, "the order book showed nothing working, so it armed"


def test_rest_checks_are_spaced_by_the_backstop_while_the_feed_stays_broken(env):
    proc = FakeProc(orders=[{
        "order_id": "O2", "stock_code": "NIFTY", "expiry_date": EXPIRY,
        "strike_price": 22750.0, "right": "Put", "status": "Ordered",
    }])
    _wait()
    t0 = time.monotonic()

    exit_arming.evaluate(proc, now=t0 + GRACE + 1)
    exit_arming.evaluate(proc, now=t0 + GRACE + 30)
    assert proc.reads == 1, "still working per REST; the next check waits for the backstop"

    exit_arming.evaluate(proc, now=t0 + GRACE + 1 + BACKSTOP)
    assert proc.reads == 2
    assert env.armed == []
    assert env.sent == [], "a stop waiting on a working order is not an alert"


def test_a_dead_socket_is_a_malfunction_even_after_acknowledgements(env):
    proc = FakeProc()
    _wait()
    t0 = time.monotonic()
    _feed("O1", "ordered")
    _feed("O2", "ordered")
    env.health.ws_user = None

    exit_arming.evaluate(proc, now=t0 + GRACE + 1)

    assert proc.reads == 1


def test_a_silent_feed_in_market_hours_is_a_malfunction(env, monkeypatch):
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.market_calendar.is_market_open", lambda: True
    )
    proc = FakeProc()
    _wait()
    t0 = time.monotonic()
    _feed("O1", "ordered")
    _feed("O2", "ordered")
    env.health.tick_age = GRACE + 60

    exit_arming.evaluate(proc, now=t0 + GRACE + 1)

    assert proc.reads == 1


def test_a_refusal_after_the_feed_says_done_retries_quickly_then_backs_off(env):
    """The REST book can lag the feed, and an unrelated live order blocks the guard too."""
    proc = FakeProc(orders=[{
        "order_id": "MANUAL1", "stock_code": "NIFTY", "expiry_date": EXPIRY,
        "strike_price": 24000.0, "right": "Call", "status": "Ordered",
    }])
    _wait()
    _feed("O1", "executed", executed=14885)
    _feed("O2", "executed", executed=14885)
    t0 = time.monotonic()

    exit_arming.evaluate(proc, now=t0)
    exit_arming.evaluate(proc, now=t0 + 5)
    assert proc.reads == 1
    exit_arming.evaluate(proc, now=t0 + 11)
    assert proc.reads == 2
    exit_arming.evaluate(proc, now=t0 + 42)
    assert proc.reads == 3
    exit_arming.evaluate(proc, now=t0 + 100)
    assert proc.reads == 3, "quick retries spent; now on the backstop"
    exit_arming.evaluate(proc, now=t0 + 42 + BACKSTOP + 1)
    assert proc.reads == 4


# --- lifecycle ---------------------------------------------------------------------------


def test_the_stop_waits_for_its_run_to_close_then_rewrites_its_verdict(env):
    run_id = repo.start_run("u1", BOT_EXPIRY_INDEX_WRITER, "manual")
    _wait(run_id=run_id)
    _feed("O1", "executed", executed=14885)
    _feed("O2", "executed", executed=14885)

    exit_arming.evaluate(FakeProc())
    assert env.armed == [], "the caller is still writing the run's first verdict"

    repo.finish_run(
        run_id,
        status="completed",
        reason_code=ReasonCode.EXIT_ARM_PENDING,
        reason_text="2 of 2 leg(s) placed" + exit_arming.pending_note("NIFTY"),
    )
    exit_arming.evaluate(FakeProc())

    run = repo.get_run(run_id)
    assert len(env.armed) == 1
    assert run["status"] == "completed"
    assert run["reason_code"] == ReasonCode.ORDERS_PLACED
    assert "NIFTY stop armed at 10:00" in run["reason_text"]
    assert "arms once every order fills" not in run["reason_text"]


def test_a_waiting_stop_survives_a_restart_and_checks_rest_at_once(env):
    """Events during the restart reached nobody, so the feed cannot be trusted to say
    what already happened -- the first check after a restart is REST."""
    proc = FakeProc()
    pending = _wait()
    exit_arming.reset_state_for_tests()  # the process died; memory is gone

    exit_arming._resume_from_database()
    exit_arming.evaluate(proc)

    assert proc.reads == 1
    assert len(env.armed) == 1
    assert exit_arming.current_status(pending) == "armed"


def test_the_close_ends_a_stop_that_never_armed_and_says_so(env):
    pending = _wait()
    _feed("O1", "ordered")
    _feed("O2", "ordered")
    env.clock.now = MORNING.replace(hour=15, minute=31)

    exit_arming.evaluate(FakeProc())

    assert exit_arming.current_status(pending) == "abandoned"
    assert any("never armed" in text for text in env.sent)


def test_yesterdays_stops_are_not_resumed(env):
    pending = _wait()
    with repo._connect() as conn:
        conn.execute(
            "UPDATE bot_pending_exits SET created_at = '2026-09-14 09:30:00' WHERE id = ?",
            (pending,),
        )
        conn.commit()
    exit_arming.reset_state_for_tests()

    exit_arming._resume_from_database()

    assert repo.get_pending_exit(pending)["status"] == "abandoned"
