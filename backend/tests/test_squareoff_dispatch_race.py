"""An SG's exits against the order feed while they are still going out (B-03 and the fixes
that share it: B-13, B-14, B-19, B-20).

The order feed can report an exit's fill before `place_order` has returned its id. Order
identity is the only thing that separates "our exit filled" from "the user traded", so
nothing may be judged until every id this fire produced is known. These tests drive the
real dispatcher and lifecycle against a fake broker that delivers notifications exactly
where the feed would: during the placement call.
"""
from __future__ import annotations

import datetime
import sqlite3
import sys
import types

import pytest

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.db.squareoff_rules_migrate import ensure_squareoff_rules_table
from icici_breeze_backend.app.repositories import squareoff_rules as repo
from icici_breeze_backend.app.services import order_notifications as on
from icici_breeze_backend.app.services import squareoff_dispatcher as d
from icici_breeze_backend.app.services import strategy_group_lifecycle as sg
from icici_breeze_backend.app.services.order_notifications import parse_order_notification
from tests.fixtures.order_notifications import executed, with_status

USER = "VIKRAMMH"
STOCK, EXPIRY = "NIFTY", "21-Jul-2026"
THROTTLED = {"Status": 429, "Error": "You have been throttled by ICICI.", "icici_throttled": True}


def _ok(order_id):
    return {"Status": 200, "Success": {"order_id": order_id}}


def _leg(strike, right, action, qty="130", ltp=10.0):
    r = "call" if right == "Call" else "put"
    return {
        "scrip_key": f"NFO|{STOCK}|{EXPIRY}|{strike}|{r}",
        "product_type": "options",
        "stock_code": STOCK,
        "exchange_code": "NFO",
        "expiry_display": EXPIRY,
        "right": right,
        "strike_price": f"{strike}.0",
        "quantity": qty,
        "action": action,
        "pnl": -500.0,
        "ltp": ltp,
    }


SHORT_CE = _leg(26000, "Call", "Buy")   # a short call being bought back
LONG_CE = _leg(26500, "Call", "Sell")   # its wing being sold
SHORT_PE = _leg(25000, "Put", "Buy")
LONG_PE = _leg(24500, "Put", "Sell")


def _note(order_id, strike, right="Call", status="Executed", **extra):
    raw = executed if status == "Executed" else (lambda **kw: with_status(status, **kw))
    return parse_order_notification(
        raw(orderReference=order_id, strikePrice=str(strike * 100), optionType=right, **extra)
    )


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_squareoff_rules_table(path)
    return path


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    sg.reset_state_for_tests()
    on.reset_state_for_tests()
    monkeypatch.setattr(d.time, "sleep", lambda *_: None)
    monkeypatch.setattr(d, "notify_squareoff_fired", lambda *a, **k: None)
    monkeypatch.setattr(d, "notify_squareoff_retrying", lambda *a, **k: None)
    monkeypatch.setattr(sg, "release_subscription", lambda *a, **k: None)
    monkeypatch.setattr(sg, "_release_subscription", lambda *a, **k: None)
    import icici_breeze_backend.app.services.telegram_alerts as tg

    resets: list[str] = []
    monkeypatch.setattr(tg, "notify_squareoff_reset", lambda uid, rule, reason, *a, **k: resets.append(reason))
    # The order book's cache would otherwise carry one test's book into the next.
    from icici_breeze_backend.app.services import order_book_cache

    monkeypatch.setattr(
        order_book_cache, "get_or_fetch", lambda uid, win, ex, fetch: fetch()
    )
    yield resets
    sg.reset_state_for_tests()
    on.reset_state_for_tests()


class Broker:
    """Answers `place_order` from a script. A script entry may be a callable, run during
    the call (where the real feed can deliver a fill) and returning the answer."""

    def __init__(self, script, book=None):
        self.script = list(script)
        self.sent: list[dict] = []
        self.book = book if book is not None else []

    def fetch_qty_limits(self, *a, **k):
        return None

    def fetch_lot_size(self, *a, **k):
        return None

    def place_order(self, **kwargs):
        self.sent.append(kwargs)
        step = self.script.pop(0) if self.script else _ok(f"ORD-{len(self.sent)}")
        return step(kwargs) if callable(step) else step

    def get_orders(self, user_id, start=None, end=None, *, exchange_codes=None):
        return {"Status": 200, "Success": list(self.book)}


