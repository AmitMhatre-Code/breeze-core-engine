"""Wait for complete canonical option chains before serving to clients."""
from __future__ import annotations

import time
from typing import Any, Callable

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, parse_strike
from icici_breeze_backend.app.db.redis_client import cache_get_json
from icici_breeze_backend.app.services.reference_data.keys import canonical_chain_key
from icici_breeze_backend.app.services.reference_data.scrip_index import list_tradeable_strikes_memory
from icici_breeze_backend.app.services.reference_data.tradable_contracts import is_tradeable_contract


def _wait_timeout_ms() -> int:
    try:
        return max(500, int(getattr(cfg, "CHAIN_WS_WAIT_TIMEOUT_MS", 8000) or 8000))
    except (TypeError, ValueError):
        return 8000


def _wait_poll_ms() -> int:
    try:
        return max(25, int(getattr(cfg, "CHAIN_WS_WAIT_POLL_MS", 100) or 100))
    except (TypeError, ValueError):
        return 100


def _cell_has_quote(cell: Any, *, exchange_code: str) -> bool:
    if not isinstance(cell, dict):
        return False
    ltp = cell.get("ltp")
    try:
        if ltp is not None and float(ltp) > 0:
            return True
    except (TypeError, ValueError):
        pass
    if exchange_code == cfg.BFO:
        for key in ("best_bid_price", "best_offer_price"):
            try:
                if cell.get(key) is not None and float(cell[key]) > 0:
                    return True
            except (TypeError, ValueError):
                pass
    buy = int(cell.get("total_buy_qty") or 0)
    sell = int(cell.get("total_sell_qty") or 0)
    return buy > 0 or sell > 0


def _atm_window_strikes(tradeable_strikes: list[Strike], spot: float) -> set[Strike]:
    ordered = sorted(tradeable_strikes)
    atm_index = min(range(len(ordered)), key=lambda i: abs(ordered[i] - spot))
    try:
        window = max(0, int(getattr(cfg, "CHAIN_READY_ATM_STRIKE_WINDOW", 20) or 20))
    except (TypeError, ValueError):
        window = 20
    lo = max(0, atm_index - window)
    hi = min(len(ordered), atm_index + window + 1)
    return set(ordered[lo:hi])


def is_chain_complete(
    payload: dict[str, Any] | None,
    *,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    spot: float | None = None,
) -> bool:
    """Completeness is judged by per-contract quotes (ltp / bid-ask / buy-sell qty via
    `_cell_has_quote`), not `payload["spot_price"]`. Real WS option ticks never carry a
    `spot_price` field at all -- it's a passthrough that only bhavcopy-sourced cells
    populate -- so a payload built purely from live WS ticks could never satisfy a
    `payload["spot_price"] > 0` gate. `payload["spot_price"]` for the response is filled
    in separately by `quote_source_router._apply_chain_spot()`.

    Every tradeable strike must still be *present* as a row (chain_rows is always built
    as a full skeleton -- see `chain_build_service.build_canonical_chain` -- so this is
    a structural sanity check, not a liveness one). But only strikes within
    `CHAIN_READY_ATM_STRIKE_WINDOW` of the ATM strike (resolved from the externally
    supplied `spot`, e.g. cache/bhavcopy-sourced -- not `payload["spot_price"]`) must
    actually have a live quote. Deep OTM/ITM strikes on a wide chain (NIFTY, BANKNIFTY)
    routinely take longer than `CHAIN_WS_WAIT_TIMEOUT_MS` to tick at all; requiring them
    too meant one illiquid strike could flip an otherwise-live chain to bhavcopy. Rows
    outside the window are still returned to the caller (possibly with null call/put
    cells) and fill in as later requests re-poll the same canonical chain. If `spot` is
    unavailable, falls back to requiring every tradeable strike (the old behavior).
    """
    if not isinstance(payload, dict):
        return False

    chain_rows = payload.get("chain_rows") or []
    if not chain_rows:
        return False

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
        return False

    if set(row_by_strike.keys()) != set(tradeable_strikes):
        return False

    required_strikes = set(tradeable_strikes)
    if spot is not None and spot > 0:
        required_strikes = _atm_window_strikes(tradeable_strikes, spot)

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
                return False
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
) -> dict[str, Any] | None:
    from icici_breeze_backend.app.services.chain_build_service import refresh_active_chains
    from icici_breeze_backend.app.services.reference_data.active_chains import chain_registry_key

    chain_key = chain_registry_key(exchange_code, stock_code, expiry_display)
    deadline = time.monotonic() + _wait_timeout_ms() / 1000.0
    poll_s = _wait_poll_ms() / 1000.0

    while time.monotonic() < deadline:
        try:
            refresh_active_chains([chain_key])
        except Exception:
            pass
        raw = cache_get_json(canonical_chain_key(exchange_code, stock_code, expiry_display))
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
) -> dict[str, Any] | None:
    return _poll_canonical_chain(
        exchange_code,
        stock_code,
        expiry_display,
        lot_size=lot_size,
        freeze_quantity=freeze_quantity,
        is_ready=lambda payload: is_chain_complete(
            payload,
            stock_code=stock_code,
            exchange_code=exchange_code,
            expiry_display=expiry_display,
            spot=spot,
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
    strikes, so this typically resolves well inside the worst-case timeout."""
    return _poll_canonical_chain(
        exchange_code,
        stock_code,
        expiry_display,
        lot_size=lot_size,
        freeze_quantity=freeze_quantity,
        is_ready=lambda payload: is_strike_quoted(
            payload,
            stock_code=stock_code,
            exchange_code=exchange_code,
            expiry_display=expiry_display,
            strike=strike,
        ),
    )
