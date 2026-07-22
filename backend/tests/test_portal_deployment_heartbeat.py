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


def test_send_startup_heartbeat_success():
    async def _run():
        with patch.object(
            hb,
            "post_heartbeat",
            return_value={"status": "OK", "heartbeat_interval_sec": 300},
        ) as post:
            ok = await hb.send_startup_heartbeat()
            assert ok is True
            post.assert_called_once()

    asyncio.run(_run())


def test_send_startup_heartbeat_runs_upgrade_when_triggered(monkeypatch):
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
                ok = await hb.send_startup_heartbeat()
                assert ok is True
                upgrade.assert_called_once_with("latest")

    asyncio.run(_run())


def test_send_startup_heartbeat_defers_upgrade_when_not_allowed():
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
                await hb.send_startup_heartbeat()
                upgrade.assert_not_called()

    asyncio.run(_run())


def test_run_heartbeat_loop_sleeps_before_tick():
    async def _run():
        order: list[str] = []

        async def fake_sleep(_n):
            order.append("sleep")
            raise asyncio.CancelledError()

        async def fake_tick():
            order.append("tick")
            return 300

        with patch.object(asyncio, "sleep", side_effect=fake_sleep):
            with patch.object(hb, "heartbeat_tick", side_effect=fake_tick):
                with pytest.raises(asyncio.CancelledError):
                    await hb.run_heartbeat_loop()
        assert order == ["sleep"]

    asyncio.run(_run())


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


def test_post_heartbeat_403_does_not_update_unsigned_status(monkeypatch):
    from icici_breeze_backend.app.services import deployment_license_status as dls

    monkeypatch.setattr(hb.cfg, "DEPLOYMENT_LICENSE_KEY", "key")
    monkeypatch.setattr(hb.cfg, "PORTAL_API_BASE_URL", "https://portal.example")
    monkeypatch.setattr(hb, "_public_ip_from_origin", lambda: "203.0.113.1")
    dls.reset_for_tests()

    class FakeResponse:
        status_code = 403
        text = '{"detail": "License revoked"}'

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
            assert dls.get_license_status() is None

    asyncio.run(_run())


def test_post_heartbeat_updates_license_status_on_200_expired(monkeypatch):
    from icici_breeze_backend.app.services import deployment_license_status as dls
    from tests.fixtures.portal_heartbeat_drm_keys import attach_test_policy_token

    monkeypatch.setattr(hb.cfg, "DEPLOYMENT_LICENSE_KEY", "key")
    monkeypatch.setattr(hb.cfg, "PORTAL_API_BASE_URL", "https://portal.example")
    monkeypatch.setattr(hb, "_public_ip_from_origin", lambda: "203.0.113.1")
    dls.reset_for_tests()

    signed_body = attach_test_policy_token(
        {
            "status": "OK",
            "trigger_upgrade": False,
            "deployment_license_status": "expired",
        },
        public_ip="203.0.113.1",
    )

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return signed_body

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


def test_heartbeat_loop_enabled_without_license_key(monkeypatch):
    monkeypatch.setattr(hb.cfg, "DEPLOYMENT_LICENSE_KEY", "")
    monkeypatch.setattr(hb.cfg, "PORTAL_API_BASE_URL", "https://portal.example")
    monkeypatch.setattr(hb, "_public_ip_from_origin", lambda: "203.0.113.1")
    assert hb.heartbeat_loop_enabled() is True


def test_post_heartbeat_omits_empty_license_key(monkeypatch):
    from tests.fixtures.portal_heartbeat_drm_keys import attach_test_policy_token

    monkeypatch.setattr(hb.cfg, "DEPLOYMENT_LICENSE_KEY", "")
    monkeypatch.setattr(hb.cfg, "PORTAL_API_BASE_URL", "https://portal.example")
    monkeypatch.setattr(hb, "_public_ip_from_origin", lambda: "203.0.113.1")

    captured: dict = {}
    signed_body = attach_test_policy_token(
        {
            "status": "OK",
            "deployment_license_status": "unlicensed",
            "deployment_license_read_only": True,
        },
        public_ip="203.0.113.1",
    )

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return signed_body

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
            captured["json"] = json
            return FakeResponse()

    async def _run():
        with patch.object(hb.httpx, "AsyncClient", FakeClient):
            await hb.post_heartbeat()

    asyncio.run(_run())
    assert "license_key" not in captured["json"]
    assert captured["json"]["public_ip"] == "203.0.113.1"


