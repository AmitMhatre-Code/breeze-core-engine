"""Tests for the persisted, runtime-configurable PNL engine clock settings.

This is the "Advanced Settings" backing store: a global (not per-user)
singleton row, cloned from `reference_data.state`'s schedule-table pattern —
loaded fresh on every call (no cache) so the flush/recompute loops can pick
up a change without a restart.
"""
from __future__ import annotations

import sqlite3

import pytest

from icici_breeze_backend.app.services import pnl_engine_settings as settings


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(settings, "_db_path", lambda: path)
    return path


def test_ensure_table_seeds_defaults_from_env_config(db_path, monkeypatch):
    import icici_breeze_backend.app.core.config as cfg

    monkeypatch.setattr(cfg, "PNL_QUOTE_FLUSH_INTERVAL_SECONDS", 1.8, raising=False)
    monkeypatch.setattr(cfg, "PNL_ENGINE_INTERVAL_SECONDS", 3.0, raising=False)

    settings.ensure_pnl_engine_settings_table(db_path)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT quote_flush_interval_seconds, pnl_recompute_interval_seconds "
            "FROM pnl_engine_settings WHERE id = 1"
        ).fetchone()
    assert row == (1.8, 3.0)


def test_ensure_table_is_idempotent(db_path):
    settings.ensure_pnl_engine_settings_table(db_path)
    settings.ensure_pnl_engine_settings_table(db_path)
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM pnl_engine_settings").fetchone()[0]
    assert count == 1


def test_load_defaults_when_table_missing(db_path):
    loaded = settings.load_pnl_engine_settings()
    assert loaded["quote_flush_interval_seconds"] == pytest.approx(2.0)
    assert loaded["pnl_recompute_interval_seconds"] == pytest.approx(2.0)


def test_save_and_load_round_trip(db_path):
    updated = settings.save_pnl_engine_settings(
        quote_flush_interval_seconds=1.5, pnl_recompute_interval_seconds=4.0
    )
    assert updated == {"quote_flush_interval_seconds": 1.5, "pnl_recompute_interval_seconds": 4.0}
    assert settings.load_pnl_engine_settings() == updated


def test_partial_update_leaves_other_field_unchanged(db_path):
    settings.save_pnl_engine_settings(quote_flush_interval_seconds=1.5, pnl_recompute_interval_seconds=4.0)
    updated = settings.save_pnl_engine_settings(quote_flush_interval_seconds=3.0)
    assert updated["quote_flush_interval_seconds"] == 3.0
    assert updated["pnl_recompute_interval_seconds"] == 4.0


def test_save_rejects_quote_flush_below_hard_min(db_path):
    with pytest.raises(ValueError):
        settings.save_pnl_engine_settings(quote_flush_interval_seconds=0.1)


def test_save_rejects_quote_flush_above_hard_max(db_path):
    with pytest.raises(ValueError):
        settings.save_pnl_engine_settings(quote_flush_interval_seconds=20.0)


def test_save_rejects_pnl_recompute_below_hard_min(db_path):
    with pytest.raises(ValueError):
        settings.save_pnl_engine_settings(pnl_recompute_interval_seconds=0.5)


def test_save_rejects_pnl_recompute_above_hard_max(db_path):
    with pytest.raises(ValueError):
        settings.save_pnl_engine_settings(pnl_recompute_interval_seconds=100.0)


def test_out_of_range_write_leaves_persisted_value_unchanged(db_path):
    settings.save_pnl_engine_settings(quote_flush_interval_seconds=1.5, pnl_recompute_interval_seconds=4.0)
    with pytest.raises(ValueError):
        settings.save_pnl_engine_settings(quote_flush_interval_seconds=999.0)
    assert settings.load_pnl_engine_settings()["quote_flush_interval_seconds"] == 1.5


def test_load_clamps_a_manually_tampered_out_of_range_row(db_path):
    settings.ensure_pnl_engine_settings_table(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE pnl_engine_settings SET quote_flush_interval_seconds = 999, "
            "pnl_recompute_interval_seconds = -5 WHERE id = 1"
        )
        conn.commit()
    loaded = settings.load_pnl_engine_settings()
    assert loaded["quote_flush_interval_seconds"] == settings.QUOTE_FLUSH_MAX_SECONDS
    assert loaded["pnl_recompute_interval_seconds"] == settings.PNL_RECOMPUTE_MIN_SECONDS


def test_bounds_dict_shape():
    b = settings.bounds()
    assert b == {
        "quote_flush_min_seconds": 0.5,
        "quote_flush_max_seconds": 10.0,
        "quote_flush_recommended_min_seconds": 1.5,
        "quote_flush_recommended_max_seconds": 2.0,
        "pnl_recompute_min_seconds": 1.0,
        "pnl_recompute_max_seconds": 30.0,
        "pnl_recompute_recommended_min_seconds": 2.0,
        "pnl_recompute_recommended_max_seconds": 5.0,
    }


def test_load_falls_back_to_defaults_on_sqlite_error(db_path, monkeypatch):
    monkeypatch.setattr(settings, "_db_path", lambda: "/nonexistent-dir-xyz/no.sqlite3")
    monkeypatch.setattr(
        settings,
        "ensure_pnl_engine_settings_table",
        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("boom")),
    )
    loaded = settings.load_pnl_engine_settings()
    assert loaded["quote_flush_interval_seconds"] == pytest.approx(2.0)
    assert loaded["pnl_recompute_interval_seconds"] == pytest.approx(2.0)
