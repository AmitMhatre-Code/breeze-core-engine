"""The Dynamic Iron Condor bot (docs/dynamic-iron-condor-plan.md section 7): the mode gates,
paper fills, Telegram approval, autonomous execution, pausing and handing a campaign back."""
from __future__ import annotations

import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.db.bots_migrate import BOT_DYNAMIC_CONDOR, ensure_bots_tables
from icici_breeze_backend.app.db.condor_migrate import ensure_condor_tables
from icici_breeze_backend.app.domain.condor import DynamicCondorBotConfig
from icici_breeze_backend.app.repositories import bots as bots_repo
from icici_breeze_backend.app.repositories import condor as repo
from icici_breeze_backend.app.services.bots import charges as charges_mod
from icici_breeze_backend.app.services.bots import hitl
from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
from icici_breeze_backend.app.services.bots.scalping import live as scalp
from icici_breeze_backend.app.services.bots.scalping import momentum_bot
from icici_breeze_backend.app.services.condor import bot, campaigns, executor, live, scheduler, tickets
from tests.fixtures.condor_chain import EXPIRY, LOT, at, market

EXP = EXPIRY.strftime("%d-%b-%Y")
U = "u1"


class FakeProc:
    def __init__(self, rows=None):
        self.rows = rows or []

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
    users = str(tmp_path / "users.sqlite3")
    cache = str(tmp_path / "backtest.sqlite3")
    for mod in (repo, bots_repo, charges_mod):
        monkeypatch.setattr(mod, "_db_path", lambda: users)
    monkeypatch.setattr(store, "db_path", lambda: cache)
    ensure_bots_tables(users)
    ensure_condor_tables(users)
    store.ensure_tables(cache)
    monkeypatch.setattr(campaigns, "_sg_armed", lambda user, expiry: False)
    monkeypatch.setattr(tickets, "_margin", lambda *a, **k: None)
    monkeypatch.setattr(tickets, "_liquidity", lambda ctx, o: None)
    state = {"spot": 24200.0, "days": 45, "live": True}

    def snapshot(proc, user_id, expiry, held=(), *, now=None):
        m = market(state["spot"], at(state["days"], "10:30"))
        return live.LiveSnapshot(m, state["live"], (), "live", True)

    monkeypatch.setattr(live, "snapshot", snapshot)

    def live_quote(proc, user_id, expiry_display, strike, right):
        q = market(state["spot"], at(state["days"], "10:30")).quote(float(strike), "Call" if right == "call" else "Put")
        return momentum_bot.Quote(q.bid, q.ask, q.ltp, source="websocket") if q else momentum_bot.Quote(None, None, None)

    monkeypatch.setattr(momentum_bot, "live_quote", live_quote)
    sent: list = []

    def place_and_confirm(proc, user_id, leg, *, price_for_attempt, timeout_seconds, attempts, **kw):
        sent.append(leg)
        return scalp.FillResult(order_id=f"OID{len(sent)}", filled_quantity=leg.quantity,
                                requested_quantity=leg.quantity, average_price=round(price_for_attempt(0), 2))

    monkeypatch.setattr(scalp, "place_and_confirm", place_and_confirm)
    alerts: list = []
    monkeypatch.setattr(scheduler, "_notify", lambda user, text: alerts.append(text))
    telegram: list = []
    monkeypatch.setattr(hitl, "_reachable", lambda user_id: "CHAT1")
    monkeypatch.setattr(hitl, "trading_allowed", lambda: True)
    from icici_breeze_backend.app.services import telegram_client, telegram_link_portal

    monkeypatch.setattr(telegram_client, "send_message_get_id", lambda chat, text, reply_markup=None: telegram.append((text, reply_markup)) or 101)
    monkeypatch.setattr(telegram_client, "send_message_sync", lambda chat, text, **k: telegram.append((text, None)))
    monkeypatch.setattr(telegram_link_portal, "register_approval_token", lambda token, ttl: True)
    monkeypatch.setattr(hitl, "_retire", lambda ask, footer: True)
    monkeypatch.setattr(hitl, "_edit_ask", lambda ask, footer: None)
    # The executor's own thread is replaced by an inline run, so each test reads the result.
    real_start = executor.start
    monkeypatch.setattr(executor, "start", lambda *a, **k: real_start(*a, **{**k, "run": lambda fn: fn()}))
    return {"proc": FakeProc(), "state": state, "sent": sent, "alerts": alerts, "telegram": telegram}