def test_resolve_upgrade_image_replaces_tag(monkeypatch):
    monkeypatch.setattr(hb.cfg, "DEPLOYMENT_GHCR_IMAGE", "ghcr.io/org/breeze-core-engine:v1.0.0")
    assert hb._resolve_upgrade_image("latest") == "ghcr.io/org/breeze-core-engine:latest"


def test_execute_upgrade_pulls_and_recreates_container(monkeypatch):
    monkeypatch.setattr(hb.cfg, "DEPLOYMENT_GHCR_IMAGE", "ghcr.io/org/breeze-core-engine:latest")
    monkeypatch.setattr(hb.cfg, "DEPLOYMENT_CONTAINER_NAME", "breeze-core-engine")

    mock_client = MagicMock()
    docker_mod = types.ModuleType("docker")
    docker_errors = types.ModuleType("docker.errors")
    docker_errors.DockerException = Exception
    docker_errors.APIError = Exception
    docker_mod.from_env = MagicMock(return_value=mock_client)
    docker_mod.errors = docker_errors
    monkeypatch.setitem(sys.modules, "docker", docker_mod)
    monkeypatch.setitem(sys.modules, "docker.errors", docker_errors)

    with patch(
        "icici_breeze_backend.app.services.deployment_container_upgrade.schedule_recreate_via_helper",
    ) as schedule:
        hb.execute_upgrade("latest")

    mock_client.images.pull.assert_called_once_with("ghcr.io/org/breeze-core-engine:latest")
    schedule.assert_called_once_with(
        mock_client,
        image="ghcr.io/org/breeze-core-engine:latest",
        container_name="breeze-core-engine",
    )


def _fake_docker_module(monkeypatch, mock_client):
    docker_mod = types.ModuleType("docker")
    docker_errors = types.ModuleType("docker.errors")
    docker_errors.DockerException = Exception
    docker_errors.APIError = Exception
    docker_mod.from_env = MagicMock(return_value=mock_client)
    docker_mod.errors = docker_errors
    monkeypatch.setitem(sys.modules, "docker", docker_mod)
    monkeypatch.setitem(sys.modules, "docker.errors", docker_errors)


def _client_running(image_ref: str, *, digests: list[str] | None = None):
    """Mock docker client whose deployment container runs `image_ref`."""
    client = MagicMock()
    container = MagicMock()
    container.attrs = {"Config": {"Image": image_ref}, "Image": "sha256:localid"}
    client.containers.get.return_value = container
    image = MagicMock()
    image.attrs = {"RepoDigests": digests if digests is not None else []}
    client.images.get.return_value = image
    return client


def test_apply_env_overrides_recreates_with_version_stamped(monkeypatch):
    monkeypatch.setattr(hb.cfg, "DEPLOYMENT_GHCR_IMAGE", "ghcr.io/org/breeze-core-engine:v1.0.0")
    monkeypatch.setattr(hb.cfg, "DEPLOYMENT_CONTAINER_NAME", "breeze-core-engine")

    mock_client = _client_running("ghcr.io/org/breeze-core-engine:v1.0.0")
    _fake_docker_module(monkeypatch, mock_client)

    with patch(
        "icici_breeze_backend.app.services.deployment_container_upgrade.schedule_recreate_via_helper",
    ) as schedule:
        hb.apply_env_overrides({"TELEGRAM_BOT_TOKEN": "123:abc"}, "hash123")

    # Same currently-running image — no upgrade, config-only recreate.
    mock_client.images.pull.assert_not_called()
    schedule.assert_called_once_with(
        mock_client,
        image="ghcr.io/org/breeze-core-engine:v1.0.0",
        container_name="breeze-core-engine",
        env_overrides={"TELEGRAM_BOT_TOKEN": "123:abc", "BREEZE_ENV_OVERRIDES_VERSION": "hash123"},
    )


