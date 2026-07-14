"""Route option chain quotes to websocket, bhavcopy, or ICICI REST."""
from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import Any, TYPE_CHECKING

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, parse_strike, strike_key
from icici_breeze_backend.app.core.market_hours import bhavcopy_is_stale, is_india_market_open
from icici_breeze_backend.app.core.timezone import IST, now_ist
from icici_breeze_backend.app.db.redis_client import cache_get_json, cache_set_json
from icici_breeze_backend.core.icici_client import icici_client
from icici_breeze_backend.app.services.reference_data.bhavcopy_store import (
    _lookup_bhav_row,
    _row_to_chain_cell,
    build_chain_from_bhavcopy,
    get_bhavcopy_source_date,
)
from icici_breeze_backend.app.services.reference_data.keys import (
    icici_rest_chain_key,
    ws_quote_key,
)
from icici_breeze_backend.app.services.reference_data.scrip_index import (
    ensure_scrip_memory_ready,
    list_tradeable_strikes_memory,
)
from icici_breeze_backend.app.services.reference_data.tradable_contracts import (
    list_tradeable_strikes,
)

if TYPE_CHECKING:
    from icici_breeze_backend.audit.strategy_builder_audit import StrategyBuilderAuditSession
    from icici_breeze_backend.app.services.processor import processor as Processor

_logger = logging.getLogger(__name__)


def _previous_trading_day(d: dt.date) -> dt.date:
    from icici_breeze_backend.app.core.market_hours import is_india_trading_day

    prev = d - dt.timedelta(days=1)
    while not is_india_trading_day(dt.datetime(prev.year, prev.month, prev.day, 12, 0, tzinfo=IST)):
        prev -= dt.timedelta(days=1)
    return prev


def latest_concluded_trading_day(now: dt.datetime | None = None) -> dt.date:
    """Last trading day whose session has fully ended (for bhavcopy freshness)."""
    dt_ist = (now or now_ist()).astimezone(IST)
    from icici_breeze_backend.app.core.market_hours import is_india_trading_day

    close = dt_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    if is_india_trading_day(dt_ist) and dt_ist >= close:
        return dt_ist.date()
    return _previous_trading_day(dt_ist.date())


def bhavcopy_is_fresh(exchange_code: str, now: dt.datetime | None = None) -> bool:
    src = get_bhavcopy_source_date(exchange_code)
    if src is None:
        return False
    return src >= latest_concluded_trading_day(now)


def resolve_quote_source(exchange_code: str, now: dt.datetime | None = None) -> str:
    if is_india_market_open(now):
        return "websocket"
    if bhavcopy_is_fresh(exchange_code, now):
        return "bhavcopy"
    return "icici_api"


def _cell_updated_at(cell: Any) -> float | None:
    if not isinstance(cell, dict):
        return None
    try:
        ts = float(cell.get("updated_at"))
    except (TypeError, ValueError):
        return None
    return ts if ts > 0 else None


def _max_cell_updated_at(chain_rows: list[Any]) -> float | None:
    best: float | None = None
    for row in chain_rows:
        if not isinstance(row, dict):
            continue
        for side in ("call", "put"):
            ts = _cell_updated_at(row.get(side))
            if ts is not None and (best is None or ts > best):
                best = ts
    return best


def _enrich_quote_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    """Attach chain-level quote_as_of for frontend transparency."""
    source = payload.get("quote_source")
    if source == "websocket":
        ts = _max_cell_updated_at(payload.get("chain_rows") or [])
        if ts is not None:
            payload["quote_as_of"] = dt.datetime.fromtimestamp(ts, tz=IST).isoformat()
    elif source == "bhavcopy":
        bd = payload.get("bhavcopy_date")
        if bd:
            payload["quote_as_of"] = str(bd)
            try:
                bhav_date = dt.date.fromisoformat(str(bd))
            except ValueError:
                bhav_date = None
            payload["bhavcopy_stale"] = bhavcopy_is_stale(bhav_date)
    elif source == "icici_api":
        payload["quote_as_of"] = now_ist().isoformat()
    return payload


def _rest_fallback_allowed(exchange_code: str) -> bool:
    return resolve_quote_source(exchange_code) != "bhavcopy"


