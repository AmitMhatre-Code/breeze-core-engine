"""B-27 / B-57: the scrip master is judged on its age, at startup and after a missed load."""
from __future__ import annotations

import datetime as dt

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.reference_data import orchestrator, scheduler


def _history(monkeypatch, rows):
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.reference_data.state.fetch_ingest_history",
        lambda limit=200: rows,
    )


def _calendar(monkeypatch, concluded: dt.date):
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.quote_source_router.latest_concluded_trading_day",
        lambda now=None: concluded,
    )


def test_a_master_loaded_before_the_last_close_is_stale(monkeypatch):
    _calendar(monkeypatch, dt.date(2026, 9, 29))
    _history(monkeypatch, [
        {"kind": "icici_scrip_master", "ok": False, "ingested_at": "2026-09-30T07:00:00+05:30"},
        {"kind": "icici_scrip_master", "ok": True, "ingested_at": "2026-09-27T21:44:00+05:30"},
    ])
    now = dt.datetime(2026, 9, 30, 8, 0, tzinfo=IST)
    assert orchestrator.last_scrip_master_ingest() == dt.datetime(2026, 9, 27, 21, 44, tzinfo=IST)
    assert orchestrator.scrip_master_is_stale(now) is True


def test_a_master_loaded_after_the_last_close_is_current(monkeypatch):
    _calendar(monkeypatch, dt.date(2026, 9, 29))
    _history(monkeypatch, [{"kind": "icici_scrip_master", "ok": True, "ingested_at": "2026-09-29T18:00:30+05:30"}])
    assert orchestrator.scrip_master_is_stale(dt.datetime(2026, 9, 30, 8, 0, tzinfo=IST)) is False


def test_no_recorded_load_is_stale(monkeypatch):
    _calendar(monkeypatch, dt.date(2026, 9, 29))
    _history(monkeypatch, [])
    assert orchestrator.scrip_master_is_stale(dt.datetime(2026, 9, 30, 8, 0, tzinfo=IST)) is True


def test_a_missed_scheduled_load_is_caught_up_outside_market_hours(monkeypatch):
    """B-27: the daily load fired only in its exact minute, with no catch-up."""
    sch = {"enabled": True, "hour_ist": 18, "minute_ist": 0}
    _calendar(monkeypatch, dt.date(2026, 9, 29))
    monkeypatch.setattr(orchestrator, "scrip_master_is_stale", lambda now=None: True)
    monkeypatch.setattr(scheduler, "_last_scrip_retry", None)
    market = {"open": False}
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.market_calendar.is_market_open",
        lambda now=None: market["open"],
    )
    assert scheduler._scrip_retry_due(dt.datetime(2026, 9, 29, 17, 0, tzinfo=IST), sch) is False
    assert scheduler._scrip_retry_due(dt.datetime(2026, 9, 29, 19, 0, tzinfo=IST), sch) is True
    market["open"] = True
    assert scheduler._scrip_retry_due(dt.datetime(2026, 9, 30, 10, 0, tzinfo=IST), sch) is False


def test_the_catch_up_is_a_full_versioned_load(monkeypatch):
    calls = []
    monkeypatch.setattr(orchestrator, "run_reference_data_load", lambda **kw: calls.append(kw) or {"ok": True})
    monkeypatch.setattr(scheduler, "_last_scrip_retry", None)
    scheduler._retry_stale_scrip_master()
    assert calls == [{"force": True, "trigger_mode": "catch_up"}]
    assert scheduler._last_scrip_retry is not None
