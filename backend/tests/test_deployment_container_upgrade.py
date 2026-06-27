"""In-place deployment container upgrade (host .env preserved)."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from icici_breeze_backend.app.services import deployment_container_upgrade as dcu


@pytest.fixture(autouse=True)
def _mock_docker_errors():
    docker_errors = types.ModuleType("docker.errors")
    docker_errors.NotFound = type("NotFound", (Exception,), {})
    docker_errors.APIError = type("APIError", (Exception,), {})
    docker_errors.DockerException = type("DockerException", (Exception,), {})
    sys.modules["docker.errors"] = docker_errors
    yield
    sys.modules.pop("docker.errors", None)


def test_ensure_redis_url_in_env_adds_default():
    env = dcu.ensure_redis_url_in_env({})
    assert env["REDIS_URL"] == dcu.DEFAULT_REDIS_URL


def test_ensure_redis_url_in_env_preserves_custom():
    custom = "redis://custom-host:6380/1"
    env = dcu.ensure_redis_url_in_env({"REDIS_URL": custom})
    assert env["REDIS_URL"] == custom


def test_parse_dotenv_file(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES=1440\n# comment\nCOOKIE_SECURE=false\n",
        encoding="utf-8",
    )
    assert dcu.parse_dotenv_file(str(env_path)) == {
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "1440",
        "COOKIE_SECURE": "false",
    }


def test_read_host_env_file_via_docker_when_not_mounted(monkeypatch):
    mock_client = MagicMock()
    mock_client.containers.run.return_value = b"JWT_ACCESS_TOKEN_EXPIRE_MINUTES=1440\n"
    monkeypatch.setattr(dcu, "parse_dotenv_file", lambda _path: {})
    env = dcu.read_host_env_file(mock_client, "/opt/breeze-core-engine/.env")
    assert env["JWT_ACCESS_TOKEN_EXPIRE_MINUTES"] == "1440"
    mock_client.containers.run.assert_called_once()


def test_resolve_recreate_environment_prefers_file(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("JWT_ACCESS_TOKEN_EXPIRE_MINUTES=1440\n", encoding="utf-8")

    mock_client = MagicMock()
    mock_old = MagicMock()
    mock_old.attrs = {"Config": {"Env": ["JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15", "PATH=/usr/bin"]}}
    mock_client.containers.get.return_value = mock_old

    monkeypatch.setattr(
        dcu,
        "read_host_env_file",
        lambda _c, _p: dcu.parse_dotenv_file(str(env_path)),
    )
    env = dcu.resolve_recreate_environment(mock_client, "breeze-core-engine", str(env_path))
    assert env["JWT_ACCESS_TOKEN_EXPIRE_MINUTES"] == "1440"
    assert env["PATH"] == "/usr/bin"


def test_format_dotenv_text_quotes_spaces():
    text = dcu.format_dotenv_text({"JWT_SECRET": "abc", "ALLOWED_ORIGINS": "http://1.2.3.4 http://5.6.7.8"})
    assert 'ALLOWED_ORIGINS="http://1.2.3.4 http://5.6.7.8"' in text
    assert "JWT_SECRET=abc\n" in text


def test_format_bytes_reclaimed():
    assert dcu._format_bytes_reclaimed(0) == "0B"
    assert dcu._format_bytes_reclaimed(512) == "512B"
    assert dcu._format_bytes_reclaimed(1536) == "1.5KB"
    assert dcu._format_bytes_reclaimed(5 * 1024 * 1024) == "5.0MB"


def test_upgrade_shell_script_uses_env_file():
    script = dcu.upgrade_shell_script(
        image="ghcr.io/org/breeze-core-engine:latest",
        container_name="breeze-core-engine",
        env_file="/opt/breeze-core-engine/.upgrade.env",
        data_host="/opt/breeze-core-engine/data",
        host_port=80,
    )
    assert 'ENV_FILE=/opt/breeze-core-engine/.upgrade.env' in script
    assert 'docker rm -f "$NAME"' in script
    assert "still exists after docker rm" in script
    assert "not running after docker run" in script
    assert "docker container prune -f" in script
    assert "docker image prune -f" in script
    assert "stopped containers pruned successfully" in script
    assert "dangling images pruned successfully" in script
    assert "prune complete:" in script
    assert "-p 80:3000" in script
    assert "upgrade.log" in script
    assert "breeze-core-net" in script
    assert "breeze-redis" in script
    assert "redis:7-alpine" in script
    assert dcu.DEFAULT_REDIS_URL in script


def test_prepare_upgrade_env_file_writes_host(tmp_path, monkeypatch):
    mock_client = MagicMock()
    written: dict[str, str] = {}

    def _capture_write(_c, path, content, **_):
        written[path] = content
        return path

    monkeypatch.setattr(
        dcu,
        "resolve_recreate_environment",
        lambda _c, _n, _p: {"JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "1440"},
    )
    monkeypatch.setattr(dcu, "write_host_file_via_docker", _capture_write)

    path = dcu.prepare_upgrade_env_file(mock_client, "breeze-core-engine")
    assert path == dcu._UPGRADE_ENV_FILE
    assert dcu.DEFAULT_REDIS_URL in written[dcu._UPGRADE_ENV_FILE]
    assert dcu.DEFAULT_REDIS_URL in written[dcu._DEFAULT_ENV_FILE]


def test_schedule_recreate_via_helper_detached_cli(monkeypatch):
    mock_client = MagicMock()
    mock_helper = MagicMock()
    mock_helper.id = "helper123"
    mock_client.containers.run.return_value = mock_helper
    monkeypatch.setattr(dcu, "prepare_upgrade_env_file", lambda _c, _n: "/opt/breeze-core-engine/.upgrade.env")
    monkeypatch.setattr(dcu, "deployment_data_host_path", lambda: "/opt/breeze-core-engine/data")
    monkeypatch.setattr(dcu, "deployment_publish_port", lambda: 80)
    monkeypatch.setattr(dcu, "pull_upgrade_helper_image", lambda _c: None)

    dcu.schedule_recreate_via_helper(
        mock_client,
        image="ghcr.io/org/breeze-core-engine:latest",
        container_name="breeze-core-engine",
    )

    mock_client.containers.run.assert_called_once()
    args, kwargs = mock_client.containers.run.call_args
    assert args[0] == "docker:cli"
    assert kwargs["detach"] is True
    assert kwargs["remove"] is True
    assert "/var/run/docker.sock" in kwargs["volumes"]
    assert dcu._DEPLOY_ROOT in kwargs["volumes"]
    assert 'ENV_FILE=' in kwargs["command"][0]


def test_recreate_deployment_container_stops_and_runs_with_env(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES=1440\nDEPLOYMENT_GHCR_IMAGE=ghcr.io/org/app:latest\n",
        encoding="utf-8",
    )
    data_path = tmp_path / "data"
    data_path.mkdir()

    monkeypatch.setattr(dcu, "deployment_env_file_path", lambda: str(env_path))
    monkeypatch.setattr(dcu, "deployment_data_host_path", lambda: str(data_path))
    monkeypatch.setattr(dcu, "deployment_publish_port", lambda: 80)
    monkeypatch.setattr(dcu, "ensure_redis_sidecar_sdk", lambda _c: None)
    monkeypatch.setattr(dcu, "write_host_file_via_docker", lambda _c, _p, _t, **_: None)

    mock_client = MagicMock()
    mock_old = MagicMock()
    mock_client.containers.get.return_value = mock_old

    dcu.recreate_deployment_container(
        mock_client,
        image="ghcr.io/org/breeze-core-engine:latest",
        container_name="breeze-core-engine",
    )

    mock_old.stop.assert_called_once()
    mock_old.remove.assert_called_once_with(force=True)
    mock_client.containers.run.assert_called_once()
    mock_client.containers.prune.assert_called_once()
    mock_client.images.prune.assert_called_once_with(filters={"dangling": True})
    _, kwargs = mock_client.containers.run.call_args
    assert kwargs["name"] == "breeze-core-engine"
    assert kwargs["environment"]["JWT_ACCESS_TOKEN_EXPIRE_MINUTES"] == "1440"
    assert kwargs["environment"]["REDIS_URL"] == dcu.DEFAULT_REDIS_URL
    assert kwargs["network"] == dcu.REDIS_NETWORK_NAME
    assert kwargs["ports"] == {"3000/tcp": 80}
    assert str(data_path) in kwargs["volumes"]
    assert str(env_path) in kwargs["volumes"]

