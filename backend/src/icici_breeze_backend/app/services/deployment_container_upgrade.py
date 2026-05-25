"""In-place Core Engine container upgrade (matches CFN bootstrap docker run)."""
from __future__ import annotations

import logging
import os
import shlex
import time
from typing import Any

import icici_breeze_backend.app.core.config as cfg

logger = logging.getLogger(__name__)

_DEFAULT_ENV_FILE = "/opt/breeze-core-engine/.env"
_DEFAULT_DATA_HOST = "/opt/breeze-core-engine/data"
_DEFAULT_HOST_PORT = 80
_CONTAINER_PORT = 3000
# Recreate must run in a sibling container — stopping the app from inside kills the upgrade process.
_UPGRADE_HELPER_IMAGE = "docker:cli"
_UPGRADE_PLATFORM = "linux/arm64"


def deployment_env_file_path() -> str:
    return (getattr(cfg, "DEPLOYMENT_ENV_FILE", None) or _DEFAULT_ENV_FILE).strip() or _DEFAULT_ENV_FILE


def deployment_data_host_path() -> str:
    return (getattr(cfg, "DEPLOYMENT_DATA_HOST_PATH", None) or _DEFAULT_DATA_HOST).strip() or _DEFAULT_DATA_HOST


def deployment_publish_port() -> int:
    raw = getattr(cfg, "DEPLOYMENT_PUBLISH_PORT", None)
    try:
        return int(raw) if raw is not None else _DEFAULT_HOST_PORT
    except (TypeError, ValueError):
        return _DEFAULT_HOST_PORT


