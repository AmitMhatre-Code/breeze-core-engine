"""Per-user ICICI Breeze API call count (daily). Used for footer counter and safety thresholds.

One 'call' = one HTTP request from this app to a Breeze REST endpoint (api.icicidirect.com/breezeapi/).
Counted in: requests_patch (SDK via requests.api.request), CustomerDetails + call_icici_api_direct (httpx).
"""
import sqlite3
import threading
from datetime import timedelta
from urllib.parse import urlparse

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import today_ist_date

API_CALLS_LIMIT_PER_DAY = 5000
GREEN_MAX = 4000
AMBER_MAX = 4500

_DB_PATH = cfg.DATA_PATH + cfg.USERS_DB
_lock = threading.Lock()


def _today_ist() -> str:
    """Return today's date in IST as YYYY-MM-DD."""
    return today_ist_date().isoformat()


def _is_breeze_url(url: str) -> bool:
    u = (url or "").strip()
    return bool(u and "api.icicidirect.com" in u and "breezeapi" in u)


# Breeze REST path segment → breeze-connect SDK method (see breeze_connect.config.APIEndPoint).
_ENDPOINT_TO_METHOD: dict[str, str] = {
    "customerdetails": "get_customer_details",
    "dematholdings": "get_demat_holdings",
    "funds": "get_funds",
    "historicalcharts": "get_historical_data",
    "margin": "get_margin",
    "order": "order",
    "portfolioholdings": "get_portfolio_holdings",
    "portfoliopositions": "get_portfolio_positions",
    "quotes": "get_quotes",
    "trades": "get_trade_list",
    "optionchain": "get_option_chain_quotes",
    "squareoff": "square_off",
    "fnolmtpriceandqtycal": "limit_calculator",
    "margincalculator": "margin_calculator",
    "gttorder": "gtt_order_book",
}


def _path_segment_from_url(url: str) -> str:
    try:
        parsed = urlparse(url or "")
        parts = [p for p in (parsed.path or "").split("/") if p]
        if not parts:
            return "unknown"
        if "api" in parts:
            idx = parts.index("api")
            if idx + 2 < len(parts):
                return (parts[idx + 2] or "unknown").lower()
        return (parts[-1] or "unknown").lower()
    except Exception:
        return "unknown"


def _sdk_method_name(url: str, path_segment: str) -> str:
    """Map Breeze URL path segment to breeze-connect SDK method name."""
    seg = (path_segment or "").lower()
    if seg == "historicalcharts" and "breezeapi.icicidirect.com/api/v2" in (url or "").lower():
        return "get_historical_data_v2"
    return _ENDPOINT_TO_METHOD.get(seg, seg)


def _normalize_api_name(api_name: str) -> str:
    """Normalize stored path segments (legacy rows) to SDK method names."""
    key = (api_name or "").strip().lower()
    return _ENDPOINT_TO_METHOD.get(key, api_name)


def _extract_api_name(url: str) -> str:
    """Map Breeze URL to breeze-connect SDK method used for API-wise aggregates."""
    path_segment = _path_segment_from_url(url)
    return _sdk_method_name(url, path_segment)


def _ensure_usage_tables(conn: sqlite3.Connection) -> None:
    """Create usage aggregate tables when missing (for compatibility)."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS api_usage_daily (
            user_id TEXT NOT NULL,
            usage_date TEXT NOT NULL,
            call_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(user_id, usage_date)
        );
        CREATE INDEX IF NOT EXISTS idx_api_usage_date ON api_usage_daily(usage_date);

        CREATE TABLE IF NOT EXISTS api_usage_daily_by_api (
            user_id TEXT NOT NULL,
            usage_date TEXT NOT NULL,
            api_name TEXT NOT NULL,
            call_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(user_id, usage_date, api_name)
        );
        CREATE INDEX IF NOT EXISTS idx_api_usage_by_api_user_date
            ON api_usage_daily_by_api(user_id, usage_date);

        CREATE TABLE IF NOT EXISTS api_usage_daily_by_route (
            user_id TEXT NOT NULL,
            usage_date TEXT NOT NULL,
            route_id TEXT NOT NULL,
            call_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(user_id, usage_date, route_id)
        );
        CREATE INDEX IF NOT EXISTS idx_api_usage_by_route_user_date
            ON api_usage_daily_by_route(user_id, usage_date);
        """
    )


