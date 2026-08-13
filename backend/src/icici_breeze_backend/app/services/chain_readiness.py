"""Wait for complete canonical option chains before serving to clients."""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, parse_strike
from icici_breeze_backend.app.db.redis_client import cache_get_json
from icici_breeze_backend.app.services.reference_data.keys import canonical_chain_key
from icici_breeze_backend.app.services.reference_data.scrip_index import list_tradeable_strikes_memory
from icici_breeze_backend.app.services.reference_data.tradable_contracts import is_tradeable_contract

_logger = logging.getLogger(__name__)

_FRESHNESS_CACHE_SECONDS = 30.0
_freshness_cache: tuple[float, float] | None = None  # (value, monotonic_expiry)


def _wait_timeout_ms() -> int:
    try:
        return max(500, int(getattr(cfg, "CHAIN_WS_WAIT_TIMEOUT_MS", 8000) or 8000))
    except (TypeError, ValueError):
        return 8000


def _chain_wait_timeout_ms() -> int:
    """How long to wait for a *whole chain* to be quotable, as opposed to a single
    contract (`_wait_timeout_ms`).

    These were one setting, which meant the window sized for a thin chain's deep-OTM
    strikes -- which may simply never trade all session -- also governed the quote
    lookup done while pricing an order. Splitting them lets the chain wait be short
    (a screen showing slightly stale prices beats a screen that hangs) without
    shortening order pricing, which keeps the original, more patient window.
    """
    try:
        return max(250, int(getattr(cfg, "CHAIN_WS_CHAIN_WAIT_TIMEOUT_MS", 2000) or 2000))
    except (TypeError, ValueError):
        return 2000


def _wait_poll_ms() -> int:
    try:
        return max(25, int(getattr(cfg, "CHAIN_WS_WAIT_POLL_MS", 100) or 100))
    except (TypeError, ValueError):
        return 100


def _chain_freshness_window_seconds() -> float:
    """A published chain counts as fresh for one rebuild interval -- the same clock
    the chain_builder worker rebuilds on, so a request only rebuilds when the worker
    genuinely hasn't yet.

    Cached: the setting is a fresh SQLite read per call by design, and this runs on
    every poll iteration of every chain request.
    """
    global _freshness_cache
    now = time.monotonic()
    cached = _freshness_cache
    if cached is not None and now < cached[1]:
        return cached[0]
    value = 2.0
    try:
        from icici_breeze_backend.app.services.pnl_engine_settings import (
            load_pnl_engine_settings,
        )

        value = max(0.25, float(load_pnl_engine_settings()["pnl_recompute_interval_seconds"]))
    except Exception:
        _logger.debug("P&L recalc interval lookup failed; using %.1fs", value, exc_info=True)
    _freshness_cache = (value, now + _FRESHNESS_CACHE_SECONDS)
    return value


def _payload_is_fresh(raw: Any) -> bool:
    """True if the cached chain was built within the current rebuild interval.

    `built_at` is stamped by `chain_build_service` when it publishes. A payload
    without one predates this change (or came from a different builder path), so it
    is treated as stale -- never as fresh, which would risk serving an old chain
    indefinitely.
    """
    if not isinstance(raw, dict):
        return False
    try:
        built_at = float(raw.get("built_at"))
    except (TypeError, ValueError):
        return False
    if built_at <= 0:
        return False
    return (time.time() - built_at) < _chain_freshness_window_seconds()


def _cell_has_quote(cell: Any, *, exchange_code: str) -> bool:
    if not isinstance(cell, dict):
        return False
    ltp = cell.get("ltp")
    try:
        if ltp is not None and float(ltp) > 0:
            return True
    except (TypeError, ValueError):
        pass
    # Bid/ask counts as a quote on every exchange, not just BFO: with bhavcopy no
    # longer inventing depth, a contract can legitimately have a real quoted price
    # and unknown book size.
    for key in ("best_bid_price", "best_offer_price"):
        try:
            if cell.get(key) is not None and float(cell[key]) > 0:
                return True
        except (TypeError, ValueError):
            pass
    buy = cell.get("total_buy_qty")
    sell = cell.get("total_sell_qty")
    if buy is None and sell is None:
        # Unknown depth is not evidence of absence -- but with no ltp and no
        # bid/ask either (both checked above), there is nothing to show.
        return False
    try:
        return int(buy or 0) > 0 or int(sell or 0) > 0
    except (TypeError, ValueError):
        return False


def _atm_window_strikes(tradeable_strikes: list[Strike], spot: float) -> set[Strike]:
    ordered = sorted(tradeable_strikes)
    atm_index = min(range(len(ordered)), key=lambda i: abs(ordered[i] - spot))
    try:
        window = max(0, int(getattr(cfg, "CHAIN_READY_ATM_STRIKE_WINDOW", 5) or 5))
    except (TypeError, ValueError):
        window = 5
    lo = max(0, atm_index - window)
    hi = min(len(ordered), atm_index + window + 1)
    return set(ordered[lo:hi])


