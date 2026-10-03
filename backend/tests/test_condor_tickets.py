"""Adjust tickets and their execution (docs/dynamic-iron-condor-plan.md sections 4 and 6a).

The broker is scripted per order: fill, partial, refusal, or an answer that says nothing. The
executor runs synchronously here so each outcome can be read straight back from the ledger and
the execution row.
"""
from __future__ import annotations

import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icici_breeze_backend.app.api.v1 import route_condor
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.db.condor_migrate import ensure_condor_tables
from icici_breeze_backend.app.domain.condor import CondorSettings
from icici_breeze_backend.app.repositories import condor as repo
from icici_breeze_backend.app.services.bots import charges as charges_mod
from icici_breeze_backend.app.services.bots.scalping import live as scalp
from icici_breeze_backend.app.services.bots.scalping import momentum_bot
from icici_breeze_backend.app.services.condor import campaigns, executor, live, tickets
from icici_breeze_backend.app.services.condor.orders import net_position, unhedged_rights
from tests.fixtures.condor_chain import EXPIRY, LOT, at, market

EXP = EXPIRY.strftime("%d-%b-%Y")
NEXT = "24-Nov-2026"
SETTINGS = CondorSettings(margin_ceiling_inr=10_00_000)


def _row(strike, right, action, qty, price, expiry=EXP):
    return {"stock_code": "NIFTY", "exchange_code": "NFO", "product_type": "Options", "action": action,
            "quantity": str(qty), "average_price": str(price), "right": right,
            "strike_price": str(strike), "expiry_date": expiry}


CONDOR_ROWS = [
    _row(21550, "Put", "Buy", 65, 15.0),
    _row(23350, "Put", "Sell", 65, 95.0),
    _row(25200, "Call", "Sell", 65, 80.0),
    _row(25850, "Call", "Buy", 65, 12.0),
]


class FakeProc:
    def __init__(self, rows):
        self.rows = rows

    def get_positions(self, user_id):
        return {"Status": 200, "Success": self.rows}

    def fetch_stock_codes(self, exchange):
        return [{"stock_code": "NIFTY", "expiry_dates": ["2026-09-29T06:00:00.000Z", "2026-11-24T06:00:00.000Z"]}]

    def fetch_lot_size(self, stock, expiry, exchange_code=None):
        return LOT

    def get_session_breeze(self, user_id):
        return object()


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "users.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    monkeypatch.setattr(charges_mod, "_db_path", lambda: path)
    monkeypatch.setattr(campaigns, "_sg_armed", lambda user, expiry: False)
    monkeypatch.setattr(tickets, "_margin", lambda *a, **k: None)
    monkeypatch.setattr(tickets, "_liquidity", lambda ctx, o: None)
    ensure_condor_tables(path)
    state = {"spot": 24650.0, "days": 27, "live": True}

    def snapshot(proc, user_id, expiry, held=(), *, now=None):
        m = market(state["spot"], at(state["days"], "15:31"))
        return live.LiveSnapshot(m, state["live"], () if state["live"] else ("NIFTY spot (close)",), "live", True)

    monkeypatch.setattr(live, "snapshot", snapshot)

    def live_quote(proc, user_id, expiry_display, strike, right):
        q = market(state["spot"], at(state["days"], "15:31")).quote(float(strike), "Call" if right == "call" else "Put")
        if q is None or not state["live"]:
            return momentum_bot.Quote(None, None, None)
        return momentum_bot.Quote(q.bid, q.ask, q.ltp, source="websocket")

    monkeypatch.setattr(momentum_bot, "live_quote", live_quote)
    monkeypatch.setattr(scalp, "rest_option_touch", lambda *a, **k: (None, None))

    sent: list = []
    script: dict = {}

    def place_and_confirm(proc, user_id, leg, *, price_for_attempt, timeout_seconds, attempts, **kw):
        n = len(sent) + 1
        sent.append(leg)
        price = round(float(price_for_attempt(0)), 2)
        outcome = script.get(n, "fill")
        if outcome == "fill":
            return scalp.FillResult(order_id=f"OID{n}", filled_quantity=leg.quantity, requested_quantity=leg.quantity, average_price=price)
        if outcome == "partial":
            half = leg.quantity // 2
            return scalp.FillResult(order_id=f"OID{n}", filled_quantity=half, requested_quantity=leg.quantity,
                                    average_price=price, cancelled=True, error="Limit did not fill.")
        if outcome == "unknown":
            return scalp.FillResult(order_id=None, requested_quantity=leg.quantity, outcome_unknown=True,
                                    error="Broker call failed while placing.")
        return scalp.FillResult(requested_quantity=leg.quantity, error="Broker rejected the order.")

    monkeypatch.setattr(scalp, "place_and_confirm", place_and_confirm)
    alerts: list = []
    from icici_breeze_backend.app.services.condor import scheduler

    monkeypatch.setattr(scheduler, "_notify", lambda user, text: alerts.append(text))
    proc = FakeProc(list(CONDOR_ROWS))
    c = campaigns.create(proc, "u1", SETTINGS, expiry=EXP, adopt=True)
    return {"proc": proc, "campaign": c, "state": state, "sent": sent, "script": script, "alerts": alerts}


