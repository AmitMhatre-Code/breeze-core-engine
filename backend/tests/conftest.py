"""Shared pytest fixtures for breeze-core-engine backend tests."""
from __future__ import annotations

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
