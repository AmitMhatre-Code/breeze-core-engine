"""The condor bot card's three entry points (design-decisions #67).

- **Play** pre-fills Basket Orders with a first tranche at the bot's saved settings
  (`campaigns.entry_proposal`).
- **Basket Orders → Manage as a Dynamic Iron Condor campaign** opens a manual campaign whose
  first tranche is the basket, placed by the campaign executor (`executor.open_campaign`).
  Anything that refuses the ticket refuses the campaign and leaves nothing behind.
- **The clock** is the other bots' backtest dialog: a period, the saved settings, an Activity
  row sharing its id with the stored run (`backtest_job.start_card`).
"""
from __future__ import annotations

import datetime

import pytest

from icici_breeze_backend.app.db.bots_migrate import BOT_DYNAMIC_CONDOR, ensure_bots_tables
from icici_breeze_backend.app.db.condor_migrate import ensure_condor_tables
from icici_breeze_backend.app.domain.condor import CondorSettings
from icici_breeze_backend.app.repositories import bots as bots_repo
from icici_breeze_backend.app.repositories import condor as repo
from icici_breeze_backend.app.services import backtest_budget as budget_mod
from icici_breeze_backend.app.services.bots import backtest_jobs as jobs
from icici_breeze_backend.app.services.bots import backtest_service as service
from icici_breeze_backend.app.services.bots import charges as charges_mod
from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
from icici_breeze_backend.app.services.bots.scalping import live as scalp
from icici_breeze_backend.app.services.bots.scalping import momentum_bot, spreads
from icici_breeze_backend.app.services.condor import backtest_job, campaigns, executor, live, tickets
from icici_breeze_backend.app.services.condor import bot as condor_bot
from tests.fixtures.condor_chain import EXPIRY, LOT, at, market

EXP = EXPIRY.strftime("%d-%b-%Y")
SETTINGS = CondorSettings(margin_ceiling_inr=10_00_000)
_TS = "%Y-%m-%d %H:%M:%S"


def _row(strike, right, action, qty, price, expiry=EXP):
    return {"stock_code": "NIFTY", "exchange_code": "NFO", "product_type": "Options", "action": action,
            "quantity": str(qty), "average_price": str(price), "right": right,
            "strike_price": str(strike), "expiry_date": expiry}


def _order(action, strike, right, qty=LOT):
    return {"action": action, "strike": strike, "right": right, "quantity": qty}


BASKET = [
    _order("Sell", 23350, "Put"),
    _order("Buy", 21550, "Put"),
    _order("Sell", 25200, "Call"),
    _order("Buy", 25850, "Call"),
]


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
    path = str(tmp_path / "users.sqlite3")
    for mod in (repo, charges_mod):
        monkeypatch.setattr(mod, "_db_path", lambda: path)
    monkeypatch.setattr(campaigns, "_sg_armed", lambda user, expiry: False)
    monkeypatch.setattr(tickets, "_margin", lambda *a, **k: None)
    monkeypatch.setattr(tickets, "_liquidity", lambda ctx, o: None)
    ensure_condor_tables(path)
    spot = 24650.0

    def snapshot(proc, user_id, expiry, held=(), *, now=None):
        return live.LiveSnapshot(market(spot, at(45, "10:30")), True, (), "live", True)

    monkeypatch.setattr(live, "snapshot", snapshot)

    def live_quote(proc, user_id, expiry_display, strike, right):
        q = market(spot, at(45, "10:30")).quote(float(strike), "Call" if right == "call" else "Put")
        return momentum_bot.Quote(q.bid, q.ask, q.ltp, source="websocket") if q else momentum_bot.Quote(None, None, None)

    monkeypatch.setattr(momentum_bot, "live_quote", live_quote)
    monkeypatch.setattr(scalp, "rest_option_touch", lambda *a, **k: (None, None))
    sent: list = []

    def place_and_confirm(proc, user_id, leg, *, price_for_attempt, timeout_seconds, attempts, **kw):
        sent.append(leg)
        price = round(float(price_for_attempt(0)), 2)
        return scalp.FillResult(order_id=f"OID{len(sent)}", filled_quantity=leg.quantity,
                                requested_quantity=leg.quantity, average_price=price)

    monkeypatch.setattr(scalp, "place_and_confirm", place_and_confirm)
    from icici_breeze_backend.app.services.condor import scheduler

    monkeypatch.setattr(scheduler, "_notify", lambda user, text: None)
    return {"sent": sent}