def _sync(fn):
    fn()


def _fresh(env):
    return repo.get_campaign(env["campaign"].id, "u1")


def _execute(env, orders, kind="blank"):
    out = executor.start(env["proc"], _fresh(env), orders, kind=kind, run=_sync)
    return repo.get_execution(out["id"], env["campaign"].id)


# ---- normalising and templates -----------------------------------------------------------


def test_rows_are_classified_against_the_ledger_and_netted(env):
    ctx = tickets.context(env["proc"], _fresh(env))
    orders = tickets.normalize(ctx, [
        {"action": "Buy", "strike": 23350, "right": "Put", "quantity": 65},     # buys back the short
        {"action": "Sell", "strike": 24300, "right": "Put", "quantity": 130},
        {"action": "Buy", "strike": 24300, "right": "Put", "quantity": 65},     # nets against the row above
    ])
    by = {(o.strike, o.action): o for o in orders}
    assert by[(23350.0, "Buy")].opening is False
    assert by[(24300.0, "Sell")].opening is True and by[(24300.0, "Sell")].quantity == 65


def test_new_positions_must_be_whole_lots(env):
    ctx = tickets.context(env["proc"], _fresh(env))
    with pytest.raises(tickets.Refused, match="whole lots"):
        tickets.normalize(ctx, [{"action": "Sell", "strike": 24000, "right": "Put", "quantity": 50}])


def test_an_odd_quantity_left_by_a_partial_can_always_be_closed(env):
    # The broker and ledger hold 32 of the short put after a partial fill.
    env["proc"].rows = [r for r in env["proc"].rows if r["strike_price"] != "23350"] + [_row(23350, "Put", "Sell", 32, 95.0)]
    repo.add_fills(env["campaign"].id, env["campaign"].cycle.id, [{
        "expiry": EXP, "strike": 23350.0, "right": "Put", "side": "Buy", "quantity": 33, "price": 40.0, "kind": "manual_adjust",
    }])
    ctx = tickets.context(env["proc"], _fresh(env))
    orders = tickets.normalize(ctx, [{"action": "Buy", "strike": 23350, "right": "Put", "quantity": 32}])
    assert orders[0].quantity == 32 and orders[0].opening is False


def test_close_all_template_buys_shorts_back_before_selling_wings(env):
    t = tickets.template(env["proc"], _fresh(env), "close_all")
    p = tickets.preview(env["proc"], _fresh(env), t["orders"], "close_all")
    assert [(o["action"], o["opening"]) for o in p["orders"]] == [("Buy", False), ("Buy", False), ("Sell", False), ("Sell", False)]
    assert p["blocked"] == [] and p["after"]["legs"] == []


