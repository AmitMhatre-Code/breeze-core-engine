"""In-place Core Engine container upgrade (matches CFN bootstrap docker run)."""
from __future__ import annotations

import base64
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
_UPGRADE_ENV_FILE = "/opt/breeze-core-engine/.upgrade.env"
_UPGRADE_LOG_FILE = "/opt/breeze-core-engine/upgrade.log"
_DEPLOY_ROOT = "/opt/breeze-core-engine"
# Recreate must run in a sibling container — stopping the app from inside kills the upgrade process.
_UPGRADE_HELPER_IMAGE = "docker:cli"
_UPGRADE_PLATFORM = "linux/arm64"

REDIS_NETWORK_NAME = "breeze-core-net"
REDIS_CONTAINER_NAME = "breeze-redis"
REDIS_IMAGE = "redis:7-alpine"
DEFAULT_REDIS_URL = "redis://breeze-redis:6379/0"


def ensure_redis_url_in_env(env: dict[str, str]) -> dict[str, str]:
    """Set REDIS_URL when absent; preserve operator overrides."""
    if not (env.get("REDIS_URL") or "").strip():
        env["REDIS_URL"] = DEFAULT_REDIS_URL
    return env


def _redis_sidecar_shell_lines() -> list[str]:
    """Idempotent bash to create network and start breeze-redis before app recreate."""
    return [
        'echo "=== ensuring redis sidecar ==="',
        f"docker network create {REDIS_NETWORK_NAME} 2>/dev/null || true",
        f'if ! docker ps --format \'{{{{.Names}}}}\' | grep -Fxq {REDIS_CONTAINER_NAME}; then',
        f"  docker pull {REDIS_IMAGE}",
        f"  docker rm -f {REDIS_CONTAINER_NAME} 2>/dev/null || true",
        f"  docker run -d --name {REDIS_CONTAINER_NAME} --network {REDIS_NETWORK_NAME} "
        f"--restart unless-stopped {REDIS_IMAGE}",
        "fi",
        f"for _i in $(seq 1 30); do",
        f"  if docker exec {REDIS_CONTAINER_NAME} redis-cli ping 2>/dev/null | grep -q PONG; then break; fi",
        "  sleep 1",
        "done",
        f"grep -q '^REDIS_URL=' \"$ENV_FILE\" || echo '{DEFAULT_REDIS_URL}' >> \"$ENV_FILE\"",
    ]


def ensure_redis_sidecar_sdk(client: Any) -> None:
    """Ensure Docker network and breeze-redis container exist (SDK path)."""
    from docker.errors import APIError, DockerException, NotFound

    try:
        client.networks.get(REDIS_NETWORK_NAME)
    except NotFound:
        try:
            client.networks.create(REDIS_NETWORK_NAME, driver="bridge")
        except APIError as exc:
            if "already exists" not in str(exc).lower():
                raise

    try:
        redis_container = client.containers.get(REDIS_CONTAINER_NAME)
        if redis_container.status != "running":
            redis_container.remove(force=True)
            raise NotFound("redis not running")
    except NotFound:
        try:
            client.images.pull(REDIS_IMAGE)
        except (APIError, DockerException) as exc:
            logger.warning("deployment upgrade: redis image pull failed: %s", exc)
            raise
        client.containers.run(
            REDIS_IMAGE,
            name=REDIS_CONTAINER_NAME,
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            network=REDIS_NETWORK_NAME,
        )
        logger.info("deployment upgrade: started %s on %s", REDIS_CONTAINER_NAME, REDIS_NETWORK_NAME)


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


def format_dotenv_text(env: dict[str, str]) -> str:
    """Serialize environment for docker --env-file (KEY=value lines)."""
    lines: list[str] = []
    for key in sorted(env):
        value = env[key]
        if not key:
            continue
        if any(ch in value for ch in " \t\n#'\""):
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key}="{escaped}"')
        else:
            lines.append(f"{key}={value}")
    return "\n".join(lines) + ("\n" if lines else "")


