"""Migration to the Strategy Group model.

The load-bearing case is the one-active-SG unique index: it is what structurally
forecloses the bug where arming a second strategy on the same scrip+expiry silently
UPSERTed onto the first one's row.
"""
from __future__ import annotations

import sqlite3

import pytest

from icici_breeze_backend.app.db.squareoff_rules_migrate import ensure_squareoff_rules_table


def _legacy_db(tmp_path):
    """A pre-SG table, exactly as the old migration created it."""
    p = str(tmp_path / "users.sqlite3")
    with sqlite3.connect(p) as conn:
        conn.execute(
            """
            CREATE TABLE portfolio_squareoff_rules (
                id TEXT PRIMARY KEY NOT NULL,
                user_id TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                expiry_display TEXT NOT NULL,
                exchange_code TEXT NOT NULL DEFAULT 'NFO',
                profit_target_pnl REAL NOT NULL,
                loss_limit_pnl REAL NOT NULL,
                target_premium_pct INTEGER NOT NULL DEFAULT 1,
                stop_loss_premium_pct INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'armed',
                leg_results TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                fired_at TIMESTAMP
            )
            """
        )
        conn.commit()
    return p


def _insert(db, rule_id, status, *, stock="NIFTY", expiry="21-Jul-2026",
            user="u1", created="2026-07-17 10:00:00"):
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO portfolio_squareoff_rules (id, user_id, stock_code, expiry_display,"
            " exchange_code, profit_target_pnl, loss_limit_pnl, status, created_at)"
            " VALUES (?,?,?,?, 'NFO', 1000, 500, ?, ?)",
            (rule_id, user, stock, expiry, status, created),
        )
        conn.commit()


def _rows(db):
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return {r["id"]: dict(r) for r in conn.execute(
            "SELECT * FROM portfolio_squareoff_rules")}


def test_adds_new_columns(tmp_path):
    db = _legacy_db(tmp_path)
    ensure_squareoff_rules_table(db)
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute('PRAGMA table_info("portfolio_squareoff_rules")')}
    assert {"legs_snapshot", "reset_reason", "resolved_at"} <= cols


def test_fire_failed_folds_into_reset_with_a_reason(tmp_path):
    db = _legacy_db(tmp_path)
    _insert(db, "r1", "fire_failed")
    ensure_squareoff_rules_table(db)
    row = _rows(db)["r1"]
    assert row["status"] == "reset"
    assert row["reset_reason"]  # a Reset with no explanation is the failure mode


def test_one_active_sg_index_is_enforced(tmp_path):
    """Two live rules can no longer collide on one (user, stock, expiry)."""
    db = _legacy_db(tmp_path)
    _insert(db, "r1", "armed")
    ensure_squareoff_rules_table(db)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(db, "r2", "armed")


def test_fired_and_armed_also_collide(tmp_path):
    """'fired' is still a live SG (waiting on its exits), so it occupies the key too --
    this is what stops a re-arm stacking a second exit order on a live one."""
    db = _legacy_db(tmp_path)
    _insert(db, "r1", "fired")
    ensure_squareoff_rules_table(db)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(db, "r2", "armed")


def test_terminal_rows_free_the_key_for_a_new_sg(tmp_path):
    """Spec section 11: after Completed/Reset the key is reusable by a NEW SG."""
    db = _legacy_db(tmp_path)
    _insert(db, "r1", "armed")
    ensure_squareoff_rules_table(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE portfolio_squareoff_rules SET status='completed' WHERE id='r1'")
        conn.commit()
    _insert(db, "r2", "armed")  # must not raise
    assert _rows(db)["r2"]["status"] == "armed"


def test_different_keys_do_not_collide(tmp_path):
    db = _legacy_db(tmp_path)
    _insert(db, "r1", "armed")
    ensure_squareoff_rules_table(db)
    _insert(db, "r2", "armed", expiry="28-Jul-2026")
    _insert(db, "r3", "armed", stock="BANKNIFTY")
    _insert(db, "r4", "armed", user="u2")
    assert len(_rows(db)) == 4


def test_pre_existing_conflicts_are_resolved_not_fatal(tmp_path):
    """Old data can legitimately hold a 'fired' row plus a newer 'armed' row for the
    same key -- CREATE UNIQUE INDEX would fail outright on that. The newest row (the
    user's current intent) survives; the rest retire with an explanation."""
    db = _legacy_db(tmp_path)
    _insert(db, "old_fired", "fired", created="2026-07-17 09:00:00")
    _insert(db, "new_armed", "armed", created="2026-07-17 11:00:00")

    ensure_squareoff_rules_table(db)  # must not raise

    rows = _rows(db)
    assert rows["new_armed"]["status"] == "armed"
    assert rows["old_fired"]["status"] == "reset"
    assert "still live" in (rows["old_fired"]["reset_reason"] or "")
    assert rows["old_fired"]["resolved_at"]


def test_migration_is_idempotent(tmp_path):
    db = _legacy_db(tmp_path)
    _insert(db, "r1", "armed")
    ensure_squareoff_rules_table(db)
    ensure_squareoff_rules_table(db)
    ensure_squareoff_rules_table(db)
    assert _rows(db)["r1"]["status"] == "armed"


def test_runs_on_a_fresh_db(tmp_path):
    db = str(tmp_path / "fresh.sqlite3")
    ensure_squareoff_rules_table(db)
    _insert(db, "r1", "armed")
    with pytest.raises(sqlite3.IntegrityError):
        _insert(db, "r2", "armed")
