"""First-boot Redis provisioning for containers recreated by a pre-Redis 2.0.x image."""
from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock

import pytest

from icici_breeze_backend.app.services import deployment_redis_selfheal as heal


@pytest.fixture(autouse=True)
def _mock_docker(monkeypatch):
    docker_errors = types.ModuleType("docker.errors")
    docker_errors.NotFound = type("NotFound", (Exception,), {})
    docker_errors.APIError = type("APIError", (Exception,), {})
    docker_errors.DockerException = type("DockerException", (Exception,), {})
    sys.modules["docker.errors"] = docker_errors

    docker_mod = types.ModuleType("docker")
    docker_mod.errors = docker_errors
    docker_mod.from_env = MagicMock()
    sys.modules["docker"] = docker_mod
    yield docker_mod
    sys.modules.pop("docker.errors", None)
    sys.modules.pop("docker", None)


@pytest.fixture(autouse=True)
def _managed_deployment(monkeypatch, tmp_path):
    """Look like a portal-managed deployment with its data volume in tmp_path."""
    monkeypatch.setattr(heal.cfg, "DEPLOYMENT_GHCR_IMAGE", "ghcr.io/org/breeze-core-engine:latest")
    monkeypatch.setattr(heal.cfg, "DEPLOYMENT_CONTAINER_NAME", "breeze-core-engine")
    monkeypatch.setattr(heal.cfg, "USERS_DB", str(tmp_path / "users.sqlite3"))
    monkeypatch.delenv("DEPLOYMENT_REDIS_SELF_HEAL", raising=False)


def _container(*, networks: dict | None = None, image_ref: str = "ghcr.io/org/breeze-core-engine:2.1.0-b"):
    c = MagicMock()
    c.attrs = {
        "Config": {"Image": image_ref},
        "Image": "sha256:abc123",
        "NetworkSettings": {"Networks": networks if networks is not None else {"bridge": {}}},
    }
    return c


def _client(container):
    client = MagicMock()
    client.containers.get.return_value = container
    image = MagicMock()
    image.attrs = {"RepoDigests": ["ghcr.io/org/breeze-core-engine@sha256:deadbeef"]}
    client.images.get.return_value = image
    return client


def _patch_redis(monkeypatch, *, available: bool):
    mod = types.ModuleType("icici_breeze_backend.app.db.redis_client")
    mod.redis_available = lambda: available
    monkeypatch.setitem(sys.modules, "icici_breeze_backend.app.db.redis_client", mod)


def _patch_upgrade(monkeypatch, ensure=None, recreate=None):
    import icici_breeze_backend.app.services.deployment_container_upgrade as dcu

    monkeypatch.setattr(dcu, "ensure_redis_sidecar_sdk", ensure or MagicMock())
    monkeypatch.setattr(dcu, "schedule_recreate_via_helper", recreate or MagicMock())
    return dcu


def test_noop_when_redis_already_reachable(monkeypatch, _mock_docker):
    _patch_redis(monkeypatch, available=True)
    heal.run_redis_self_heal_if_needed()
    _mock_docker.from_env.assert_not_called()


def test_noop_outside_managed_deployment(monkeypatch, _mock_docker):
    """Dev and compose have no DEPLOYMENT_GHCR_IMAGE; never recreate a dev container."""
    monkeypatch.setattr(heal.cfg, "DEPLOYMENT_GHCR_IMAGE", "")
    _patch_redis(monkeypatch, available=False)
    heal.run_redis_self_heal_if_needed()
    _mock_docker.from_env.assert_not_called()


def test_opt_out_env_disables(monkeypatch, _mock_docker):
    monkeypatch.setenv("DEPLOYMENT_REDIS_SELF_HEAL", "false")
    _patch_redis(monkeypatch, available=False)
    heal.run_redis_self_heal_if_needed()
    _mock_docker.from_env.assert_not_called()


def test_provisions_and_recreates_when_off_network(monkeypatch, _mock_docker):
    """The 2.0.x-upgraded fingerprint: Redis down, container on the default bridge."""
    _patch_redis(monkeypatch, available=False)
    ensure, recreate = MagicMock(), MagicMock()
    _patch_upgrade(monkeypatch, ensure, recreate)
    client = _client(_container(networks={"bridge": {}}))
    _mock_docker.from_env.return_value = client

    heal.run_redis_self_heal_if_needed()

    ensure.assert_called_once_with(client)
    recreate.assert_called_once()
    assert recreate.call_args.kwargs["container_name"] == "breeze-core-engine"


