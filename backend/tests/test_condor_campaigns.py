"""Condor campaigns: the ledger, its agreement with the broker, live evaluation, the routes,
and the PB/SL exclusivity (docs/dynamic-iron-condor-plan.md sections 2 and 6)."""
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
from icici_breeze_backend.app.services.condor import campaigns, live
from icici_breeze_backend.app.services.condor.engine import decide
from icici_breeze_backend.app.services.condor.model import CampaignState
from tests.fixtures.condor_chain import CHARGES, EXPIRY, LOT, at, market

EXP = EXPIRY.strftime("%d-%b-%Y")
SETTINGS = CondorSettings(margin_ceiling_inr=10_00_000)


class FakeProc:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.available = True

    def get_positions(self, user_id):
        if not self.available:
            return {"Status": 500, "Error": "down", "Success": None}
        return {"Status": 200, "Success": self.rows}

    def fetch_stock_codes(self, exchange):
        return [{"stock_code": "NIFTY", "expiry_dates": ["2026-09-29T06:00:00.000Z", "2026-10-27T06:00:00.000Z"]}]

    def fetch_lot_size(self, stock, expiry, exchange_code=None):
        return LOT


def _row(strike, right, action, qty, price, expiry=EXP):
    return {
        "stock_code": "NIFTY", "exchange_code": "NFO", "product_type": "Options", "action": action,
        "quantity": str(qty), "average_price": str(price), "right": right,
        "strike_price": str(strike), "expiry_date": expiry,
    }


CONDOR_ROWS = [
    _row(21550, "Put", "Buy", 65, 15.0),
    _row(23350, "Put", "Sell", 65, 95.0),
    _row(25200, "Call", "Sell", 65, 80.0),
    _row(25850, "Call", "Buy", 65, 12.0),
]


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "users.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    monkeypatch.setattr(charges_mod, "_db_path", lambda: path)
    monkeypatch.setattr(campaigns, "_sg_armed", lambda user, expiry: False)
    ensure_condor_tables(path)
    return path


@pytest.fixture
def snap(monkeypatch):
    """The live snapshot: a synthetic chain, live unless told otherwise."""
    state = {"spot": 24200.0, "days": 40, "live": True}

    def fake(proc, user_id, expiry, held=(), *, now=None):
        m = market(state["spot"], at(state["days"], "15:31"))
        stand = () if state["live"] else ("NIFTY spot (close)",)
        return live.LiveSnapshot(market=m, live=state["live"], stand_ins=stand, spot_source="live", chain_ready=True)

    monkeypatch.setattr(live, "snapshot", fake)
    return state


# ---- the ledger --------------------------------------------------------------------------


def _fill(side, qty, price, strike=24000.0, right="Call", kind="entry"):
    return repo.Fill(0, "c", 1, None, EXP, strike, right, side, qty, price, 0.0, kind, "t", None)


def test_ledger_position_tracks_average_and_partial_closes():
    fills = [_fill("Sell", 65, 100), _fill("Sell", 65, 120), _fill("Buy", 65, 50)]
    pos = campaigns.ledger_position(fills, EXP)
    assert pos[(24000.0, "Call")] == (-65, pytest.approx(110))
    assert campaigns.ledger_cash(fills) == pytest.approx(65 * (100 + 120 - 50))


def test_ledger_position_crossing_zero_restarts_the_average():
    fills = [_fill("Sell", 65, 100), _fill("Buy", 130, 40)]
    assert campaigns.ledger_position(fills, EXP)[(24000.0, "Call")] == (65, 40)


def test_flat_contracts_drop_out_of_the_position():
    fills = [_fill("Sell", 65, 100), _fill("Buy", 65, 40)]
    assert campaigns.ledger_position(fills, EXP) == {}


def test_reconcile_and_what_may_be_left_out():
    ledger = {(23350.0, "Put"): (-65, 95.0)}
    broker = {(23350.0, "Put"): (-130, 96.0), (24000.0, "Call"): (65, 10.0)}
    diffs = {(d.strike, d.right): d for d in campaigns.reconcile(ledger, broker, {})}
    assert diffs[(23350.0, "Put")].units == -65 and diffs[(23350.0, "Put")].can_leave_out
    assert diffs[(24000.0, "Call")].can_leave_out
    gone = campaigns.reconcile(ledger, {}, {})[0]
    assert gone.units == 65 and not gone.can_leave_out


