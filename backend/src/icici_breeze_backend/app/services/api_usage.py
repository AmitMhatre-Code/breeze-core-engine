"""Per-user ICICI Breeze API call count (daily). Used for footer counter and safety thresholds.

One 'call' = one HTTP request from this app to a Breeze REST endpoint (api.icicidirect.com/breezeapi/).
Counted in: requests_patch (SDK via requests.api.request), CustomerDetails + call_icici_api_direct (httpx).
"""
import json
import sqlite3
import threading
from datetime import timedelta
from urllib.parse import urlparse

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import SQLITE_NOW_IST, today_ist_date

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


# Breeze REST path segment → breeze-connect SDK method for 1:1 endpoints only.
# Shared endpoints (order, funds, trades, gttorder) are resolved via HTTP verb + body.
_SIMPLE_ENDPOINT_TO_METHOD: dict[str, str] = {
    "customerdetails": "get_customer_details",
    "dematholdings": "get_demat_holdings",
    "historicalcharts": "get_historical_data",
    "margin": "get_margin",
    "portfolioholdings": "get_portfolio_holdings",
    "portfoliopositions": "get_portfolio_positions",
    "quotes": "get_quotes",
    "optionchain": "get_option_chain_quotes",
    "squareoff": "square_off",
    "fnolmtpriceandqtycal": "limit_calculator",
    "margincalculator": "margin_calculator",
    "preview_order": "preview_order",
}

# Best-effort normalization for legacy rows stored as raw path segments (no verb/body).
_LEGACY_SEGMENT_NORMALIZE: dict[str, str] = {
    **_SIMPLE_ENDPOINT_TO_METHOD,
    "funds": "get_funds",
    "trades": "get_trade_list",
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


def _parse_request_body(raw: str | bytes | None) -> dict:
    if raw is None:
        return {}
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    text = text.strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _body_has(body: dict, key: str) -> bool:
    val = body.get(key)
    if val is None:
        return False
    if isinstance(val, str) and not val.strip():
        return False
    return True


def _gtt_type(body: dict) -> str:
    return str(body.get("gtt_type") or "").strip().lower()


def _resolve_shared_endpoint(seg: str, method: str | None, body: dict) -> str | None:
    """Map shared REST paths to SDK methods using HTTP verb and JSON body (breeze_connect)."""
    verb = (method or "").upper()

    if seg == "order":
        if verb == "POST":
            return "place_order"
        if verb == "PUT":
            return "modify_order"
        if verb == "DELETE":
            return "cancel_order"
        if verb == "GET":
            if _body_has(body, "from_date"):
                return "get_order_list"
            if _body_has(body, "order_id"):
                return "get_order_detail"
        return None

    if seg == "funds":
        if verb == "POST":
            return "set_funds"
        if verb == "GET":
            return "get_funds"
        return None

    if seg == "trades":
        if verb == "GET":
            if _body_has(body, "from_date"):
                return "get_trade_list"
            if _body_has(body, "order_id"):
                return "get_trade_detail"
        return None

    if seg == "gttorder":
        if verb == "GET" and _body_has(body, "from_date"):
            return "gtt_order_book"
        if verb == "POST":
            if _body_has(body, "fresh_order_action") or _gtt_type(body) in ("oco", "cover_oco"):
                return "gtt_three_leg_place_order"
            if _gtt_type(body) == "single":
                return "gtt_single_leg_place_order"
            return "gtt_three_leg_place_order"
        if verb == "PUT":
            if _gtt_type(body) == "single":
                return "gtt_single_leg_modify_order"
            return "gtt_three_leg_modify_order"
        if verb == "DELETE":
            # Three-leg vs single-leg cancel share identical HTTP signatures.
            return "gtt_three_leg_cancel_order"
        return None

    return None


def _resolve_sdk_method_name(
    url: str,
    http_method: str | None = None,
    request_body: str | bytes | None = None,
) -> str:
    """Map Breeze URL (+ optional verb/body) to breeze-connect SDK method name."""
    path_segment = _path_segment_from_url(url)
    seg = (path_segment or "").lower()

    if seg == "historicalcharts" and "breezeapi.icicidirect.com/api/v2" in (url or "").lower():
        return "get_historical_data_v2"

    body = _parse_request_body(request_body) if request_body is not None else {}
    shared = _resolve_shared_endpoint(seg, http_method, body)
    if shared:
        return shared

    return _SIMPLE_ENDPOINT_TO_METHOD.get(seg, seg)


def _normalize_api_name(api_name: str) -> str:
    """Normalize stored path segments (legacy rows) to SDK method names."""
    key = (api_name or "").strip().lower()
    return _LEGACY_SEGMENT_NORMALIZE.get(key, api_name)


def _extract_api_name(
    url: str,
    http_method: str | None = None,
    request_body: str | bytes | None = None,
) -> str:
    """Map Breeze URL to breeze-connect SDK method used for API-wise aggregates."""
    return _resolve_sdk_method_name(url, http_method, request_body)


def _ensure_usage_tables(conn: sqlite3.Connection) -> None:
    """Create usage aggregate tables when missing (for compatibility)."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS api_usage_daily (
            user_id TEXT NOT NULL,
            usage_date TEXT NOT NULL,
            call_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
            PRIMARY KEY(user_id, usage_date)
        );
        CREATE INDEX IF NOT EXISTS idx_api_usage_date ON api_usage_daily(usage_date);

        CREATE TABLE IF NOT EXISTS api_usage_daily_by_api (
            user_id TEXT NOT NULL,
            usage_date TEXT NOT NULL,
            api_name TEXT NOT NULL,
            call_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
            PRIMARY KEY(user_id, usage_date, api_name)
        );
        CREATE INDEX IF NOT EXISTS idx_api_usage_by_api_user_date
            ON api_usage_daily_by_api(user_id, usage_date);

        CREATE TABLE IF NOT EXISTS api_usage_daily_by_route (
            user_id TEXT NOT NULL,
            usage_date TEXT NOT NULL,
            route_id TEXT NOT NULL,
            call_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
            PRIMARY KEY(user_id, usage_date, route_id)
        );
        CREATE INDEX IF NOT EXISTS idx_api_usage_by_route_user_date
            ON api_usage_daily_by_route(user_id, usage_date);
        """
    )