def test_roll_selected_moves_the_wing_with_the_short(env):
    t = tickets.template(env["proc"], _fresh(env), "roll_selected",
                         {"legs": [{"strike": 23350, "right": "Put"}], "to_strike": 24300})
    strikes = {(o["strike"], o["action"], o["opening"]) for o in t["orders"]}
    assert (24300.0, "Sell", True) in strikes and (23350.0, "Buy", False) in strikes
    # The 1,800-point put wing follows: 24,300 - 1,800 = 22,500.
    assert (22500.0, "Buy", True) in strikes and (21550.0, "Sell", False) in strikes
    p = tickets.preview(env["proc"], _fresh(env), t["orders"], "roll_selected")
    assert p["blocked"] == []
    assert not any("tested side" in w for w in p["warnings"])
    assert p["credit_points"] > 0


def test_moving_the_tested_side_is_a_warning_not_a_block(env):
    t = tickets.template(env["proc"], _fresh(env), "roll_selected",
                         {"legs": [{"strike": 25200, "right": "Call"}], "to_strike": 25600})
    p = tickets.preview(env["proc"], _fresh(env), t["orders"], "roll_selected")
    assert p["blocked"] == []
    assert any("Moves the tested side (calls)" in w for w in p["warnings"])


def test_iron_fly_template_moves_the_untested_short_to_the_tested_strike(env):
    t = tickets.template(env["proc"], _fresh(env), "iron_fly")
    opened = {(o["strike"], o["right"], o["action"]) for o in t["orders"] if o["opening"]}
    assert (25200.0, "Put", "Sell") in opened
    assert (24550.0, "Put", "Buy") in opened  # the call side's 650-point width


def test_a_naked_short_is_the_one_block(env):
    p = tickets.preview(env["proc"], _fresh(env), [{"action": "Sell", "strike": 25850, "right": "Call", "quantity": 65}], "blank")
    assert p["blocked"] and "short call" in p["blocked"][0]
    with pytest.raises(executor.Refused, match="no long to cap it"):
        executor.start(env["proc"], _fresh(env), [{"action": "Sell", "strike": 25850, "right": "Call", "quantity": 65}], kind="blank", run=_sync)


def test_inversion_is_warned(env):
    orders = [
        {"action": "Buy", "strike": 23350, "right": "Put", "quantity": 65},
        {"action": "Sell", "strike": 25500, "right": "Put", "quantity": 65},
        {"action": "Buy", "strike": 25450, "right": "Put", "quantity": 65},
    ]
    p = tickets.preview(env["proc"], _fresh(env), orders, "blank")
    assert any("Inverts" in w for w in p["warnings"])


def test_time_roll_template_closes_then_opens_the_next_expiry(env):
    t = tickets.template(env["proc"], _fresh(env), "time_roll", {"lots": 2})
    closes = [o for o in t["orders"] if not o["expiry"]]
    opens = [o for o in t["orders"] if o["expiry"] == NEXT]
    assert len(closes) == 4 and all(not o["opening"] for o in closes)
    assert len(opens) == 4 and all(o["quantity"] == 130 for o in opens)
    p = tickets.preview(env["proc"], _fresh(env), t["orders"], "time_roll")
    assert p["after"]["expiry"] == NEXT and len(p["after"]["legs"]) == 4


def test_add_tranche_template_sizes_by_lots(env):
    t = tickets.template(env["proc"], _fresh(env), "add_tranche", {"lots": 3})
    assert len(t["orders"]) == 4 and {o["quantity"] for o in t["orders"]} == {195}


def test_stand_in_prices_are_said_in_the_preview(env):
    env["state"]["live"] = False
    t = tickets.template(env["proc"], _fresh(env), "close_all")
    p = tickets.preview(env["proc"], _fresh(env), t["orders"], "close_all")
    assert p["indicative"] and any("stand-ins" in w for w in p["warnings"])


# ---- executing ---------------------------------------------------------------------------


def _roll_orders(env):
    return tickets.template(env["proc"], _fresh(env), "roll_selected",
                            {"legs": [{"strike": 23350, "right": "Put"}], "to_strike": 24300})["orders"]