# ---- creating ----------------------------------------------------------------------------


def test_adopt_books_the_groups_legs_at_broker_prices(db):
    proc = FakeProc(CONDOR_ROWS)
    c = campaigns.create(proc, "u1", SETTINGS, expiry=EXP, adopt=True, charges=CHARGES)
    fills = repo.list_fills(c.id)
    assert {(f.strike, f.right, f.side, f.kind) for f in fills} == {
        (21550.0, "Put", "Buy", "adopted"), (23350.0, "Put", "Sell", "adopted"),
        (25200.0, "Call", "Sell", "adopted"), (25850.0, "Call", "Buy", "adopted"),
    }
    credit = 65 * (95 + 80 - 15 - 12)
    assert campaigns.ledger_cash(fills) == pytest.approx(credit - sum(f.charges for f in fills))
    assert c.cycle.tranches_entered == 1


def test_one_campaign_per_group(db):
    proc = FakeProc(CONDOR_ROWS)
    campaigns.create(proc, "u1", SETTINGS, expiry=EXP, adopt=True)
    with pytest.raises(campaigns.Refused, match="already manages"):
        campaigns.create(proc, "u1", SETTINGS, expiry=EXP)


def test_adopting_an_empty_group_is_refused(db):
    with pytest.raises(campaigns.Refused, match="No open NIFTY"):
        campaigns.create(FakeProc([]), "u1", SETTINGS, expiry=EXP, adopt=True)


def test_new_empty_campaign_picks_the_cycle_expiry(db):
    c = campaigns.create(FakeProc(), "u1", SETTINGS, today=datetime.date(2026, 8, 20))
    # 29-Sep is 40 days out (>= the 30-day cut-off) and the earliest monthly that qualifies.
    assert c.cycle.expiry == "29-Sep-2026"


def test_armed_pbsl_blocks_a_campaign(db, monkeypatch):
    monkeypatch.setattr(campaigns, "_sg_armed", lambda user, expiry: True)
    with pytest.raises(campaigns.Refused, match="PB/SL"):
        campaigns.create(FakeProc(CONDOR_ROWS), "u1", SETTINGS, expiry=EXP, adopt=True)


def test_an_active_campaign_blocks_arming_pbsl(db):
    from icici_breeze_backend.app.services.strategy_group_arm_guard import ArmPreconditionError, assert_can_arm

    campaigns.create(FakeProc(CONDOR_ROWS), "u1", SETTINGS, expiry=EXP, adopt=True)
    with pytest.raises(ArmPreconditionError, match="Dynamic Iron Condor"):
        assert_can_arm(None, "u1", "NIFTY", EXP)


# ---- evaluating --------------------------------------------------------------------------


def test_evaluation_matches_the_engine_on_the_ledger(db, snap):
    proc = FakeProc(CONDOR_ROWS)
    c = campaigns.create(proc, "u1", SETTINGS, expiry=EXP, adopt=True, charges=CHARGES)
    out = campaigns.evaluate(proc, c, "eod", charges=CHARGES)
    assert out["differences"] == [] and not out["indicative"]
    legs = campaigns.legs_from(campaigns.ledger_position(repo.list_fills(c.id), EXP))
    direct = decide(
        CampaignState(EXPIRY, LOT, legs, out["ledger"]["cash_inr"], 1),
        market(24200.0, at(40, "15:31")), SETTINGS, "eod", CHARGES,
    )
    assert (out["decision"]["action"], out["decision"]["reason"]) == (direct.action, direct.reason)
    assert out["decision"]["metrics"]["campaign_pnl_inr"] == pytest.approx(direct.metrics.campaign_pnl_inr, abs=1)


def test_a_trade_outside_the_campaign_blocks_decisions_until_settled(db, snap):
    proc = FakeProc(CONDOR_ROWS)
    c = campaigns.create(proc, "u1", SETTINGS, expiry=EXP, adopt=True)
    proc.rows = CONDOR_ROWS + [_row(24000, "Call", "Buy", 65, 210.0)]
    out = campaigns.evaluate(proc, c, "eod")
    assert out["decision"]["reason"] == "ledger_mismatch"
    campaigns.leave_out(proc, c, 24000.0, "Call")
    out = campaigns.evaluate(proc, c, "eod")
    assert out["decision"]["reason"] != "ledger_mismatch"
    assert out["differences"][0]["left_out"] is True


