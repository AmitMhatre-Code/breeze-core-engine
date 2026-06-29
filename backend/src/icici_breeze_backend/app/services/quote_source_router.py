"""Route option chain quotes to websocket, bhavcopy, or ICICI REST."""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any, TYPE_CHECKING

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, parse_strike, strike_for_broker, strike_key
from icici_breeze_backend.app.core.market_hours import is_india_market_open
from icici_breeze_backend.app.core.timezone import IST, now_ist
from icici_breeze_backend.app.db.redis_client import cache_get_json
from icici_breeze_backend.app.services.reference_data.bhavcopy_store import (
    _lookup_bhav_row,
    _row_to_chain_cell,
    build_chain_from_bhavcopy,
    get_bhavcopy_source_date,
)
from icici_breeze_backend.app.services.reference_data.keys import ws_quote_key
from icici_breeze_backend.app.services.reference_data.scrip_index import get_strikes

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
    elif source == "icici_api":
        payload["quote_as_of"] = now_ist().isoformat()
    return payload


def _rest_fallback_allowed(exchange_code: str) -> bool:
    return resolve_quote_source(exchange_code) != "bhavcopy"


def _bhavcopy_miss_response() -> dict[str, Any]:
    return {
        "Status": 404,
        "Error": "Quote not available in bhavcopy",
        "Success": None,
        "quote_source": "bhavcopy",
    }


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

    return None, None


def _resolve_chain_metadata(
    proc: "Processor",
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
) -> tuple[int | None, int | None, list[int]]:
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

    strikes = get_strikes(stock_code, expiry_display, exchange_code=exchange_code)
    if not strikes:
        strikes = proc.list_option_strikes(stock_code, expiry_display, exchange_code=exchange_code)
    return lot_size, freeze_quantity, strikes or []


def fetch_chain_payload_routed(
    proc: "Processor",
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    *,
    holder_id: str | None = None,
) -> dict[str, Any] | None:
    """Build inner Success payload using websocket / bhavcopy / REST per routing rules."""
    expiry_display = _normalize_expiry_display(expiry_display)
    source = resolve_quote_source(exchange_code)
    lot_size, freeze_quantity, strikes = _resolve_chain_metadata(
        proc, stock_code, exchange_code, expiry_display
    )

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
        )
        if ws_payload is not None:
            return _enrich_quote_metadata(ws_payload)

        _logger.warning("WebSocket chain empty for %s %s; falling back", stock_code, expiry_display)
        if bhavcopy_is_fresh(exchange_code):
            source = "bhavcopy"
        else:
            source = "icici_api"

    if source == "bhavcopy":
        payload = build_chain_from_bhavcopy(
            stock_code,
            expiry_display,
            exchange_code,
            lot_size=int(lot_size) if lot_size else None,
            freeze_quantity=freeze_quantity,
            strikes=strikes,
        )
        if payload:
            if not payload.get("chain_rows") and strikes:
                payload["chain_rows"] = [
                    {"strike_price": s, "call": None, "put": None} for s in strikes
                ]
            return _enrich_quote_metadata(payload)
        _logger.warning("Bhavcopy chain missing for %s %s; falling back to REST", stock_code, expiry_display)
        source = "icici_api"

    if source == "icici_api":
        result = proc._get_full_option_chain_icici_rest(
            user_id, stock_code, exchange_code, expiry_display
        )
        if result.get("Status") == 200 and isinstance(result.get("Success"), dict):
            payload = dict(result["Success"])
            payload["quote_source"] = "icici_api"
            payload["bhavcopy_date"] = None
            return _enrich_quote_metadata(payload)
    return None


def assemble_chain_with_router(
    proc: "Processor",
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    *,
    holder_id: str | None = None,
) -> dict[str, Any]:
    """Build option chain using websocket / bhavcopy / REST per routing rules."""
    expiry_display = _normalize_expiry_display(expiry_display)
    payload = fetch_chain_payload_routed(
        proc, user_id, stock_code, exchange_code, expiry_display, holder_id=holder_id
    )
    if payload:
        return {"Status": 200, "Error": None, "Success": payload}
    return {
        "Status": 400,
        "Error": "Unable to fetch option chain from cache or REST",
        "Success": None,
    }


