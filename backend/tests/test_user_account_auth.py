"""user_account PK migration, direct passwords, and JWT context without google_id."""
import os
import sqlite3
import tempfile

from icici_breeze_backend.app.auth.context import extract_user_context
from icici_breeze_backend.app.auth.jwt_handler import JWTHandler
from icici_breeze_backend.app.auth.user_account import (
    create_direct_user_account,
    hash_app_password,
    verify_app_password,
    verify_direct_account_password,
)
from icici_breeze_backend.app.db.user_account_migrate import migrate_user_account_if_needed


def test_hash_and_verify_password():
    h = hash_app_password("correct horse battery")
    assert verify_app_password("correct horse battery", h)
    assert not verify_app_password("wrong", h)


def test_migrate_pk_google_id_to_user_id():
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        with sqlite3.connect(path) as conn:
            conn.executescript(
                """
                CREATE TABLE user_account (
                    google_id TEXT PRIMARY KEY NOT NULL,
                    user_id TEXT NOT NULL UNIQUE,
                    username TEXT NOT NULL,
                    email TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1,
                    roles TEXT DEFAULT '["trader"]',
                    is_admin INTEGER DEFAULT 0
                );
                INSERT INTO user_account (google_id, user_id, username, email)
                VALUES ('g1', 'u1', 'u1', 'u1@x.test');
                """
            )
        assert migrate_user_account_if_needed(path) is True
        with sqlite3.connect(path) as conn:
            cols = conn.execute("PRAGMA table_info(user_account)").fetchall()
            pk_col = [c[1] for c in cols if c[5] == 1]
            assert pk_col == ["user_id"]
            row = conn.execute(
                "SELECT google_id, auth_provider FROM user_account WHERE user_id = ?",
                ("u1",),
            ).fetchone()
            assert row == ("g1", "google")
    finally:
        os.unlink(path)


def test_direct_account_password_verify():
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        with sqlite3.connect(path) as conn:
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
                """
            )
            create_direct_user_account(conn, "icici_user", "icici_user@local", "secretpass12")
        with sqlite3.connect(path) as conn:
            assert verify_direct_account_password(conn, "icici_user", "secretpass12")
            assert not verify_direct_account_password(conn, "icici_user", "nope")
            assert not verify_direct_account_password(conn, "other", "secretpass12")
    finally:
        os.unlink(path)


def test_jwt_without_google_id_accepted():
    class _Req:
        def __init__(self):
            self.headers = {}
            self.cookies = {"access_token": ""}
            self.client = type("c", (), {"host": "127.0.0.1"})()
            self.state = type("s", (), {})()

    secret = "test-jwt-secret-for-unit-tests-only"
    handler = JWTHandler(secret_key=secret, access_token_expire_minutes=60)
    token = handler.create_access_token("uid1", "uid1", google_id=None)

    req = _Req()
    req.cookies["access_token"] = token
    req.cookies["icici_broker_token"] = "broker-tok"
    import icici_breeze_backend.app.core.config as app_cfg
    import icici_breeze_backend.core.config as core_cfg

    prev_a = app_cfg.JWT_SECRET
    prev_c = getattr(core_cfg, "JWT_SECRET", None)
    try:
        app_cfg.JWT_SECRET = secret
        core_cfg.JWT_SECRET = secret
        ctx = extract_user_context(req)
        assert ctx is not None
        assert ctx.user_id == "uid1"
        assert ctx.google_id is None
        assert ctx.broker_token == "broker-tok"
    finally:
        app_cfg.JWT_SECRET = prev_a
        core_cfg.JWT_SECRET = prev_c