def parse_dotenv_text(text: str) -> dict[str, str]:
    """Parse KEY=value lines from .env file content."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip('"').strip("'")
        out[key] = value
    return out


def parse_dotenv_file(path: str) -> dict[str, str]:
    """Parse KEY=value lines from a .env file on the local filesystem."""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return parse_dotenv_text(fh.read())
    except OSError as exc:
        logger.warning("deployment upgrade: could not read env file %s: %s", path, exc)
        return {}


def read_host_env_file(client: Any, path: str) -> dict[str, str]:
    """
    Load host .env for upgrade. Prefer a local read (bind-mounted path); otherwise
    read via a one-shot container on the host Docker socket (pre-mount EC2 stacks).
    """
    env = parse_dotenv_file(path)
    if env:
        return env

    try:
        from docker.errors import APIError, DockerException

        raw = client.containers.run(
            "alpine:3.20",
            ["cat", path],
            remove=True,
            volumes={path: {"bind": path, "mode": "ro"}},
        )
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="replace")
        else:
            text = str(raw or "")
        env = parse_dotenv_text(text)
        if env:
            logger.info(
                "deployment upgrade: loaded %d keys from host env file %s via docker",
                len(env),
                path,
            )
        return env
    except (APIError, DockerException, OSError) as exc:
        logger.warning("deployment upgrade: could not read host env file %s: %s", path, exc)
        return {}


def _env_list_from_container_attrs(attrs: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in attrs.get("Config", {}).get("Env") or []:
        if not isinstance(item, str) or "=" not in item:
            continue
        key, _, value = item.partition("=")
        if key:
            out[key] = value
    return out


def resolve_recreate_environment(client: Any, container_name: str, env_file: str) -> dict[str, str]:
    """
    Environment for the new container: on-disk .env wins over the running container.
    """
    env = read_host_env_file(client, env_file)
    try:
        old = client.containers.get(container_name)
        inherited = _env_list_from_container_attrs(old.attrs or {})
        if inherited:
            merged = 0
            for key, value in inherited.items():
                if key not in env:
                    env[key] = value
                    merged += 1
            if merged:
                logger.info(
                    "deployment upgrade: merged %d keys from existing container %s",
                    merged,
                    container_name,
                )
    except Exception as exc:  # noqa: BLE001
        if exc.__class__.__name__ != "NotFound":
            logger.debug("deployment upgrade: could not read env from existing container: %s", exc)

    if not env:
        logger.warning(
            "deployment upgrade: no environment resolved (env file %s missing or empty?)",
            env_file,
        )
    return env


def upgrade_shell_script(
    *,
    image: str,
    container_name: str,
    env_file: str,
    data_host: str,
    host_port: int,
) -> str:
    """Shell recreate matching CFN bootstrap; runs on host via docker CLI helper."""
    return "\n".join(
        [
            "set -eu",
            f"docker pull {shlex.quote(image)}",
            f"docker rm -f {shlex.quote(container_name)} 2>/dev/null || true",
            "docker run -d "
            f"--name {shlex.quote(container_name)} "
            "--restart unless-stopped "
            f"-p {int(host_port)}:{_CONTAINER_PORT} "
            f"-v {shlex.quote(data_host)}:/app/backend/data "
            f"-v {shlex.quote(env_file)}:{shlex.quote(env_file)}:ro "
            "-v /var/run/docker.sock:/var/run/docker.sock "
            f"--env-file {shlex.quote(env_file)} "
            f"{shlex.quote(image)}",
        ]
    )


def pull_upgrade_helper_image(client: Any) -> None:
    """Ensure the docker-cli helper image is present (EC2 hosts are arm64)."""
    from docker.errors import APIError, DockerException

    try:
        logger.info("deployment upgrade: pulling helper image %s", _UPGRADE_HELPER_IMAGE)
        client.images.pull(_UPGRADE_HELPER_IMAGE, platform=_UPGRADE_PLATFORM)
    except TypeError:
        client.images.pull(_UPGRADE_HELPER_IMAGE)
    except (APIError, DockerException) as exc:
        logger.warning("deployment upgrade: helper image pull failed: %s", exc)
        raise


def schedule_recreate_via_helper(client: Any, *, image: str, container_name: str) -> None:
    """
    Run stop/rm/run in a detached docker-cli container so the app container can be
    replaced without killing the process that scheduled the upgrade.
    """
    from docker.errors import APIError, DockerException

    env_file = deployment_env_file_path()
    data_host = deployment_data_host_path()
    host_port = deployment_publish_port()
    script = upgrade_shell_script(
        image=image,
        container_name=container_name,
        env_file=env_file,
        data_host=data_host,
        host_port=host_port,
    )
    pull_upgrade_helper_image(client)

    helper_name = f"breeze-upgrade-{int(time.time())}"
    logger.info(
        "deployment upgrade: launching detached helper %s to recreate %s (env_file=%s); "
        "this container will be replaced shortly",
        helper_name,
        container_name,
        env_file,
    )
    try:
        helper = client.containers.run(
            _UPGRADE_HELPER_IMAGE,
            entrypoint=["sh", "-c"],
            command=[script],
            name=helper_name,
            detach=True,
            remove=True,
            volumes={"/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"}},
        )
    except (APIError, DockerException) as exc:
        logger.warning("deployment upgrade: helper launch failed: %s", exc)
        raise
    helper_id = getattr(helper, "id", helper)
    logger.info(
        "deployment upgrade: helper started id=%s name=%s — inspect with: docker logs %s",
        helper_id,
        helper_name,
        helper_name,
    )


def recreate_deployment_container(client: Any, *, image: str, container_name: str) -> None:
    """
    Pull image and replace the deployment container using the host .env file
    (same contract as infra/breeze-core-engine-stack.yaml bootstrap).
    """
    from docker.errors import APIError, DockerException, NotFound

    env_file = deployment_env_file_path()
    data_host = deployment_data_host_path()
    host_port = deployment_publish_port()

    environment = resolve_recreate_environment(client, container_name, env_file)

    try:
        old = client.containers.get(container_name)
        logger.info("deployment upgrade: stopping %s", container_name)
        old.stop(timeout=120)
        old.remove(force=True)
    except NotFound:
        pass
    except (APIError, DockerException) as exc:
        logger.warning("deployment upgrade: could not remove old container: %s", exc)
        raise

    volumes = {
        data_host: {"bind": "/app/backend/data", "mode": "rw"},
        "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"},
    }
    volumes[env_file] = {"bind": env_file, "mode": "ro"}

    logger.info(
        "deployment upgrade: starting %s from %s (env_file=%s, port %s:%s)",
        container_name,
        image,
        env_file,
        host_port,
        _CONTAINER_PORT,
    )
    client.containers.run(
        image,
        name=container_name,
        detach=True,
        restart_policy={"Name": "unless-stopped"},
        ports={f"{_CONTAINER_PORT}/tcp": host_port},
        volumes=volumes,
        environment=environment,
    )
    logger.info("deployment upgrade: container %s is up", container_name)