def test_a_leg_closed_outside_must_be_assigned(db, snap):
    proc = FakeProc(CONDOR_ROWS)
    c = campaigns.create(proc, "u1", SETTINGS, expiry=EXP, adopt=True)
    proc.rows = [r for r in CONDOR_ROWS if r["strike_price"] != "25850"]
    with pytest.raises(campaigns.Refused, match="cannot be left out"):
        campaigns.leave_out(proc, c, 25850.0, "Call")
    campaigns.assign(proc, c, 25850.0, "Call", 4.0)
    fills = repo.list_fills(c.id)
    assigned = [f for f in fills if f.kind == "assigned"]
    assert len(assigned) == 1 and assigned[0].side == "Sell" and assigned[0].price == 4.0
    assert campaigns.evaluate(proc, c, "eod")["differences"] == []


def test_stand_in_prices_are_indicative_on_demand_and_unavailable_on_schedule(db, snap):
    proc = FakeProc(CONDOR_ROWS)
    c = campaigns.create(proc, "u1", SETTINGS, expiry=EXP, adopt=True)
    snap["live"] = False
    on_demand = campaigns.evaluate(proc, c, "on_demand")
    assert on_demand["indicative"] is True
    assert on_demand["decision"]["action"] != "unavailable"
    scheduled = campaigns.evaluate(proc, c, "eod")
    assert (scheduled["decision"]["action"], scheduled["decision"]["reason"]) == ("unavailable", "feed_down")
    assert "stand-in" in scheduled["decision"]["text"]


def test_unreadable_positions_are_unavailable(db, snap):
    proc = FakeProc(CONDOR_ROWS)
    c = campaigns.create(proc, "u1", SETTINGS, expiry=EXP, adopt=True)
    proc.available = False
    assert campaigns.evaluate(proc, c, "eod")["decision"]["reason"] == "positions_unreadable"


# ---- routes ------------------------------------------------------------------------------


@pytest.fixture
def client(db, snap, monkeypatch):
    proc = FakeProc(CONDOR_ROWS)
    monkeypatch.setattr(route_condor, "_proc", lambda: proc)
    app = FastAPI()
    app.include_router(route_condor.router, prefix="/api/condor")
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user_id="u1", username="u1", roles=[], is_authenticated=True
    )
    return TestClient(app), proc


def test_campaign_routes_end_to_end(client):
    http, proc = client
    r = http.post("/api/condor/campaigns", json={"settings": {"margin_ceiling_inr": 10_00_000}, "expiry": EXP, "adopt": True})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    assert r.json()["cycle"]["expiry"] == EXP

    again = http.post("/api/condor/campaigns", json={"settings": {"margin_ceiling_inr": 10_00_000}, "expiry": EXP})
    assert again.status_code == 409

    listed = http.get("/api/condor/campaigns").json()["campaigns"]
    assert [c["id"] for c in listed] == [cid]

    ev = http.post(f"/api/condor/campaigns/{cid}/evaluate").json()
    assert ev["check_kind"] == "on_demand" and "decision" in ev

    detail = http.get(f"/api/condor/campaigns/{cid}").json()
    assert len(detail["fills"]) == 4 and detail["decisions"] == []

    bad = http.put(f"/api/condor/campaigns/{cid}/settings", json={"margin_ceiling_inr": 1, "wing_width_pct": 0})
    assert bad.status_code == 400
    ok = http.put(f"/api/condor/campaigns/{cid}/settings", json={"margin_ceiling_inr": 5_00_000, "tranches": 2})
    assert ok.json()["settings"]["tranches"] == 2

    proc.rows = CONDOR_ROWS + [_row(24000, "Call", "Buy", 65, 210.0)]
    assert http.post(f"/api/condor/campaigns/{cid}/leave-out", json={"strike": 24000, "right": "Call"}).status_code == 200
    assert http.post(f"/api/condor/campaigns/{cid}/assign", json={"strike": 23350, "right": "Put", "price": 1}).status_code == 409

    closed = http.post(f"/api/condor/campaigns/{cid}/close", json={}).json()
    assert closed["status"] == "closed" and closed["close_reason"] == "closed_by_user"
    assert http.get("/api/condor/campaigns").json()["campaigns"] == []
    assert http.get("/api/condor/campaigns/nope").status_code == 404


# ---- scheduled checks ---------------------------------------------------------------------

