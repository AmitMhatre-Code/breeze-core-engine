"""Structured logging configuration."""
import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

from icici_breeze_backend.app.core.log_buffer import (
    ErrorContextBufferHandler,
    buffer_enabled,
    build_handler,
    capture_level_for,
)
from icici_breeze_backend.app.core.log_sink import (
    build_handler as build_sink_handler,
    prune_expired,
    sink_enabled,
)

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Telegram puts the bot token in the URL path, so anything that logs a request
# URL leaks it — `httpx`'s own INFO line for every call, and any exception
# string carrying the URL. These logs live on customer-owned hosts while the bot
# is shared fleet-wide, so a leaked token is a cross-tenant credential, not a
# local one. Redact at the handler so it covers every logger, ours or a
# library's, without having to silence anything.
_BOT_TOKEN_PATTERN = re.compile(r"/bot\d+:[A-Za-z0-9_-]+")

# Credentials that ride in a *query string* rather than a path. The one that made
# this necessary is ICICI's `apisession`: the broker hands it back on the
# `/icici-return` redirect, so `uvicorn.access` writes a live, day-valid broker
# session key into the log — and `/diagnostics/logs/download` zips that up for
# support. Names are matched whole (the lookbehind rejects a `_`-joined prefix) so
# `stock_token=4.1!40879`, which is not a secret and is worth reading, survives
# while `broker_token=` does not. Listed longest-first: alternation is
# leftmost-first, and `token` must not win ahead of `session_token`.
_SENSITIVE_QUERY_PARAMS = (
    "refresh_token",
    "session_token",
    "access_token",
    "broker_token",
    "sessiontoken",
    "api_session",
    "session_key",
    "apisession",
    "auth_token",
    "api_secret",
    "sessionkey",
    "password",
    "checksum",
    "api_key",
    "passwd",
    "apikey",
    "secret",
    "token",
    "pwd",
)
# The value is `+`, not `*`, and excludes `<>` so the rule cannot match its own output:
# this filter is attached to *every* root handler, so each record passes through it once
# per handler, and a value pattern that also matched the empty string turned one secret
# into `apisession=<redacted><redacted><redacted>`. An empty value has nothing to hide.
_SENSITIVE_QUERY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(" + "|".join(_SENSITIVE_QUERY_PARAMS) + r")=[^&\s\"'<>]+",
    re.IGNORECASE,
)

# (pattern, replacement) rather than one shared replacement string: the query-param
# rule has to put the parameter *name* back, or a redacted log line no longer says
# which credential it was hiding.
_SECRET_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (_BOT_TOKEN_PATTERN, "/bot<redacted>"),
    (_SENSITIVE_QUERY_PATTERN, r"\1=<redacted>"),
)


# Machine traffic, not user traffic: the container HEALTHCHECK hits /health every 30s
# (Dockerfile) and /metrics is scraped on its own cadence. At one access line each they
# dominate the log without ever saying anything.
_QUIET_ACCESS_PATHS = frozenset({"/health", "/metrics"})

# Browser polling, which is user traffic but says nothing while it succeeds: an open
# tab hits these three roughly every 2.4s, and they accounted for 9,572 of 12,352 lines
# in a 16-hour capture — crowding real events out of a size-capped sink. Dropped only
# while they succeed: a burst of 401s here is how an expired session announces itself,
# and a 5xx here is a real fault, so failures still get logged.
_QUIET_WHEN_OK_ACCESS_PATHS = frozenset(
    {"/dashboard/ws-health", "/dashboard/index-quotes", "/portfolio/squareoff-rules"}
)
_QUIET_STATUS_CEILING = 400
_RENDERED_ACCESS_STATUS = re.compile(r"\"\s+(\d{3})\s*$")


def _access_line_is_quiet(path: str, status: int | None) -> bool:
    """True when this access line carries no information worth retaining."""
    if path in _QUIET_ACCESS_PATHS:
        return True
    if path not in _QUIET_WHEN_OK_ACCESS_PATHS:
        return False
    # Unknown status -> keep the line. Erring towards a noisier log is the same
    # trade the arg-shape fallback below makes.
    return status is not None and status < _QUIET_STATUS_CEILING


class QuietAccessPathFilter(logging.Filter):
    """Drops `uvicorn.access` lines for unattended probes and successful polling."""

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn.access formats as (client_addr, method, path, http_version, status);
        # read the path positionally and fall back to the rendered line if that shape
        # ever changes, so a uvicorn upgrade degrades to "logs too much", not a crash.
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            path = str(args[2]).split("?", 1)[0]
            try:
                status = int(args[4])
            except (TypeError, ValueError):
                status = None
            return not _access_line_is_quiet(path, status)
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never let a filter drop a log line
            return True
        rendered_status = _RENDERED_ACCESS_STATUS.search(message)
        status = int(rendered_status.group(1)) if rendered_status else None
        for quiet_path in _QUIET_ACCESS_PATHS | _QUIET_WHEN_OK_ACCESS_PATHS:
            if f" {quiet_path} HTTP/" in message or f" {quiet_path}?" in message:
                return not _access_line_is_quiet(quiet_path, status)
        return True


