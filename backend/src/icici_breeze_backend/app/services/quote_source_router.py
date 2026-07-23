"""Route option chain quotes to websocket, bhavcopy, or ICICI REST."""
from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import Any, TYPE_CHECKING

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, parse_strike, strike_key
from icici_breeze_backend.app.services.market_calendar import (
    bhavcopy_is_stale,
    get_calendar_config,
    is_market_open,
    is_trading_day,
)
from icici_breeze_backend.app.core.timezone import IST, now_ist
from icici_breeze_backend.app.db.redis_client import cache_get_json, cache_set_json
from icici_breeze_backend.core.icici_client import icici_client
from icici_breeze_backend.app.services.reference_data.bhavcopy_store import (
    _lookup_bhav_row,
    _row_to_chain_cell,
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
    is_tradeable_contract,
    list_tradeable_strikes,
)
from icici_breeze_backend.app.services.ws_quote_snapshot import (
    SNAPSHOT_SOURCE,
    build_chain_from_snapshot,
    cell_depth_as_of,
    contract_field,
    snapshot_cell,
    snapshot_is_fresh,
)

if TYPE_CHECKING:
    from icici_breeze_backend.audit.strategy_builder_audit import StrategyBuilderAuditSession
    from icici_breeze_backend.app.services.processor import processor as Processor

_logger = logging.getLogger(__name__)


def _previous_trading_day(d: dt.date) -> dt.date:
    prev = d - dt.timedelta(days=1)
    while not is_trading_day(dt.datetime(prev.year, prev.month, prev.day, 12, 0, tzinfo=IST)):
        prev -= dt.timedelta(days=1)
    return prev


def latest_concluded_trading_day(now: dt.datetime | None = None) -> dt.date:
    """Last trading day whose session has fully ended (for bhavcopy freshness)."""
    dt_ist = (now or now_ist()).astimezone(IST)
    close = get_calendar_config().close_time(dt_ist)
    if is_trading_day(dt_ist) and dt_ist >= close:
        return dt_ist.date()
    return _previous_trading_day(dt_ist.date())


def bhavcopy_is_fresh(exchange_code: str, now: dt.datetime | None = None) -> bool:
    src = get_bhavcopy_source_date(exchange_code)
    if src is None:
        return False
    return src >= latest_concluded_trading_day(now)


def resolve_quote_source(exchange_code: str, now: dt.datetime | None = None) -> str:
    """Chain-level *primary* source. Post-close chains are actually assembled
    per-cell by `_build_offline_chain`, which walks `offline_source_order` for
    every contract; this returns the tier that leads that walk."""
    if is_market_open(now):
        return "websocket"
    if snapshot_is_fresh(exchange_code, now):
        return SNAPSHOT_SOURCE
    if bhavcopy_is_fresh(exchange_code, now):
        return "bhavcopy"
    return "icici_api"


def offline_source_order(exchange_code: str, now: dt.datetime | None = None) -> list[str]:
    """Per-cell fallback order once the market is closed.

    The snapshot always leads: it is the only source carrying real market depth,
    since BSE wipes its book at close and bhavcopy has no depth columns at all.

    A *fresh* bhavcopy ends the walk -- REST is not consulted to patch individual
    holes in it. A missing bhavcopy row means the contract did not trade that
    session, and REST would neither know better (it covers only a limited strike
    band) nor be worth an ICICI call per untraded strike. REST exists here purely
    as the remedy for a *stale* bhavcopy, where it at least reflects the session
    that just concluded rather than a previous one.

    Derived from `resolve_quote_source` rather than calling `bhavcopy_is_fresh`
    directly, so the primary tier and the fallback walk can never disagree.
    """
    primary = resolve_quote_source(exchange_code, now)
    if primary == "bhavcopy":
        return [SNAPSHOT_SOURCE, "bhavcopy"]
    if primary == "icici_api":
        return [SNAPSHOT_SOURCE, "icici_api", "bhavcopy"]
    # Snapshot primary, or a websocket miss falling through mid-session.
    if bhavcopy_is_fresh(exchange_code, now):
        return [SNAPSHOT_SOURCE, "bhavcopy"]
    return [SNAPSHOT_SOURCE, "icici_api", "bhavcopy"]


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
    elif source == SNAPSHOT_SOURCE:
        payload["quote_as_of"] = payload.get("depth_as_of") or now_ist().isoformat()
    return payload


