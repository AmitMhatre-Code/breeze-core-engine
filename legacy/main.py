import os
import sys
import logging
from typing import Optional

_root = os.path.dirname(os.path.abspath(__file__))
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

from app.core.logging import configure_logging

configure_logging(level=LOG_LEVEL, log_file=LOG_FILE)
_logger = logging.getLogger("main")

# Patch requests so GET with data= sends body (breeze_connect uses GET+body)
from app.core.requests_patch import apply_requests_patch
apply_requests_patch()

from app.api.router import app_router
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.services.processor import processor
from fastapi.responses import RedirectResponse
from core.errors import ConflictError, UnauthorizedError, ForbiddenError
from app.auth.context import RedirectToLogin
from app.middleware import CorrelationIdMiddleware, RateLimitMiddleware, RequestLoggerMiddleware
from app.exceptions import AppException


def include_router(app):
    app.include_router(app_router)


def start_application():
    import app.core.config as cfg
    has_secret = bool(cfg.JWT_SECRET and cfg.JWT_SECRET.strip())
    if has_secret:
        _logger.info("JWT_SECRET loaded (length=%d). Login and credential decryption will use it.", len(cfg.JWT_SECRET))
    else:
        _logger.warning(
            "JWT_SECRET is empty. Login and credential decryption will fail. "
            "Set JWT_SECRET=... or ENCRYPTION_KEY=... in .env. Paths tried: %s",
            _env_paths_tried or "none",
        )
    breeze = processor()
    try:
        breeze.update_ICICImaster()
    except Exception as e:
        _logger.warning("ICICI master update failed at startup: %s", e, exc_info=True)

    app = FastAPI(trust_env=True)
    # Session for Google OAuth (Authlib stores state here)
    session_secret = (cfg.JWT_SECRET or "dev-session-secret").strip()[:32].ljust(32, "0")
    app.add_middleware(SessionMiddleware, secret_key=session_secret)
    # CORS: allow web UI domain only (Phase 6 T104)
    origins = os.environ.get("CORS_ORIGINS", "http://localhost:8000").split(",")
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in origins], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(RequestLoggerMiddleware)
    app.mount("/static", StaticFiles(directory="static"), name="static")
    include_router(app)

    @app.exception_handler(RedirectToLogin)
    async def redirect_to_login_handler(request: Request, exc: RedirectToLogin):
        return RedirectResponse(url="/", status_code=302)

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

    from app.api.health import router as health_router
    app.include_router(health_router)

    return app

app = start_application()