def _sync(fn):
    fn()


# ---- play: the pre-filled first tranche -------------------------------------------------


def test_entry_proposal_is_a_wings_capped_condor_on_the_cycle_expiry(env):
    out = campaigns.entry_proposal(FakeProc(), "u1", SETTINGS, today=at(45).date())
    assert out["expiry"] == EXP and out["lot_size"] == LOT and out["tranches"] == SETTINGS.tranches
    by = {(leg["right"], leg["side"]): leg["strike"] for leg in out["legs"]}
    assert by[("Put", "Buy")] < by[("Put", "Sell")] < by[("Call", "Sell")] < by[("Call", "Buy")]


def test_entry_proposal_refuses_when_no_expiry_qualifies(env):
    with pytest.raises(campaigns.Refused):
        campaigns.entry_proposal(FakeProc(), "u1", SETTINGS, today=datetime.date(2026, 11, 20))


# ---- Basket Orders: a campaign whose first tranche is the basket ------------------------


def test_a_basket_opens_a_campaign_wings_first_and_books_every_fill(env):
    campaign, ex = executor.open_campaign(FakeProc(), "u1", SETTINGS, EXP, BASKET, run=_sync)
    ex = repo.get_execution(ex["id"], campaign.id)
    assert ex["status"] == "completed" and ex["kind"] == "add_tranche"
    assert [s["action"] for s in ex["steps"]] == ["Buy", "Buy", "Sell", "Sell"]
    fresh = repo.get_campaign(campaign.id, "u1")
    assert fresh.origin == "manual" and fresh.mode == "live" and fresh.cycle.tranches_entered == 1
    pos = campaigns.ledger_position(repo.list_fills(campaign.id), EXP)
    assert pos[(23350.0, "Put")][0] == -LOT and pos[(25850.0, "Call")][0] == LOT


def test_a_refused_basket_leaves_no_campaign_and_places_nothing(env):
    with pytest.raises(executor.Refused):
        executor.open_campaign(FakeProc(), "u1", SETTINGS, EXP, [_order("Sell", 25200, "Call")], run=_sync)
    assert repo.list_campaigns("u1", include_closed=True) == [] and env["sent"] == []


def test_a_group_already_holding_legs_is_refused_before_any_order(env):
    proc = FakeProc([_row(23350, "Put", "Sell", LOT, 95.0)])
    with pytest.raises(executor.Refused, match="already holds legs"):
        executor.open_campaign(proc, "u1", SETTINGS, EXP, BASKET, run=_sync)
    assert repo.list_campaigns("u1", include_closed=True) == [] and env["sent"] == []


def test_an_expiry_another_campaign_manages_is_refused(env):
    existing = repo.create_campaign("u1", SETTINGS, expiry=EXP, origin="bot")
    with pytest.raises(executor.Refused, match="already manages"):
        executor.open_campaign(FakeProc(), "u1", SETTINGS, EXP, BASKET, run=_sync)
    assert [c.id for c in repo.list_campaigns("u1")] == [existing.id] and env["sent"] == []


# ---- the clock: the card's backtest -----------------------------------------------------


@pytest.fixture
def bt(tmp_path, monkeypatch):
    users = str(tmp_path / "users.sqlite3")
    cache = str(tmp_path / "backtest.sqlite3")
    for mod in (repo, bots_repo, budget_mod, spreads, charges_mod):
        monkeypatch.setattr(mod, "_db_path", lambda: users)
    monkeypatch.setattr(store, "db_path", lambda: cache)
    monkeypatch.setattr(jobs, "_store_ready", False)
    monkeypatch.setattr(service, "holidays", lambda: set())
    monkeypatch.setattr(jobs, "broker_live", lambda: False)
    from icici_breeze_backend.audit import bot_audit

    root = tmp_path / "bots-audit"
    root.mkdir()
    monkeypatch.setattr(bot_audit, "audit_dir", lambda: str(root))
    ensure_bots_tables(users)
    ensure_condor_tables(users)
    store.ensure_tables(cache)
    rows = []
    for day in (datetime.date(2026, 3, 16), datetime.date(2026, 3, 17), datetime.date(2026, 3, 18)):
        for hh, mm in ((10, 20), (15, 20)):
            base = datetime.datetime.combine(day, datetime.time(hh, mm))
            for i in range(15):
                ts = base + datetime.timedelta(minutes=i)
                rows.append({"datetime": ts.strftime(_TS), "open": 24200, "high": 24200, "low": 24200, "close": 24200, "volume": 0})
    store.store_candles(rows, stock_code="NIFTY", table="spot_candles", path=cache)
    bots_repo.update_bot("u1", BOT_DYNAMIC_CONDOR, config={"lots_per_tranche": 2})
    yield
    if jobs._thread is not None:
        jobs._thread.join(timeout=30)