def _rest_fallback_allowed(exchange_code: str) -> bool:
    """Governs the *spot* lookup only. Deliberately still allows REST when the
    snapshot is the primary source: real WS option ticks carry no `spot_price`
    (see `chain_readiness.is_chain_complete`), so a snapshot can never answer a
    spot query and blocking REST here would leave the chain without an ATM."""
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
    # Preserve unknown depth as None rather than folding it into 0 -- see
    # `bhavcopy_store._row_to_chain_cell`.
    raw_buy = cell.get("total_buy_qty")
    raw_sell = cell.get("total_sell_qty")
    depth_known = raw_buy is not None or raw_sell is not None
    total_buy = int(raw_buy or 0) if depth_known else None
    total_sell = int(raw_sell or 0) if depth_known else None
    ratio = cell.get("buy_sell_ratio")
    if ratio is None and total_buy is not None and total_sell is not None:
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
        "depth_as_of": cell.get("depth_as_of"),
        "quote_source": cell.get("quote_source"),
        "buy_sell_ratio": ratio,
        "best_bid_price": cell.get("best_bid_price"),
        "best_offer_price": cell.get("best_offer_price"),
        "spot_price": cell.get("spot_price"),
        "lot_size": cell.get("lot_size"),
    }


def _apply_buy_sell_ratio(row: dict[str, Any]) -> None:
    if row.get("total_buy_qty") is None or row.get("total_sell_qty") is None:
        row["buy_sell_ratio"] = None
        return
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

    # Closed market (or a websocket miss): walk the same per-cell fallback order
    # the chain builder uses, so a single-contract lookup and a full chain build
    # can never disagree about which source owns a contract.
    for candidate in offline_source_order(exchange_code):
        cell = None
        if candidate == SNAPSHOT_SOURCE:
            cell = snapshot_cell(exchange_code, stock_code, expiry_display, strike, right_key)
        elif candidate == "bhavcopy":
            right_label = cfg.CALL if right_key == "call" else cfg.PUT
            bhav_row = _lookup_bhav_row(
                stock_code, expiry_display, right_label, strike, exchange_code
            )
            if bhav_row:
                cell = _row_to_chain_cell(
                    bhav_row, stock_code, expiry_display, exchange_code, right_label, lot_val
                )
        else:
            chain_strikes = list_tradeable_strikes(
                stock_code, expiry_display, exchange_code=exchange_code
            )
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
            cell = _index_rest_chain(rest_payload).get(contract_field(strike, right_key))
        if not cell:
            continue
        cell = dict(cell)
        if lot_val:
            cell["lot_size"] = lot_val
        if candidate == SNAPSHOT_SOURCE:
            as_of = cell_depth_as_of(cell)
            if as_of:
                cell["depth_as_of"] = as_of
        return cell, candidate

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