def record_breeze_call(
    user_id: str,
    url: str,
    route_id: str | None = None,
    *,
    http_method: str | None = None,
    request_body: str | bytes | None = None,
) -> None:
    """Increment daily total + api-wise + route-wise usage for one Breeze HTTP call."""
    uid = (user_id or "").strip()
    if not uid:
        return
    usage_date = _today_ist()
    api_name = _extract_api_name(url, http_method, request_body)
    route = (route_id or "").strip() or "unknown"
    with _lock:
        try:
            with sqlite3.connect(_DB_PATH) as conn:
                _ensure_usage_tables(conn)
                conn.execute(
                    f"""
                    INSERT INTO api_usage_daily (user_id, usage_date, call_count, updated_at)
                    VALUES (?, ?, 1, {SQLITE_NOW_IST})
                    ON CONFLICT(user_id, usage_date) DO UPDATE SET
                        call_count = call_count + 1,
                        updated_at = {SQLITE_NOW_IST}
                    """,
                    (uid, usage_date),
                )
                conn.execute(
                    f"""
                    INSERT INTO api_usage_daily_by_api (user_id, usage_date, api_name, call_count, updated_at)
                    VALUES (?, ?, ?, 1, {SQLITE_NOW_IST})
                    ON CONFLICT(user_id, usage_date, api_name) DO UPDATE SET
                        call_count = call_count + 1,
                        updated_at = {SQLITE_NOW_IST}
                    """,
                    (uid, usage_date, api_name),
                )
                conn.execute(
                    f"""
                    INSERT INTO api_usage_daily_by_route (user_id, usage_date, route_id, call_count, updated_at)
                    VALUES (?, ?, ?, 1, {SQLITE_NOW_IST})
                    ON CONFLICT(user_id, usage_date, route_id) DO UPDATE SET
                        call_count = call_count + 1,
                        updated_at = {SQLITE_NOW_IST}
                    """,
                    (uid, usage_date, route),
                )
        except sqlite3.OperationalError:
            pass


