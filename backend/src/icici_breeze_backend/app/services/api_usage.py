"""Per-user ICICI Breeze API call count (daily). Used for footer counter and safety thresholds.

One 'call' = one HTTP request from this app to a Breeze REST endpoint (api.icicidirect.com/breezeapi/).
Counted in: requests_patch (SDK via requests.api.request), CustomerDetails + call_icici_api_direct (httpx).
"""
import sqlite3
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import icici_breeze_backend.app.core.config as cfg

API_CALLS_LIMIT_PER_DAY = 5000
GREEN_MAX = 4000
AMBER_MAX = 4500

_DB_PATH = cfg.DATA_PATH + "db.sqlite3"
_lock = threading.Lock()


def _today_ist() -> str:
    """Return today's date in IST as YYYY-MM-DD."""
    return datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()


def record_breeze_call_if_in_request(url: str) -> None:
    """If url is a Breeze REST endpoint and we have user_id in request context, count one API call (per ICICI: one call = one HTTP request)."""
    if not url or "api.icicidirect.com" not in url or "breezeapi" not in url:
        return
    try:
        from icici_breeze_backend.app.auth.context import get_current_user_id
        user_id = get_current_user_id()
        if user_id:
            record_call(user_id)
    except Exception:
        pass


def record_call(user_id: str) -> None:
    """Increment today's API call count for user_id. Thread-safe."""
    if not user_id or not user_id.strip():
        return
    usage_date = _today_ist()
    with _lock:
        try:
            with sqlite3.connect(_DB_PATH) as conn:
                conn.execute(
                    """
                    INSERT INTO api_usage_daily (user_id, usage_date, call_count, updated_at)
                    VALUES (?, ?, 1, datetime('now'))
                    ON CONFLICT(user_id, usage_date) DO UPDATE SET
                        call_count = call_count + 1,
                        updated_at = datetime('now')
                    """,
                    (user_id.strip(), usage_date),
                )
        except sqlite3.OperationalError:
            pass


def get_today_count(user_id: str) -> int:
    """Return today's API call count for user_id."""
    if not user_id or not user_id.strip():
        return 0
    usage_date = _today_ist()
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                "SELECT call_count FROM api_usage_daily WHERE user_id = ? AND usage_date = ?",
                (user_id.strip(), usage_date),
            ).fetchone()
            return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0


def get_usage_for_display(user_id: str) -> dict:
    """Return dict for template: api_calls_today, api_calls_limit, api_usage_band (green|amber|red)."""
    count = get_today_count(user_id) if user_id else 0
    if count <= GREEN_MAX:
        band = "green"
    elif count <= AMBER_MAX:
        band = "amber"
    else:
        band = "red"
    return {
        "api_calls_today": count,
        "api_calls_limit": API_CALLS_LIMIT_PER_DAY,
        "api_usage_band": band,
    }
