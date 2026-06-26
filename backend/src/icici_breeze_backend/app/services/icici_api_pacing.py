"""Universal per-user ICICI Breeze API gate (proactive spacing, concurrency, 429/503 retry)."""
from __future__ import annotations

import logging
import math
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

_logger = logging.getLogger(__name__)

_MAX_BACKOFF_SEC = 10.0
_MAX_HTTP_ATTEMPTS = 4

T = TypeVar("T")


def is_breeze_rate_limited(status: Any, error_text: str | None = None) -> bool:
    """True when ICICI / Breeze indicates HTTP 429/503 or equivalent in body."""
    try:
        st = int(status or 0)
    except (TypeError, ValueError):
        st = 0
    if st in (429, 503):
        return True
    err = str(error_text or "")
    el = err.lower()
    if "too many requests" in el and ("429" in el or "rate" in el):
        return True
    compact = "".join(el.split())
    if "429toomanyrequests" in compact or "<title>429toomanyrequests</title>" in compact:
        return True
    return False


def rate_limit_reason(http_status: int | Any) -> str:
    """Human-readable reason for UI when ICICI rate-limits a call."""
    try:
        st = int(http_status or 0)
    except (TypeError, ValueError):
        st = 0
    if st == 503:
        return "ICICI returned HTTP 503 (Service Unavailable)"
    return "ICICI returned HTTP 429 (Too Many Requests)"


def is_icici_daily_limit_exceeded(error_text: str | None) -> bool:
    e = str(error_text or "").lower()
    return (
        "limit exceed" in e
        or "api call per minute" in e
        or ("5000" in e and "call" in e)
        or ("daily" in e and "limit" in e)
    )


@dataclass(frozen=True)
class BackoffSnapshot:
    active: bool
    reason: str
    seconds_remaining: int
    endpoint: str = "icici"
    throttling_active: bool = False


class GlobalIciciApiPacer:
    """Thread-safe register of last ICICI API call time per user."""

    _lock = threading.Lock()
    _last_call_mono: dict[str, float] = {}
    _consecutive_rate_limited: dict[str, int] = {}
    _throttling_active: dict[str, bool] = {}
    _backoff_visible: dict[str, BackoffSnapshot] = {}

    @classmethod
    def is_throttling_active(cls, user_id: str) -> bool:
        with cls._lock:
            return bool(cls._throttling_active.get(user_id))

    @classmethod
    def activate_throttling(cls, user_id: str) -> None:
        with cls._lock:
            cls._throttling_active[user_id] = True

    @classmethod
    def get_backoff_snapshot(cls, user_id: str) -> BackoffSnapshot | None:
        with cls._lock:
            snap = cls._backoff_visible.get(user_id)
            throttling = bool(cls._throttling_active.get(user_id))
        if snap is not None:
            return BackoffSnapshot(
                active=snap.active,
                reason=snap.reason,
                seconds_remaining=snap.seconds_remaining,
                endpoint=snap.endpoint,
                throttling_active=throttling,
            )
        if throttling:
            return BackoffSnapshot(
                active=False,
                reason="",
                seconds_remaining=0,
                throttling_active=True,
            )
        return None

    @classmethod
    def peek_next_backoff_seconds(cls, user_id: str, base_spacing_sec: float) -> float:
        """Next backoff delay if rate-limited again (does not increment counter)."""
        base = max(0.0, float(base_spacing_sec))
        with cls._lock:
            n = cls._consecutive_rate_limited.get(user_id, 0) + 1
        return min(_MAX_BACKOFF_SEC, base * (2 ** (n - 1)))

    @classmethod
    def _clear_backoff_visible(cls, user_id: str) -> None:
        with cls._lock:
            cls._backoff_visible.pop(user_id, None)

    @classmethod
    def _publish_backoff_visible(
        cls,
        user_id: str,
        *,
        reason: str,
        seconds_remaining: int,
        endpoint: str,
    ) -> None:
        with cls._lock:
            cls._backoff_visible[user_id] = BackoffSnapshot(
                active=True,
                reason=reason,
                seconds_remaining=max(0, seconds_remaining),
                endpoint=endpoint,
                throttling_active=bool(cls._throttling_active.get(user_id)),
            )

    @classmethod
    def _sleep_with_status(
        cls,
        user_id: str,
        seconds: float,
        *,
        reason: str,
        endpoint: str = "icici",
    ) -> None:
        total = max(0.0, float(seconds))
        if total <= 0:
            return
        if total > 1.0 and user_id:
            remaining = int(math.ceil(total))
            cls._publish_backoff_visible(
                user_id,
                reason=reason,
                seconds_remaining=remaining,
                endpoint=endpoint,
            )
            slept = 0.0
            while slept < total:
                tick = min(1.0, total - slept)
                time.sleep(tick)
                slept += tick
                remaining = max(0, int(math.ceil(total - slept)))
                if remaining > 0:
                    cls._publish_backoff_visible(
                        user_id,
                        reason=reason,
                        seconds_remaining=remaining,
                        endpoint=endpoint,
                    )
            cls._clear_backoff_visible(user_id)
        else:
            time.sleep(total)

    @classmethod
    def wait_for_slot(cls, user_id: str, base_spacing_sec: float, *, endpoint: str = "icici") -> None:
        base = max(0.0, float(base_spacing_sec))
        with cls._lock:
            now = time.monotonic()
            last = cls._last_call_mono.get(user_id, 0.0)
            wait = max(0.0, base - (now - last))
        if wait > 0:
            _logger.info(
                "ICICI API pacing: sleeping %.3fs before %s (user=%s)",
                wait,
                endpoint,
                user_id,
            )
            time.sleep(wait)

    @classmethod
    def mark_call_complete(cls, user_id: str) -> None:
        with cls._lock:
            cls._last_call_mono[user_id] = time.monotonic()

    @classmethod
    def on_success(cls, user_id: str) -> None:
        with cls._lock:
            cls._consecutive_rate_limited[user_id] = 0
            cls._throttling_active.pop(user_id, None)
            cls._backoff_visible.pop(user_id, None)

    @classmethod
    def rate_limit_backoff(cls, user_id: str, base_spacing_sec: float, *, endpoint: str = "icici") -> float:
        base = max(0.0, float(base_spacing_sec))
        with cls._lock:
            n = cls._consecutive_rate_limited.get(user_id, 0) + 1
            cls._consecutive_rate_limited[user_id] = n
            backoff = min(_MAX_BACKOFF_SEC, base * (2 ** (n - 1)))
        _logger.warning(
            "ICICI rate limit %s: consecutive=%d backoff=%.3fs (user=%s)",
            endpoint,
            n,
            backoff,
            user_id,
        )
        return backoff

    @classmethod
    def reset_user(cls, user_id: str) -> None:
        with cls._lock:
            cls._last_call_mono.pop(user_id, None)
            cls._consecutive_rate_limited.pop(user_id, None)
            cls._throttling_active.pop(user_id, None)
            cls._backoff_visible.pop(user_id, None)


