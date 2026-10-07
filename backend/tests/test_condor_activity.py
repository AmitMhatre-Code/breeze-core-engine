"""The Dynamic Iron Condor bot in the Activity table (#73): one `campaign` row per bot campaign,
running for as long as the campaign is open, restated at each check, finished with the outcome."""
from __future__ import annotations

import datetime

from icici_breeze_backend.app.db.bots_migrate import BOT_DYNAMIC_CONDOR, BOT_HOLDINGS_WRITER
from icici_breeze_backend.app.repositories import bots as bots_repo
from icici_breeze_backend.app.repositories import condor as repo
from icici_breeze_backend.app.services.bots import hitl
from icici_breeze_backend.app.services.condor import activity, bot, live, scheduler
from tests.test_condor_bot import U, _backtest, _campaign, _configure, _enable, env  # noqa: F401

AT = datetime.datetime(2026, 8, 15, 10, 30, tzinfo=live.IST)


def _rows(**kw):
    return [r for r in bots_repo.list_runs(U, limit=None, **kw) if r.trigger == "campaign"]


def test_a_bot_campaign_gets_one_running_row_that_names_it(env):
    cfg = _configure()
    c = _campaign(cfg, "paper")
    activity.sync()
    activity.sync()  # idempotent: still one row
    rows = _rows()
    assert len(rows) == 1
    row = rows[0]
    assert (row.bot_type, row.status, row.reason_code) == (BOT_DYNAMIC_CONDOR, "running", "campaign_opened")
    assert row.detail["campaign_id"] == c.id and row.detail["mode"] == "paper"
    assert "Simulation" in row.reason_text


def test_a_manual_campaign_gets_no_row(env):
    cfg = _configure()
    repo.create_campaign(U, cfg.campaign, expiry="29-Sep-2026", origin="manual", mode="live", note=None)
    activity.sync()
    assert _rows() == []


def test_each_check_restates_the_row(env):
    cfg = _configure()
    _enable()
    c = _campaign(cfg, "paper")
    activity.sync()
    scheduler.run_check(env["proc"], c, "sod", AT)
    row = _rows()[0]
    assert row.status == "running"
    assert row.reason_code == "enter_tranche"
    assert row.reason_text.startswith("15 Aug 10:30 Start-of-day check")
    assert "Filled in Simulation" in row.reason_text
    # Re-read after the fill: the row's detail counts the tranche just entered.
    assert row.detail["tranches_entered"] == 1 and row.detail["campaign_id"] == c.id


def test_a_check_on_a_campaign_with_no_row_yet_opens_one(env):
    cfg = _configure()
    _enable()
    c = _campaign(cfg, "paper")
    scheduler.run_check(env["proc"], c, "sod", AT)
    rows = _rows()
    assert len(rows) == 1 and rows[0].reason_code == "enter_tranche"


def test_a_closed_campaign_finishes_its_row_with_the_reason(env):
    cfg = _configure()
    c = _campaign(cfg, "paper")
    activity.sync()
    repo.close_campaign(c.id, U, "moved_to_paper")
    activity.sync()
    row = _rows()[0]
    assert row.status == "completed" and row.reason_code == "moved_to_paper"
    assert "before any tranche" in row.reason_text and row.finished_at


def test_a_flat_closed_campaign_reports_its_net(env):
    cfg = _configure()
    c = _campaign(cfg, "paper")
    activity.sync()
    repo.add_fills(c.id, c.cycle.id, [
        {"expiry": c.cycle.expiry, "strike": 24000.0, "right": "Put", "side": "Sell", "quantity": 75,
         "price": 100.0, "charges": 10.0, "kind": "entry", "note": "paper"},
        {"expiry": c.cycle.expiry, "strike": 24000.0, "right": "Put", "side": "Buy", "quantity": 75,
         "price": 60.0, "charges": 10.0, "kind": "exit", "note": "paper"},
    ])
    repo.close_campaign(c.id, U, "exit_dte")
    activity.sync()
    row = _rows()[0]
    assert row.detail["net_pnl"] == 75 * 40 - 20
    assert "Net ₹2,980 after charges" in row.reason_text


def test_a_live_campaign_handed_back_finishes_its_row(env):
    cfg = _configure(mode="auto")
    c = _campaign(cfg, "live")
    activity.sync()
    bot.on_config_change(U, before=cfg.model_dump(mode="json"), after=cfg.model_dump(mode="json"), after_enabled=False)
    activity.sync()
    row = _rows()[0]
    assert (row.status, row.reason_code) == ("completed", "handed_to_you")
    assert repo.get_campaign(c.id, U).status == "active"  # the row ends; the campaign does not


def test_a_rejection_is_noted_on_the_row(env):
    cfg = _configure(mode="telegram")
    _backtest(cfg)
    _enable()
    c = _campaign(cfg, "live")
    scheduler.run_check(env["proc"], c, "sod", AT)
    assert _rows()[0].reason_text.endswith("Asked you on Telegram.")
    token = env["telegram"][-1][1]["inline_keyboard"][0][0]["callback_data"][2:]
    hitl.handle_callback({"token": token, "chat_id": "CHAT1", "action": "r"})
    assert _rows()[0].reason_code == "approval_rejected"


def test_the_reaper_never_ends_a_campaign_row(env):
    cfg = _configure()
    _campaign(cfg, "paper")
    activity.sync()
    bots_repo.start_run(U, BOT_HOLDINGS_WRITER, "schedule")
    assert bots_repo.reap_stale_runs() == 1  # the writer's, not the campaign's
    assert _rows()[0].status == "running"


def test_a_date_range_shows_runs_active_in_it_not_only_started_in_it(env):
    import sqlite3

    cfg = _configure()
    _campaign(cfg, "paper")
    activity.sync()
    old = bots_repo.start_run(U, BOT_HOLDINGS_WRITER, "schedule")
    bots_repo.finish_run(old, status="skipped", reason_code="x", reason_text="x")
    ended_today = bots_repo.start_run(U, BOT_HOLDINGS_WRITER, "manual")
    bots_repo.finish_run(ended_today, status="completed", reason_code="y", reason_text="y")
    with sqlite3.connect(bots_repo._db_path()) as conn:  # noqa: SLF001
        conn.execute("UPDATE bot_runs SET started_at = '2026-10-01 09:20:00'")
        conn.execute("UPDATE bot_runs SET finished_at = '2026-10-01 09:21:00' WHERE id = ?", (old,))
        conn.execute("UPDATE bot_runs SET finished_at = '2026-10-07 09:00:00' WHERE id = ?", (ended_today,))
        conn.commit()
    today = bots_repo.list_runs(U, limit=None, date_from="2026-10-07", date_to="2026-10-07")
    # The campaign row (still running) and the run that finished today; not the one that ended days ago.
    assert {r.id for r in today} == {_rows()[0].id, ended_today}
    # A range that ends before a run started never shows it.
    assert bots_repo.list_runs(U, limit=None, date_from="2026-09-01", date_to="2026-09-30") == []