def _wait():
    if jobs._thread is not None:
        jobs._thread.join(timeout=30)


def test_the_card_backtest_is_an_activity_row_sharing_the_stored_runs_id(bt):
    job = jobs.start_bot_backtest("u1", "condor", "custom", datetime.date(2026, 3, 16), datetime.date(2026, 3, 18))
    _wait()
    row = bots_repo.get_run(job["run_id"])
    assert row["bot_type"] == BOT_DYNAMIC_CONDOR and row["trigger"] == "backtest"
    # Mock: nothing can be fetched, so the replay stops at its first check and says so.
    assert row["status"] == "completed" and row["reason_code"] == "backtest_gaps"
    stored = store.get_run(job["run_id"], "u1")
    assert stored["bot"] == "condor" and stored["status"] == "partial"
    assert stored["params"]["period"] == "custom" and stored["params"]["lots_per_tranche"] == 2
    assert "net_pnl" in stored["summary"] and isinstance(stored["trades"], list)
    # The comparison (#71) rides on the stored run, not on the Activity row, and the row's
    # download carries every combination's campaigns.
    assert len(stored["summary"]["comparison"]) == 108
    assert "comparison" not in row["detail"]["summary"]
    assert "108 combination(s) replayed in part" in row["reason_text"]
    import glob
    import zipfile

    from icici_breeze_backend.audit import bot_audit

    [archive] = glob.glob(f"{bot_audit.audit_dir()}/**/*.zip", recursive=True)
    names = zipfile.ZipFile(archive).namelist()
    assert {"summary.csv", "campaigns.csv", "actions.csv", "run.json"} <= set(names)
    assert sum(n.startswith("combinations/") for n in names) == 108
    # A partial run is not shown as the settings' backtest.
    cfg = condor_bot.config_of(bots_repo.get_or_create_bot("u1", BOT_DYNAMIC_CONDOR).config)
    assert condor_bot.eligibility("u1", cfg).backtest is None


def test_a_period_before_the_history_is_cut_at_its_start_and_says_so(bt):
    job = jobs.start_bot_backtest("u1", "condor", "custom", datetime.date(2025, 12, 1), datetime.date(2026, 1, 2))
    _wait()
    stored = store.get_run(job["run_id"], "u1")
    assert stored["params"]["from"] == "2026-01-01"
    assert any("option history starts" in n for n in stored["summary"]["notes"])


def test_sizing_from_margin_on_a_mock_instance_is_refused_without_a_row(bt):
    bots_repo.update_bot("u1", BOT_DYNAMIC_CONDOR, config={"lots_per_tranche": None})
    with pytest.raises(ValueError, match="lots per tranche"):
        jobs.start_bot_backtest("u1", "condor", "last_week")
    assert bots_repo.list_runs("u1", bot_type=BOT_DYNAMIC_CONDOR) == []


def test_card_trades_mark_an_open_campaign_at_its_last_check():
    summary = {"campaigns": [
        {"started": "2026-03-02T10:30", "ended": "2026-03-20T15:31", "end_reason": "exit", "pnl": 4000.0,
         "charges": 300.0, "worst_pnl_at_check": -900.0, "pnl_at_last_check": 4000.0,
         "cycles": [{"expiry": "2026-03-31", "tranches": 3, "rolls": 1}]},
        {"started": "2026-03-23T10:30", "ended": None, "end_reason": "open", "pnl": 9000.0,
         "charges": 120.0, "worst_pnl_at_check": -200.0, "pnl_at_last_check": 1500.0,
         "cycles": [{"expiry": "2026-04-28", "tranches": 1, "rolls": 0}]},
    ]}
    trades = backtest_job.card_trades(summary)
    assert [t["net_pnl"] for t in trades] == [4000.0, 1500.0]
    assert trades[1]["exit_reason"] == "open_at_period_end" and trades[1]["exited_at"] is None
    assert trades[0]["gross_pnl"] == 4300.0 and trades[0]["tranches"] == 3
    assert backtest_job.card_totals(trades) == {"net_pnl": 5500.0, "trades": 2}