from icici_breeze_backend.app.services.condor import scheduler  # noqa: E402

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


class SessionProc(FakeProc):
    def __init__(self, rows=None, session=True):
        super().__init__(rows)
        self.session = session

    def get_session_breeze(self, user_id):
        return object() if self.session else None


@pytest.fixture
def sched(db, snap, monkeypatch):
    from icici_breeze_backend.app.services import market_calendar

    monkeypatch.setattr(market_calendar, "is_trading_day", lambda now=None: now.weekday() < 5)
    sent = []
    monkeypatch.setattr(scheduler, "_notify", lambda user, text: sent.append(text))
    warmed = []
    real_snapshot = live.snapshot
    monkeypatch.setattr(live, "snapshot", lambda *a, **k: warmed.append(a[2]) or real_snapshot(*a, **k))
    scheduler._warmed.clear()
    return sent, warmed


def _t(hhmm, day=datetime.date(2026, 8, 20)):
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.datetime.combine(day, datetime.time(h, m), tzinfo=IST)


def test_each_check_runs_once_inside_its_window(sched):
    sent, warmed = sched
    proc = SessionProc(CONDOR_ROWS)
    c = campaigns.create(proc, "u1", SETTINGS, expiry=EXP, adopt=True)
    scheduler.tick(proc, _t("10:27"))
    assert warmed == [EXP] and repo.list_decisions(c.id) == []
    scheduler.tick(proc, _t("10:30"))
    scheduler.tick(proc, _t("10:31"))
    scheduler.tick(proc, _t("15:31"))
    kinds = sorted(d["check_kind"] for d in repo.list_decisions(c.id))
    assert kinds == ["eod", "sod"]
    assert repo.last_scheduled_decision(c.id)["check_kind"] == "eod"


def test_a_missed_check_is_not_run_late(sched):
    proc = SessionProc(CONDOR_ROWS)
    c = campaigns.create(proc, "u1", SETTINGS, expiry=EXP, adopt=True)
    scheduler.tick(proc, _t("11:15"))
    assert repo.list_decisions(c.id) == []


def test_no_checks_on_a_holiday(sched):
    proc = SessionProc(CONDOR_ROWS)
    c = campaigns.create(proc, "u1", SETTINGS, expiry=EXP, adopt=True)
    scheduler.tick(proc, _t("10:30", datetime.date(2026, 8, 22)))  # a Saturday
    assert repo.list_decisions(c.id) == []


def test_alerts_only_when_there_is_something_to_do(sched, snap):
    sent, _ = sched
    proc = SessionProc(CONDOR_ROWS)
    c = campaigns.create(proc, "u1", SETTINGS, expiry=EXP, adopt=True)
    snap["spot"], snap["days"] = 24650.0, 27
    out = scheduler.run_check(proc, c, "eod")
    assert out["decision"]["action"] == "roll_untested"
    assert sent and "Review it on the Portfolio page" in sent[-1]
    assert repo.list_decisions(c.id)[0]["outcome"] == "suggested"
    sent.clear()
    snap["spot"], snap["days"] = 24200.0, 40
    assert scheduler.run_check(proc, c, "eod")["decision"]["action"] == "no_action"
    assert sent == []


def test_no_session_is_recorded_and_alerted(sched):
    sent, _ = sched
    proc = SessionProc(CONDOR_ROWS, session=False)
    c = campaigns.create(proc, "u1", SETTINGS, expiry=EXP, adopt=True)
    scheduler.run_check(proc, c, "sod")
    assert repo.list_decisions(c.id)[0]["reason"] == "no_session"
    assert "Could not decide" in sent[-1]


def test_indicative_evaluation_prices_a_bookless_chain_at_ltp():
    from icici_breeze_backend.app.services.condor.pricing import ChainRow, Quote

    m = market(24200.0, at(40, "15:31"))
    bare = m.__class__(now=m.now, spot=m.spot, spot_live=True, feeds_ok=True, chain=tuple(
        ChainRow(r.strike, call=Quote(ltp=r.call.ltp), put=Quote(ltp=r.put.ltp)) for r in m.chain
    ))
    fixed = live.with_ltp_stand_ins(bare)
    q = fixed.quote(24200.0, "Call")
    assert q.bid == q.ask == q.ltp > 0
    # A real two-sided book is left alone.
    assert live.with_ltp_stand_ins(m).quote(24200.0, "Call") == m.quote(24200.0, "Call")
