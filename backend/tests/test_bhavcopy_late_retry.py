"""Late-publish bhavcopy retry: a daily load that found only the previous session's file must
not leave it standing for another whole day (2026-09-29: Friday's closes all Tuesday)."""
from __future__ import annotations

import datetime as dt
import sys
import time
from unittest.mock import MagicMock

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.reference_data import orchestrator, scheduler

_ORCH = "icici_breeze_backend.app.services.reference_data.orchestrator"
_SCHEDULE = {"enabled": True, "hour_ist": 18, "minute_ist": 0}
_EVENING = dt.datetime(2026, 6, 25, 19, 0, tzinfo=IST)  # Thursday, a trading day
_TARGET = dt.date(2026, 6, 25)
_STALE = dt.date(2026, 6, 24)


@pytest.fixture(autouse=True)
def _real_orchestrator(monkeypatch):
    # test_reference_data_startup swaps a fake orchestrator into sys.modules for good.
    monkeypatch.setitem(sys.modules, _ORCH, orchestrator)
    monkeypatch.setattr(scheduler, "_last_bhavcopy_retry", None)


@pytest.fixture
def held(monkeypatch):
    dates = {cfg.NFO: _STALE, cfg.BFO: _TARGET}
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.quote_source_router.get_bhavcopy_source_date",
        lambda ex: dates.get(ex),
    )
    return dates


@pytest.fixture
def publish(monkeypatch):
    calls = []
    monkeypatch.setattr(orchestrator.bhavcopy_store, "publish_bhavcopy_rows", lambda rows, **kw: calls.append(kw))
    monkeypatch.setattr(orchestrator.scrip_index, "current_version", lambda: 7)
    monkeypatch.setattr(orchestrator, "_set_source", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_record_ingest", lambda *a, **k: None)
    return calls


def test_retry_loads_only_the_stale_segment_into_the_live_generation(held, publish, monkeypatch):
    nse = MagicMock(return_value=([{"row": "1"}], "https://nse/x.zip"))
    bse = MagicMock()
    monkeypatch.setattr(orchestrator.bhavcopy_nse, "fetch_nse_fo_bhavcopy_for_date", nse)
    monkeypatch.setattr(orchestrator.bhavcopy_bse, "fetch_bse_fo_bhavcopy_for_date", bse)

    assert orchestrator.retry_stale_bhavcopy(_EVENING) == {"nfo": "loaded"}

    nse.assert_called_once_with(_TARGET)
    bse.assert_not_called()
    # Version 7 is the live one: a new version would purge the scrip index and SPAN with it.
    assert publish == [
        {"segment": "nfo", "source_date": _TARGET, "source_url": "https://nse/x.zip", "version": 7}
    ]


def test_retry_before_the_file_is_published_touches_nothing(held, publish, monkeypatch):
    monkeypatch.setattr(orchestrator.bhavcopy_nse, "fetch_nse_fo_bhavcopy_for_date", lambda day: None)
    assert orchestrator.retry_stale_bhavcopy(_EVENING) == {"nfo": "not_published"}
    assert publish == []


def test_retry_stands_aside_while_a_full_load_runs(held, publish):
    with orchestrator._load_mutex:
        assert orchestrator.retry_stale_bhavcopy(_EVENING) == {}
    assert publish == []


def test_retry_due_after_the_scheduled_time_while_stale(held):
    assert scheduler._bhavcopy_retry_due(_EVENING, _SCHEDULE) is True


def test_retry_due_overnight_for_the_previous_session(held):
    next_morning = dt.datetime(2026, 6, 25, 23, 45, tzinfo=IST)
    assert scheduler._bhavcopy_retry_due(next_morning, _SCHEDULE) is True


def test_retry_waits_for_the_scheduled_load(held):
    before_schedule = dt.datetime(2026, 6, 25, 17, 0, tzinfo=IST)
    assert scheduler._bhavcopy_retry_due(before_schedule, _SCHEDULE) is False


def test_retry_never_in_market_hours(held):
    assert scheduler._bhavcopy_retry_due(dt.datetime(2026, 6, 25, 11, 0, tzinfo=IST), _SCHEDULE) is False


def test_retry_not_due_when_both_segments_are_current(held):
    held[cfg.NFO] = _TARGET
    assert scheduler._bhavcopy_retry_due(_EVENING, _SCHEDULE) is False


def test_retry_is_throttled(held, monkeypatch):
    monkeypatch.setattr(scheduler, "_last_bhavcopy_retry", time.monotonic())
    assert scheduler._bhavcopy_retry_due(_EVENING, _SCHEDULE) is False
