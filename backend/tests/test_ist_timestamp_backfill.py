"""Shifting stored UTC wall-clock timestamps to IST.

The load-bearing cases are the two guards: shape (a stamp that already carries its own
offset must not move) and version (a second run must not shift the same row again — an
11-hour error is undetectable after the fact).
"""
from __future__ import annotations

import sqlite3

import pytest

from icici_breeze_backend.app.core.timezone import ist_timestamp
from icici_breeze_backend.app.db.ist_timestamp_backfill import (
    backfill_ist_timestamps_if_needed,
)


def _db(tmp_path):
    """A pre-switch database: naive UTC stamps, plus the one column that isn't naive."""
    p = str(tmp_path / "users.sqlite3")
    with sqlite3.connect(p) as conn:
        conn.execute(
            "CREATE TABLE portfolio_squareoff_rules ("
            "id TEXT PRIMARY KEY, created_at TIMESTAMP, fired_at TIMESTAMP, "
            "resolved_at TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO portfolio_squareoff_rules VALUES (?, ?, ?, ?)",
            ("r1", "2026-07-21 07:37:54", "2026-07-21 04:06:32", "2026-07-21 07:37:54"),
        )
        conn.execute(
            "INSERT INTO portfolio_squareoff_rules VALUES (?, ?, ?, ?)",
            ("r2", "2026-07-20 11:53:58", None, None),
        )
        conn.execute(
            "CREATE TABLE user_broker_session ("
            "user_id TEXT PRIMARY KEY, created_at TIMESTAMP, expires_at TEXT)"
        )
        conn.execute(
            "INSERT INTO user_broker_session VALUES (?, ?, ?)",
            ("u1", "2026-07-17 06:23:58", "2026-07-17T18:30:00+00:00"),
        )
        conn.commit()
    return p


def _rules(db):
    with sqlite3.connect(db) as conn:
        return dict(
            (r[0], r[1:])
            for r in conn.execute(
                "SELECT id, created_at, fired_at, resolved_at "
                "FROM portfolio_squareoff_rules"
            ).fetchall()
        )


def test_shifts_naive_stamps_by_ist_offset(tmp_path):
    db = _db(tmp_path)
    assert backfill_ist_timestamps_if_needed(db) is True
    assert _rules(db)["r1"] == (
        "2026-07-21 13:07:54",
        "2026-07-21 09:36:32",
        "2026-07-21 13:07:54",
    )


def test_leaves_nulls_alone(tmp_path):
    db = _db(tmp_path)
    backfill_ist_timestamps_if_needed(db)
    assert _rules(db)["r2"] == ("2026-07-20 17:23:58", None, None)


def test_does_not_touch_a_stamp_that_carries_its_own_offset(tmp_path):
    """`expires_at` is already an unambiguous instant — shifting it would corrupt it."""
    db = _db(tmp_path)
    backfill_ist_timestamps_if_needed(db)
    with sqlite3.connect(db) as conn:
        created, expires = conn.execute(
            "SELECT created_at, expires_at FROM user_broker_session"
        ).fetchone()
    assert created == "2026-07-17 11:53:58"
    assert expires == "2026-07-17T18:30:00+00:00"


def test_is_idempotent_across_restarts(tmp_path):
    db = _db(tmp_path)
    assert backfill_ist_timestamps_if_needed(db) is True
    after_first = _rules(db)
    assert backfill_ist_timestamps_if_needed(db) is False
    assert backfill_ist_timestamps_if_needed(db) is False
    assert _rules(db) == after_first


def test_bumps_user_version(tmp_path):
    db = _db(tmp_path)
    backfill_ist_timestamps_if_needed(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_skips_tables_that_do_not_exist(tmp_path):
    """Most deployments are missing at least one optional table — not an error."""
    p = str(tmp_path / "sparse.sqlite3")
    with sqlite3.connect(p) as conn:
        conn.execute("CREATE TABLE parked_orders (id TEXT, created_at TIMESTAMP)")
        conn.execute("INSERT INTO parked_orders VALUES ('p1', '2026-07-21 07:37:54')")
        conn.commit()
    assert backfill_ist_timestamps_if_needed(p) is True
    with sqlite3.connect(p) as conn:
        assert (
            conn.execute("SELECT created_at FROM parked_orders").fetchone()[0]
            == "2026-07-21 13:07:54"
        )


def test_ist_timestamp_matches_the_stored_column_format(tmp_path):
    """The whole migration assumes new writes keep CURRENT_TIMESTAMP's exact shape —
    if that drifts, the shape guard silently stops matching future rows."""
    import re

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", ist_timestamp())


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-07-21T07:37:54+05:30",  # reference_data_ingest_history.ingested_at
        "2026-07-21T07:37:54Z",
        "not-a-timestamp",
        "",
    ],
)
def test_only_the_naive_format_is_shifted(tmp_path, stamp):
    p = str(tmp_path / "shapes.sqlite3")
    with sqlite3.connect(p) as conn:
        conn.execute("CREATE TABLE parked_orders (id TEXT, created_at TIMESTAMP)")
        conn.execute("INSERT INTO parked_orders VALUES ('p1', ?)", (stamp,))
        conn.commit()
    backfill_ist_timestamps_if_needed(p)
    with sqlite3.connect(p) as conn:
        assert conn.execute("SELECT created_at FROM parked_orders").fetchone()[0] == stamp
