"""Per-user aggressive-order defaults (mode + tolerance), stored on user_account."""
from __future__ import annotations

import sqlite3

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services import aggressive_order_prefs as aop


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "users.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE user_account (user_id TEXT PRIMARY KEY, username TEXT, email TEXT)"
        )
        conn.execute(
            "INSERT INTO user_account (user_id, username, email) VALUES ('u1','u1','u1@x.com')"
        )
        conn.commit()
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "USERS_DB", "users.sqlite3")
    monkeypatch.setattr(aop.cfg, "DATA_PATH", str(tmp_path) + "/", raising=False)
    monkeypatch.setattr(aop.cfg, "USERS_DB", "users.sqlite3", raising=False)
    return path


def test_defaults_when_unset(db, monkeypatch):
    monkeypatch.setattr(cfg, "AGGRESSIVE_LIMIT_DEFAULT_TOLERANCE_PCT", 5.0)
    prefs = aop.get_aggressive_order_prefs("u1")
    assert prefs["mode"] == "limit_tolerance"
    assert prefs["tolerance_pct"] == 5.0


def test_set_and_get_roundtrip(db):
    aop.set_aggressive_order_prefs("u1", mode="market", tolerance_pct=8.0)
    prefs = aop.get_aggressive_order_prefs("u1")
    assert prefs["mode"] == "market"
    assert prefs["tolerance_pct"] == 8.0


def test_invalid_mode_falls_back(db):
    aop.set_aggressive_order_prefs("u1", mode="nonsense")
    assert aop.get_aggressive_order_prefs("u1")["mode"] == "limit_tolerance"


def test_tolerance_clamped_on_write(db, monkeypatch):
    monkeypatch.setattr(cfg, "AGGRESSIVE_LIMIT_MAX_TOLERANCE_PCT", 25.0)
    aop.set_aggressive_order_prefs("u1", tolerance_pct=999.0)
    assert aop.get_aggressive_order_prefs("u1")["tolerance_pct"] == 25.0


def test_partial_update_preserves_other_field(db):
    aop.set_aggressive_order_prefs("u1", mode="market", tolerance_pct=7.0)
    aop.set_aggressive_order_prefs("u1", tolerance_pct=3.0)
    prefs = aop.get_aggressive_order_prefs("u1")
    assert prefs["mode"] == "market"
    assert prefs["tolerance_pct"] == 3.0