def test_a_roll_executes_in_the_safe_order_and_books_every_fill(env):
    ex = _execute(env, _roll_orders(env), "roll_selected")
    assert ex["status"] == "completed"
    assert [(s["action"], s["opening"]) for s in ex["steps"]] == [("Buy", True), ("Buy", False), ("Sell", True), ("Sell", False)]
    fills = [f for f in repo.list_fills(env["campaign"].id) if f.kind == "manual_adjust"]
    assert [f.order_id for f in fills] == ["OID1", "OID2", "OID3", "OID4"]
    pos = campaigns.ledger_position(repo.list_fills(env["campaign"].id), EXP)
    assert pos[(24300.0, "Put")][0] == -65 and (23350.0, "Put") not in pos
    assert repo.list_decisions(env["campaign"].id)[0]["check_kind"] == "manual"


def test_a_failed_step_stops_the_ticket_with_the_position_still_hedged(env):
    env["script"][3] = "reject"  # the new short is refused
    ex = _execute(env, _roll_orders(env), "roll_selected")
    assert ex["status"] == "stopped"
    assert [s["status"] for s in ex["steps"]] == ["filled", "filled", "failed", "pending"]
    assert len(env["sent"]) == 3
    pos = {k: v[0] for k, v in campaigns.ledger_position(repo.list_fills(env["campaign"].id), EXP).items()}
    assert not unhedged_rights(pos)
    assert env["alerts"] and "every short still has its wing" in env["alerts"][-1]


def test_a_partial_books_what_filled_and_stops(env):
    env["script"][2] = "partial"
    ex = _execute(env, _roll_orders(env), "roll_selected")
    assert ex["steps"][1]["status"] == "partial" and ex["steps"][1]["filled"] == 32
    booked = [f for f in repo.list_fills(env["campaign"].id) if f.order_id == "OID2"]
    assert booked[0].quantity == 32


def test_an_unknown_outcome_stops_everything_and_alerts(env):
    env["script"][1] = "unknown"
    ex = _execute(env, _roll_orders(env), "roll_selected")
    assert ex["status"] == "unaccounted" and len(env["sent"]) == 1
    assert "Order Book" in env["alerts"][-1]


def test_nothing_opens_on_a_stand_in_price(env):
    env["state"]["live"] = False
    ex = _execute(env, _roll_orders(env), "roll_selected")
    assert ex["steps"][0]["status"] == "not_sent" and env["sent"] == []


def test_a_close_still_goes_out_on_a_rest_quote(env, monkeypatch):
    env["state"]["live"] = False
    monkeypatch.setattr(scalp, "rest_option_touch", lambda *a, **k: (10.0, 11.0))
    t = tickets.template(env["proc"], _fresh(env), "close_all")
    ex = _execute(env, t["orders"], "close_all")
    assert ex["status"] == "completed" and len(env["sent"]) == 4


def test_closing_everything_ends_the_campaign(env):
    t = tickets.template(env["proc"], _fresh(env), "close_all")
    _execute(env, t["orders"], "close_all")
    c = _fresh(env)
    assert c.status == "closed" and c.close_reason == "close_all"


def test_a_time_roll_opens_the_next_cycle_in_the_same_campaign(env):
    t = tickets.template(env["proc"], _fresh(env), "time_roll", {"lots": 1})
    ex = _execute(env, t["orders"], "time_roll")
    assert ex["status"] == "completed"
    c = _fresh(env)
    assert c.status == "active" and c.cycle.expiry == NEXT
    assert [y.close_reason for y in c.cycles if y.closed_at] == ["time_roll"]
    assert campaigns.ledger_position(repo.list_fills(c.id), NEXT)
    assert c.cycle.tranches_entered == 1


def test_one_ticket_at_a_time_per_campaign(env):
    queued = []
    executor.start(env["proc"], _fresh(env), _roll_orders(env), kind="roll_selected", run=queued.append)
    with pytest.raises(executor.Refused, match="already executing"):
        executor.start(env["proc"], _fresh(env), _roll_orders(env), kind="roll_selected", run=_sync)
    queued[0]()