def _armed_rule():
    return repo.arm_rule(
        USER, stock_code=STOCK, expiry_display=EXPIRY, exchange_code="NFO",
        profit_target_pnl=1e9, loss_limit_pnl=1e9, target_premium_pct=10,
        stop_loss_premium_pct=5, legs_snapshot={SHORT_CE["scrip_key"]: 130},
    )


def _fire(monkeypatch, rule, legs, broker):
    monkeypatch.setattr(d, "processor", lambda: broker)
    d._handle_group_rule_hit({
        "user_id": USER, "rule_id": rule.id, "reason": "group_stop_loss_hit",
        "stock_code": STOCK, "expiry_display": EXPIRY, "total_pnl": -5000.0,
        "target_premium_pct": 10, "stop_loss_premium_pct": 5, "legs": legs,
    })
    return repo.get_rule(rule.id)


def _feed(note):
    sg.on_order_notification(note)


def _legs_open(monkeypatch, open_=False):
    monkeypatch.setattr(sg.portfolio_pnl_engine, "group_legs_for_user", lambda *a, **k: [] if not open_ else [object()])


# ------------------------------------------------------------------------------ B-03


def test_own_fill_reported_during_placement_is_not_a_manual_trade(db_path, monkeypatch):
    """Probe 1 from the audit: leg 1's fill arrives while its placement call is still
    running, and leg 2 is throttled. The old code reset the SG and abandoned leg 2."""
    rule = _armed_rule()
    _legs_open(monkeypatch, open_=True)

    def leg1(_):
        _feed(_note("ORD-A", 26000))
        return _ok("ORD-A")

    broker = Broker([leg1, THROTTLED, _ok("ORD-B")])
    after = _fire(monkeypatch, rule, [SHORT_CE, SHORT_PE], broker)

    assert after.status == "fired", after.reset_reason
    assert [r.status for r in after.leg_results] == ["success", "success"]
    assert repo.order_ids_for_rule(after) == {"ORD-A", "ORD-B"}


def test_fills_held_during_placement_complete_the_sg(db_path, monkeypatch):
    rule = _armed_rule()
    _legs_open(monkeypatch)
    import icici_breeze_backend.app.services.processor as proc_mod

    book = [{"order_id": "ORD-A", "status": "Executed"}, {"order_id": "ORD-B", "status": "Executed"}]

    def filled(oid, strike, right):
        def step(_):
            _feed(_note(oid, strike, right))
            return _ok(oid)
        return step

    broker = Broker([filled("ORD-A", 26000, "Call"), filled("ORD-B", 25000, "Put")], book=book)
    monkeypatch.setattr(proc_mod, "processor", lambda: broker)
    after = _fire(monkeypatch, rule, [SHORT_CE, SHORT_PE], broker)

    assert after.status == "completed"


def test_own_rejection_held_during_placement_resets_once_fired(db_path, monkeypatch):
    """Dropped before: `_handle_own_exit_order` ignored anything not `fired`, so a
    rejection reported mid-dispatch left the SG Fired over a dead order forever."""
    rule = _armed_rule()
    _legs_open(monkeypatch, open_=True)

    def rejected(_):
        _feed(_note("ORD-A", 26000, status="Rejected", executedQuantity="0"))
        return _ok("ORD-A")

    after = _fire(monkeypatch, rule, [SHORT_CE], Broker([rejected]))

    assert after.status == "reset"
    assert "rejected" in after.reset_reason
    assert repo.order_ids_for_rule(after) == {"ORD-A"}