def _spot_cache_key(exchange_code: str, stock_code: str) -> str:
    return f"quotes:spot:{exchange_code.upper()}:{stock_code.upper()}"


def _spot_cache_ttl_seconds() -> int:
    try:
        return max(30, int(getattr(cfg, "CHAIN_SPOT_CACHE_TTL_SECONDS", 60) or 60))
    except (TypeError, ValueError):
        return 60


def _parse_positive_spot(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        spot = float(raw)
    except (TypeError, ValueError):
        return None
    return spot if spot > 0 else None


def _remember_chain_spot(exchange_code: str, stock_code: str, spot: float | None) -> float | None:
    parsed = _parse_positive_spot(spot)
    if parsed is None:
        return None
    cache_set_json(
        _spot_cache_key(exchange_code, stock_code),
        {"spot_price": parsed},
        ex=_spot_cache_ttl_seconds(),
    )
    return parsed


def _cached_chain_spot(exchange_code: str, stock_code: str) -> float | None:
    cached = cache_get_json(_spot_cache_key(exchange_code, stock_code))
    if not isinstance(cached, dict):
        return None
    return _parse_positive_spot(cached.get("spot_price"))


def remember_chain_spot(exchange_code: str, stock_code: str, spot: float | None) -> float | None:
    """Public entry point for external live-spot sources (e.g. `index_spot_feed`'s
    NIFTY/SENSEX index ticks) to seed the same cache `_resolve_chain_spot` reads
    first -- keeping it warm from a source that doesn't depend on bhavcopy/REST
    lets `chain_readiness.is_chain_complete` reliably use its lenient ATM-window
    gate instead of falling back to requiring every tradeable strike to tick."""
    return _remember_chain_spot(exchange_code, stock_code, spot)


def _spot_from_bhavcopy(
    stock_code: str,
    expiry_display: str,
    exchange_code: str,
    strikes: list[Strike],
) -> float | None:
    """Scans every tradeable strike, not just the first few -- bhavcopy only carries
    a row for strikes that actually traded that day, and for thinner chains (e.g.
    Sensex weeklies) the deep extremes of the tradeable-strike range routinely have
    no trades at all, so checking a handful of arbitrary strikes was fragile."""
    for strike in strikes:
        for right in (cfg.CALL, cfg.PUT):
            row = _lookup_bhav_row(stock_code, expiry_display, right, strike, exchange_code)
            if not row:
                continue
            spot = _parse_positive_spot(row.get("spot_price"))
            if spot is not None:
                return spot
    return None


def _resolve_chain_spot(
    proc: "Processor",
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    strikes: list[Strike],
) -> float | None:
    spot = _cached_chain_spot(exchange_code, stock_code)
    if spot is not None:
        return spot
    spot = _spot_from_bhavcopy(stock_code, expiry_display, exchange_code, strikes)
    if spot is not None:
        return _remember_chain_spot(exchange_code, stock_code, spot)
    if not _rest_fallback_allowed(exchange_code):
        return None
    rest_payload = _get_or_build_icici_rest_chain(
        proc,
        user_id,
        stock_code,
        exchange_code,
        expiry_display,
        strikes,
        lot_size=None,
        freeze_quantity=None,
    )
    spot = _parse_positive_spot(rest_payload.get("spot_price")) if rest_payload else None
    if spot is not None:
        return _remember_chain_spot(exchange_code, stock_code, spot)
    return None


def _apply_chain_spot(
    payload: dict[str, Any],
    *,
    proc: "Processor",
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    strikes: list[Strike],
    spot: float | None = None,
) -> dict[str, Any]:
    resolved = _parse_positive_spot(payload.get("spot_price"))
    if resolved is None:
        resolved = spot if spot is not None else _resolve_chain_spot(
            proc, user_id, stock_code, exchange_code, expiry_display, strikes
        )
    else:
        resolved = _remember_chain_spot(exchange_code, stock_code, resolved)
    if resolved is None:
        return payload
    spot = resolved
    payload["spot_price"] = spot
    chain_rows = payload.get("chain_rows") or []
    chain_strikes = sorted(
        {
            parse_strike(row.get("strike_price"))
            for row in chain_rows
            if isinstance(row, dict) and parse_strike(row.get("strike_price")) is not None
        }
    )
    if chain_strikes:
        payload["atm_strike"] = min(chain_strikes, key=lambda s: abs(s - spot))
    return payload


def _bhavcopy_miss_response() -> dict[str, Any]:
    return {
        "Status": 404,
        "Error": "Quote not available in bhavcopy",
        "Success": None,
        "quote_source": "bhavcopy",
    }


def _icici_api_miss_response() -> dict[str, Any]:
    return {
        "Status": 404,
        "Error": "Quote not available via ICICI REST fallback.",
        "Success": None,
        "quote_source": "icici_api",
    }


def _quote_miss_response(exchange_code: str) -> dict[str, Any]:
    """Miss response shaped after the currently-resolved quote source, so a
    REST-fallback failure isn't misreported as a bhavcopy miss."""
    if resolve_quote_source(exchange_code) == "icici_api":
        return _icici_api_miss_response()
    return _bhavcopy_miss_response()


def _normalize_expiry_display(expiry: str) -> str:
    s = str(expiry or "").strip()
    if not s:
        return s
    if len(s) == 10 and s[4] == "-":
        try:
            return dt.datetime.strptime(s, "%Y-%m-%d").strftime("%d-%b-%Y")
        except ValueError:
            return s
    if "T" in s:
        from icici_breeze_backend.app.services.processor import _expiry_api_to_display

        return _expiry_api_to_display(s)
    return s


def _normalize_right_key(right: str) -> str:
    r = str(right or "").strip().lower()
    if r in {"call", "ce", "c"} or r == cfg.CALL.lower():
        return "call"
    return "put"


def _is_spot_strike(strike_price: Any) -> bool:
    if strike_price is None:
        return True
    strike_f = parse_strike(strike_price)
    return strike_f is None or strike_f <= 0


def _cell_to_icici_row(cell: dict[str, Any]) -> dict[str, Any]:
    total_buy = int(cell.get("total_buy_qty") or 0)
    total_sell = int(cell.get("total_sell_qty") or 0)
    ratio = cell.get("buy_sell_ratio")
    if ratio is None:
        if total_sell > 0:
            ratio = total_buy / total_sell
        elif total_buy == 0:
            ratio = 0
        else:
            ratio = "NA"
    return {
        "stock_code": cell.get("stock_code"),
        "strike_price": parse_strike(cell.get("strike_price")) or 0.0,
        "right": cell.get("right"),
        "expiry_date": cell.get("expiry_date"),
        "ltp": cell.get("ltp"),
        "open_interest": int(cell.get("open_interest") or 0),
        "total_buy_qty": total_buy,
        "total_sell_qty": total_sell,
        "buy_sell_ratio": ratio,
        "best_bid_price": cell.get("best_bid_price"),
        "best_offer_price": cell.get("best_offer_price"),
        "spot_price": cell.get("spot_price"),
        "lot_size": cell.get("lot_size"),
    }


def _apply_buy_sell_ratio(row: dict[str, Any]) -> None:
    try:
        if int(row["total_sell_qty"]) > 0:
            row["buy_sell_ratio"] = int(row["total_buy_qty"]) / int(row["total_sell_qty"])
        else:
            row["buy_sell_ratio"] = 0
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass


def _chain_side_key(right: str) -> str:
    return "call" if _normalize_right_key(right) == "call" else "put"


def _flatten_chain_side_rows(payload: dict[str, Any], right: str) -> list[dict[str, Any]]:
    side = _chain_side_key(right)
    rows: list[dict[str, Any]] = []
    for chain_row in payload.get("chain_rows") or []:
        cell = chain_row.get(side)
        if not cell:
            continue
        rows.append(_cell_to_icici_row(cell))
    return rows


def _fetch_cell_from_cache(
    proc: "Processor",
    user_id: str,
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    strike: Strike,
    right: str,
    *,
    lot_size: int | None = None,
    holder_id: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return (cell, quote_source) or (None, None) on miss."""
    source = resolve_quote_source(exchange_code)
    right_key = _normalize_right_key(right)
    lot_val = int(lot_size or 0)

    if source == "websocket":
        from icici_breeze_backend.app.services.breeze_websocket_manager import subscribe_option

        subscribe_option(proc, user_id, exchange_code, stock_code, expiry_display, strike, right_key, holder_id=holder_id)
        key = ws_quote_key(exchange_code, stock_code, expiry_display, strike, right_key)
        cell = cache_get_json(key)
        if cell:
            if lot_val:
                cell["lot_size"] = lot_val
            return cell, "websocket"
        if bhavcopy_is_fresh(exchange_code):
            source = "bhavcopy"
        else:
            return None, None

    if source == "bhavcopy":
        right_label = cfg.CALL if right_key == "call" else cfg.PUT
        bhav_row = _lookup_bhav_row(stock_code, expiry_display, right_label, strike, exchange_code)
        if bhav_row:
            cell = _row_to_chain_cell(
                bhav_row, stock_code, expiry_display, exchange_code, right_label, lot_val
            )
            return cell, "bhavcopy"

    if source == "icici_api":
        chain_strikes = list_tradeable_strikes(stock_code, expiry_display, exchange_code=exchange_code)
        rest_payload = _get_or_build_icici_rest_chain(
            proc,
            user_id,
            stock_code,
            exchange_code,
            expiry_display,
            chain_strikes,
            lot_size=lot_val or None,
            freeze_quantity=None,
        )
        if rest_payload:
            side = "call" if right_key == "call" else "put"
            for row in rest_payload.get("chain_rows") or []:
                if isinstance(row, dict) and parse_strike(row.get("strike_price")) == strike:
                    cell = row.get(side)
                    if cell:
                        if lot_val:
                            cell = dict(cell)
                            cell["lot_size"] = lot_val
                        return cell, "icici_api"
                    break

    return None, None


def _resolve_chain_metadata(
    proc: "Processor",
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
) -> tuple[int | None, int | None, list[Strike]]:
    ensure_scrip_memory_ready()
    lot_size = proc.fetch_lot_size(stock_code, expiry_display, exchange_code=exchange_code)
    freeze_quantity = None
    try:
        if lot_size is not None:
            ls = int(lot_size)
            if ls > 0:
                qty_limits = proc.fetch_qty_limits(stock_code, exchange_code=exchange_code)
                if qty_limits is not None:
                    freeze_quantity = (max(1, int(qty_limits)) // ls) * ls
    except (TypeError, ValueError):
        freeze_quantity = None

    strikes = list_tradeable_strikes(stock_code, expiry_display, exchange_code=exchange_code)
    return lot_size, freeze_quantity, strikes or []


_REST_CHAIN_BUILD_LOCKS: dict[str, threading.Lock] = {}
_REST_CHAIN_BUILD_LOCKS_GUARD = threading.Lock()


def _rest_chain_build_lock(cache_key: str) -> threading.Lock:
    with _REST_CHAIN_BUILD_LOCKS_GUARD:
        lock = _REST_CHAIN_BUILD_LOCKS.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            _REST_CHAIN_BUILD_LOCKS[cache_key] = lock
        return lock


def _icici_rest_chain_cache_ttl_seconds() -> int:
    try:
        return max(60, int(getattr(cfg, "ICICI_REST_CHAIN_CACHE_TTL_SECONDS", 1200) or 1200))
    except (TypeError, ValueError):
        return 1200


def _get_or_build_icici_rest_chain(
    proc: "Processor",
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    strikes: list[Strike],
    *,
    lot_size: int | None,
    freeze_quantity: int | None,
) -> dict[str, Any] | None:
    """Shared, TTL'd REST-sourced chain: one fetch per (exchange, stock, expiry)
    serves every user for the rest of the post-close/pre-bhavcopy gap, since the
    market is closed for the whole window this is read in and the data is static.
    `resolve_quote_source` stops reading this the instant bhavcopy/websocket
    becomes available again -- the TTL here is only a safety net against a stuck
    reference-data pipeline pinning the app to one stale snapshot indefinitely.
    The in-process lock (this app runs exactly one API process per deployment,
    see CLAUDE.md) prevents concurrent cache-miss requests for the same chain
    from firing duplicate REST call pairs."""
    cache_key = icici_rest_chain_key(exchange_code, stock_code, expiry_display)
    cached = cache_get_json(cache_key)
    if isinstance(cached, dict):
        return cached

    with _rest_chain_build_lock(cache_key):
        cached = cache_get_json(cache_key)
        if isinstance(cached, dict):
            return cached

        result = proc._get_full_option_chain_icici_rest(
            user_id,
            stock_code,
            exchange_code,
            expiry_display,
            strikes=strikes,
            lot_size=lot_size,
            freeze_quantity=freeze_quantity,
        )
        if not isinstance(result, dict) or result.get("Status") != 200:
            return None
        payload = result.get("Success")
        if not isinstance(payload, dict):
            return None
        payload = dict(payload)
        payload.setdefault("quote_source", "icici_api")
        payload.setdefault("bhavcopy_date", None)
        cache_set_json(cache_key, payload, ex=_icici_rest_chain_cache_ttl_seconds())
        return payload


def fetch_chain_payload_routed(
    proc: "Processor",
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    *,
    holder_id: str | None = None,
) -> dict[str, Any] | None:
    """Build inner Success payload using websocket, bhavcopy, or (when neither has
    fresh data -- the post-close, pre-bhavcopy gap) the ICICI REST fallback."""
    from icici_breeze_backend.app.services.chain_readiness import is_chain_complete

    expiry_display = _normalize_expiry_display(expiry_display)
    source = resolve_quote_source(exchange_code)
    lot_size, freeze_quantity, strikes = _resolve_chain_metadata(
        proc, stock_code, exchange_code, expiry_display
    )
    if not strikes:
        return None

    # Resolved once up front (cache/bhavcopy-sourced, cheap after the first call) so the
    # completeness gate below can require live quotes only near the ATM strike instead of
    # every tradeable strike -- see `chain_readiness.is_chain_complete`.
    spot = _resolve_chain_spot(proc, user_id, stock_code, exchange_code, expiry_display, strikes)

    if source == "websocket":
        from icici_breeze_backend.app.services.breeze_websocket_manager import ensure_chain_subscriptions

        ws_payload = ensure_chain_subscriptions(
            proc,
            user_id,
            stock_code,
            exchange_code,
            expiry_display,
            strikes,
            lot_size=int(lot_size) if lot_size else 0,
            freeze_quantity=freeze_quantity,
            holder_id=holder_id,
            spot=spot,
        )
        if ws_payload is not None:
            ws_payload = _apply_chain_spot(
                ws_payload,
                proc=proc,
                user_id=user_id,
                stock_code=stock_code,
                exchange_code=exchange_code,
                expiry_display=expiry_display,
                strikes=strikes,
                spot=spot,
            )
        if ws_payload is not None and is_chain_complete(
            ws_payload,
            stock_code=stock_code,
            exchange_code=exchange_code,
            expiry_display=expiry_display,
            spot=spot,
        ):
            return _enrich_quote_metadata(ws_payload)

        _logger.warning("WebSocket chain incomplete for %s %s; trying bhavcopy", stock_code, expiry_display)
        if bhavcopy_is_fresh(exchange_code):
            source = "bhavcopy"
        else:
            return None

    if source == "bhavcopy":
        payload = build_chain_from_bhavcopy(
            stock_code,
            expiry_display,
            exchange_code,
            lot_size=int(lot_size) if lot_size else None,
            freeze_quantity=freeze_quantity,
            strikes=strikes,
        )
        if payload and is_chain_complete(
            payload,
            stock_code=stock_code,
            exchange_code=exchange_code,
            expiry_display=expiry_display,
            spot=spot,
        ):
            return _enrich_quote_metadata(
                _apply_chain_spot(
                    payload,
                    proc=proc,
                    user_id=user_id,
                    stock_code=stock_code,
                    exchange_code=exchange_code,
                    expiry_display=expiry_display,
                    strikes=strikes,
                    spot=spot,
                )
            )
        _logger.warning("Bhavcopy chain incomplete for %s %s", stock_code, expiry_display)
        return None

    if source == "icici_api":
        payload = _get_or_build_icici_rest_chain(
            proc,
            user_id,
            stock_code,
            exchange_code,
            expiry_display,
            strikes,
            lot_size=int(lot_size) if lot_size else None,
            freeze_quantity=freeze_quantity,
        )
        if payload and is_chain_complete(
            payload,
            stock_code=stock_code,
            exchange_code=exchange_code,
            expiry_display=expiry_display,
            spot=spot,
        ):
            return _enrich_quote_metadata(
                _apply_chain_spot(
                    payload,
                    proc=proc,
                    user_id=user_id,
                    stock_code=stock_code,
                    exchange_code=exchange_code,
                    expiry_display=expiry_display,
                    strikes=strikes,
                    spot=spot,
                )
            )
        _logger.warning("ICICI REST chain incomplete for %s %s", stock_code, expiry_display)
    return None


def chain_fetch_error_response(
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
) -> dict[str, Any]:
    """Map chain build failure to HTTP-shaped API response."""
    expiry_display = _normalize_expiry_display(expiry_display)
    if not list_tradeable_strikes_memory(stock_code, expiry_display, exchange_code=exchange_code):
        return {
            "Status": 400,
            "Error": "No tradeable contracts for this expiry.",
            "Success": None,
        }
    if is_india_market_open():
        return {
            "Status": 400,
            "Error": "Live option chain not ready; try again in a moment.",
            "Success": None,
        }
    if not bhavcopy_is_fresh(exchange_code):
        return {
            "Status": 503,
            "Error": (
                "ICICI REST fallback unavailable and Bhavcopy not loaded yet — "
                "try again shortly, or refresh reference data in Settings."
            ),
            "Success": None,
        }
    return {
        "Status": 400,
        "Error": "Unable to build option chain from Bhavcopy.",
        "Success": None,
    }


def assemble_chain_with_router(
    proc: "Processor",
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    *,
    holder_id: str | None = None,
) -> dict[str, Any]:
    """Build option chain using websocket, bhavcopy, or the ICICI REST fallback."""
    expiry_display = _normalize_expiry_display(expiry_display)
    payload = fetch_chain_payload_routed(
        proc, user_id, stock_code, exchange_code, expiry_display, holder_id=holder_id
    )
    if payload:
        return {"Status": 200, "Error": None, "Success": payload}
    return chain_fetch_error_response(exchange_code, stock_code, expiry_display)


def _find_chain_row(payload: dict[str, Any] | None, strike: Strike) -> dict[str, Any] | None:
    if not payload:
        return None
    for row in payload.get("chain_rows") or []:
        if isinstance(row, dict) and parse_strike(row.get("strike_price")) == strike:
            return row
    return None


def fetch_payoff_quote_routed(
    proc: "Processor",
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    *,
    holder_id: str | None = None,
) -> dict[str, Any] | None:
    """Minimal chain fetch for the portfolio payoff panel: spot + the ATM
    strike's own quote + lot size. Unlike `fetch_chain_payload_routed`, this
    never waits on strikes other than ATM — the payoff panel only ever reads
    chain_rows[0] (for lot size) and the ATM row (for a flat IV estimate); each
    leg is priced from the position's own average price/LTP, not a chain quote
    at that strike, so the rest of the chain is never actually consulted."""
    expiry_display = _normalize_expiry_display(expiry_display)
    source = resolve_quote_source(exchange_code)
    lot_size, freeze_quantity, strikes = _resolve_chain_metadata(
        proc, stock_code, exchange_code, expiry_display
    )
    if not strikes:
        return None

    spot = _resolve_chain_spot(proc, user_id, stock_code, exchange_code, expiry_display, strikes)
    if spot is None:
        return None
    atm_strike = min(strikes, key=lambda s: abs(s - spot))

    if source == "websocket":
        from icici_breeze_backend.app.services.breeze_websocket_manager import ensure_atm_quote_subscription

        ws_payload = ensure_atm_quote_subscription(
            proc,
            user_id,
            stock_code,
            exchange_code,
            expiry_display,
            strikes,
            atm_strike,
            lot_size=int(lot_size) if lot_size else 0,
            freeze_quantity=freeze_quantity,
            holder_id=holder_id,
        )
        if ws_payload is not None and _find_chain_row(ws_payload, atm_strike) is not None:
            ws_payload["spot_price"] = spot
            ws_payload["atm_strike"] = atm_strike
            return _enrich_quote_metadata(ws_payload)

        _logger.warning(
            "WebSocket ATM quote incomplete for %s %s; trying bhavcopy", stock_code, expiry_display
        )
        if bhavcopy_is_fresh(exchange_code):
            source = "bhavcopy"
        else:
            return None

    if source == "bhavcopy":
        payload = build_chain_from_bhavcopy(
            stock_code,
            expiry_display,
            exchange_code,
            lot_size=int(lot_size) if lot_size else None,
            freeze_quantity=freeze_quantity,
            strikes=strikes,
        )
        if payload is not None and _find_chain_row(payload, atm_strike) is not None:
            payload["spot_price"] = spot
            payload["atm_strike"] = atm_strike
            return _enrich_quote_metadata(payload)
        _logger.warning("Bhavcopy ATM quote unavailable for %s %s", stock_code, expiry_display)
        return None

    if source == "icici_api":
        payload = _get_or_build_icici_rest_chain(
            proc,
            user_id,
            stock_code,
            exchange_code,
            expiry_display,
            strikes,
            lot_size=int(lot_size) if lot_size else None,
            freeze_quantity=freeze_quantity,
        )
        if payload is not None and _find_chain_row(payload, atm_strike) is not None:
            payload = dict(payload)
            payload["spot_price"] = spot
            payload["atm_strike"] = atm_strike
            return _enrich_quote_metadata(payload)
        _logger.warning("ICICI REST ATM quote unavailable for %s %s", stock_code, expiry_display)
    return None


def assemble_payoff_quote_with_router(
    proc: "Processor",
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    *,
    holder_id: str | None = None,
) -> dict[str, Any]:
    """Build the minimal payoff-panel payload — see `fetch_payoff_quote_routed`."""
    expiry_display = _normalize_expiry_display(expiry_display)
    payload = fetch_payoff_quote_routed(
        proc, user_id, stock_code, exchange_code, expiry_display, holder_id=holder_id
    )
    if payload:
        return {"Status": 200, "Error": None, "Success": payload}
    return chain_fetch_error_response(exchange_code, stock_code, expiry_display)


def fetch_chain_side_icici_response(
    proc: "Processor",
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_raw: str,
    right: str,
) -> dict[str, Any]:
    """ICICI-shaped response for one chain side (CE or PE), cache-first --
    `fetch_chain_payload_routed` transparently tries websocket, then bhavcopy,
    then the ICICI REST fallback on its own."""
    expiry_display = _normalize_expiry_display(expiry_raw)
    payload = fetch_chain_payload_routed(proc, user_id, stock_code, exchange_code, expiry_display)
    if payload:
        rows = _flatten_chain_side_rows(payload, right)
        if rows:
            return {
                "Status": 200,
                "Error": None,
                "Success": rows,
                "quote_source": payload.get("quote_source"),
            }

    return _quote_miss_response(exchange_code)


def fetch_quote_icici_response(
    proc: "Processor",
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_raw: str,
    right: str,
    strike_price: str | int | None,
    *,
    product_type: str = cfg.OPTIONS,
    audit: "StrategyBuilderAuditSession | None" = None,
    audit_rationale: str | None = None,
    audit_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """ICICI-shaped single-quote (or spot) response, cache-first."""
    del product_type, audit_request  # kept for processor.get_quote signature compat
    expiry_display = _normalize_expiry_display(expiry_raw)

    if _is_spot_strike(strike_price):
        payload = fetch_chain_payload_routed(proc, user_id, stock_code, exchange_code, expiry_display)
        if payload and payload.get("spot_price") is not None:
            try:
                spot = float(payload["spot_price"])
            except (TypeError, ValueError):
                spot = None
            if spot is not None and spot > 0:
                return {
                    "Status": 200,
                    "Error": None,
                    "Success": [{"spot_price": spot, "strike_price": 0}],
                    "quote_source": payload.get("quote_source"),
                }
        return _quote_miss_response(exchange_code)

    strike = parse_strike(strike_price)
    if strike is None:
        return {"Status": 400, "Error": f"Invalid strike_price: {strike_price!r}"}

    lot_size = proc.fetch_lot_size(stock_code, expiry_display, exchange_code=exchange_code)
    cell, quote_source = _fetch_cell_from_cache(
        proc,
        user_id,
        exchange_code,
        stock_code,
        expiry_display,
        strike,
        right,
        lot_size=int(lot_size) if lot_size else None,
    )
    if cell:
        row = _cell_to_icici_row(cell)
        _apply_buy_sell_ratio(row)
        return {
            "Status": 200,
            "Error": None,
            "Success": [row],
            "quote_source": quote_source,
        }

    return _quote_miss_response(exchange_code)