def _configure(**over) -> DynamicCondorBotConfig:
    cfg = DynamicCondorBotConfig(lots_per_tranche=2, **over)
    bots_repo.get_or_create_bot(U, BOT_DYNAMIC_CONDOR)
    bots_repo.update_bot(U, BOT_DYNAMIC_CONDOR, config=cfg.model_dump(mode="json"))
    return cfg


def _backtest(cfg: DynamicCondorBotConfig, status: str = "completed", *, engine_version=None,
              created_at: str = "2026-10-01T18:00:00", run_id: str | None = None) -> None:
    from icici_breeze_backend.app.services.condor.engine import ENGINE_VERSION

    params = {"settings": cfg.campaign.model_dump(mode="json"), "exit_action": cfg.exit_action,
              "from": "2026-01-05", "to": "2026-09-30"}
    if engine_version != "absent":
        params["engine_version"] = ENGINE_VERSION if engine_version is None else engine_version
    store.save_run({
        "id": run_id or f"run-{status}-{cfg.exit_action}-{engine_version}", "user_id": U, "bot": "condor",
        "created_at": created_at, "status": status, "params": params,
        "summary": {"closed_pnl": 1000.0, "max_drawdown": 500.0}, "trades": [],
    })


def _enable():
    bots_repo.update_bot(U, BOT_DYNAMIC_CONDOR, enabled=True)
    return bots_repo.get_or_create_bot(U, BOT_DYNAMIC_CONDOR)


def _campaign(cfg, mode):
    return repo.create_campaign(U, cfg.campaign, expiry=EXP, origin="bot", mode=mode, note=f"bot:{bot.hash_of(cfg)}")


# ---- gates -------------------------------------------------------------------------------


def test_simulation_is_open_from_day_one_without_a_backtest(env):
    # Like every other bot (#70): a backtest is shown beside the evidence, never required.
    cfg = _configure()
    assert bot.eligibility(U, cfg).backtest is None
    bot.guard(U, after_enabled=True, after_config=cfg.model_dump(mode="json"))
    changed = cfg.model_copy(update={"exit_action": "close"})
    bot.guard(U, after_enabled=True, after_config=changed.model_dump(mode="json"))
    # Saving while switched off is always allowed, whatever the mode.
    bot.guard(U, after_enabled=False, after_config={**changed.model_dump(mode="json"), "mode": "auto"})


def test_semi_auto_needs_a_simulation_cycle_and_auto_an_approved_ticket(env):
    cfg = _configure()  # no backtest: the ladder alone gates Semi-auto and Auto
    with pytest.raises(bot.Refused, match="Simulation cycle"):
        bot.guard(U, after_enabled=True, after_config={**cfg.model_dump(mode="json"), "mode": "telegram"})
    paper = _campaign(cfg, "paper")
    repo.bump_tranches(paper.cycle.id)
    repo.close_cycle(paper.cycle.id, "exit_dte")
    bot.guard(U, after_enabled=True, after_config={**cfg.model_dump(mode="json"), "mode": "telegram"})
    with pytest.raises(bot.Refused, match="approved in Semi-auto"):
        bot.guard(U, after_enabled=True, after_config={**cfg.model_dump(mode="json"), "mode": "auto"})
    assert bot.eligibility(U, cfg).as_dict()["may_telegram"] is True


# ---- the campaign ------------------------------------------------------------------------