def test_users_fill_during_a_throttle_wait_stands_the_dispatch_down(db_path, monkeypatch):
    """The retry alert promises to stop if the user acts. That must still hold now that
    fills are held rather than judged on arrival — and it must stop *every* later send,
    not only the retry that was waiting."""
    rule = _armed_rule()
    _legs_open(monkeypatch, open_=True)

    def throttled_while_user_trades(_):
        _feed(_note("USERS-OWN", 25000, right="Put"))
        return THROTTLED

    broker = Broker([throttled_while_user_trades])
    after = _fire(monkeypatch, rule, [SHORT_CE, SHORT_PE], broker)

    assert len(broker.sent) == 1, "nothing may be sent once the user has traded the group"
    assert after.status == "reset"
    assert "you traded NIFTY 25000 PE" in after.reset_reason
    assert all(r.status == "failed" for r in after.leg_results)
    assert "Not sent" in (after.leg_results[1].error or "")


def test_a_resting_manual_order_does_not_stand_down(db_path, monkeypatch):
    rule = _armed_rule()
    _legs_open(monkeypatch, open_=True)

    def placed_by_user(_):
        _feed(_note("USERS-OWN", 25000, right="Put", status="Ordered", executedQuantity="0"))
        return THROTTLED

    after = _fire(monkeypatch, rule, [SHORT_CE], Broker([placed_by_user, _ok("ORD-A")]))
    assert after.status == "fired"


def test_duplicate_dispatch_sends_nothing(db_path, monkeypatch):
    rule = _armed_rule()
    assert repo.mark_triggered(rule.id)
    broker = Broker([])
    _fire(monkeypatch, rule, [SHORT_CE], broker)
    assert broker.sent == []


def test_final_write_never_overwrites_a_settled_rule(db_path):
    rule = _armed_rule()
    assert repo.mark_triggered(rule.id)
    assert repo.mark_reset(rule.id, "settled elsewhere")
    assert repo.mark_fired(rule.id, []) is False
    assert repo.mark_fire_failed(rule.id, [], "late") is False
    assert repo.get_rule(rule.id).reset_reason == "settled elsewhere"


def test_orders_are_saved_on_the_row_while_they_go_out(db_path, monkeypatch):
    """B-13's other half: the ids are on the row before the dispatch ends."""
    rule = _armed_rule()
    _legs_open(monkeypatch, open_=True)
    seen: list[list[str]] = []

    def second(_):
        seen.append(sorted(repo.order_ids_for_rule(repo.get_rule(rule.id))))
        return _ok("ORD-B")

    _fire(monkeypatch, rule, [SHORT_CE, SHORT_PE], Broker([_ok("ORD-A"), second]))
    assert seen == [["ORD-A"]]


# ----------------------------------------------------------------------- lost answers


def _book_row(order_id, leg, qty=130):
    return {
        "order_id": order_id, "stock_code": STOCK, "right": leg["right"],
        "action": leg["action"], "strike_price": leg["strike_price"], "quantity": str(qty),
        "expiry_date": EXPIRY, "status": "Ordered",
        "order_datetime": datetime.datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S"),
    }


def test_a_lost_answer_found_in_the_book_counts_as_ours(db_path, monkeypatch):
    rule = _armed_rule()
    _legs_open(monkeypatch, open_=True)
    lost = {"Status": 503, "Error": "Service Unavailable", "outcome_unknown": True}
    broker = Broker([lost], book=[_book_row("ORD-FOUND", SHORT_CE)])

    after = _fire(monkeypatch, rule, [SHORT_CE], broker)

    assert len(broker.sent) == 1, "a 503 is never re-sent"
    assert after.status == "fired"
    assert repo.order_ids_for_rule(after) == {"ORD-FOUND"}


def test_every_exit_order_carries_its_own_tag(db_path, monkeypatch):
    rule = _armed_rule()
    _legs_open(monkeypatch, open_=True)
    broker = Broker([])
    _fire(monkeypatch, rule, [SHORT_CE, SHORT_PE], broker)
    tags = [o["user_remark"] for o in broker.sent]
    assert len(set(tags)) == 2
    assert all(len(t) == 8 and t.isalpha() and t.islower() for t in tags)