def test_recreates_from_own_image_not_deployment_ghcr_image(monkeypatch, _mock_docker):
    """Recreating from DEPLOYMENT_GHCR_IMAGE (:latest == 2.0.1) would downgrade us."""
    _patch_redis(monkeypatch, available=False)
    recreate = MagicMock()
    _patch_upgrade(monkeypatch, recreate=recreate)
    _mock_docker.from_env.return_value = _client(_container(networks={"bridge": {}}))

    heal.run_redis_self_heal_if_needed()

    image = recreate.call_args.kwargs["image"]
    assert image == "ghcr.io/org/breeze-core-engine@sha256:deadbeef"
    assert "latest" not in image


def test_falls_back_to_config_image_without_digest(monkeypatch, _mock_docker):
    _patch_redis(monkeypatch, available=False)
    recreate = MagicMock()
    _patch_upgrade(monkeypatch, recreate=recreate)
    client = _client(_container(networks={"bridge": {}}))
    client.images.get.side_effect = RuntimeError("no such image")
    _mock_docker.from_env.return_value = client

    heal.run_redis_self_heal_if_needed()

    assert recreate.call_args.kwargs["image"] == "ghcr.io/org/breeze-core-engine:2.1.0-b"


def test_on_network_provisions_sidecar_but_does_not_recreate(monkeypatch, _mock_docker):
    """Already correctly homed: a transient Redis outage must not bounce the app."""
    _patch_redis(monkeypatch, available=False)
    ensure, recreate = MagicMock(), MagicMock()
    _patch_upgrade(monkeypatch, ensure, recreate)
    _mock_docker.from_env.return_value = _client(_container(networks={"breeze-core-net": {}}))

    heal.run_redis_self_heal_if_needed()

    ensure.assert_called_once()
    recreate.assert_not_called()


def test_attempt_is_recorded_before_recreate(monkeypatch, _mock_docker, tmp_path):
    """The recreate kills this process; an attempt written after it never lands."""
    _patch_redis(monkeypatch, available=False)
    seen = {}

    def _recreate(client, **kwargs):
        seen["attempts"] = json.loads((tmp_path / heal._STATE_FILENAME).read_text())["attempts"]

    _patch_upgrade(monkeypatch, recreate=MagicMock(side_effect=_recreate))
    _mock_docker.from_env.return_value = _client(_container(networks={"bridge": {}}))

    heal.run_redis_self_heal_if_needed()

    assert seen["attempts"] == 1


def test_gives_up_after_max_attempts(monkeypatch, _mock_docker, tmp_path):
    _patch_redis(monkeypatch, available=False)
    recreate = MagicMock()
    _patch_upgrade(monkeypatch, recreate=recreate)
    _mock_docker.from_env.return_value = _client(_container(networks={"bridge": {}}))
    (tmp_path / heal._STATE_FILENAME).write_text(json.dumps({"attempts": heal._MAX_ATTEMPTS}))

    heal.run_redis_self_heal_if_needed()

    recreate.assert_not_called()


def test_success_clears_attempt_marker(monkeypatch, _mock_docker, tmp_path):
    """Post-recreate boot sees working Redis and resets, so later outages get retries."""
    marker = tmp_path / heal._STATE_FILENAME
    marker.write_text(json.dumps({"attempts": 1}))
    _patch_redis(monkeypatch, available=True)

    heal.run_redis_self_heal_if_needed()

    assert not marker.exists()


def test_survives_missing_docker_socket(monkeypatch, _mock_docker):
    """Degraded start beats no start: never raise into the lifespan."""
    _patch_redis(monkeypatch, available=False)
    _patch_upgrade(monkeypatch)
    _mock_docker.from_env.side_effect = RuntimeError("permission denied: /var/run/docker.sock")

    heal.run_redis_self_heal_if_needed()  # must not raise


def test_survives_sidecar_provisioning_failure(monkeypatch, _mock_docker):
    _patch_redis(monkeypatch, available=False)
    recreate = MagicMock()
    _patch_upgrade(monkeypatch, ensure=MagicMock(side_effect=RuntimeError("pull failed")), recreate=recreate)
    _mock_docker.from_env.return_value = _client(_container(networks={"bridge": {}}))

    heal.run_redis_self_heal_if_needed()  # must not raise

    recreate.assert_not_called()