def test_an_interrupted_execution_is_marked_at_startup(env):
    eid = repo.create_execution(env["campaign"].id, "blank", [])
    assert repo.fail_interrupted_executions() == 1
    assert repo.get_execution(eid, env["campaign"].id)["status"] == "interrupted"


# ---- routes ------------------------------------------------------------------------------


@pytest.fixture
def client(env, monkeypatch):
    monkeypatch.setattr(route_condor, "_proc", lambda: env["proc"])
    real_start = executor.start
    monkeypatch.setattr(executor, "start", lambda *a, **k: real_start(*a, **{**k, "run": _sync}))
    app = FastAPI()
    app.include_router(route_condor.router, prefix="/api/condor")
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user_id="u1", username="u1", roles=[], is_authenticated=True
    )
    return TestClient(app)


def test_read_only_mode_blocks_opening_but_not_closing(env, client, monkeypatch):
    from icici_breeze_backend.app.api import deps_license

    monkeypatch.setattr(deps_license, "trading_mutations_allowed", lambda: False)
    cid = env["campaign"].id
    roll = client.post(f"/api/condor/campaigns/{cid}/ticket/template", json={"kind": "roll_selected",
                       "params": {"legs": [{"strike": 23350, "right": "Put"}], "to_strike": 24300}}).json()
    r = client.post(f"/api/condor/campaigns/{cid}/ticket/execute", json={"kind": "roll_selected", "orders": roll["orders"]})
    assert r.status_code == 403
    close = client.post(f"/api/condor/campaigns/{cid}/ticket/template", json={"kind": "close_all"}).json()
    r = client.post(f"/api/condor/campaigns/{cid}/ticket/execute", json={"kind": "close_all", "orders": close["orders"]})
    assert r.status_code == 200 and r.json()["status"] == "completed"
    assert client.get(f"/api/condor/campaigns/{cid}/executions").json()["executions"][0]["kind"] == "close_all"


def test_preview_and_template_routes(env, client):
    cid = env["campaign"].id
    t = client.post(f"/api/condor/campaigns/{cid}/ticket/template", json={"kind": "iron_fly"})
    assert t.status_code == 200
    p = client.post(f"/api/condor/campaigns/{cid}/ticket/preview", json={"kind": "iron_fly", "orders": t.json()["orders"]})
    assert p.status_code == 200 and p.json()["blocked"] == []
    bad = client.post(f"/api/condor/campaigns/{cid}/ticket/template", json={"kind": "nope"})
    assert bad.status_code == 409


def test_a_suggestion_is_booked_by_what_it_does(env, client):
    cid = env["campaign"].id
    t = client.post(f"/api/condor/campaigns/{cid}/ticket/template", json={"kind": "suggested"}).json()
    assert t["orders"], t  # at 24,650 the engine rolls the put side
    ex = client.post(f"/api/condor/campaigns/{cid}/ticket/execute", json={"kind": "suggested", "orders": t["orders"]}).json()
    assert ex["kind"] == "suggested_roll"
    kinds = {f.kind for f in repo.list_fills(cid)} - {"adopted"}
    assert kinds == {"roll_open", "roll_close"}


def test_a_stop_never_claims_a_hedge_the_position_does_not_have(env):
    # Start from a group whose short call has no wing, then fail the first close.
    env["proc"].rows = [r for r in env["proc"].rows if r["strike_price"] != "25850"]
    repo.add_fills(env["campaign"].id, env["campaign"].cycle.id, [{
        "expiry": EXP, "strike": 25850.0, "right": "Call", "side": "Sell", "quantity": 65, "price": 12.0, "kind": "assigned",
    }])
    env["script"][1] = "reject"
    t = tickets.template(env["proc"], _fresh(env), "close_all")
    ex = _execute(env, t["orders"], "close_all")
    assert "short with no wing" in ex["message"] and ".." not in ex["message"]