def test_a_lost_answer_is_found_by_its_tag_among_identical_orders(db_path, monkeypatch):
    """Two orders identical in contract, side, quantity and time — one the user's own.
    Without the tag this was "unknown" and the SG reset; the tag tells them apart."""
    rule = _armed_rule()
    _legs_open(monkeypatch, open_=True)
    broker = Broker([], book=[])

    def lost(kwargs):
        broker.book.extend([
            {**_book_row("USERS-OWN", SHORT_CE), "user_remark": ""},
            {**_book_row("ORD-OURS", SHORT_CE), "user_remark": kwargs["user_remark"]},
        ])
        return {"Status": 503, "Error": "Service Unavailable", "outcome_unknown": True}

    broker.script = [lost]
    after = _fire(monkeypatch, rule, [SHORT_CE], broker)

    assert after.status == "fired"
    assert repo.order_ids_for_rule(after) == {"ORD-OURS"}


def test_an_identical_manual_order_is_not_taken_for_a_lost_exit(db_path, monkeypatch):
    """The exit never went in, but the user placed the same order by hand. The old match
    would have claimed the user's order as ours."""
    rule = _armed_rule()
    _legs_open(monkeypatch, open_=True)
    lost = {"Status": 503, "Error": "Service Unavailable", "outcome_unknown": True}
    broker = Broker([lost], book=[{**_book_row("USERS-OWN", SHORT_CE), "user_remark": ""}])

    after = _fire(monkeypatch, rule, [SHORT_CE], broker)

    assert after.status == "reset"
    assert repo.order_ids_for_rule(after) == set()
    assert "not placed" in after.leg_results[0].error


def test_a_lost_answer_the_book_cannot_settle_resets_and_says_so(db_path, monkeypatch):
    rule = _armed_rule()
    _legs_open(monkeypatch, open_=True)
    lost = {"Status": 502, "Error": "Invalid response", "outcome_unknown": True}
    two = [_book_row("X1", SHORT_CE), _book_row("X2", SHORT_CE)]  # identical: ambiguous

    after = _fire(monkeypatch, rule, [SHORT_CE], Broker([lost], book=two))

    assert after.status == "reset"
    assert "check the Order Book" in after.reset_reason


def test_fill_on_a_contract_whose_answer_was_lost_does_not_stand_down(db_path, monkeypatch):
    """That fill may be the very order whose answer was lost."""
    rule = _armed_rule()
    _legs_open(monkeypatch, open_=True)

    def lost_but_filled(_):
        _feed(_note("UNSEEN", 26000))
        return {"Status": None, "Error": "timeout", "outcome_unknown": True}

    broker = Broker([lost_but_filled, _ok("ORD-B")], book=None)
    broker.get_orders = lambda *a, **k: {"Status": 500, "Error": "down"}
    after = _fire(monkeypatch, rule, [SHORT_CE, SHORT_PE], broker)

    assert len(broker.sent) == 2
    assert after.leg_results[1].status == "success"


# ------------------------------------------------------------------------------ B-14


def test_shorts_are_bought_back_before_wings_are_sold(db_path, monkeypatch):
    rule = _armed_rule()
    _legs_open(monkeypatch, open_=True)
    broker = Broker([])
    _fire(monkeypatch, rule, [LONG_CE, LONG_PE, SHORT_CE, SHORT_PE], broker)
    assert [o["action"] for o in broker.sent] == ["Buy", "Buy", "Sell", "Sell"]


def test_a_wing_is_held_back_when_its_short_could_not_be_bought_back(db_path, monkeypatch):
    rule = _armed_rule()
    _legs_open(monkeypatch, open_=True)
    rejected = {"Status": 400, "Error": "RMS: Margin Exceeds"}
    broker = Broker([rejected, _ok("ORD-PE"), _ok("ORD-PE-WING")])

    after = _fire(monkeypatch, rule, [LONG_CE, SHORT_CE, SHORT_PE, LONG_PE], broker)

    sent = [(o["strike_price"], o["action"]) for o in broker.sent]
    assert ("26500.0", "Sell") not in sent, "selling the CE wing would leave 26000 CE naked"
    assert ("24500.0", "Sell") in sent, "the put side closed cleanly, so its wing goes"
    by_strike = {r.strike_price: r for r in after.leg_results}
    assert by_strike["26500.0"].status == "failed"
    assert "Held back" in by_strike["26500.0"].error


