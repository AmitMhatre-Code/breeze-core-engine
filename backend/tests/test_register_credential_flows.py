"""Broker recovery crypto and user_account helpers used by /api/register/*."""
import os
import sqlite3
import tempfile
import time

from icici_breeze_backend.app.auth.credentials import (
    decrypt_broker_recovery_token,
    encrypt_broker_recovery_token,
)
from icici_breeze_backend.app.auth.user_account import (
    create_direct_user_account,
    update_direct_app_password,
    user_has_active_broker_credentials,
    verify_direct_account_password,
)


def _schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE user_account (
            user_id TEXT PRIMARY KEY NOT NULL,
            google_id TEXT UNIQUE,
            username TEXT NOT NULL,
            email TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT 1,
            roles TEXT DEFAULT '["trader"]',
            is_admin INTEGER DEFAULT 0,
            password_hash TEXT,
            auth_provider TEXT NOT NULL DEFAULT 'google'
        );
        CREATE TABLE user_credentials (
            credential_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            broker_api_key TEXT NOT NULL,
            secret_fragment BLOB NOT NULL,
            encryption_salt BLOB NOT NULL,
            fragment_position TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER NOT NULL DEFAULT 1,
            rotated_at TIMESTAMP
        );
        """
    )


def test_recovery_token_roundtrip():
    key = "unit-test-jwt-secret-key-32chars!!"
    uid = "icici_u1"
    tok = encrypt_broker_recovery_token(uid, key, max_age_seconds=120)
    assert tok
    out = decrypt_broker_recovery_token(tok, key)
    assert out is not None
    assert out[0] == uid
    assert out[1] > int(time.time())


def test_recovery_token_expired():
    key = "unit-test-jwt-secret-key-32chars!!"
    uid = "icici_u1"
    tok = encrypt_broker_recovery_token(uid, key, max_age_seconds=-10)
    assert decrypt_broker_recovery_token(tok, key) is None


def test_user_has_active_broker_credentials_and_password_update():
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        with sqlite3.connect(path) as conn:
            _schema(conn)
            create_direct_user_account(conn, "u1", "u1@local", "oldpassword12")
            conn.execute(
                """
                INSERT INTO user_credentials (
                    credential_id, user_id, broker_api_key, secret_fragment,
                    encryption_salt, fragment_position, is_active
                ) VALUES ('c1', 'u1', 'k', x'00', x'', 'first_half', 1)
                """
            )
            conn.commit()
        with sqlite3.connect(path) as conn:
            assert user_has_active_broker_credentials(conn, "u1")
            assert not user_has_active_broker_credentials(conn, "missing")
        with sqlite3.connect(path) as conn:
            assert update_direct_app_password(conn, "u1", "newpassword12")
        with sqlite3.connect(path) as conn:
            assert verify_direct_account_password(conn, "u1", "newpassword12")
            assert not verify_direct_account_password(conn, "u1", "oldpassword12")
    finally:
        os.unlink(path)