def _fetch_quote_icici_rest(
    proc: "Processor",
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_raw: str,
    right: str,
    strike_price: str | None,
    *,
    audit: "StrategyBuilderAuditSession | None" = None,
    audit_rationale: str | None = None,
) -> dict[str, Any]:
    from icici_breeze_backend.app.services.processor import _expiry_display_to_api, _icici_option_chain_enums

    breeze = proc.get_session_breeze(user_id)
    expiry_display = _normalize_expiry_display(expiry_raw)
    try:
        expiry_api = _expiry_display_to_api(expiry_display)
    except ValueError:
        expiry_api = expiry_raw

    icici_request: dict[str, Any] = {
        "stock_code": stock_code,
        "exchange_code": exchange_code,
        "expiry_date": expiry_api,
        "product_type": cfg.OPTIONS,
        "right": right,
    }
    if strike_price is not None and not _is_spot_strike(strike_price):
        icici_request["strike_price"] = strike_price

    if breeze is None:
        quote: dict[str, Any] = {
            "Status": 400,
            "Error": "Unable to connect to broker. Please check your credentials and re-login.",
        }
        if audit:
            audit.record_icici_api_call(
                "get_option_chain_quotes",
                icici_request,
                quote,
                rationale=audit_rationale,
                latency_ms=0.0,
            )
        return quote

    _pt, _rt = _icici_option_chain_enums(cfg.OPTIONS, right)
    _t0 = time.perf_counter()
    try:
        if strike_price is not None and not _is_spot_strike(strike_price):
            quote = breeze.get_option_chain_quotes(
                stock_code, exchange_code, expiry_api, _pt, _rt, strike_price
            )
        else:
            quote = breeze.get_option_chain_quotes(
                stock_code=stock_code,
                exchange_code=exchange_code,
                product_type=_pt,
                expiry_date=expiry_api,
                right=_rt,
            )
    except Exception as exc:
        quote = {
            "Status": 400,
            "Error": f"Error calling ICICI Breeze API get_option_chain_quotes: {exc}",
        }
    _latency_ms = (time.perf_counter() - _t0) * 1000
    if audit:
        audit.record_icici_api_call(
            "get_option_chain_quotes",
            icici_request,
            quote if isinstance(quote, dict) else None,
            rationale=audit_rationale,
            latency_ms=_latency_ms,
        )
    if not isinstance(quote, dict):
        return {"Status": 400, "Error": "Invalid response from get_option_chain_quotes"}
    if quote.get("Status") == 200:
        quote["quote_source"] = "icici_api"
        for row in quote.get("Success") or []:
            if isinstance(row, dict):
                _apply_buy_sell_ratio(row)
    return quote


def fetch_chain_side_icici_response(
    proc: "Processor",
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_raw: str,
    right: str,
) -> dict[str, Any]:
    """ICICI-shaped response for one chain side (CE or PE), cache-first."""
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

    if not _rest_fallback_allowed(exchange_code):
        return _bhavcopy_miss_response()

    from icici_breeze_backend.app.services.processor import _expiry_display_to_api

    try:
        expiry_api = _expiry_display_to_api(expiry_display)
    except ValueError:
        expiry_api = expiry_raw
    result = proc._fetch_icici_chain_side_raw(
        user_id, stock_code, exchange_code, expiry_api, right
    )
    if result.get("Status") == 200:
        result["quote_source"] = "icici_api"
    return result


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
        if not _rest_fallback_allowed(exchange_code):
            return _bhavcopy_miss_response()
        return _fetch_quote_icici_rest(
            proc,
            user_id,
            stock_code,
            exchange_code,
            expiry_raw,
            right,
            strike_price,
            audit=audit,
            audit_rationale=audit_rationale,
        )

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

    if not _rest_fallback_allowed(exchange_code):
        return _bhavcopy_miss_response()

    quote = _fetch_quote_icici_rest(
        proc,
        user_id,
        stock_code,
        exchange_code,
        expiry_raw,
        right,
        strike_for_broker(strike),
        audit=audit,
        audit_rationale=audit_rationale,
    )
    return quote
