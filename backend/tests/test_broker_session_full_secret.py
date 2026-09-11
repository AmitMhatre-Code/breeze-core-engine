"""The full API secret persisted beside the broker token (docs/design-decisions.md #31).

A session restored from the persisted token used to be signed with only the stored app half of
the secret, so every signed ICICI call after a restart failed with "Invalid Checksum". These tests
pin the storage rules that keep persisting the secret tolerable, and the lookup order that makes
background sessions sign with it.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from icici_breeze_backend.app.auth import credentials as cred_mod
from icici_breeze_backend.app.auth.context import set_full_secret_for_request
from icici_breeze_backend.app.db.broker_session_migrate import ensure_broker_session_table
from icici_breeze_backend.app.repositories import broker_session as bs

_KEY = "test-jwt-secret"
_SECRET = "FULL-api-secret-1234"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "users.sqlite3")
    ensure_broker_session_table(path)
    monkeypatch.setattr(bs, "_db_path", lambda: path)
    monkeypatch.setattr(bs, "_encryption_key", lambda: _KEY)
    return path


def _raw(db, column):
    with sqlite3.connect(db) as conn:
        row = conn.execute(f"SELECT {column} FROM user_broker_session WHERE user_id = 'u1'").fetchone()
    return row[0] if row else None


def _set_expiry(db, when: datetime):
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE user_broker_session SET expires_at = ?", (when.isoformat(),))
        conn.commit()


def test_round_trip_under_its_own_key(db):
    bs.save_broker_session_token("u1", "tok", full_secret=_SECRET)

    assert bs.get_broker_full_secret("u1") == _SECRET
    stored = _raw(db, "encrypted_full_secret")
    assert _SECRET not in stored
    # Its own key: neither the token-store nor the session-cookie cipher can open it.
    assert cred_mod.decrypt_broker_session_token(stored, _KEY) is None
    assert cred_mod.decrypt_from_session_cookie(stored, _KEY) is None


def test_a_login_without_a_secret_drops_the_previous_one(db):
    bs.save_broker_session_token("u1", "tok", full_secret=_SECRET)
    bs.save_broker_session_token("u1", "tok2")

    assert bs.get_broker_full_secret("u1") is None
    assert _raw(db, "encrypted_full_secret") is None
    assert bs.get_broker_session_token("u1") == "tok2"


def test_reading_an_expired_row_deletes_it(db):
    bs.save_broker_session_token("u1", "tok", full_secret=_SECRET)
    _set_expiry(db, datetime.now(timezone.utc) - timedelta(seconds=1))

    assert bs.get_broker_full_secret("u1") is None
    assert _raw(db, "user_id") is None


def test_purge_deletes_only_expired_rows_in_either_notation(db):
    bs.save_broker_session_token("u1", "tok", full_secret=_SECRET)
    bs.save_broker_session_token("u2", "tok", full_secret=_SECRET)
    past_utc = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()  # legacy +00:00
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE user_broker_session SET expires_at = ? WHERE user_id = 'u2'", (past_utc,))
        conn.execute("INSERT INTO user_broker_session (user_id, encrypted_token, expires_at) VALUES ('u3', 'x', 'garbage')")
        conn.commit()

    assert bs.purge_expired_broker_sessions() == 2
    assert bs.list_users_with_session() == ["u1"]


def test_clearing_the_secret_keeps_the_token(db):
    bs.save_broker_session_token("u1", "tok", full_secret=_SECRET)
    bs.clear_broker_full_secret("u1")

    assert bs.get_broker_full_secret("u1") is None
    assert bs.get_broker_session_token("u1") == "tok"


def test_a_credential_change_clears_the_persisted_secret(db):
    bs.save_broker_session_token("u1", "tok", full_secret=_SECRET)
    cred_mod._clear_persisted_full_secret("u1")
    assert bs.get_broker_full_secret("u1") is None


def test_migration_adds_the_column_to_an_existing_table(tmp_path):
    path = str(tmp_path / "old.sqlite3")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE user_broker_session (user_id TEXT PRIMARY KEY NOT NULL, "
            "encrypted_token TEXT NOT NULL, expires_at TIMESTAMP NOT NULL, created_at TIMESTAMP)"
        )
        conn.execute("INSERT INTO user_broker_session VALUES ('u1', 'x', '2099-01-01T00:00:00+05:30', NULL)")
        conn.commit()

    ensure_broker_session_table(path)
    ensure_broker_session_table(path)  # runs every boot

    with sqlite3.connect(path) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(user_broker_session)")]
        kept = conn.execute("SELECT encrypted_token, encrypted_full_secret FROM user_broker_session").fetchone()
    assert cols.count("encrypted_full_secret") == 1
    assert kept == ("x", None)


@pytest.fixture
def proc(monkeypatch):
    import icici_breeze_backend.app.services.processor as proc_mod

    monkeypatch.setattr(proc_mod.cfg, "JWT_SECRET", _KEY, raising=False)
    monkeypatch.setattr(
        cred_mod.CredentialManager,
        "reconstruct_full_api_secret",
        lambda self, user_id, user_fragment="": "APPHALF" + (user_fragment or ""),
    )
    p = proc_mod.processor()
    monkeypatch.setattr(p, "fetch_credentials", lambda uid: {"Status": 200, "Success": {"broker_api_key": "k"}})
    set_full_secret_for_request(None)
    yield p
    set_full_secret_for_request(None)


def test_background_session_signs_with_the_persisted_secret_not_the_app_half(db, proc):
    """The production bug: no request in scope used to yield the app half alone."""
    bs.save_broker_session_token("u1", "tok", full_secret=_SECRET)
    secret, _ = proc._get_full_secret_for_user("u1")
    assert secret == _SECRET


def test_the_request_cookie_secret_still_wins(db, proc):
    bs.save_broker_session_token("u1", "tok", full_secret=_SECRET)
    set_full_secret_for_request("FROM-COOKIE")
    secret, _ = proc._get_full_secret_for_user("u1")
    assert secret == "FROM-COOKIE"


def test_an_explicit_fragment_beats_the_persisted_secret(db, proc):
    bs.save_broker_session_token("u1", "tok", full_secret=_SECRET)
    secret, _ = proc._get_full_secret_for_user("u1", "USERHALF")
    assert secret == "APPHALFUSERHALF"


def test_without_any_secret_it_falls_back_to_the_stored_value(db, proc):
    """A deployment whose DB holds the whole secret keeps working exactly as before."""
    secret, _ = proc._get_full_secret_for_user("u1")
    assert secret == "APPHALF"