def test_apply_env_overrides_never_reinstalls_latest(monkeypatch):
    """
    Regression: an env-override push must not downgrade an upgraded instance.

    DEPLOYMENT_GHCR_IMAGE reads `...:latest` on deployment hosts. Recreating from
    it reinstalled whatever `latest` pointed at — observed live, where a portal
    upgrade to 2.1.0-b was undone 11 seconds later by a Telegram-token push.
    """
    monkeypatch.setattr(hb.cfg, "DEPLOYMENT_GHCR_IMAGE", "ghcr.io/org/breeze-core-engine:latest")
    monkeypatch.setattr(hb.cfg, "DEPLOYMENT_CONTAINER_NAME", "breeze-core-engine")

    mock_client = _client_running(
        "ghcr.io/org/breeze-core-engine:2.1.0-b",
        digests=["ghcr.io/org/breeze-core-engine@sha256:47a30968"],
    )
    _fake_docker_module(monkeypatch, mock_client)

    with patch(
        "icici_breeze_backend.app.services.deployment_container_upgrade.schedule_recreate_via_helper",
    ) as schedule:
        hb.apply_env_overrides({"TELEGRAM_BOT_TOKEN": "123:abc"}, "hash123")

    image = schedule.call_args.kwargs["image"]
    assert image == "ghcr.io/org/breeze-core-engine@sha256:47a30968"
    assert not image.endswith(":latest")


def test_apply_env_overrides_falls_back_when_inspect_fails(monkeypatch):
    """Config pushes must still land when the container can't be inspected."""
    monkeypatch.setattr(hb.cfg, "DEPLOYMENT_GHCR_IMAGE", "ghcr.io/org/breeze-core-engine:v1.0.0")
    monkeypatch.setattr(hb.cfg, "DEPLOYMENT_CONTAINER_NAME", "breeze-core-engine")

    mock_client = MagicMock()
    mock_client.containers.get.side_effect = Exception("no such container")
    _fake_docker_module(monkeypatch, mock_client)

    with patch(
        "icici_breeze_backend.app.services.deployment_container_upgrade.schedule_recreate_via_helper",
    ) as schedule:
        hb.apply_env_overrides({"TELEGRAM_BOT_TOKEN": "123:abc"}, "hash123")

    assert schedule.call_args.kwargs["image"] == "ghcr.io/org/breeze-core-engine:v1.0.0"


def test_maybe_apply_env_overrides_noop_when_absent():
    async def _run():
        with patch.object(hb, "apply_env_overrides") as apply:
            await hb._maybe_apply_env_overrides({"status": "OK"})
            apply.assert_not_called()

    asyncio.run(_run())


def test_maybe_apply_env_overrides_noop_when_version_unchanged(monkeypatch):
    monkeypatch.setenv("BREEZE_ENV_OVERRIDES_VERSION", "hash123")

    async def _run():
        with patch.object(hb, "apply_env_overrides") as apply:
            await hb._maybe_apply_env_overrides(
                {
                    "status": "OK",
                    "env_overrides": {"TELEGRAM_BOT_TOKEN": "123:abc"},
                    "env_overrides_version": "hash123",
                }
            )
            apply.assert_not_called()

    asyncio.run(_run())


def test_maybe_apply_env_overrides_applies_when_version_changed(monkeypatch):
    monkeypatch.setenv("BREEZE_ENV_OVERRIDES_VERSION", "old-hash")

    async def _run():
        with patch.object(hb, "apply_env_overrides") as apply:
            await hb._maybe_apply_env_overrides(
                {
                    "status": "OK",
                    "env_overrides": {"TELEGRAM_BOT_TOKEN": "123:abc"},
                    "env_overrides_version": "new-hash",
                }
            )
            apply.assert_called_once_with({"TELEGRAM_BOT_TOKEN": "123:abc"}, "new-hash")

    asyncio.run(_run())


def test_heartbeat_tick_applies_env_overrides_independently_of_upgrade(monkeypatch):
    monkeypatch.delenv("BREEZE_ENV_OVERRIDES_VERSION", raising=False)

    async def _run():
        with patch.object(
            hb,
            "post_heartbeat",
            return_value={
                "status": "OK",
                "env_overrides": {"TELEGRAM_BOT_USERNAME": "MyBot"},
                "env_overrides_version": "v2",
            },
        ):
            with patch.object(hb, "execute_upgrade") as upgrade, patch.object(
                hb, "apply_env_overrides"
            ) as apply:
                await hb.heartbeat_tick()
                upgrade.assert_not_called()
                apply.assert_called_once_with({"TELEGRAM_BOT_USERNAME": "MyBot"}, "v2")

    asyncio.run(_run())