def _payload_spot(payload: dict[str, Any]) -> float | None:
    """Last-resort spot when the caller couldn't resolve one: bhavcopy/REST-sourced
    cells carry `spot_price` through to the payload, so an offline chain can still
    locate its own ATM rather than falling through to the all-strikes gate."""
    try:
        spot = float(payload.get("spot_price"))
    except (TypeError, ValueError):
        return None
    return spot if spot > 0 else None


def is_chain_complete(
    payload: dict[str, Any] | None,
    *,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    spot: float | None = None,
    detail: dict[str, Any] | None = None,
) -> bool:
    """Completeness is judged by per-contract quotes (ltp / bid-ask / buy-sell qty via
    `_cell_has_quote`), not `payload["spot_price"]`. Real WS option ticks never carry a
    `spot_price` field at all -- it's a passthrough that only bhavcopy-sourced cells
    populate -- so a payload built purely from live WS ticks could never satisfy a
    `payload["spot_price"] > 0` gate. `payload["spot_price"]` for the response is filled
    in separately by `quote_source_router._apply_chain_spot()`.

    Every tradeable strike must still be *present* as a row -- both chain builders emit
    chain_rows as a full skeleton (`chain_build_service.build_canonical_chain` for the
    websocket path, `quote_source_router._build_offline_chain` for the offline one), so
    this is a structural sanity check, not a liveness one. But only strikes within
    `CHAIN_READY_ATM_STRIKE_WINDOW` of the ATM strike must actually have a live quote.

    That window is deliberately tight. Deep OTM/ITM strikes don't merely take longer than
    `CHAIN_WS_WAIT_TIMEOUT_MS` to tick -- on a far-dated or thin chain (BSESEN monthlies,
    single-stock options) they may not trade or be quoted *at all* that session, so any
    window wide enough to include them makes the chain permanently un-ready rather than
    slow. Rows outside the window are still returned to the caller (possibly with null
    call/put cells) and fill in as later requests re-poll the same canonical chain.

    ATM is located from the externally supplied `spot` (e.g. cache/bhavcopy-sourced),
    falling back to `payload["spot_price"]`, and only if neither resolves does the gate
    widen to every tradeable strike -- a rule a thin chain can rarely satisfy, so
    `detail["gate"]` reports which one ran and callers log it on failure.
    """
    label = f"{exchange_code}/{stock_code} {expiry_display}"

    def _fail(reason: str, **fields: Any) -> bool:
        if detail is not None:
            detail.clear()
            detail["reason"] = reason
            detail.update(fields)
        _logger.debug("chain incomplete %s: %s %s", label, reason, fields or "")
        return False

    if not isinstance(payload, dict):
        return _fail("no payload")

    chain_rows = payload.get("chain_rows") or []
    if not chain_rows:
        return _fail("empty chain_rows")

    row_by_strike: dict[Strike, dict[str, Any]] = {}
    for row in chain_rows:
        if not isinstance(row, dict):
            continue
        strike = parse_strike(row.get("strike_price"))
        if strike is not None:
            row_by_strike[strike] = row

    tradeable_strikes = list_tradeable_strikes_memory(
        stock_code, expiry_display, exchange_code=exchange_code
    )
    if not tradeable_strikes:
        return _fail("no tradeable strikes")

    if set(row_by_strike.keys()) != set(tradeable_strikes):
        return _fail(
            "row/strike mismatch",
            rows=len(row_by_strike),
            tradeable_strikes=len(tradeable_strikes),
        )

    effective_spot = spot if (spot is not None and spot > 0) else _payload_spot(payload)
    gate = "atm_window"
    required_strikes = set(tradeable_strikes)
    if effective_spot is not None:
        required_strikes = _atm_window_strikes(tradeable_strikes, effective_spot)
    else:
        gate = "all_strikes_no_spot"

    missing: list[Strike] = []
    for strike in tradeable_strikes:
        if strike not in required_strikes:
            continue
        row = row_by_strike.get(strike) or {}
        for opt, side in ((cfg.CALL, "call"), (cfg.PUT, "put")):
            if not is_tradeable_contract(
                stock_code, expiry_display, strike, opt, exchange_code=exchange_code
            ):
                continue
            cell = row.get(side)
            if not _cell_has_quote(cell, exchange_code=exchange_code):
                missing.append(strike)

    if missing:
        return _fail(
            "unquoted contracts",
            gate=gate,
            spot=effective_spot,
            missing=len(missing),
            required_strikes=len(required_strikes),
            sample=sorted(set(missing))[:6],
        )
    if detail is not None:
        detail.clear()
    return True


