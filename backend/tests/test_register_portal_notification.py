"""Portal notification after direct user registration."""

import sqlite3
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icici_breeze_backend.app.api.v1 import route_register as rr


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


@pytest.fixture
def register_client(tmp_path, monkeypatch):
    db_path = tmp_path / "users.sqlite3"
    with sqlite3.connect(db_path) as conn:
        _schema(conn)
    monkeypatch.setattr(rr, "DB_PATH", str(db_path))
    monkeypatch.setattr(rr.cfg, "JWT_SECRET", "unit-test-jwt-secret-key-32chars!!")
    monkeypatch.setattr(rr.cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(rr.cfg, "USERS_DB", "users.sqlite3")

    app = FastAPI()
    app.include_router(rr.router)
    with TestClient(app) as client:
        yield client


def test_register_direct_notifies_portal(register_client):
    client = register_client
    with patch.object(rr, "cred_manager") as cred_mgr:
        cred_mgr.update_credentials.return_value = True
        with patch(
            "icici_breeze_backend.app.services.portal_deployment_user_registration.notify_portal_deployment_user_registration"
        ) as notify:
            r = client.post(
                "/api/register/direct",
                json={
                    "user_id": "trader1",
                    "password": "password123",
                    "api_key": "api-key",
                    "secret_fragment": "secret",
                },
            )
    assert r.status_code == 200
    notify.assert_called_once_with(user_id="trader1")


def test_register_direct_conflict_does_not_notify_portal(register_client):
    client = register_client
    payload = {
        "user_id": "trader1",
        "password": "password123",
        "api_key": "api-key",
        "secret_fragment": "secret",
    }
    with patch.object(rr, "cred_manager") as cred_mgr:
        cred_mgr.update_credentials.return_value = True
        with patch(
            "icici_breeze_backend.app.services.portal_deployment_user_registration.notify_portal_deployment_user_registration"
        ) as notify:
            assert client.post("/api/register/direct", json=payload).status_code == 200
            assert client.post("/api/register/direct", json=payload).status_code == 409
    notify.assert_called_once()
