import asyncio
import os
import sys
import logging
from contextlib import asynccontextmanager
from typing import Optional

_root = os.path.dirname(os.path.abspath(__file__))
# backend/ directory (parent of src/) — stable when cwd is repo root (e.g. ./dev.sh).
_BACKEND_ROOT = os.path.abspath(os.path.join(_root, "..", ".."))
_env_paths_tried = [
    os.path.abspath(os.path.join(_root, ".env")),
    os.path.abspath(os.path.join(os.getcwd(), ".env")),
]

# Keys read only from .env file (never from os.environ).
_LOG_KEYS_FROM_ENV_FILE = ("LOG_LEVEL", "LOG_FILE")


def _parse_env_file_key(path: str, key: str) -> Optional[str]:
    """Read a single KEY=value from .env file. Returns value or None. No os.environ."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                if k == key:
                    return v.strip().strip('"').strip("'") or None
    except OSError:
        pass
    return None


def _get_log_config_from_env_file() -> dict:
    """Read LOG_LEVEL and LOG_FILE only from .env file. Never from os.environ."""
    out = {}
    for _p in _env_paths_tried:
        if not os.path.isfile(_p):
            continue
        for key in _LOG_KEYS_FROM_ENV_FILE:
            val = _parse_env_file_key(_p, key)
            if val is not None:
                out[key] = val
        break
    return out


def _load_env_early():
    """Load .env before any config read. Returns True if any file was loaded."""
    loaded = False
    try:
        from dotenv import load_dotenv
        for _p in _env_paths_tried:
            if os.path.isfile(_p):
                load_dotenv(_p, override=True)
                loaded = True
                break
        else:
            load_dotenv(override=True)
            loaded = True
    except ImportError:
        for _p in _env_paths_tried:
            if os.path.isfile(_p):
                with open(_p, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, _, v = line.partition("=")
                        k, v = k.strip(), v.strip().strip('"').strip("'")
                        if k:
                            os.environ[k] = v
                loaded = True
                break
    return loaded


# Load .env before reading LOG_LEVEL/LOG_FILE so they are available from file
_load_env_early()

# LOG_LEVEL and LOG_FILE: only from .env file, never from os.environ
_log_config = _get_log_config_from_env_file()
LOG_LEVEL = _log_config.get("LOG_LEVEL") or "INFO"
LOG_FILE = _log_config.get("LOG_FILE")

from icici_breeze_backend.app.core.logging import configure_logging

configure_logging(level=LOG_LEVEL, log_file=LOG_FILE)
_logger = logging.getLogger("main")

# Patch requests so GET with data= sends body (breeze_connect uses GET+body)
from icici_breeze_backend.app.core.requests_patch import apply_requests_patch
apply_requests_patch()

# Dev-only TLS workaround:
# `breeze_connect` downloads ICICI's `SecurityMaster.zip` at import time using
# `urllib.request.urlopen` (no custom verify hook). If your network/corporate
# proxy MITMs TLS with a self-signed cert chain, the import can fail and the
# whole FastAPI app won't start. Opt-in via env var.
if os.environ.get("ICICI_BREEZE_INSECURE_SSL", "").lower() in ("1", "true", "yes"):
    import ssl

    ssl._create_default_https_context = ssl._create_unverified_context

from icici_breeze_backend.app.api.router import app_router
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import RedirectResponse
from icici_breeze_backend.core.errors import ConflictError, UnauthorizedError, ForbiddenError
from icici_breeze_backend.app.auth.context import RedirectToLogin
from icici_breeze_backend.app.middleware import CorrelationIdMiddleware, RateLimitMiddleware, RequestLoggerMiddleware
from icici_breeze_backend.app.exceptions import AppException


def include_router(app):
    app.include_router(app_router)


def _resolve_sqlite_template(cfg, empty_filename: str) -> str | None:
    """Prefer template under DATA_PATH; fall back to DB_TEMPLATE_PATH (not hidden by data bind mounts)."""
    for base in (cfg.DATA_PATH, getattr(cfg, "DB_TEMPLATE_PATH", "") or ""):
        if not base:
            continue
        p = base + empty_filename
        if os.path.isfile(p):
            return p
    return None


def _ensure_app_database() -> None:
    """Create users.sqlite3 from template when missing, empty, or unreadable (e.g. bind-mounted data dir)."""
    import shutil

    from icici_breeze_backend.core import config as cfg

    db_path = cfg.DATA_PATH + cfg.USERS_DB
    template = _resolve_sqlite_template(cfg, cfg.USERS_EMPTY_DB)

    if os.path.isfile(db_path) and os.path.getsize(db_path) == 0:
        _logger.warning("Removing empty users DB file so it can be re-seeded: %s", db_path)
        try:
            os.remove(db_path)
        except OSError as e:
            _logger.warning("Could not remove empty users DB: %s", e)

    if not os.path.isfile(db_path):
        if template:
            shutil.copy2(template, db_path)
            _logger.info("Initialized app database at %s from %s.", db_path, template)
        else:
            _logger.warning(
                "App database missing at %s and no template (checked DATA_PATH and DB_TEMPLATE_PATH).",
                db_path,
            )
    if os.path.isfile(db_path):
        try:
            from icici_breeze_backend.app.db.user_account_migrate import migrate_user_account_if_needed
            from icici_breeze_backend.app.db.ai_provider_migrate import ensure_ai_provider_table
            from icici_breeze_backend.app.db.outlook_preferences_migrate import ensure_outlook_preferences_table
            from icici_breeze_backend.app.db.parked_orders_migrate import ensure_parked_orders_table
            from icici_breeze_backend.app.db.user_exchange_calendar_migrate import (
                ensure_user_exchange_calendar_table,
            )

            migrate_user_account_if_needed(db_path)
            ensure_ai_provider_table(db_path)
            ensure_outlook_preferences_table(db_path)
            ensure_parked_orders_table(db_path)
            ensure_user_exchange_calendar_table(db_path)
            from icici_breeze_backend.app.services.user_rate_limit_prefs import (
                migrate_legacy_rate_limit_pause_default,
                migrate_rate_limit_pause_bounds,
            )

            migrate_legacy_rate_limit_pause_default()
            migrate_rate_limit_pause_bounds()
            from icici_breeze_backend.app.services.reference_data.state import ensure_reference_data_tables

            ensure_reference_data_tables(db_path)
        except Exception:
            _logger.exception("user_account schema migration failed")


def _ensure_freeze_limit_files() -> None:
    """Copy NSE/BSE freeze limits from db-templates into DATA_PATH when missing.

    Production often bind-mounts host data over /app/backend/data, which hides files
    baked into the image; templates live under db-templates/ (see Dockerfile).
    """
    import shutil

    from icici_breeze_backend.core import config as cfg

    tpl = getattr(cfg, "DB_TEMPLATE_PATH", "") or ""
    if not tpl or not os.path.isdir(tpl.rstrip(os.sep)):
        return
    for name in (cfg.LIMITS_MASTER_NSE, cfg.LIMITS_MASTER_BSE, "exchange_holidays.json"):
        dest = cfg.DATA_PATH + name
        if os.path.isfile(dest):
            continue
        src = tpl + name
        if os.path.isfile(src):
            try:
                shutil.copy2(src, dest)
                _logger.info(
                    "Seeded %s into data dir from db-templates (volume had no copy).",
                    name,
                )
            except OSError as e:
                _logger.warning("Could not copy %s to data dir: %s", name, e)


def _ensure_scrips_database() -> None:
    """Create scrips.sqlite3 from template when missing or empty."""
    import shutil
    import sqlite3

    from icici_breeze_backend.core import config as cfg

    db_path = cfg.DATA_PATH + cfg.SCRIP_DB
    template = _resolve_sqlite_template(cfg, cfg.SCRIPS_EMPTY_DB)

    if os.path.isfile(db_path) and os.path.getsize(db_path) == 0:
        _logger.warning("Removing empty scrips DB file so it can be re-seeded: %s", db_path)
        try:
            os.remove(db_path)
        except OSError as e:
            _logger.warning("Could not remove empty scrips DB: %s", e)

    if os.path.isfile(db_path):
        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute("PRAGMA quick_check").fetchone()
                qc = str(row[0]) if row and row[0] is not None else ""
            if qc.lower() == "ok":
                return
            _logger.warning("scrips DB quick_check failed (%s). Re-seeding from template.", qc[:200])
            try:
                os.remove(db_path)
            except OSError as e:
                _logger.warning("Could not remove malformed scrips DB: %s", e)
        except Exception as e:
            _logger.warning("scrips DB integrity check failed; re-seeding from template: %s", e)
            try:
                os.remove(db_path)
            except OSError:
                pass
    if template:
        shutil.copy2(template, db_path)
        _logger.info("Initialized scrips database at %s from %s.", db_path, template)
    else:
        _logger.warning(
            "Scrips database missing at %s and no template (checked DATA_PATH and DB_TEMPLATE_PATH).",
            db_path,
        )


def start_application():
    _ensure_app_database()
    _ensure_scrips_database()
    _ensure_freeze_limit_files()
    import icici_breeze_backend.app.core.config as cfg
    from icici_breeze_backend.core import config as core_cfg

    if getattr(core_cfg, "ICICI_BROKER_MODE", "live") == "mock":
        _logger.warning(
            "ICICI_BROKER_MODE=mock: outbound ICICI Breeze calls are stubbed. Do not use in production."
        )
        if core_cfg.COOKIE_SECURE:
            _logger.error(
                "ICICI_BROKER_MODE=mock with COOKIE_SECURE=true is unsafe for a real deployment; "
                "fix environment before going live."
            )
    has_secret = bool(cfg.JWT_SECRET and cfg.JWT_SECRET.strip())
    if has_secret:
        _logger.info("JWT_SECRET loaded (length=%d). Login and credential decryption will use it.", len(cfg.JWT_SECRET))
    else:
        _logger.warning(
            "JWT_SECRET is empty. Login and credential decryption will fail. "
            "Set JWT_SECRET=... or ENCRYPTION_KEY=... in .env. Paths tried: %s",
            _env_paths_tried or "none",
        )
    from icici_breeze_backend.app.services.portal_deployment_heartbeat import (
        heartbeat_loop_enabled,
        run_heartbeat_loop,
        send_startup_heartbeat,
    )

    @asynccontextmanager
    async def _portal_heartbeat_lifespan(_app: FastAPI):
        task: asyncio.Task | None = None
        if heartbeat_loop_enabled():
            await send_startup_heartbeat()
            task = asyncio.create_task(run_heartbeat_loop())
        from icici_breeze_backend.app.services.reference_data.scheduler import (
            bootstrap_reference_data_on_startup,
        )

        bootstrap_reference_data_on_startup()
        yield
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        from icici_breeze_backend.app.services.breeze_websocket_manager import shutdown_websocket

        shutdown_websocket()

    app = FastAPI(trust_env=True, lifespan=_portal_heartbeat_lifespan)
    # CORS: allow web UI. .env uses ALLOWED_ORIGINS; CORS_ORIGINS overrides if set.
    _cors_raw = (
        os.environ.get("CORS_ORIGINS")
        or os.environ.get("ALLOWED_ORIGINS")
        or "http://localhost:8000,http://localhost:3000,http://127.0.0.1:3000,http://127.0.0.1:8000"
    )
    _parsed = [o.strip() for o in _cors_raw.split(",") if o.strip()]
    # Browsers send localhost vs 127.0.0.1 as distinct origins; mirror localhost entries.
    origins = list(_parsed)
    for o in _parsed:
        if "://localhost" in o and "127.0.0.1" not in o:
            twin = o.replace("://localhost", "://127.0.0.1", 1)
            if twin not in origins:
                origins.append(twin)
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(RequestLoggerMiddleware)
    _static_dir = os.path.join(_BACKEND_ROOT, "static")
    if os.path.isdir(_static_dir):
        app.mount("/static", StaticFiles(directory=_static_dir), name="static")
    else:
        _logger.warning("Static directory not found at %s; /static not mounted.", _static_dir)
    include_router(app)

    @app.exception_handler(RedirectToLogin)
    async def redirect_to_login_handler(request: Request, exc: RedirectToLogin):
        from icici_breeze_backend.app.api.frontend_redirect import frontend_url

        return RedirectResponse(url=frontend_url("/login"), status_code=302)

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        cid = getattr(request.state, "correlation_id", None)
        _logger.warning(
            "AppException: status=%s path=%s detail=%s correlation_id=%s",
            exc.status_code, request.url.path, exc.detail, cid,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "correlation_id": cid},
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        cid = getattr(request.state, "correlation_id", None)
        _logger.warning(
            "HTTP error: status=%s path=%s detail=%s correlation_id=%s",
            exc.status_code, request.url.path, exc.detail, cid,
        )
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "correlation_id": cid})

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        cid = getattr(request.state, "correlation_id", None)
        _logger.exception(
            "Unhandled exception: path=%s correlation_id=%s error=%s",
            request.url.path, cid, exc,
        )
        # User-friendly message; internal detail only in logs
        user_message = "An unexpected error occurred. Please try again or contact support."
        return JSONResponse(
            status_code=500,
            content={"message": user_message, "correlation_id": cid},
        )

    from icici_breeze_backend.app.api.health import router as health_router
    app.include_router(health_router)

    return app

app = start_application()