# ---- several campaigns: one per expiry, any number across expiries ----------------------

NEXT = "24-Nov-2026"


def test_campaigns_on_different_expiries_coexist(env):
    other = repo.create_campaign("u1", SETTINGS, expiry=NEXT)
    campaign, ex = executor.open_campaign(FakeProc(), "u1", SETTINGS, EXP, BASKET, run=_sync)
    assert repo.get_execution(ex["id"], campaign.id)["status"] == "completed"
    assert {c.id for c in repo.list_campaigns("u1")} == {other.id, campaign.id}


def test_a_time_roll_into_an_expiry_another_campaign_manages_is_refused(env):
    campaign, _ = executor.open_campaign(FakeProc(), "u1", SETTINGS, EXP, BASKET, run=_sync)
    repo.create_campaign("u1", SETTINGS, expiry=NEXT)
    with pytest.raises(tickets.Refused, match="already managed by another campaign"):
        tickets.template(FakeProc(), repo.get_campaign(campaign.id, "u1"), "time_roll", {"lots": 1})


def test_a_ticket_row_on_another_campaigns_expiry_is_refused_before_any_order(env):
    campaign, _ = executor.open_campaign(FakeProc(), "u1", SETTINGS, EXP, BASKET, run=_sync)
    repo.create_campaign("u1", SETTINGS, expiry=NEXT)
    sent_before = len(env["sent"])
    row = {**_order("Buy", 21000, "Put"), "expiry": NEXT}
    with pytest.raises(tickets.Refused):
        executor.start(FakeProc(), repo.get_campaign(campaign.id, "u1"), [row], kind="blank", run=_sync)
    assert len(env["sent"]) == sent_before


def test_a_live_bot_waits_and_says_why_when_its_expiry_is_taken(env):
    from icici_breeze_backend.app.domain.condor import DynamicCondorBotConfig

    repo.create_campaign("u1", SETTINGS, expiry=EXP)
    live_cfg = DynamicCondorBotConfig(mode="telegram")
    expiry, why = condor_bot.waiting_reason(FakeProc(), "u1", live_cfg, at(45).date())
    assert expiry == EXP and "managed by another campaign" in why
    # A paper bot holds nothing at the broker, so it never waits on a live campaign.
    assert condor_bot.waiting_reason(FakeProc(), "u1", DynamicCondorBotConfig(mode="paper"), at(45).date())[1] is None


def test_entry_proposal_says_when_nothing_is_listed_beyond_a_short(env, monkeypatch):
    import dataclasses

    from icici_breeze_backend.app.services.condor.pricing import build_greeks_model
    from icici_breeze_backend.app.services.condor.strikes import strike_for_delta

    full = market(24650.0, at(45, "10:30"))
    model = build_greeks_model(full.chain, full.spot, EXPIRY, full.now)
    short_put = strike_for_delta(model, full.strikes, "Put", SETTINGS.short_delta)

    def cut_chain(proc, user_id, expiry, held=(), *, now=None):
        m = dataclasses.replace(full, chain=tuple(r for r in full.chain if r.strike >= short_put))
        return live.LiveSnapshot(m, True, (), "live", True)

    monkeypatch.setattr(live, "snapshot", cut_chain)
    with pytest.raises(campaigns.Refused, match="put is listed beyond the"):
        campaigns.entry_proposal(FakeProc(), "u1", SETTINGS, today=at(45).date())


def test_a_replay_only_opens_legs_inside_todays_tradeable_band():
    from icici_breeze_backend.app.services.condor.backtest import CondorReplay

    m = market(24000.0, at(45))
    replay = CondorReplay.__new__(CondorReplay)
    replay.listed_band = (0.05, 0.10)
    held = {(EXPIRY, 21000.0, "Put")}
    listed = replay._listed(m.chain, 24000.0, held)
    inside = [k for k in listed if k != 21000.0]
    assert min(inside) >= 24000 * 0.95 and max(inside) <= 24000 * 1.10
    assert 21000.0 in listed  # a held leg stays closable
    import dataclasses

    assert dataclasses.replace(m, listed=listed).strikes == sorted(k for k in m.strikes if k in set(listed))
    replay.listed_band = None
    assert replay._listed(m.chain, 24000.0, held) is None


def test_an_unreadable_strike_list_leaves_the_replay_unlimited_and_says_so(monkeypatch):
    monkeypatch.setattr(backtest_job, "_listed_expiry_near", lambda *a, **k: None)
    band, note = backtest_job.tradeable_band(SETTINGS, at(45).date())
    assert band is None and "not limited" in note