def test_an_enabled_bot_opens_one_paper_campaign_that_owns_no_group(env):
    cfg = _configure()
    _backtest(cfg)
    record = _enable()
    c = bot.ensure_campaign(env["proc"], U, record, datetime.date(2026, 8, 20))
    assert (c.origin, c.mode, c.cycle.expiry) == ("bot", "paper", EXP)
    assert bot.ensure_campaign(env["proc"], U, record, datetime.date(2026, 8, 20)).id == c.id
    assert repo.active_owner(U, "NIFTY", EXP) is None


def test_paper_enters_a_tranche_on_paper_and_places_nothing(env):
    cfg = _configure()
    _backtest(cfg)
    _enable()
    c = _campaign(cfg, "paper")
    out = scheduler.run_check(env["proc"], c, "sod", datetime.datetime(2026, 8, 15, 10, 30, tzinfo=live.IST))
    assert out["decision"]["action"] == "enter_tranche"
    assert out["bot"] == "paper_completed"
    fills = repo.list_fills(c.id)
    assert len(fills) == 4 and {f.note for f in fills} == {"paper"} and {f.quantity for f in fills} == {2 * LOT}
    assert env["sent"] == []
    assert repo.get_campaign(c.id, U).cycle.tranches_entered == 1
    assert any("Nothing was placed" in a for a in env["alerts"])


def test_the_book_shrinks_an_entry(env, monkeypatch):
    from icici_breeze_backend.app.services.liquidity import check as liq

    class V:
        def __init__(self, ok):
            self.ok = ok

    monkeypatch.setattr(liq, "check_contract", lambda *a, **k: V(a[6] <= LOT))  # one lot fits, two do not
    cfg = _configure()
    _backtest(cfg)
    _enable()
    c = _campaign(cfg, "paper")
    scheduler.run_check(env["proc"], c, "sod", datetime.datetime(2026, 8, 15, 10, 30, tzinfo=live.IST))
    assert {f.quantity for f in repo.list_fills(c.id)} == {LOT}


def test_telegram_proposes_and_an_approval_executes(env):
    cfg = _configure(mode="telegram")
    _backtest(cfg)
    _enable()
    c = _campaign(cfg, "live")
    out = scheduler.run_check(env["proc"], c, "sod", datetime.datetime(2026, 8, 15, 10, 30, tzinfo=live.IST))
    assert out["bot"] == "awaiting_approval"
    text, markup = env["telegram"][-1]
    assert "Iron Condor bot" in text and markup["inline_keyboard"][0][0]["callback_data"].startswith("a:")
    token = markup["inline_keyboard"][0][0]["callback_data"][2:]
    assert env["sent"] == []
    hitl.handle_callback({"token": token, "chat_id": "CHAT1", "action": "a"})
    assert len(env["sent"]) == 4
    assert repo.get_decision(out["decision_id"])["outcome"] == "approved_completed"
    assert bot.eligibility(U, cfg).approved_executions == 1
    # A token is single-use: a second tap places nothing.
    hitl.handle_callback({"token": token, "chat_id": "CHAT1", "action": "a"})
    assert len(env["sent"]) == 4


def test_a_rejection_places_nothing(env):
    cfg = _configure(mode="telegram")
    _backtest(cfg)
    _enable()
    c = _campaign(cfg, "live")
    out = scheduler.run_check(env["proc"], c, "sod", datetime.datetime(2026, 8, 15, 10, 30, tzinfo=live.IST))
    token = env["telegram"][-1][1]["inline_keyboard"][0][0]["callback_data"][2:]
    hitl.handle_callback({"token": token, "chat_id": "CHAT1", "action": "r"})
    assert env["sent"] == [] and repo.get_decision(out["decision_id"])["outcome"] == "rejected"