def record_breeze_call_if_in_request(
    url: str,
    *,
    http_method: str | None = None,
    request_body: str | bytes | None = None,
) -> None:
    """If url is a Breeze REST endpoint and we have user_id in request context, count one API call (per ICICI: one call = one HTTP request)."""
    if not _is_breeze_url(url):
        return
    try:
        from icici_breeze_backend.app.auth.context import get_current_route_id, get_current_user_id

        user_id = get_current_user_id()
        if user_id:
            record_breeze_call(
                user_id=user_id,
                url=url,
                route_id=get_current_route_id(),
                http_method=http_method,
                request_body=request_body,
            )
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


def is_daily_limit_reached(user_id: str) -> bool:
    """True when the user's internal daily Breeze call count has reached the cap."""
    return get_today_count(user_id) >= API_CALLS_LIMIT_PER_DAY


def advisory_budget_exhausted(user_id: str) -> bool:
    """True once advisory traffic must stop to protect the reserve.

    The reserve line is `AMBER_MAX` (4500), not a new constant — the UI already turns
    amber there and tells the user to spend carefully on "critical operations such as
    placing or cancelling orders". This makes that advice structural instead of advisory:
    past 4500 the app itself stops spending on decoration.

    500 calls is a large reserve in practice. On the day this was designed against,
    `place_order` accounted for 51 calls and every genuinely critical API combined for
    well under 200 — so the reserve has never come close to blocking real trading, while
    the advisory traffic it fences off had consumed 90% of the cap.
    """
    return get_today_count(user_id) >= AMBER_MAX


def get_usage_warning(user_id: str) -> str | None:
    """Return a proactive warning when the user is in the final 1000-call band."""
    count = get_today_count(user_id)
    if count < GREEN_MAX or count >= API_CALLS_LIMIT_PER_DAY:
        return None
    remaining = API_CALLS_LIMIT_PER_DAY - count
    return (
        f"You have used {count} of your {API_CALLS_LIMIT_PER_DAY} daily ICICI API calls. "
        f"You have only {remaining} calls remaining — use them carefully during critical "
        "operations such as placing or cancelling orders."
    )


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
            out.sort(key=lambda r: (-r["call_count"], r["api_name"]))
            out.sort(key=lambda r: r["usage_date"], reverse=True)
            return out
    except sqlite3.OperationalError:
        return []


_ORDER_PLACEMENT_METHODS = frozenset(
    {
        "place_order",
        "modify_order",
        "cancel_order",
        "square_off",
        "preview_order",
        "set_funds",
        "gtt_three_leg_place_order",
        "gtt_three_leg_modify_order",
        "gtt_three_leg_cancel_order",
        "gtt_single_leg_place_order",
        "gtt_single_leg_modify_order",
        "gtt_single_leg_cancel_order",
        "gtt_order_book",
    }
)
_MARKET_QUOTES_METHODS = frozenset(
    {
        "get_quotes",
        "get_option_chain_quotes",
        "get_historical_data",
        "get_historical_data_v2",
        "get_names",
        "ws_connect",
        "ws_disconnect",
        "subscribe_feeds",
    }
)
_PORTFOLIO_METHODS = frozenset(
    {
        "get_customer_details",
        "get_demat_holdings",
        "get_funds",
        "get_margin",
        "get_order_detail",
        "get_order_list",
        "get_portfolio_holdings",
        "get_portfolio_positions",
        "get_trade_list",
        "get_trade_detail",
        "limit_calculator",
        "margin_calculator",
    }
)


def _category_for_api_name(api_name: str) -> str:
    if api_name in _ORDER_PLACEMENT_METHODS:
        return "order_placement"
    if api_name in _MARKET_QUOTES_METHODS:
        return "market_quotes"
    if api_name in _PORTFOLIO_METHODS:
        return "portfolio"
    return "other"


def get_daily_usage_by_category(user_id: str, days: int = 30) -> list[dict]:
    """Roll up the per-method breakdown into the three semantic buckets the
    Settings > API Usage screen displays: order placement / market quotes / portfolio.
    """
    by_api = get_daily_usage_by_api(user_id, days)
    totals: dict[tuple[str, str], int] = {}
    for row in by_api:
        cat = _category_for_api_name(str(row["api_name"]))
        key = (str(row["usage_date"]), cat)
        totals[key] = totals.get(key, 0) + int(row["call_count"] or 0)
    out = [
        {"usage_date": d, "category": c, "call_count": n} for (d, c), n in totals.items()
    ]
    out.sort(key=lambda r: (r["usage_date"], r["category"]), reverse=True)
    return out


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