# ------------------------------------------------------------------------------ B-13


def _interrupted(records):
    rule = _armed_rule()
    assert repo.mark_triggered(rule.id)
    if records is not None:
        assert repo.record_exit_orders(rule.id, records)
    return rule


def _record(leg, order_ids, placed, sending=None):
    rec = {
        "scrip_key": leg["scrip_key"], "stock_code": STOCK, "strike_price": leg["strike_price"],
        "right": leg["right"], "quantity": leg["quantity"], "status": "placing", "error": None,
        "order_ids": order_ids, "action": leg["action"], "price": None, "placed_quantity": placed,
    }
    if sending:
        rec["sending"] = sending
    return rec


def test_restart_mid_dispatch_resets_and_keeps_what_was_sent(db_path, monkeypatch, _isolate):
    now = datetime.datetime.now(IST).timestamp()
    rule = _interrupted([
        _record(SHORT_CE, ["ORD-A"], 130),
        _record(SHORT_PE, [], 0, sending={"quantity": 130, "price": 9.5, "sent_at": now}),
        _record(LONG_CE, [], 0),
    ])
    broker = Broker([], book=[_book_row("ORD-LOST", SHORT_PE)])
    monkeypatch.setattr(d, "processor", lambda: broker)
    _legs_open(monkeypatch)

    d._recover_interrupted_dispatch(USER, rule.id)

    after = repo.get_rule(rule.id)
    assert after.status == "reset"
    assert "restarted" in after.reset_reason
    assert repo.order_ids_for_rule(after) == {"ORD-A", "ORD-LOST"}
    assert [r.status for r in after.leg_results] == ["success", "success", "failed"]
    assert _isolate and "restarted" in _isolate[-1], "the user is told on Telegram"


def test_restart_with_no_record_of_orders_says_to_check_the_book(db_path, monkeypatch, _isolate):
    rule = _interrupted(None)
    monkeypatch.setattr(d, "processor", lambda: Broker([]))

    d._recover_interrupted_dispatch(USER, rule.id)

    after = repo.get_rule(rule.id)
    assert after.status == "reset"
    assert "no record" in after.reset_reason


def test_a_triggered_rule_can_be_dismissed_after_recovery(db_path, monkeypatch):
    rule = _interrupted(None)
    monkeypatch.setattr(d, "processor", lambda: Broker([]))
    d._recover_interrupted_dispatch(USER, rule.id)
    assert repo.disarm_rule(USER, rule.id) is True


# ------------------------------------------------------------------------------ B-19


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    from icici_breeze_backend.core import config as cfg

    path = tmp_path / "accounts"
    path.mkdir(parents=True)
    monkeypatch.setattr(cfg, "DATA_PATH", str(path) + "/")
    monkeypatch.setattr(cfg, "USERS_DB", "users.sqlite3")
    with sqlite3.connect(str(path / "users.sqlite3")) as conn:
        conn.execute("CREATE TABLE user_account (user_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO user_account VALUES ('APAM9HcH')")
    monkeypatch.delitem(sys.modules, on._WS_MANAGER_MODULE, raising=False)
    return path


def test_notification_user_id_takes_the_stored_spelling(accounts):
    n = parse_order_notification(executed(userId="APAM9HCH"))
    assert n.user_id == "APAM9HcH"


def test_unknown_user_id_is_kept_as_sent(accounts):
    n = parse_order_notification(executed(userId="SOMEONE"))
    assert n.user_id == "SOMEONE"