# ---- handing a manual campaign to the bot (#68) -----------------------------------------

HELD = [
    _row(21550, "Put", "Buy", LOT, 15.0),
    _row(23350, "Put", "Sell", LOT, 95.0),
    _row(25200, "Call", "Sell", LOT, 80.0),
    _row(25850, "Call", "Buy", LOT, 12.0),
]


@pytest.fixture
def bots(env, tmp_path, monkeypatch):
    path = str(tmp_path / "users.sqlite3")
    monkeypatch.setattr(bots_repo, "_db_path", lambda: path)
    ensure_bots_tables(path)
    told: list = []
    monkeypatch.setattr(condor_bot, "_notify", lambda user, text: told.append(text))
    return told


def _manual_campaign():
    campaign, _ = executor.open_campaign(FakeProc(), "u1", SETTINGS, EXP, BASKET, run=_sync)
    return repo.get_campaign(campaign.id, "u1")


def _arm_bot(**config):
    bots_repo.update_bot("u1", BOT_DYNAMIC_CONDOR, enabled=True, config={"mode": "telegram", **config})


def test_a_bot_that_is_off_or_on_paper_cannot_take_a_campaign(env, bots):
    c = _manual_campaign()
    p = condor_bot.handover_preview(FakeProc(HELD), c)
    assert not p["allowed"] and any("Semi-auto or Auto" in b for b in p["blockers"])
    bots_repo.update_bot("u1", BOT_DYNAMIC_CONDOR, enabled=True, config={"mode": "paper"})
    assert not condor_bot.handover_preview(FakeProc(HELD), c)["allowed"]
    with pytest.raises(condor_bot.Refused):
        condor_bot.hand_over(FakeProc(HELD), c)
    assert repo.get_campaign(c.id, "u1").origin == "manual"


def test_handing_over_takes_the_bots_settings_and_fingerprint(env, bots):
    c = _manual_campaign()
    _arm_bot(campaign={**SETTINGS.model_dump(mode="json"), "short_delta": 0.25})
    p = condor_bot.handover_preview(FakeProc(HELD), c)
    assert p["allowed"], p["blockers"]
    assert [d["field"] for d in p["settings_changes"]] == ["short_delta"]
    assert p["tranches_entered"] == 1 and p["tranches_remaining"] == SETTINGS.tranches - 1
    taken = condor_bot.hand_over(FakeProc(HELD), c)
    cfg = condor_bot.config_of(bots_repo.get_or_create_bot("u1", BOT_DYNAMIC_CONDOR).config)
    assert taken.origin == "bot" and taken.settings.short_delta == 0.25
    assert taken.note == f"bot:{condor_bot.hash_of(cfg)}"
    assert repo.bot_campaign("u1").id == c.id and bots


def test_a_ledger_that_differs_from_the_broker_must_be_settled_first(env, bots):
    c = _manual_campaign()
    _arm_bot()
    p = condor_bot.handover_preview(FakeProc(HELD[:3]), c)
    assert not p["allowed"] and any("differs" in b for b in p["blockers"])


def test_the_bot_takes_one_campaign_at_a_time(env, bots):
    c = _manual_campaign()
    _arm_bot()
    repo.create_campaign("u1", SETTINGS, expiry=NEXT, origin="bot")
    p = condor_bot.handover_preview(FakeProc(HELD), c)
    assert not p["allowed"] and any("already runs a campaign" in b for b in p["blockers"])


def test_saving_other_settings_on_a_bot_campaign_hands_it_back(env, bots):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from icici_breeze_backend.app.api.v1.route_condor import router
    from icici_breeze_backend.app.auth.context import RequestContext, get_request_context

    app = FastAPI()
    app.include_router(router, prefix="/api/condor")
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user_id="u1", username="u1", roles=[], is_authenticated=True
    )
    c = repo.create_campaign("u1", SETTINGS, expiry=EXP, origin="bot")
    client = TestClient(app)
    same = client.put(f"/api/condor/campaigns/{c.id}/settings", json=SETTINGS.model_dump(mode="json"))
    assert same.status_code == 200 and same.json()["origin"] == "bot"
    other = client.put(f"/api/condor/campaigns/{c.id}/settings", json={**SETTINGS.model_dump(mode="json"), "exit_dte": 14})
    assert other.json()["origin"] == "manual" and other.json()["settings"]["exit_dte"] == 14