class SecretRedactingFilter(logging.Filter):
    """Rewrites records whose formatted message contains a known secret shape."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never let redaction drop a log line
            return True
        redacted = message
        for pattern, replacement in _SECRET_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def configure_logging(
    level: str = "INFO",
    format_string: Optional[str] = None,
    stream: Optional[object] = None,
    log_file: Optional[str] = None,
    process_name: str = "backend",
) -> None:
    """Configure root logging: console at `level`, plus buffer and download sinks.

    `process_name` names this process's file in the download sink — the API process and
    the chain-builder worker must not share one, since each rotates without knowing
    about the other's open file handles.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    fmt = format_string or LOG_FORMAT
    formatter = logging.Formatter(fmt, datefmt=LOG_DATE_FORMAT)
    root = logging.getLogger()

    # The *console handler* carries the configured level, not the root logger. Root has
    # to stay more verbose than the console so sub-threshold records still reach the
    # error-context buffer below; the handler level is what decides what gets printed.
    # Prefer an existing console handler; a FileHandler is a StreamHandler subclass, so
    # exclude it explicitly or a configured LOG_FILE would end up being treated as the
    # console and the terminal would go silent.
    existing = [
        h for h in root.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, (logging.FileHandler, ErrorContextBufferHandler))
    ]
    handler = existing[0] if existing else logging.StreamHandler(stream or sys.stderr)
    handler.setLevel(log_level)
    handler.setFormatter(formatter)
    if handler not in root.handlers:
        root.addHandler(handler)

    # Root must sit at the most verbose level any attached sink wants, or records are
    # dropped before a handler ever sees them. Each sink contributes its own floor, so
    # turning one off can't silently starve another — the download sink in particular
    # must keep receiving INFO when the console is at WARNING.
    capture_floors = [log_level]

    for stale in [h for h in root.handlers if isinstance(h, ErrorContextBufferHandler)]:
        root.removeHandler(stale)
        stale.close()
    if buffer_enabled():
        capture_floors.append(capture_level_for(log_level))
        buffer_handler = build_handler(handler)
        root.addHandler(buffer_handler)
        # Must run *before* the console handler: handlers fire in list order, and the
        # replayed context reads as nonsense if it lands after the error it leads up to.
        # Safe to reorder in place — configure_logging runs at startup, single-threaded.
        root.handlers.remove(buffer_handler)
        root.handlers.insert(0, buffer_handler)

    if sink_enabled():
        for stale_sink in [
            h for h in root.handlers if isinstance(h, RotatingFileHandler)
        ]:
            root.removeHandler(stale_sink)
            stale_sink.close()
        sink_handler = build_sink_handler(process_name)
        if sink_handler is not None:
            capture_floors.append(sink_handler.level)
            root.addHandler(sink_handler)
            prune_expired()

    root.setLevel(min(capture_floors))

    if log_file and log_file.strip():
        try:
            log_path = os.path.abspath(log_file.strip())
            already = any(
                isinstance(h, logging.FileHandler)
                and os.path.abspath(getattr(h, "baseFilename", "")) == log_path
                for h in root.handlers
            )
            if not already:
                os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
                file_handler = logging.FileHandler(log_path, encoding="utf-8")
                file_handler.setLevel(log_level)
                file_handler.setFormatter(formatter)
                root.addHandler(file_handler)
        except OSError:
            pass
    # Lifecycle lines (`uvicorn.error`, despite the name: "Started server process",
    # "Application startup complete") are once-per-boot and worth keeping even at
    # WARNING. Access lines are per-request and now inherit the root level instead of
    # being pinned to INFO — pinning them left LOG_LEVEL unable to quieten the loudest
    # source in the log.
    #
    # The parent `uvicorn` logger must go to NOTSET as well, not just `uvicorn.access`:
    # level lookup walks up the hierarchy until it finds a logger with a level set, so
    # leaving the parent at INFO would keep access lines at INFO however the child is
    # configured. uvicorn's own dictConfig sets all three to INFO; it runs in
    # `Config.__init__`, before the app module is imported, so this runs last and wins.
    # uvicorn's dictConfig makes its loggers self-contained sinks: `uvicorn` and
    # `uvicorn.access` each get their own handler and propagate=False. Records therefore
    # never reach the root handlers, which means the console level doesn't gate them,
    # SecretRedactingFilter doesn't see them, and the error-context buffer can't capture
    # them — access lines being the single noisiest source in the log. Strip the handlers
    # and let everything propagate to root, so all three get the same treatment as
    # application logging. `uvicorn` (the parent) matters as much as the child here: it
    # holds both a handler and propagate=False, so leaving it alone would keep access
    # records printing there and stop them reaching root.
    for uvicorn_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(uvicorn_name)
        uvicorn_logger.setLevel(logging.NOTSET)
        uvicorn_logger.propagate = True
        for uvicorn_handler in list(uvicorn_logger.handlers):
            uvicorn_logger.removeHandler(uvicorn_handler)
    # On the logger, not a handler: the filter must apply before the record reaches
    # either the console or the buffer.
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, QuietAccessPathFilter) for f in access_logger.filters):
        access_logger.addFilter(QuietAccessPathFilter())
    # breeze_connect logs ERROR on empty/invalid API response (e.g. JSON decode); we handle it and return error to user
    for breeze_logger_name in ("APILogger", "breeze_connect"):
        logging.getLogger(breeze_logger_name).setLevel(logging.WARNING)
    # Last, so it covers whichever handlers ended up attached above. Idempotent:
    # configure_logging() runs again in tests and on re-entry.
    for root_handler in root.handlers:
        if not any(isinstance(f, SecretRedactingFilter) for f in root_handler.filters):
            root_handler.addFilter(SecretRedactingFilter())


def get_logger(name: str) -> logging.Logger:
    """Return a logger for the given module name."""
    return logging.getLogger(name)