def test_first_sighting_of_a_user_id_is_logged_once(accounts, caplog):
    """The live check for B-19: the log bundle shows how ICICI spells the id."""
    import logging

    with caplog.at_level(logging.INFO, logger=on.__name__):
        parse_order_notification(executed(userId="APAM9HCH"))
        parse_order_notification(executed(userId="APAM9HCH"))
    lines = [r.getMessage() for r in caplog.records if "Order feed userId" in r.getMessage()]
    assert len(lines) == 1
    assert "'APAM9HCH'" in lines[0] and "'APAM9HcH'" in lines[0]


def test_socket_user_is_matched_without_a_database(monkeypatch):
    fake = types.ModuleType(on._WS_MANAGER_MODULE)
    fake.current_ws_user_id = lambda: "Vikrammh"
    monkeypatch.setitem(sys.modules, on._WS_MANAGER_MODULE, fake)
    n = parse_order_notification(executed(userId="VIKRAMMH"))
    assert n.user_id == "Vikrammh"


# ------------------------------------------------------------------------------ B-20


class _Raw:
    def __init__(self, status, text):
        self.status_code = status
        self.text = text
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        import json

        return json.loads(self.text)


PLACE_URL = "https://api.icicidirect.com/breezeapi/api/v1/order"


def _send(method, url, responses):
    from icici_breeze_backend.app.core import requests_patch as rp

    calls = []

    def perform():
        calls.append(1)
        return responses[min(len(calls), len(responses)) - 1]

    return rp._run_breeze_request(method, url, perform).json(), len(calls)


def test_a_503_on_placement_is_sent_once_and_marked_unknown(monkeypatch):
    monkeypatch.setattr("icici_breeze_backend.app.services.icici_api_pacing.time.sleep", lambda *_: None)
    body, calls = _send("POST", PLACE_URL, [_Raw(503, "Service Unavailable")])
    assert calls == 1
    assert body["outcome_unknown"] is True


def test_a_503_on_a_read_is_still_retried(monkeypatch):
    monkeypatch.setattr("icici_breeze_backend.app.services.icici_api_pacing.time.sleep", lambda *_: None)
    ok = _Raw(200, '{"Status": 200, "Success": []}')
    body, calls = _send("GET", PLACE_URL, [_Raw(503, "Service Unavailable"), ok])
    assert calls == 2
    assert "outcome_unknown" not in body


def test_a_429_on_placement_is_still_retried(monkeypatch):
    """A throttle is ICICI refusing; re-sending it cannot duplicate (#24)."""
    monkeypatch.setattr("icici_breeze_backend.app.services.icici_api_pacing.time.sleep", lambda *_: None)
    ok = _Raw(200, '{"Status": 200, "Success": {"order_id": "X"}}')
    body, calls = _send("POST", PLACE_URL, [_Raw(429, "Too Many Requests"), ok])
    assert calls == 2
    assert body["Success"]["order_id"] == "X"


@pytest.mark.parametrize("raw", [_Raw(200, "<html>oops</html>"), _Raw(200, "null"), _Raw(504, "Gateway Timeout")])
def test_garbled_or_gateway_answers_to_a_placement_are_unknown(raw):
    body, _ = _send("POST", PLACE_URL, [raw])
    assert body["outcome_unknown"] is True


def test_a_plain_rejection_on_placement_is_not_unknown():
    body, _ = _send("POST", PLACE_URL, [_Raw(200, '{"Status": 500, "Error": "RMS: Margin Exceeds"}')])
    assert "outcome_unknown" not in body


def test_garbled_answers_elsewhere_are_not_tagged():
    body, _ = _send("POST", "https://api.icicidirect.com/breezeapi/api/v1/gttorder", [_Raw(200, "<html>")])
    assert "outcome_unknown" not in body


def test_manual_order_flow_does_not_resend_an_unknown_placement():
    """The browser's chunk loop re-sends whatever the backend calls rate limited."""
    from icici_breeze_backend.app.services.processor import _order_rate_limit_flags

    assert _order_rate_limit_flags({"Status": 503, "Error": "x", "outcome_unknown": True}) == (False, False)
    assert _order_rate_limit_flags({"Status": 429, "Error": "x", "icici_throttled": True})[0] is True
