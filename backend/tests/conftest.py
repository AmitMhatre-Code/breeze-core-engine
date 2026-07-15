"""Shared pytest fixtures for breeze-core-engine backend tests."""
from __future__ import annotations

# Must run before anything else imports `breeze_connect` (directly, or via
# icici_breeze_backend.app.services.processor/core.icici_client): breeze_connect
# downloads ICICI's SecurityMaster.zip with a bare urlopen() at import time,
# which crashes the whole test session without internet access to icicidirect.com.
# See tests/breeze_mock_env.py for details.
import tests.breeze_mock_env  # noqa: E402,F401

import pytest

from tests.fixtures.portal_heartbeat_drm_keys import TEST_PUBLIC_KEY_PEM


@pytest.fixture(autouse=True)
def portal_heartbeat_verify_files(tmp_path, monkeypatch):
    """Bake test public key and allowed portal host for DRM verification tests."""
    pub = tmp_path / "portal_heartbeat_public.pem"
    hosts = tmp_path / "portal_allowed_hosts.txt"
    pub.write_text(TEST_PUBLIC_KEY_PEM, encoding="utf-8")
    hosts.write_text("portal.example\nbreeze-ui.com\n", encoding="utf-8")
    monkeypatch.setenv("PORTAL_HEARTBEAT_JWT_PUBLIC_KEY_PATH", str(pub))
    monkeypatch.setenv("PORTAL_ALLOWED_HOSTS_PATH", str(hosts))


@pytest.fixture(autouse=True)
def exchange_calendar_db(tmp_path, monkeypatch):
    """Point the global exchange-calendar repo at a fresh temp DB seeded with
    bundled defaults, so unmocked calls to app.services.market_calendar never
    read or write the real backend/data/users.sqlite3 dev database. Tests that
    want a specific calendar can still monkeypatch
    `icici_breeze_backend.app.repositories.exchange_calendar._db_path`
    themselves (it will simply override this one for that test)."""
    from icici_breeze_backend.app.db.exchange_calendar_migrate import (
        ensure_exchange_calendar_table,
    )
    from icici_breeze_backend.app.repositories import exchange_calendar as ec_repo

    db_path = str(tmp_path / "exchange_calendar_default.sqlite3")
    ensure_exchange_calendar_table(db_path)
    monkeypatch.setattr(ec_repo, "_db_path", lambda: db_path)
