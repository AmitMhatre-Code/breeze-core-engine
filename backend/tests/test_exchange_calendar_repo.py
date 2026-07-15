"""Global (singleton) exchange calendar repository tests."""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.db.exchange_calendar_migrate import (
    ensure_exchange_calendar_table,
)
from icici_breeze_backend.app.repositories import exchange_calendar as ec_repo


@pytest.fixture
def calendar_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "users.sqlite3")
    ensure_exchange_calendar_table(db_path)
    monkeypatch.setattr(ec_repo, "_db_path", lambda: db_path)
    return db_path


def test_get_calendar_seeds_defaults(calendar_db):
    row = ec_repo.get_calendar()
    assert row.source == "local"
    assert row.open_hour == 9
    assert row.open_minute == 15
    assert row.close_hour == 15
    assert row.close_minute == 30
    assert len(row.holidays) > 0


def test_save_local_marks_has_edits(calendar_db):
    ec_repo.save_calendar(
        open_hour=10,
        open_minute=0,
        close_hour=16,
        close_minute=0,
        holidays={"2026-12-25": "Christmas"},
        source="local",
    )
    updated = ec_repo.get_calendar()
    assert updated.open_hour == 10
    assert ec_repo.has_local_edits(updated) is True


def test_save_calendar_is_singleton(calendar_db):
    ec_repo.save_calendar(
        open_hour=10,
        open_minute=0,
        close_hour=16,
        close_minute=0,
        holidays={},
        source="local",
    )
    ec_repo.save_calendar(
        open_hour=11,
        open_minute=0,
        close_hour=17,
        close_minute=0,
        holidays={},
        source="local",
    )
    row = ec_repo.get_calendar()
    assert row.open_hour == 11
    assert row.close_hour == 17


def test_add_and_delete_holiday(calendar_db):
    row = ec_repo.add_holiday("2026-11-10", "Muhurat trading eve")
    assert row.holidays["2026-11-10"] == "Muhurat trading eve"
    row = ec_repo.delete_holiday("2026-11-10")
    assert "2026-11-10" not in row.holidays


def test_delete_missing_holiday_returns_none(calendar_db):
    assert ec_repo.delete_holiday("1999-01-01") is None


def test_apply_console_sync_clears_local_edits_flag(calendar_db):
    ec_repo.apply_console_sync(
        open_hour=9,
        open_minute=15,
        close_hour=15,
        close_minute=30,
        holidays={"2026-01-26": "Republic Day"},
        console_updated_at="2026-06-27T10:00:00+05:30",
    )
    row = ec_repo.get_calendar()
    assert row.source == "console_sync"
    assert ec_repo.has_local_edits(row) is False
