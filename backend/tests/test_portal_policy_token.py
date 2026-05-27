"""Unit tests for portal heartbeat policy JWT verification."""

import time

import jwt
import pytest

from icici_breeze_backend.app.services.portal_policy_token import (
    parse_verified_portal_body,
    portal_host_allowed,
    verify_policy_token,
)
from tests.fixtures.portal_heartbeat_drm_keys import (
    TEST_PRIVATE_KEY_PEM,
    attach_test_policy_token,
)


def test_verify_policy_token_success():
    body = attach_test_policy_token(
        {"status": "OK", "deployment_license_status": "active", "trigger_upgrade": False},
        public_ip="203.0.113.10",
    )
    policy = verify_policy_token(body["policy_token"], public_ip="203.0.113.10")
    assert policy["deployment_license_status"] == "active"


def test_verify_rejects_ip_mismatch():
    body = attach_test_policy_token(
        {"status": "OK", "deployment_license_status": "active"},
        public_ip="203.0.113.10",
    )
    with pytest.raises(ValueError, match="public_ip mismatch"):
        verify_policy_token(body["policy_token"], public_ip="198.51.100.1")


def test_parse_verified_portal_body_missing_token():
    assert parse_verified_portal_body({"status": "OK"}, public_ip="203.0.113.10") is None


def test_portal_host_allowed():
    assert portal_host_allowed("https://portal.example/api") is True
    assert portal_host_allowed("https://evil.example/api") is False


def test_verify_rejects_expired_token():
    now = int(time.time())
    claims = {
        "status": "OK",
        "deployment_license_status": "active",
        "public_ip": "203.0.113.10",
        "iss": "breeze-portal",
        "aud": "breeze-core-engine",
        "iat": now - 900,
        "exp": now - 300,
    }
    token = jwt.encode(claims, TEST_PRIVATE_KEY_PEM, algorithm="ES256")
    with pytest.raises(jwt.PyJWTError):
        verify_policy_token(token, public_ip="203.0.113.10")
