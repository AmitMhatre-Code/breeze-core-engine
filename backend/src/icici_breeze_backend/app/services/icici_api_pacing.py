"""Universal per-user ICICI Breeze API gate (spacing, concurrency, 429/503 retry)."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Any, Callable, TypeVar

_logger = logging.getLogger(__name__)

_MAX_BACKOFF_SEC = 3.0
_MAX_HTTP_ATTEMPTS = 4

# ICICI's documented ceiling is ~100 requests/minute. 90 leaves headroom for calls this
# process cannot see — the same broker account used from ICICI's own web or mobile app
# spends the identical quota, and nothing here can count those.
_MAX_CALLS_PER_MINUTE = 90
_RATE_WINDOW_SEC = 60.0
# Longest a critical call will wait for a minute-slot. Beyond this the window is so full
# that waiting no longer beats surfacing the problem to the caller.
_MAX_SLOT_WAIT_SEC = 20.0

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


def is_icici_daily_limit_exceeded(error_text: str | None) -> bool:
    e = str(error_text or "").lower()
    return (
        "limit exceed" in e
        or "api call per minute" in e
        or ("5000" in e and "call" in e)
        or ("daily" in e and "limit" in e)
    )


class GlobalIciciApiPacer:
    """Thread-safe register of last ICICI API call time per user."""

    _lock = threading.Lock()
    _last_call_mono: dict[str, float] = {}
    _consecutive_rate_limited: dict[str, int] = {}
    _recent_calls: dict[str, deque[float]] = {}

    @classmethod
    def _prune(cls, uid: str, now: float) -> deque[float]:
        """Caller must hold `_lock`."""
        dq = cls._recent_calls.setdefault(uid, deque())
        cutoff = now - _RATE_WINDOW_SEC
        while dq and dq[0] < cutoff:
            dq.popleft()
        return dq

    @classmethod
    def calls_in_window(cls, user_id: str) -> int:
        with cls._lock:
            return len(cls._prune(user_id, time.monotonic()))

    @classmethod
    def note_call(cls, user_id: str) -> None:
        now = time.monotonic()
        with cls._lock:
            cls._prune(user_id, now).append(now)

    @classmethod
    def wait_for_minute_slot(cls, user_id: str, *, advisory: bool, endpoint: str = "icici") -> bool:
        """Reserve a slot in the trailing-60s window. False means "do not make this call".

        The pre-existing pacer only ever remembered the *last* call, so it could space
        calls but had no idea how many it had made in the last minute — it could not see a
        throttle coming and only ever reacted after ICICI returned 429, then gave up in
        ~4s against a minute-scale cooldown. This is the missing half: refuse to exceed the
        window in the first place.

        Advisory callers are refused immediately rather than queued. Making them wait would
        hand the scarce slots to whichever call arrived first, which is exactly the
        random-loser behaviour the classification exists to end.
        """
        deadline = time.monotonic() + _MAX_SLOT_WAIT_SEC
        while True:
            now = time.monotonic()
            with cls._lock:
                dq = cls._prune(user_id, now)
                if len(dq) < _MAX_CALLS_PER_MINUTE:
                    dq.append(now)
                    return True
                oldest = dq[0]
            if advisory:
                _logger.info(
                    "ICICI minute-window full (%d/%ds): shedding advisory %s (user=%s)",
                    _MAX_CALLS_PER_MINUTE,
                    int(_RATE_WINDOW_SEC),
                    endpoint,
                    user_id,
                )
                return False
            if now >= deadline:
                _logger.warning(
                    "ICICI minute-window still full after %.0fs; letting critical %s "
                    "through (user=%s)",
                    _MAX_SLOT_WAIT_SEC,
                    endpoint,
                    user_id,
                )
                with cls._lock:
                    cls._prune(user_id, time.monotonic()).append(time.monotonic())
                return True
            # Sleep only until the oldest call ages out of the window, never past the
            # deadline. A fixed poll interval would either burn CPU or overshoot.
            time.sleep(max(0.05, min(oldest + _RATE_WINDOW_SEC - now, deadline - now)))

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
            cls._recent_calls.pop(user_id, None)


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
    def build_shed_error(cls, user_id: str | None, *, endpoint: str = "icici") -> dict[str, Any]:
        """Refusal for an advisory call held back to protect the critical budget.

        Distinct from `build_throttle_error` on purpose: nothing failed and ICICI was
        never asked. Reporting it as a broker throttle would send users chasing a problem
        at ICICI's end that does not exist, and would hide the fact that the app made a
        deliberate choice on their behalf.
        """
        _logger.info("Shedding advisory ICICI call %s (user=%s)", endpoint, user_id)
        return {
            "Status": 429,
            "Error": (
                "Skipped a non-essential broker refresh to protect your remaining daily "
                "API calls for placing and cancelling orders. This display may be "
                "slightly out of date."
            ),
            "icici_throttled": False,
            "advisory_shed": True,
        }

    @classmethod
    def _record_call(
        cls,
        user_id: str | None,
        record_url: str,
        *,
        record_method: str | None = None,
        record_body: str | bytes | None = None,
    ) -> None:
        try:
            from icici_breeze_backend.app.auth.context import get_current_route_id
            from icici_breeze_backend.app.services.api_usage import (
                record_breeze_call,
                record_breeze_call_if_in_request,
            )

            uid = (user_id or "").strip()
            url = str(record_url or "")
            if uid:
                record_breeze_call(
                    user_id=uid,
                    url=url,
                    route_id=get_current_route_id(),
                    http_method=record_method,
                    request_body=record_body,
                )
            else:
                record_breeze_call_if_in_request(
                    url,
                    http_method=record_method,
                    request_body=record_body,
                )
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
        record_method: str | None = None,
        record_body: str | bytes | None = None,
        classify_response: Callable[[T], tuple[int, dict[str, Any] | None, str | None]],
        build_result: Callable[[dict[str, Any]], T],
    ) -> T:
        """Execute one Breeze HTTP call with spacing, per-user lock, retry, and recording."""
        uid = cls.resolve_user_id(user_id)
        ep = endpoint or cls._endpoint_from_url(record_url)

        if uid:
            from icici_breeze_backend.app.services.api_usage import (
                advisory_budget_exhausted,
                is_daily_limit_reached,
            )
            from icici_breeze_backend.app.services.icici_call_class import is_advisory
            from icici_breeze_backend.app.services.user_rate_limit_prefs import (
                get_icici_rate_limit_pause_seconds,
            )

            advisory = is_advisory()

            if is_daily_limit_reached(uid):
                return build_result(cls.build_throttle_error(uid))
            # The reserved floor: advisory traffic stops early so the last 500 calls of
            # the day stay available for placing and cancelling orders.
            if advisory and advisory_budget_exhausted(uid):
                return build_result(cls.build_shed_error(uid, endpoint=ep))
            # Proactive minute-window gate. Everything below this line is the old reactive
            # path — it now only has to handle throttles we could not predict.
            if not GlobalIciciApiPacer.wait_for_minute_slot(uid, advisory=advisory, endpoint=ep):
                return build_result(cls.build_shed_error(uid, endpoint=ep))

            base_pause = get_icici_rate_limit_pause_seconds(uid)
            GlobalIciciApiPacer.wait_for_slot(uid, base_pause, endpoint=ep)
            user_lock = cls._user_lock(uid)
        else:
            base_pause = 0.0
            user_lock = None

        def _attempt_loop() -> T:
            broker_error: str | None = None
            for attempt in range(_MAX_HTTP_ATTEMPTS):
                if uid and attempt > 0:
                    # The first attempt reserved its slot in `wait_for_minute_slot`; every
                    # retry is another real request against the same quota and has to be
                    # counted, or a throttle storm would be invisible to the window that
                    # exists to prevent it.
                    GlobalIciciApiPacer.note_call(uid)
                raw = perform_http()
                http_status, body, err_text = classify_response(raw)
                broker_error = err_text or broker_error
                if uid:
                    cls._record_call(
                        uid,
                        record_url,
                        record_method=record_method,
                        record_body=record_body,
                    )
                    GlobalIciciApiPacer.mark_call_complete(uid)

                if not is_breeze_rate_limited(http_status, err_text) and not (
                    body and is_breeze_rate_limited(body.get("Status"), body.get("Error"))
                ):
                    if uid:
                        GlobalIciciApiPacer.on_success(uid)
                    return raw

                if attempt >= _MAX_HTTP_ATTEMPTS - 1:
                    break

                if uid:
                    sleep_sec = GlobalIciciApiPacer.rate_limit_backoff(uid, base_pause, endpoint=ep)
                else:
                    sleep_sec = min(_MAX_BACKOFF_SEC, base_pause * (2**attempt))
                time.sleep(sleep_sec)

            return build_result(cls.build_throttle_error(uid, broker_error_text=broker_error))

        if user_lock is not None:
            with user_lock:
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
        record_method: str | None = None,
        record_body: str | bytes | None = None,
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
            record_method=record_method,
            record_body=record_body,
            classify_response=classify,
            build_result=lambda d: d,
        )

    @classmethod
    def request_breeze_httpx(
        cls,
        perform_http: Callable[[], Any],
        *,
        user_id: str | None = None,
        endpoint: str | None = None,
        record_url: str,
        record_method: str | None = None,
        record_body: str | bytes | None = None,
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
            record_method=record_method,
            record_body=record_body,
            classify_response=classify,
            build_result=build_result,
        )
        return finalize(result)


@contextmanager
def icici_user_scope(user_id: str):
    """Set request user_id context for login/bootstrap ICICI calls."""
    from icici_breeze_backend.app.auth import context as auth_ctx

    token = auth_ctx._user_id_ctx.set((user_id or "").strip())
    try:
        yield
    finally:
        auth_ctx._user_id_ctx.reset(token)