def record_breeze_call(user_id: str, url: str, route_id: str | None = None) -> None:
    """Increment daily total + api-wise + route-wise usage for one Breeze HTTP call."""
    uid = (user_id or "").strip()
    if not uid:
        return
    usage_date = _today_ist()
    api_name = _extract_api_name(url)
    route = (route_id or "").strip() or "unknown"
    with _lock:
        try:
            with sqlite3.connect(_DB_PATH) as conn:
                _ensure_usage_tables(conn)
                conn.execute(
                    """
                    INSERT INTO api_usage_daily (user_id, usage_date, call_count, updated_at)
                    VALUES (?, ?, 1, datetime('now'))
                    ON CONFLICT(user_id, usage_date) DO UPDATE SET
                        call_count = call_count + 1,
                        updated_at = datetime('now')
                    """,
                    (uid, usage_date),
                )
                conn.execute(
                    """
                    INSERT INTO api_usage_daily_by_api (user_id, usage_date, api_name, call_count, updated_at)
                    VALUES (?, ?, ?, 1, datetime('now'))
                    ON CONFLICT(user_id, usage_date, api_name) DO UPDATE SET
                        call_count = call_count + 1,
                        updated_at = datetime('now')
                    """,
                    (uid, usage_date, api_name),
                )
                conn.execute(
                    """
                    INSERT INTO api_usage_daily_by_route (user_id, usage_date, route_id, call_count, updated_at)
                    VALUES (?, ?, ?, 1, datetime('now'))
                    ON CONFLICT(user_id, usage_date, route_id) DO UPDATE SET
                        call_count = call_count + 1,
                        updated_at = datetime('now')
                    """,
                    (uid, usage_date, route),
                )
        except sqlite3.OperationalError:
            pass


def record_breeze_call_if_in_request(url: str) -> None:
    """If url is a Breeze REST endpoint and we have user_id in request context, count one API call (per ICICI: one call = one HTTP request)."""
    if not _is_breeze_url(url):
        return
    try:
        from icici_breeze_backend.app.auth.context import get_current_route_id, get_current_user_id

        user_id = get_current_user_id()
        if user_id:
            record_breeze_call(user_id=user_id, url=url, route_id=get_current_route_id())
    except Exception:
        pass


def record_call(user_id: str) -> None:
    """Backward-compatible daily counter increment without API/route dimensions."""
    record_breeze_call(user_id=user_id, url="https://api.icicidirect.com/breezeapi/api/v1/unknown", route_id="unknown")


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


def get_daily_usage_by_api(user_id: str, days: int = 30) -> list[dict]:
    uid = (user_id or "").strip()
    if not uid:
        return []
    span = max(1, min(int(days or 30), 120))
    cutoff = (today_ist_date() - timedelta(days=span - 1)).isoformat()
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT usage_date, api_name, call_count
                FROM api_usage_daily_by_api
                WHERE user_id = ? AND usage_date >= ?
                ORDER BY usage_date DESC, call_count DESC, api_name ASC
                """,
                (uid, cutoff),
            ).fetchall()
            merged: dict[tuple[str, str], int] = {}
            for usage_date, api_name, call_count in rows:
                name = _normalize_api_name(str(api_name or ""))
                key = (str(usage_date), name)
                merged[key] = merged.get(key, 0) + int(call_count or 0)
            out = [
                {"usage_date": d, "api_name": n, "call_count": c}
                for (d, n), c in merged.items()
            ]
            out.sort(key=lambda r: (-r["usage_date"], -r["call_count"], r["api_name"]))
            return out
    except sqlite3.OperationalError:
        return []


def get_daily_usage_by_route(user_id: str, days: int = 30) -> list[dict]:
    uid = (user_id or "").strip()
    if not uid:
        return []
    span = max(1, min(int(days or 30), 120))
    cutoff = (today_ist_date() - timedelta(days=span - 1)).isoformat()
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT usage_date, route_id, call_count
                FROM api_usage_daily_by_route
                WHERE user_id = ? AND usage_date >= ?
                ORDER BY usage_date DESC, call_count DESC, route_id ASC
                """,
                (uid, cutoff),
            ).fetchall()
            return [{"usage_date": r[0], "route_id": r[1], "call_count": int(r[2] or 0)} for r in rows]
    except sqlite3.OperationalError:
        return []