def is_strike_quoted(
    payload: dict[str, Any] | None,
    *,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    strike: Strike,
) -> bool:
    """Same per-cell quote check as `is_chain_complete`, narrowed to one strike.

    Used by callers (the portfolio payoff panel) that only ever read the ATM row
    out of the whole chain and price every other leg from its own position data,
    not a live quote — so they shouldn't have to wait for every other strike to tick.
    """
    if not isinstance(payload, dict):
        return False
    row: dict[str, Any] | None = None
    for r in payload.get("chain_rows") or []:
        if isinstance(r, dict) and parse_strike(r.get("strike_price")) == strike:
            row = r
            break
    if row is None:
        return False
    for opt, side in ((cfg.CALL, "call"), (cfg.PUT, "put")):
        if not is_tradeable_contract(
            stock_code, expiry_display, strike, opt, exchange_code=exchange_code
        ):
            continue
        cell = row.get(side)
        if not _cell_has_quote(cell, exchange_code=exchange_code):
            return False
    return True


def _poll_canonical_chain(
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    *,
    lot_size: int,
    freeze_quantity: int | None,
    is_ready: Callable[[dict[str, Any]], bool],
    timeout_ms: int | None = None,
    reuse_fresh: bool = True,
) -> dict[str, Any] | None:
    """`reuse_fresh` skips this request's own rebuild when the chain_builder worker
    already published one within the rebuild interval -- without it, every request
    rebuilds the chain itself before reading it, which simply relocates the CPU the
    worker's cadence gate just saved. Callers that must have the freshest possible
    quote regardless of cost (order pricing) pass False."""
    from icici_breeze_backend.app.services.chain_build_service import refresh_active_chains
    from icici_breeze_backend.app.services.reference_data.active_chains import chain_registry_key

    chain_key = chain_registry_key(exchange_code, stock_code, expiry_display)
    deadline = time.monotonic() + (timeout_ms if timeout_ms is not None else _wait_timeout_ms()) / 1000.0
    poll_s = _wait_poll_ms() / 1000.0

    cache_key = canonical_chain_key(exchange_code, stock_code, expiry_display)
    while time.monotonic() < deadline:
        raw = cache_get_json(cache_key)
        if not (reuse_fresh and _payload_is_fresh(raw)):
            try:
                refresh_active_chains([chain_key])
            except Exception:
                pass
            raw = cache_get_json(cache_key)
        if not isinstance(raw, dict):
            time.sleep(poll_s)
            continue
        payload = dict(raw)
        if lot_size and not payload.get("lot_size"):
            payload["lot_size"] = lot_size
        if freeze_quantity is not None and payload.get("freeze_quantity") is None:
            payload["freeze_quantity"] = freeze_quantity
        if is_ready(payload):
            return payload
        time.sleep(poll_s)
    return None


def wait_for_canonical_chain(
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    *,
    lot_size: int = 0,
    freeze_quantity: int | None = None,
    spot: float | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """`detail`, if given, is left holding the *last* poll's incompleteness reason,
    so a caller that gives up after the wait window can log why rather than just
    that it happened.

    Uses the short chain-wait window and reuses a freshly published chain: this
    feeds screens, where showing slightly stale prices beats hanging on strikes
    that may never trade."""
    return _poll_canonical_chain(
        exchange_code,
        stock_code,
        expiry_display,
        lot_size=lot_size,
        freeze_quantity=freeze_quantity,
        timeout_ms=_chain_wait_timeout_ms(),
        is_ready=lambda payload: is_chain_complete(
            payload,
            stock_code=stock_code,
            exchange_code=exchange_code,
            expiry_display=expiry_display,
            spot=spot,
            detail=detail,
        ),
    )


def wait_for_strike_quote(
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    strike: Strike,
    *,
    lot_size: int = 0,
    freeze_quantity: int | None = None,
) -> dict[str, Any] | None:
    """Same wait/poll shape as `wait_for_canonical_chain`, gated on a single
    strike's quote — the ATM strike ticks far more reliably than deep OTM
    strikes, so this typically resolves well inside the worst-case timeout.

    This is the order-pricing path, so it deliberately keeps the longer window and
    rebuilds on every poll rather than reusing the worker's last publish: an
    aggressive limit order priced off a two-second-old book can miss its fill, and
    the cost is paid only when an order is actually placed."""
    return _poll_canonical_chain(
        exchange_code,
        stock_code,
        expiry_display,
        lot_size=lot_size,
        freeze_quantity=freeze_quantity,
        reuse_fresh=False,
        is_ready=lambda payload: is_strike_quoted(
            payload,
            stock_code=stock_code,
            exchange_code=exchange_code,
            expiry_display=expiry_display,
            strike=strike,
        ),
    )
