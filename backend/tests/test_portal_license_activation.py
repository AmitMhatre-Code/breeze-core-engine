"""Portal license activation service."""

import asyncio

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services import deployment_license_status as dls
from icici_breeze_backend.app.services.portal_license_activation import (
    request_portal_license_activation,
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(cfg, "DEPLOYMENT_LICENSE_KEY", "test-key")
    monkeypatch.setattr(cfg, "PORTAL_API_BASE_URL", "https://portal.example")
    monkeypatch.setattr(cfg, "PUBLIC_FRONTEND_ORIGIN", "http://203.0.113.10")
    dls.reset_for_tests()
    yield
    dls.reset_for_tests()


def _sample_customer(user_id: str = "USER1", name: str = "Test User") -> dict:
    return {
        "Status": 200,
        "Success": {"id": user_id, "idirect_user_name": name},
    }


def test_activation_skipped_when_portal_not_configured(monkeypatch):
    monkeypatch.setattr(cfg, "PORTAL_API_BASE_URL", "")
    allowed, err = asyncio.run(
        request_portal_license_activation(_sample_customer(), fallback_user_id="USER1")
    )
    assert allowed is True
    assert err is None


def test_activation_denied_on_trial_denied_policy(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "deployment_license_status": "trial_denied",
        "activation_rejected_reason": "icici_trial_consumed",
        "policy_token": "fake",
    }

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch(
        "icici_breeze_backend.app.services.portal_license_activation.portal_host_allowed",
        return_value=True,
    ), patch(
        "icici_breeze_backend.app.services.portal_license_activation.parse_verified_portal_body",
        return_value={
            "deployment_license_status": "trial_denied",
            "activation_rejected_reason": "icici_trial_consumed",
        },
    ), patch(
        "icici_breeze_backend.app.services.portal_license_activation.httpx.AsyncClient",
        return_value=mock_client,
    ):
        allowed, err = asyncio.run(
            request_portal_license_activation(_sample_customer(), fallback_user_id="USER1")
        )

    assert allowed is False
    assert err and "14-day trial" in err
    assert dls.get_license_status() == "trial_denied"


def test_activation_post_includes_display_name(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "deployment_license_status": "active",
        "policy_token": "fake",
    }

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch(
        "icici_breeze_backend.app.services.portal_license_activation.portal_host_allowed",
        return_value=True,
    ), patch(
        "icici_breeze_backend.app.services.portal_license_activation.parse_verified_portal_body",
        return_value={"deployment_license_status": "active"},
    ), patch(
        "icici_breeze_backend.app.services.portal_license_activation.httpx.AsyncClient",
        return_value=mock_client,
    ):
        asyncio.run(
            request_portal_license_activation(
                _sample_customer("vikrammh", "VIKRAM M HATRE"),
                fallback_user_id="OTHER",
            )
        )

    call_kwargs = mock_client.post.call_args.kwargs
    assert call_kwargs["json"]["icici_user_id"] == "VIKRAMMH"
    assert call_kwargs["json"]["idirect_user_name"] == "VIKRAM M HATRE"