def write_host_file_via_docker(client: Any, host_path: str, content: str, *, mode: int = 0o600) -> None:
    """Write a file on the EC2 host by bind-mounting its parent directory into alpine."""
    from docker.errors import APIError, DockerException

    parent = os.path.dirname(host_path) or "/"
    filename = os.path.basename(host_path)
    payload = base64.b64encode(content.encode("utf-8")).decode("ascii")
    script = "\n".join(
        [
            "set -eu",
            f"mkdir -p {shlex.quote(parent)}",
            f"echo {shlex.quote(payload)} | base64 -d > {shlex.quote(host_path)}",
            f"chmod {mode:o} {shlex.quote(host_path)}",
        ]
    )
    try:
        client.containers.run(
            "alpine:3.20",
            ["sh", "-c", script],
            remove=True,
            volumes={parent: {"bind": parent, "mode": "rw"}},
        )
    except (APIError, DockerException, OSError) as exc:
        logger.warning("deployment upgrade: could not write host file %s: %s", host_path, exc)
        raise


def prepare_upgrade_env_file(client: Any, container_name: str) -> str:
    """
    Resolve full environment (host .env + running container) and write .upgrade.env on the host.
    The helper uses this file so recreate survives missing or stale host .env files.
    """
    canonical = deployment_env_file_path()
    env = resolve_recreate_environment(client, container_name, canonical)
    if not env:
        logger.warning(
            "deployment upgrade: no env keys resolved; helper will fall back to %s if present",
            canonical,
        )
        return canonical

    env = ensure_redis_url_in_env(env)
    dotenv_text = format_dotenv_text(env)
    write_host_file_via_docker(client, _UPGRADE_ENV_FILE, dotenv_text)
    write_host_file_via_docker(client, canonical, dotenv_text)
    logger.info(
        "deployment upgrade: wrote %d keys to %s and %s for helper recreate",
        len(env),
        _UPGRADE_ENV_FILE,
        canonical,
    )
    return _UPGRADE_ENV_FILE


def _post_upgrade_prune_shell_lines() -> list[str]:
    """Prune stopped containers and dangling images after a successful recreate."""
    return [
        'if ! docker ps --filter "name=^/${NAME}$" --filter status=running --format \'{{.ID}}\' | grep -q .; then',
        '  echo "ERROR: container $NAME not running after docker run"',
        "  exit 1",
        "fi",
        'echo "=== pruning stopped containers ==="',
        'CONTAINER_PRUNE_OUT="$(docker container prune -f 2>&1)" || { echo "ERROR: container prune failed"; exit 1; }',
        'printf "%s\n" "$CONTAINER_PRUNE_OUT"',
        'CONTAINER_SPACE="$(printf "%s\n" "$CONTAINER_PRUNE_OUT" | sed -n "s/^Total reclaimed space: //p" | tail -1)"',
        'if [ -n "$CONTAINER_SPACE" ]; then',
        '  echo "stopped containers pruned successfully; space reclaimed: $CONTAINER_SPACE"',
        "else",
        '  echo "stopped containers pruned successfully"',
        "fi",
        'echo "=== pruning dangling images ==="',
        'IMAGE_PRUNE_OUT="$(docker image prune -f 2>&1)" || { echo "ERROR: image prune failed"; exit 1; }',
        'printf "%s\n" "$IMAGE_PRUNE_OUT"',
        'IMAGE_SPACE="$(printf "%s\n" "$IMAGE_PRUNE_OUT" | sed -n "s/^Total reclaimed space: //p" | tail -1)"',
        'if [ -n "$IMAGE_SPACE" ]; then',
        '  echo "dangling images pruned successfully; space reclaimed: $IMAGE_SPACE"',
        "else",
        '  echo "dangling images pruned successfully"',
        "fi",
        'echo "=== prune complete: containers=${CONTAINER_SPACE:-0B}, images=${IMAGE_SPACE:-0B} ==="',
    ]


def _format_bytes_reclaimed(n: int | float | None) -> str:
    """Human-readable byte size (similar to docker prune CLI output)."""
    try:
        size = int(n or 0)
    except (TypeError, ValueError):
        return "0B"
    if size <= 0:
        return "0B"
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)}B"
            return f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{size}B"