def test_an_expired_approval_places_nothing(env):
    cfg = _configure(mode="telegram")
    _backtest(cfg)
    _enable()
    c = _campaign(cfg, "live")
    out = scheduler.run_check(env["proc"], c, "sod", datetime.datetime(2026, 8, 15, 10, 30, tzinfo=live.IST))
    snap = repo.get_decision(out["decision_id"])["snapshot"]
    repo.update_decision(out["decision_id"], snapshot={**snap, "expires_at": "2026-01-01T00:00:00+05:30"})
    bot.handle_approval(U, str(out["decision_id"]), "a", None, "10:40")
    assert env["sent"] == [] and "no longer valid" in env["alerts"][-1]


def test_auto_executes_straight_away(env):
    cfg = _configure(mode="auto")
    _backtest(cfg)
    _enable()
    c = _campaign(cfg, "live")
    out = scheduler.run_check(env["proc"], c, "sod", datetime.datetime(2026, 8, 15, 10, 30, tzinfo=live.IST))
    assert out["bot"] == "executing" and len(env["sent"]) == 4
    assert {f.kind for f in repo.list_fills(c.id)} == {"entry"}


def test_read_only_mode_blocks_a_bot_entry(env, monkeypatch):
    monkeypatch.setattr(hitl, "trading_allowed", lambda: False)
    cfg = _configure(mode="auto")
    _backtest(cfg)
    _enable()
    c = _campaign(cfg, "live")
    out = scheduler.run_check(env["proc"], c, "sod", datetime.datetime(2026, 8, 15, 10, 30, tzinfo=live.IST))
    assert out["bot"] == "read_only" and env["sent"] == []


# ---- the user wins -----------------------------------------------------------------------


def test_a_paused_bot_acts_on_nothing(env):
    cfg = _configure(mode="auto")
    _backtest(cfg)
    _enable()
    c = _campaign(cfg, "live")
    bot.pause(U, "testing.")
    out = scheduler.run_check(env["proc"], c, "sod", datetime.datetime(2026, 8, 15, 10, 30, tzinfo=live.IST))
    assert out["bot"] == "paused" and env["sent"] == []


def test_switching_off_hands_a_live_campaign_back_and_ends_a_paper_one(env):
    cfg = _configure(mode="auto")
    live_c = _campaign(cfg, "live")
    bot.on_config_change(U, before=cfg.model_dump(mode="json"), after=cfg.model_dump(mode="json"), after_enabled=False)
    assert repo.get_campaign(live_c.id, U).origin == "manual"
    assert "handed" in env["alerts"][-1]
    paper_c = _campaign(cfg.model_copy(update={"mode": "paper"}), "paper")
    before = cfg.model_copy(update={"mode": "paper"}).model_dump(mode="json")
    bot.on_config_change(U, before=before, after={**before, "mode": "telegram"}, after_enabled=True)
    assert repo.get_campaign(paper_c.id, U).status == "closed"


@pytest.fixture
def client(env, monkeypatch):
    from icici_breeze_backend.app.api.v1 import route_bots, route_condor

    monkeypatch.setattr(route_condor, "_proc", lambda: env["proc"])
    app = FastAPI()
    app.include_router(route_condor.router, prefix="/api/condor")
    app.include_router(route_bots.router, prefix="/bots")
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user_id=U, username=U, roles=[], is_authenticated=True
    )
    from icici_breeze_backend.app.api import deps_license

    monkeypatch.setattr(deps_license, "trading_mutations_allowed", lambda: True)
    return TestClient(app)


def test_the_bot_routes_enforce_the_gate_and_report_eligibility(client, env):
    _configure()
    r = client.patch(f"/bots/config?bot_type={BOT_DYNAMIC_CONDOR}", json={"enabled": True, "config": {"mode": "telegram"}})
    assert r.status_code == 409 and "Simulation cycle" in r.json()["detail"]
    assert client.patch(f"/bots/config?bot_type={BOT_DYNAMIC_CONDOR}", json={"enabled": True}).status_code == 200
    overview = client.get("/api/condor/bot").json()
    assert overview["eligibility"]["backtest"] is None and overview["eligibility"]["may_telegram"] is False