def _index_rest_chain(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """REST chain rows -> {contract_field: cell} for the per-cell merge."""
    if not isinstance(payload, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in payload.get("chain_rows") or []:
        if not isinstance(row, dict):
            continue
        strike = parse_strike(row.get("strike_price"))
        if strike is None:
            continue
        for right_key in ("call", "put"):
            cell = row.get(right_key)
            if isinstance(cell, dict):
                out[contract_field(strike, right_key)] = cell
    return out


def _build_offline_chain(
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
    """Assemble a closed-market chain per contract, so each strike independently
    takes the best source available to it.

    This matters because the sources have complementary coverage: the snapshot
    holds real depth but only for chains someone subscribed to during the
    session; bhavcopy covers every strike but carries no depth; REST covers only
    a limited strike band. Merging per cell means a subscribed chain keeps its
    real close-time book while unsubscribed strikes in the same chain still fill
    in, instead of one missing strike discarding the whole snapshot.
    """
    order = offline_source_order(exchange_code)
    lot_val = int(lot_size or 0)
    snapshot_cells = build_chain_from_snapshot(
        stock_code,
        expiry_display,
        exchange_code,
        lot_size=lot_val or None,
        strikes=strikes,
    )
    rest_index: dict[str, dict[str, Any]] | None = None

    def _rest_cells() -> dict[str, dict[str, Any]]:
        # Built at most once, and only if some contract actually falls through to
        # REST -- a fully snapshot/bhavcopy-covered chain never spends the call.
        nonlocal rest_index
        if rest_index is None:
            rest_index = _index_rest_chain(
                _get_or_build_icici_rest_chain(
                    proc,
                    user_id,
                    stock_code,
                    exchange_code,
                    expiry_display,
                    strikes,
                    lot_size=lot_val or None,
                    freeze_quantity=freeze_quantity,
                )
            )
        return rest_index

    calls: list[dict[str, Any]] = []
    puts: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    spot_price: float | None = None
    depth_as_of: str | None = None

    for strike in strikes:
        for right in (cfg.CALL, cfg.PUT):
            if not is_tradeable_contract(
                stock_code, expiry_display, strike, right, exchange_code=exchange_code
            ):
                continue
            right_key = "call" if right == cfg.CALL else "put"
            field = contract_field(strike, right_key)
            cell: dict[str, Any] | None = None
            resolved_source: str | None = None
            for candidate in order:
                if candidate == SNAPSHOT_SOURCE:
                    cell = snapshot_cells.get(field)
                elif candidate == "bhavcopy":
                    row = _lookup_bhav_row(
                        stock_code, expiry_display, right, strike, exchange_code
                    )
                    cell = (
                        _row_to_chain_cell(
                            row, stock_code, expiry_display, exchange_code, right, lot_val
                        )
                        if row
                        else None
                    )
                else:
                    cell = _rest_cells().get(field)
                if cell:
                    resolved_source = candidate
                    break
            if not cell or resolved_source is None:
                continue

            cell = dict(cell)
            cell["quote_source"] = resolved_source
            if lot_val:
                cell["lot_size"] = lot_val
            if resolved_source == SNAPSHOT_SOURCE:
                as_of = cell_depth_as_of(cell)
                if as_of:
                    cell["depth_as_of"] = as_of
                    if depth_as_of is None or as_of > depth_as_of:
                        depth_as_of = as_of
            source_counts[resolved_source] = source_counts.get(resolved_source, 0) + 1
            if spot_price is None:
                spot_price = _parse_positive_spot(cell.get("spot_price"))
            (calls if right == cfg.CALL else puts).append(cell)

    if not calls and not puts:
        return None

    call_by = {parse_strike(c.get("strike_price")): c for c in calls}
    put_by = {parse_strike(p.get("strike_price")): p for p in puts}
    chain_strikes = sorted(
        {k for k in (set(call_by) | set(put_by)) if k is not None}
    )
    chain_rows = [
        {"strike_price": k, "call": call_by.get(k), "put": put_by.get(k)}
        for k in chain_strikes
    ]
    atm_strike = None
    if spot_price is not None and chain_strikes:
        atm_strike = min(chain_strikes, key=lambda s: abs(s - spot_price))
    bhav_date = get_bhavcopy_source_date(exchange_code)
    dominant = max(source_counts, key=lambda s: source_counts[s]) if source_counts else "icici_api"
    return {
        "chain_rows": chain_rows,
        "max_call_oi": max((int(c.get("open_interest") or 0) for c in calls), default=0),
        "max_put_oi": max((int(p.get("open_interest") or 0) for p in puts), default=0),
        "expiry_display": expiry_display,
        "stock_code": stock_code,
        "exchange_code": exchange_code,
        "spot_price": spot_price,
        "atm_strike": atm_strike,
        "lot_size": lot_val or None,
        "freeze_quantity": freeze_quantity,
        "quote_source": dominant,
        "quote_source_counts": source_counts,
        "depth_as_of": depth_as_of,
        "bhavcopy_date": bhav_date.isoformat() if bhav_date else None,
    }


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
        from icici_breeze_backend.app.services.index_spot_feed import (
            ensure_underlying_spot_subscription,
        )

        # Keep this underlying's cash spot warm from live ticks so `spot`
        # (resolved above / on the next poll) reflects the real intraday spot
        # rather than a stale bhavcopy close -- demand-driven, deduped, and a
        # no-op for indices already covered by the session-start index feed.
        ensure_underlying_spot_subscription(proc, user_id, exchange_code, stock_code)

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

        _logger.warning(
            "WebSocket chain incomplete for %s %s; falling back to offline sources",
            stock_code,
            expiry_display,
        )

    payload = _build_offline_chain(
        proc,
        user_id,
        stock_code,
        exchange_code,
        expiry_display,
        strikes,
        lot_size=int(lot_size) if lot_size else None,
        freeze_quantity=freeze_quantity,
    )
    if not payload:
        _logger.warning("Offline chain unavailable for %s %s", stock_code, expiry_display)
        return None

    # The readiness gate exists to wait for live WS ticks still arriving. A chain
    # that drew on the snapshot is final -- nothing further will ever fill in --
    # so gating it would only burn CHAIN_WS_WAIT_TIMEOUT_MS before returning the
    # same rows. Pure bhavcopy/REST chains keep the original completeness check.
    used_snapshot = bool((payload.get("quote_source_counts") or {}).get(SNAPSHOT_SOURCE))
    if used_snapshot or is_chain_complete(
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
    _logger.warning("Offline chain incomplete for %s %s", stock_code, expiry_display)
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
    if is_market_open():
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


def _fetch_chain_payload_atm_gated(
    proc: "Processor",
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    *,
    holder_id: str | None = None,
) -> dict[str, Any] | None:
    """Full routed chain payload gated on the ATM strike's quote only, not every
    strike. Returns the complete canonical `chain_rows` (so callers can still read
    OI across every strike that has ticked), with `spot_price`/`atm_strike` filled
    in. The ATM strike ticks far more reliably than deep OTM/ITM strikes, so this
    resolves well inside the wait window even when `fetch_chain_payload_routed`'s
    fuller gate would still be waiting on the illiquid wings. Callers that only
    need the ATM row (payoff panel) and callers that sum OI across the chain
    (dashboard PCR) share this."""
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
        from icici_breeze_backend.app.services.index_spot_feed import (
            ensure_underlying_spot_subscription,
        )

        ensure_underlying_spot_subscription(proc, user_id, exchange_code, stock_code)

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
            "WebSocket ATM quote incomplete for %s %s; falling back to offline sources",
            stock_code,
            expiry_display,
        )

    # Offline (market closed, or a mid-session WS miss): assemble the chain per
    # cell across snapshot -> bhavcopy -> REST, exactly like the full-chain
    # builder, then gate on the ATM row only. Sharing `_build_offline_chain` is
    # what gives this path the WS snapshot tier -- the earlier per-source dispatch
    # here had no `snapshot` branch, so a fresh post-close snapshot resolved to a
    # source this function could not build and it returned nothing (surfacing the
    # "Bhavcopy not loaded yet" error even though captured depth was available).
    payload = _build_offline_chain(
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
    _logger.warning("Offline ATM quote unavailable for %s %s", stock_code, expiry_display)
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
    return _fetch_chain_payload_atm_gated(
        proc, user_id, stock_code, exchange_code, expiry_display, holder_id=holder_id
    )


def cached_chain_spot(exchange_code: str, stock_code: str) -> float | None:
    """Live spot from the WS index-tick-fed cache (seeded by `index_spot_feed`
    via `remember_chain_spot`), or None. No bhavcopy/REST fallback — callers that
    want those use `_resolve_chain_spot`. Lets the dashboard show NIFTY spot with
    no ICICI REST round-trip."""
    return _cached_chain_spot(exchange_code, stock_code)


def fetch_chain_sides_atm_gated(
    proc: "Processor",
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_raw: str,
    *,
    holder_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float | None]:
    """`(call_rows, put_rows, spot)` from ONE ATM-gated routed chain build, in the
    same ICICI row shape as `fetch_chain_side_icici_response`. For the dashboard
    ATM IV + PCR/OI tiles: IV needs only the ATM strike quoted, and PCR/OI sums
    whatever strikes have ticked — so this never waits on the illiquid wings the
    full-chain gate requires, and builds the chain once instead of once per side."""
    expiry_display = _normalize_expiry_display(expiry_raw)
    payload = _fetch_chain_payload_atm_gated(
        proc, user_id, stock_code, exchange_code, expiry_display, holder_id=holder_id
    )
    if not payload:
        return [], [], None
    spot = _parse_positive_spot(payload.get("spot_price"))
    calls = _flatten_chain_side_rows(payload, cfg.CALL)
    puts = _flatten_chain_side_rows(payload, cfg.PUT)
    return calls, puts, spot


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
