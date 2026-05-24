"""Unit tests for portal deployment heartbeat and IST market-hours guard."""

import asyncio
import sys
import types
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from icici_breeze_backend.app.services import portal_deployment_heartbeat as hb

_IST = ZoneInfo("Asia/Kolkata")


def _ist(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=_IST)


@pytest.mark.parametrize(
    "dt,blocked",
    [
        (_ist(2026, 5, 23, 8, 59), False),
        (_ist(2026, 5, 23, 9, 0), True),
        (_ist(2026, 5, 23, 15, 59), True),
        (_ist(2026, 5, 23, 16, 0), False),
    ],
)
def test_is_ist_market_hours(dt, blocked):
    assert hb.is_ist_market_hours(dt) is blocked


def test_heartbeat_tick_always_posts():
    async def _run():
        with patch.object(
            hb,
            "post_heartbeat",
            return_value={"status": "OK", "trigger_upgrade": False, "heartbeat_interval_sec": 600},
        ) as post:
            interval = await hb.heartbeat_tick()
            post.assert_called_once()
            assert interval == 600

    asyncio.run(_run())


def test_heartbeat_tick_defers_upgrade_when_not_allowed():
    async def _run():
        with patch.object(
            hb,
            "post_heartbeat",
            return_value={
                "status": "OK",
                "trigger_upgrade": True,
                "target_tag": "latest",
                "upgrade_allowed_now": False,
            },
        ):
            with patch.object(hb, "execute_upgrade") as upgrade:
                await hb.heartbeat_tick()
                upgrade.assert_not_called()

    asyncio.run(_run())


def test_heartbeat_tick_runs_upgrade_when_triggered(monkeypatch):
    monkeypatch.setattr(hb.cfg, "DEPLOYMENT_GHCR_IMAGE", "ghcr.io/example/breeze-core-engine:latest")

    async def _run():
        with patch.object(
            hb,
            "post_heartbeat",
            return_value={
                "status": "OK",
                "trigger_upgrade": True,
                "target_tag": "latest",
                "upgrade_allowed_now": True,
            },
        ):
            with patch.object(hb, "execute_upgrade") as upgrade:
                await hb.heartbeat_tick()
                upgrade.assert_called_once_with("latest")

    asyncio.run(_run())


def test_post_heartbeat_updates_license_status_on_403(monkeypatch):
    from icici_breeze_backend.app.services import deployment_license_status as dls

    monkeypatch.setattr(hb.cfg, "DEPLOYMENT_LICENSE_KEY", "key")
    monkeypatch.setattr(hb.cfg, "PORTAL_API_BASE_URL", "https://portal.example")
    monkeypatch.setattr(hb, "_public_ip_from_origin", lambda: "203.0.113.1")
    dls.reset_for_tests()

    class FakeResponse:
        status_code = 403

        @staticmethod
        def json():
            return {"detail": "License revoked"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json):
            return FakeResponse()

    async def _run():
        with patch.object(hb.httpx, "AsyncClient", FakeClient):
            result = await hb.post_heartbeat()
            assert result is None
            assert dls.get_license_status() == "revoked"

    asyncio.run(_run())


def test_post_heartbeat_updates_license_status_on_200_expired(monkeypatch):
    from icici_breeze_backend.app.services import deployment_license_status as dls

    monkeypatch.setattr(hb.cfg, "DEPLOYMENT_LICENSE_KEY", "key")
    monkeypatch.setattr(hb.cfg, "PORTAL_API_BASE_URL", "https://portal.example")
    monkeypatch.setattr(hb, "_public_ip_from_origin", lambda: "203.0.113.1")
    dls.reset_for_tests()

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "status": "OK",
                "trigger_upgrade": False,
                "deployment_license_status": "expired",
            }

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json):
            return FakeResponse()

    async def _run():
        with patch.object(hb.httpx, "AsyncClient", FakeClient):
            result = await hb.post_heartbeat()
            assert result is not None
            assert result["deployment_license_status"] == "expired"
            assert dls.get_license_status() == "expired"

    asyncio.run(_run())


def test_resolve_upgrade_image_replaces_tag(monkeypatch):
    monkeypatch.setattr(hb.cfg, "DEPLOYMENT_GHCR_IMAGE", "ghcr.io/org/breeze-core-engine:v1.0.0")
    assert hb._resolve_upgrade_image("latest") == "ghcr.io/org/breeze-core-engine:latest"


def test_execute_upgrade_pulls_and_starts_watchtower(monkeypatch):
    monkeypatch.setattr(hb.cfg, "DEPLOYMENT_GHCR_IMAGE", "ghcr.io/org/breeze-core-engine:latest")
    monkeypatch.setattr(hb.cfg, "DEPLOYMENT_CONTAINER_NAME", "breeze-core-engine")

    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_container.id = "wt-abc"
    mock_client.containers.run.return_value = mock_container

    docker_mod = types.ModuleType("docker")
    docker_errors = types.ModuleType("docker.errors")
    docker_errors.DockerException = Exception
    docker_errors.APIError = Exception
    docker_mod.from_env = MagicMock(return_value=mock_client)
    docker_mod.errors = docker_errors
    monkeypatch.setitem(sys.modules, "docker", docker_mod)
    monkeypatch.setitem(sys.modules, "docker.errors", docker_errors)

    hb.execute_upgrade("latest")

    mock_client.images.pull.assert_called_once_with("ghcr.io/org/breeze-core-engine:latest")
    mock_client.containers.run.assert_called_once()
    args, kwargs = mock_client.containers.run.call_args
    assert args[0] == "containrrr/watchtower"
    assert kwargs["command"] == ["--run-once", "breeze-core-engine"]