def test_a_manual_ticket_on_the_bots_campaign_pauses_it(client, env):
    cfg = _configure(mode="auto")
    _backtest(cfg)
    _enable()
    c = _campaign(cfg, "live")
    t = client.post(f"/api/condor/campaigns/{c.id}/ticket/template", json={"kind": "add_tranche", "params": {"lots": 1}}).json()
    r = client.post(f"/api/condor/campaigns/{c.id}/ticket/execute", json={"kind": "add_tranche", "orders": t["orders"]})
    assert r.status_code == 200
    assert bot.config_of(bots_repo.get_or_create_bot(U, BOT_DYNAMIC_CONDOR).config).paused is True


def test_a_paper_campaign_never_places_real_orders(env):
    cfg = _configure()
    c = _campaign(cfg, "paper")
    t = tickets.template(env["proc"], c, "add_tranche", {"lots": 1})
    with pytest.raises(executor.Refused, match="Simulation campaign"):
        executor.start(env["proc"], c, t["orders"], kind="add_tranche")
    assert env["sent"] == []



# ---- what counts as evidence -------------------------------------------------------------


def test_a_run_from_other_rules_does_not_unlock_the_bot(env, monkeypatch):
    from icici_breeze_backend.app.services.condor import engine

    cfg = _configure()
    _backtest(cfg, engine_version="absent")  # recorded before versions existed
    assert bot.eligibility(U, cfg).backtest is None
    _backtest(cfg)  # this release's rules
    assert bot.eligibility(U, cfg).backtest is not None
    monkeypatch.setattr(engine, "ENGINE_VERSION", engine.ENGINE_VERSION + 1)  # a later release changes the rules
    assert bot.eligibility(U, cfg).backtest is None


def test_an_old_run_of_identical_settings_still_counts_however_many_runs_since(env):
    cfg = _configure()
    _backtest(cfg, created_at="2026-02-01T18:00:00", run_id="old")
    other = cfg.model_copy(update={"exit_action": "close"})
    for i in range(60):
        _backtest(other, created_at=f"2026-09-{1 + i % 28:02d}T18:{i:02d}:00", run_id=f"other-{i}")
    assert bot.eligibility(U, cfg).backtest["run_id"] == "old"


def test_auto_needs_a_paper_cycle_and_an_approval_on_these_settings(env):
    cfg = _configure()
    _backtest(cfg)
    other = cfg.model_copy(update={"exit_action": "close"})
    # Approvals earned on different settings do not carry over.
    elsewhere = repo.create_campaign(U, other.campaign, expiry=EXP, origin="bot", mode="live", note=f"bot:{bot.hash_of(other)}")
    eid = repo.create_execution(elsewhere.id, "suggested_roll", [], note=bot.APPROVED_NOTE)
    repo.update_execution(eid, status="completed")
    assert bot.eligibility(U, cfg).approved_executions == 0
    repo.close_campaign(elsewhere.id, U, "settings_changed")
    # An approval on these settings, but no Simulation cycle: still locked.
    here = _campaign(cfg, "live")
    eid = repo.create_execution(here.id, "suggested_roll", [], note=bot.APPROVED_NOTE)
    repo.update_execution(eid, status="completed")
    with pytest.raises(bot.Refused, match="Simulation cycle and then"):
        bot.guard(U, after_enabled=True, after_config={**cfg.model_dump(mode="json"), "mode": "auto"})
    paper = _campaign(cfg, "paper")
    repo.bump_tranches(paper.cycle.id)
    repo.close_cycle(paper.cycle.id, "exit_dte")
    bot.guard(U, after_enabled=True, after_config={**cfg.model_dump(mode="json"), "mode": "auto"})
    # A campaign handed back to the user keeps its fingerprint, so its approval still counts.
    repo.set_origin(here.id, "manual")
    assert bot.eligibility(U, cfg).as_dict()["may_auto"] is True
