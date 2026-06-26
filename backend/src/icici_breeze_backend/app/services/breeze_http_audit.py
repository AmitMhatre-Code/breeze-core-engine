"""DEBUG-gated forensic audit lines for outbound ICICI Breeze HTTP calls."""
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

_logger = logging.getLogger(__name__)

_HEADER_WHITELIST = frozenset(
    {
        "server",
        "date",
        "content-type",
        "content-length",
        "retry-after",
        "x-request-id",
        "x-amzn-requestid",
        "via",
        "cf-ray",
    }
)

_ORIGIN_SYNTHETIC = "synthetic"
_ORIGIN_UPSTREAM = "upstream"
_ORIGIN_DAILY_BLOCKED = "daily_blocked"


def is_breeze_http_audit_enabled() -> bool:
    return _logger.isEnabledFor(logging.DEBUG)


def _preview_for_log(text: str, max_len: int = 320) -> str:
    if not text:
        return ""
    one = " ".join(str(text).split())
    return one if len(one) <= max_len else one[: max_len - 3] + "..."


def _body_sha16(text: str) -> str:
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _safe_headers(raw: Any) -> dict[str, str]:
    headers = getattr(raw, "headers", None) or {}
    out: dict[str, str] = {}
    try:
        items = headers.items() if hasattr(headers, "items") else []
    except Exception:
        return out
    for key, value in items:
        k = str(key or "").lower()
        if k in _HEADER_WHITELIST:
            out[k] = str(value)
    return out


def resolve_breeze_http_origin(raw: Any) -> str:
    marker = getattr(raw, "breeze_origin", None)
    if marker:
        return str(marker)
    return _ORIGIN_UPSTREAM


def _response_text(raw: Any) -> str:
    text = getattr(raw, "text", None)
    if text is not None:
        return str(text)
    if isinstance(raw, dict):
        import json

        try:
            return json.dumps(raw, separators=(",", ":"), default=str)
        except Exception:
            return str(raw)
    return ""


def _breeze_fields(body: dict[str, Any] | None) -> tuple[Any, str | None]:
    if not body:
        return None, None
    st = body.get("Status")
    if st is None:
        st = body.get("status")
    err = body.get("Error") or body.get("error")
    return st, str(err) if err is not None else None


def log_breeze_http_attempt(
    *,
    breeze_call_id: str,
    method: str,
    url: str,
    endpoint: str,
    attempt: int,
    origin: str,
    elapsed_ms: float | None,
    http_status: int,
    body: dict[str, Any] | None,
    err_text: str | None,
    raw: Any = None,
    user_id: str | None = None,
    route_id: str | None = None,
    correlation_id: str | None = None,
) -> None:
    """Emit one structured DEBUG line when audit logging is enabled."""
    if not is_breeze_http_audit_enabled():
        return

    from icici_breeze_backend.app.services.icici_api_pacing import (
        is_breeze_rate_limited,
        is_icici_per_minute_limit_exceeded,
    )

    breeze_status, breeze_error = _breeze_fields(body)
    if breeze_error is None and err_text:
        breeze_error = err_text

    text = _response_text(raw) if raw is not None else ""
    if not text and body:
        import json

        try:
            text = json.dumps(body, separators=(",", ":"), default=str)
        except Exception:
            text = str(body)

    merged_err = " ".join(
        x for x in (str(breeze_error or ""), str(err_text or "")) if x
    )
    rate_limited = is_breeze_rate_limited(http_status, err_text) or (
        body is not None and is_breeze_rate_limited(breeze_status, breeze_error)
    )
    icici_minute_limit = is_icici_per_minute_limit_exceeded(merged_err)

    headers_repr = _safe_headers(raw) if raw is not None else {}
    body_preview = ""
    if http_status != 200 or (breeze_status not in (None, 200, "200")):
        body_preview = _preview_for_log(text)

    parts = [
        "breeze_http_audit",
        f"breeze_call_id={breeze_call_id}",
        f"correlation_id={correlation_id or ''}",
        f"user_id={user_id or ''}",
        f"route_id={route_id or ''}",
        f"method={method or '?'}",
        f"endpoint={endpoint or 'icici'}",
        f"url={url or ''}",
        f"attempt={attempt}",
        f"origin={origin}",
        f"elapsed_ms={elapsed_ms:.1f}" if elapsed_ms is not None else "elapsed_ms=",
        f"http_status={http_status}",
        f"breeze_status={breeze_status if breeze_status is not None else ''}",
        f"breeze_error={_preview_for_log(breeze_error or '', 200)!r}",
        f"response_headers={headers_repr!r}",
        f"body_len={len(text)}",
        f"body_sha256={_body_sha16(text)}",
        f"rate_limited={rate_limited}",
        f"icici_minute_limit={icici_minute_limit}",
    ]
    if body_preview:
        parts.append(f"body_preview={body_preview!r}")

    _logger.debug(" ".join(parts))


def new_breeze_call_id() -> str:
    return str(uuid.uuid4())
