"""User exchange calendar repository and market resolver tests."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from icici_breeze_backend.app.db.user_exchange_calendar_migrate import (
    ensure_user_exchange_calendar_table,
)
from icici_breeze_backend.app.repositories import user_exchange_calendar as uec_repo
from icici_breeze_backend.app.services import market_calendar as mc


@pytest.fixture
def user_cal_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "users.sqlite3")
    ensure_user_exchange_calendar_table(db_path)
    monkeypatch.setattr(uec_repo, "_db_path", lambda: db_path)
    return db_path


def test_get_user_calendar_seeds_defaults(user_cal_db):
    row = uec_repo.get_user_calendar("user-a")
    assert row.source == "local"
    assert row.open_hour == 9
    assert row.open_minute == 15
    assert row.close_minute == 30
    assert len(row.holidays) > 0


def test_save_local_marks_has_edits(user_cal_db):
    row = uec_repo.get_user_calendar("user-b")
    uec_repo.save_user_calendar(
        "user-b",
        open_hour=10,
        open_minute=0,
        close_hour=16,
        close_minute=0,
        holidays={"2026-12-25": "Christmas"},
        source="local",
    )
    updated = uec_repo.get_user_calendar("user-b")
    assert updated.open_hour == 10
    assert uec_repo.has_local_edits(updated) is True


def test_user_market_open_respects_custom_hours(user_cal_db):
    uec_repo.save_user_calendar(
        "user-c",
        open_hour=10,
        open_minute=0,
        close_hour=16,
        close_minute=0,
        holidays={},
        source="local",
    )
    from icici_breeze_backend.app.core.timezone import IST

    open_dt = datetime(2026, 6, 25, 10, 30, tzinfo=IST)
    closed_dt = datetime(2026, 6, 25, 9, 30, tzinfo=IST)
    assert mc.is_market_open("user-c", open_dt) is True
    assert mc.is_market_open("user-c", closed_dt) is False


def test_user_market_closed_on_holiday(user_cal_db):
    uec_repo.save_user_calendar(
        "user-d",
        open_hour=9,
        open_minute=15,
        close_hour=15,
        close_minute=30,
        holidays={"2026-06-26": "Muharram"},
        source="local",
    )
    from icici_breeze_backend.app.core.timezone import IST

    dt = datetime(2026, 6, 26, 11, 0, tzinfo=IST)
    assert mc.is_market_open("user-d", dt) is False
    assert "Muharram" in mc.market_closed_reason("user-d", dt)


def test_apply_console_sync_clears_local_edits_flag(user_cal_db):
    uec_repo.apply_console_sync(
        "user-e",
        open_hour=9,
        open_minute=15,
        close_hour=15,
        close_minute=30,
        holidays={"2026-01-26": "Republic Day"},
        console_updated_at="2026-06-27T10:00:00+05:30",
    )
    row = uec_repo.get_user_calendar("user-e")
    assert row.source == "console_sync"
    assert uec_repo.has_local_edits(row) is False