def _prune_docker_after_upgrade(client: Any) -> None:
    """SDK equivalent of post-upgrade container/image prune on the host."""
    from docker.errors import APIError, DockerException

    try:
        containers_result = client.containers.prune()
        images_result = client.images.prune(filters={"dangling": True})
        container_space = int(containers_result.get("SpaceReclaimed") or 0)
        image_space = int(images_result.get("SpaceReclaimed") or 0)
        containers_deleted = containers_result.get("ContainersDeleted") or []
        images_deleted = images_result.get("ImagesDeleted") or []
        logger.info(
            "deployment upgrade: stopped containers pruned successfully (%d removed, %s reclaimed)",
            len(containers_deleted),
            _format_bytes_reclaimed(container_space),
        )
        logger.info(
            "deployment upgrade: dangling images pruned successfully (%d removed, %s reclaimed)",
            len(images_deleted),
            _format_bytes_reclaimed(image_space),
        )
        logger.info(
            "deployment upgrade: prune complete (total reclaimed: %s)",
            _format_bytes_reclaimed(container_space + image_space),
        )
    except (APIError, DockerException) as exc:
        logger.warning("deployment upgrade: docker prune failed: %s", exc)


def upgrade_shell_script(
    *,
    image: str,
    container_name: str,
    env_file: str,
    data_host: str,
    host_port: int,
    log_file: str = _UPGRADE_LOG_FILE,
) -> str:
    """Shell recreate matching CFN bootstrap; runs on host via docker CLI helper."""
    qn = shlex.quote(container_name)
    qe = shlex.quote(env_file)
    qi = shlex.quote(image)
    qd = shlex.quote(data_host)
    ql = shlex.quote(log_file)
    return "\n".join(
        [
            "set -eu",
            f"LOG={ql}",
            f"NAME={qn}",
            f"IMAGE={qi}",
            f"ENV_FILE={qe}",
            'mkdir -p "$(dirname "$LOG")"',
            'exec >>"$LOG" 2>&1',
            'echo "=== breeze upgrade $(date -Iseconds 2>/dev/null || date) image=$IMAGE container=$NAME ==="',
            f"test -f \"$ENV_FILE\" || {{ echo \"ERROR: env file missing: $ENV_FILE\"; exit 1; }}",
            *_redis_sidecar_shell_lines(),
            f"docker pull \"$IMAGE\"",
            "docker rm -f \"$NAME\" 2>/dev/null || true",
            "if docker ps -a --format '{{.Names}}' | grep -Fxq \"$NAME\"; then",
            '  echo "ERROR: container $NAME still exists after docker rm -f"',
            "  exit 1",
            "fi",
            "docker run -d "
            '--name "$NAME" '
            "--restart unless-stopped "
            f"--network {REDIS_NETWORK_NAME} "
            f"-p {int(host_port)}:{_CONTAINER_PORT} "
            f"-v {qd}:/app/backend/data "
            '-v "$ENV_FILE":"$ENV_FILE":ro '
            "-v /var/run/docker.sock:/var/run/docker.sock "
            '--env-file "$ENV_FILE" '
            '"$IMAGE"',
            *_post_upgrade_prune_shell_lines(),
            'echo "=== upgrade complete: $(docker ps --filter name=^/$NAME$ --format {{.Status}}) ==="',
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

    env_file = prepare_upgrade_env_file(client, container_name)
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
            volumes={
                "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"},
                _DEPLOY_ROOT: {"bind": _DEPLOY_ROOT, "mode": "rw"},
            },
        )
    except (APIError, DockerException) as exc:
        logger.warning("deployment upgrade: helper launch failed: %s", exc)
        raise
    helper_id = getattr(helper, "id", helper)
    logger.info(
        "deployment upgrade: helper started id=%s name=%s — on failure inspect: "
        "docker logs %s; cat %s",
        helper_id,
        helper_name,
        helper_name,
        _UPGRADE_LOG_FILE,
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

    ensure_redis_sidecar_sdk(client)
    environment = ensure_redis_url_in_env(
        resolve_recreate_environment(client, container_name, env_file)
    )
    if environment:
        write_host_file_via_docker(client, env_file, format_dotenv_text(environment))

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
        network=REDIS_NETWORK_NAME,
        ports={f"{_CONTAINER_PORT}/tcp": host_port},
        volumes=volumes,
        environment=environment,
    )
    logger.info("deployment upgrade: container %s is up", container_name)
    _prune_docker_after_upgrade(client)