class GlobalIciciApiLimiter:
    """Global gate for all outbound Breeze REST calls."""

    _meta_lock = threading.Lock()
    _user_locks: dict[str, threading.Lock] = {}

    @classmethod
    def _user_lock(cls, user_id: str) -> threading.Lock:
        with cls._meta_lock:
            lock = cls._user_locks.get(user_id)
            if lock is None:
                lock = threading.Lock()
                cls._user_locks[user_id] = lock
            return lock

    @classmethod
    def resolve_user_id(cls, user_id: str | None) -> str | None:
        uid = (user_id or "").strip()
        if uid:
            return uid
        try:
            from icici_breeze_backend.app.auth.context import get_current_user_id

            ctx_uid = get_current_user_id()
            return (ctx_uid or "").strip() or None
        except Exception:
            return None

    @classmethod
    def build_throttle_error(cls, user_id: str | None, *, broker_error_text: str | None = None) -> dict[str, Any]:
        from icici_breeze_backend.app.services.api_usage import (
            API_CALLS_LIMIT_PER_DAY,
            is_daily_limit_reached,
        )

        uid = (user_id or "").strip()
        daily_exhausted = bool(uid and is_daily_limit_reached(uid)) or is_icici_daily_limit_exceeded(
            broker_error_text
        )
        msg = "You have been throttled by ICICI."
        if daily_exhausted:
            msg += (
                f" You have crossed the daily limit of {API_CALLS_LIMIT_PER_DAY} API calls and "
                "cannot use broker features until midnight IST."
            )
        else:
            msg += " Please try again in a minute."
        return {
            "Status": 429,
            "Error": msg,
            "icici_throttled": True,
            "daily_limit_exhausted": daily_exhausted,
        }

    @classmethod
    def _record_call(cls, user_id: str | None, record_url: str) -> None:
        try:
            from icici_breeze_backend.app.auth.context import get_current_route_id
            from icici_breeze_backend.app.services.api_usage import (
                record_breeze_call,
                record_breeze_call_if_in_request,
            )

            uid = (user_id or "").strip()
            url = str(record_url or "")
            if uid:
                record_breeze_call(user_id=uid, url=url, route_id=get_current_route_id())
            else:
                record_breeze_call_if_in_request(url)
        except Exception:
            pass

    @classmethod
    def _endpoint_from_url(cls, url: str) -> str:
        try:
            from icici_breeze_backend.app.services.api_usage import _path_segment_from_url

            return _path_segment_from_url(url)
        except Exception:
            parts = [p for p in str(url or "").split("/") if p]
            return parts[-1] if parts else "icici"

    @classmethod
    def request_breeze_http(
        cls,
        perform_http: Callable[[], T],
        *,
        user_id: str | None = None,
        endpoint: str | None = None,
        record_url: str,
        classify_response: Callable[[T], tuple[int, dict[str, Any] | None, str | None]],
        build_result: Callable[[dict[str, Any]], T],
        method: str = "?",
    ) -> T:
        """Execute one Breeze HTTP call with spacing, per-user lock, retry, and recording."""
        from icici_breeze_backend.app.auth.context import (
            get_current_correlation_id,
            get_current_route_id,
        )
        from icici_breeze_backend.app.services.breeze_http_audit import (
            _ORIGIN_DAILY_BLOCKED,
            _ORIGIN_SYNTHETIC,
            log_breeze_http_attempt,
            new_breeze_call_id,
            resolve_breeze_http_origin,
        )

        uid = cls.resolve_user_id(user_id)
        ep = endpoint or cls._endpoint_from_url(record_url)
        http_method = (method or "?").upper()
        route_id = get_current_route_id()
        correlation_id = get_current_correlation_id()

        def _audit(
            *,
            breeze_call_id: str,
            attempt: int,
            origin: str,
            elapsed_ms: float | None,
            http_status: int,
            body: dict[str, Any] | None,
            err_text: str | None,
            raw: Any = None,
        ) -> None:
            log_breeze_http_attempt(
                breeze_call_id=breeze_call_id,
                method=http_method,
                url=record_url,
                endpoint=ep,
                attempt=attempt,
                origin=origin,
                elapsed_ms=elapsed_ms,
                http_status=http_status,
                body=body,
                err_text=err_text,
                raw=raw,
                user_id=uid,
                route_id=route_id,
                correlation_id=correlation_id,
            )

        if uid:
            from icici_breeze_backend.app.services.api_usage import is_daily_limit_reached
            from icici_breeze_backend.app.services.user_rate_limit_prefs import (
                get_icici_rate_limit_pause_seconds,
            )

            if is_daily_limit_reached(uid):
                throttle = cls.build_throttle_error(uid)
                _audit(
                    breeze_call_id=new_breeze_call_id(),
                    attempt=0,
                    origin=_ORIGIN_DAILY_BLOCKED,
                    elapsed_ms=None,
                    http_status=int(throttle.get("Status") or 429),
                    body=throttle,
                    err_text=str(throttle.get("Error") or ""),
                    raw=None,
                )
                return build_result(throttle)

            base_pause = get_icici_rate_limit_pause_seconds(uid)
            user_lock = cls._user_lock(uid)
        else:
            base_pause = 0.5
            user_lock = None

        def _attempt_loop() -> T:
            broker_error: str | None = None
            for attempt in range(_MAX_HTTP_ATTEMPTS):
                breeze_call_id = new_breeze_call_id()
                t0 = time.perf_counter()
                raw = perform_http()
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                http_status, body, err_text = classify_response(raw)
                origin = resolve_breeze_http_origin(raw)
                _audit(
                    breeze_call_id=breeze_call_id,
                    attempt=attempt + 1,
                    origin=origin,
                    elapsed_ms=elapsed_ms,
                    http_status=http_status,
                    body=body,
                    err_text=err_text,
                    raw=raw,
                )
                broker_error = err_text or broker_error
                if uid:
                    cls._record_call(uid, record_url)
                    GlobalIciciApiPacer.mark_call_complete(uid)

                rate_limited = is_breeze_rate_limited(http_status, err_text) or (
                    body and is_breeze_rate_limited(body.get("Status"), body.get("Error"))
                )
                if not rate_limited:
                    if uid:
                        GlobalIciciApiPacer.on_success(uid)
                    return raw

                if uid:
                    GlobalIciciApiPacer.activate_throttling(uid)

                if attempt >= _MAX_HTTP_ATTEMPTS - 1:
                    break

                reason = rate_limit_reason(http_status)
                if uid:
                    sleep_sec = GlobalIciciApiPacer.rate_limit_backoff(uid, base_pause, endpoint=ep)
                    GlobalIciciApiPacer._sleep_with_status(
                        uid, sleep_sec, reason=reason, endpoint=ep
                    )
                else:
                    sleep_sec = min(_MAX_BACKOFF_SEC, base_pause * (2**attempt))
                    time.sleep(sleep_sec)

            throttle = cls.build_throttle_error(uid, broker_error_text=broker_error)
            _audit(
                breeze_call_id=new_breeze_call_id(),
                attempt=_MAX_HTTP_ATTEMPTS,
                origin=_ORIGIN_SYNTHETIC,
                elapsed_ms=None,
                http_status=int(throttle.get("Status") or 429),
                body=throttle,
                err_text=str(throttle.get("Error") or ""),
                raw=None,
            )
            return build_result(throttle)

        if user_lock is not None:
            with user_lock:
                GlobalIciciApiPacer.wait_for_slot(uid, base_pause, endpoint=ep)
                return _attempt_loop()
        return _attempt_loop()

    @classmethod
    def request_breeze_dict(
        cls,
        perform_http: Callable[[], dict[str, Any]],
        *,
        user_id: str | None = None,
        endpoint: str | None = None,
        record_url: str,
        method: str = "?",
    ) -> dict[str, Any]:
        """Limiter entry for httpx/direct dict responses."""

        def classify(raw: dict[str, Any]) -> tuple[int, dict[str, Any] | None, str | None]:
            st = raw.get("Status") or raw.get("status") or 0
            err = raw.get("Error") or raw.get("error")
            try:
                http_st = int(st)
            except (TypeError, ValueError):
                http_st = 0
            return http_st, raw if isinstance(raw, dict) else None, str(err) if err else None

        return cls.request_breeze_http(
            perform_http,
            user_id=user_id,
            endpoint=endpoint,
            record_url=record_url,
            classify_response=classify,
            build_result=lambda d: d,
            method=method,
        )

    @classmethod
    def request_breeze_httpx(
        cls,
        perform_http: Callable[[], Any],
        *,
        user_id: str | None = None,
        endpoint: str | None = None,
        record_url: str,
    ) -> dict[str, Any]:
        """Limiter entry for httpx.Response objects; returns Breeze-style dict."""

        def classify(raw: Any) -> tuple[int, dict[str, Any] | None, str | None]:
            http_status = int(getattr(raw, "status_code", 0) or 0)
            body: dict[str, Any] | None = None
            err_text: str | None = None
            text = getattr(raw, "text", None) or ""
            try:
                parsed = raw.json() if text else {}
            except Exception:
                parsed = {}
            if isinstance(parsed, dict):
                body = parsed
                try:
                    http_status = int(parsed.get("Status") or parsed.get("status") or http_status)
                except (TypeError, ValueError):
                    pass
                err_text = str(parsed.get("Error") or parsed.get("error") or "") or None
            if not err_text and http_status not in (200,):
                err_text = text[:500] if text else "Request failed"
            return http_status, body, err_text

        def build_result(error_dict: dict[str, Any]) -> dict[str, Any]:
            return error_dict

        def finalize(raw: Any) -> dict[str, Any]:
            if isinstance(raw, dict):
                return raw
            text = getattr(raw, "text", None) or ""
            if text:
                try:
                    data = raw.json()
                    if isinstance(data, dict):
                        return data
                except Exception:
                    pass
            return {
                "Status": int(getattr(raw, "status_code", 502) or 502),
                "Error": text[:500] if text else "Empty response",
            }

        result = cls.request_breeze_http(
            perform_http,
            user_id=user_id,
            endpoint=endpoint,
            record_url=record_url,
            classify_response=classify,
            build_result=build_result,
            method="GET",
        )
        return finalize(result)


def client_rate_limit_pause_seconds(user_id: str) -> float:
    """Suggested client wait after rate_limited response (matches server backoff semantics)."""
    from icici_breeze_backend.app.services.user_rate_limit_prefs import (
        get_icici_rate_limit_pause_seconds,
    )

    base = get_icici_rate_limit_pause_seconds(user_id)
    return GlobalIciciApiPacer.peek_next_backoff_seconds(user_id, base)


@contextmanager
def icici_user_scope(user_id: str):
    """Set request user_id context for login/bootstrap ICICI calls."""
    from icici_breeze_backend.app.auth import context as auth_ctx

    token = auth_ctx._user_id_ctx.set((user_id or "").strip())
    try:
        yield
    finally:
        auth_ctx._user_id_ctx.reset(token)
