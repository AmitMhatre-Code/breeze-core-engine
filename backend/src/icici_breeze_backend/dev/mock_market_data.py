"""Shared random-walk tick math for dev/test mock Breeze data sources.

Used by both `MockBreezeSdk` (`dev.mock_broker`, the live process behind
`ICICI_BROKER_MODE=mock`) and `backend/tests/mock_breeze_server.py` (the
pytest-only protocol-level Socket.IO mock), so "what does a realistic
random-walk option tick look like" is defined exactly once.
"""
from __future__ import annotations

import random
import sqlite3
import time
from functools import lru_cache


def _format_strike(strike: float) -> str:
    return str(int(strike)) if float(strike).is_integer() else str(strike)


def resolve_underlying_spot(stock_code: str) -> float | None:
    """Real spot price for `stock_code`, read from the local bhavcopy store.

    Any F&O bhavcopy row for an underlying carries that day's real spot price
    (ICICI's own "UndrlygPric" column, already normalized into `spot_price` by
    `bhavcopy_common.py`), regardless of which strike/expiry/right the row is
    for -- so a single indexed lookup by `stock_code` is enough. Returns None
    for symbols with no F&O presence at all (e.g. INDVIX isn't itself
    F&O-traded), which callers should handle with their own fallback.

    Deliberately uncached (unlike `enrich_contract_fields`): this is called at
    most once per REST quote request, not per-tick, so a fresh lookup is cheap
    and avoids serving stale spot data across a long-running dev session after
    reference data refreshes.
    """
    try:
        import icici_breeze_backend.app.core.config as cfg

        with sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB) as conn:
            row = conn.execute(
                "SELECT spot_price FROM fo_bhavcopy WHERE UPPER(TRIM(stock_code)) = ? "
                "AND spot_price IS NOT NULL AND spot_price != '' LIMIT 1",
                (stock_code.strip().upper(),),
            ).fetchone()
        if not row:
            return None
        return float(row[0])
    except Exception:
        return None


@lru_cache(maxsize=4096)
def enrich_contract_fields(symbol: str) -> dict:
    """Best-effort contract-identity enrichment for a WS symbol.

    Mirrors what the real SDK's `on_message()` merges in via
    `get_data_from_stock_token_value()` when a real SecurityMaster is loaded
    (`exchange`, `stock_name`, `product_type`, `expiry_date`, `strike_price`,
    `right`) -- sourced here from the real local scrip-master DB instead,
    since `MockBreezeSdk` never has a real ICICI session/SecurityMaster of
    its own. Cached per symbol: contract identity for a given token doesn't
    change over the life of a subscription.
    """
    try:
        import icici_breeze_backend.app.core.config as cfg
        from icici_breeze_backend.app.services.reference_data.ws_token_index import (
            lookup_contract_by_ws_symbol,
        )

        contract = lookup_contract_by_ws_symbol(symbol)
        if contract is None:
            return {}
        exchange_label = "NSE Futures & Options" if contract.exchange_code == "NFO" else "BSE Futures & Options"
        with sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB) as conn:
            row = conn.execute(
                "SELECT CompanyName FROM scrip_master WHERE ShortName = ? AND SegmentCode = ? LIMIT 1",
                (contract.stock_code, contract.exchange_code),
            ).fetchone()
        company_name = (row[0] if row else None) or contract.stock_code
        return {
            "exchange": exchange_label,
            "stock_name": company_name,
            "product_type": "Options",
            "expiry_date": contract.expiry_display,
            "strike_price": _format_strike(contract.strike_price),
            "right": "Call" if contract.option_type.upper().startswith("C") else "Put",
        }
    except Exception:
        return {}


# Symbol -> contract identity for synthetic FUTURES tokens, which by construction are
# absent from `ws_token_index` (it ingests `Series = "OPTION"` only). Populated by
# `MockBreezeSdk.get_stock_token_value` at resolution time, i.e. always before the first
# tick for that symbol.
_MOCK_FUTURES_CONTRACTS: dict[str, dict] = {}


def register_mock_futures_contract(symbol: str, stock_code: str, expiry_display: str) -> None:
    _MOCK_FUTURES_CONTRACTS[str(symbol)] = {
        "exchange": "NSE Futures & Options",
        "stock_name": stock_code,
        "product_type": "Futures",
        "expiry_date": expiry_display,
        "strike_price": "",
        "right": "",
    }


def futures_identity(symbol: str) -> dict:
    """Identity for a synthetic futures token, or {} if `symbol` is not one."""
    return dict(_MOCK_FUTURES_CONTRACTS.get(str(symbol), {}))


def is_mock_futures_symbol(symbol: str) -> bool:
    return str(symbol) in _MOCK_FUTURES_CONTRACTS


def seed_running_state(seed_key: str, *, base_price: float | None = None) -> dict:
    """Starting point for a token's random walk.

    `base_price=None` derives a plausible-looking premium from a hash of
    `seed_key` -- used when the caller doesn't know a real price (e.g. an
    arbitrary real scrip-master token subscribed to by the live app in mock
    mode, where identity/pricing isn't known to the mock, only the token
    string is).
    """
    if base_price is None:
        base_price = round(20 + (abs(hash(seed_key)) % 28000) / 100, 2)
    ttq = random.randint(500_000, 2_000_000)
    return {
        "last": base_price, "high": base_price, "low": base_price,
        "open": base_price, "prev_close": base_price,
        "ttq": ttq,
        # Turnover must be consistent with quantity or the VWAP cross-check in
        # `services/bots/scalping/candles.py` fires on every mock tick. Seeding it as
        # qty x price makes ttv/ttq land on the opening price, as it does on a real feed.
        "ttv": float(ttq) * base_price,
    }


# Per-tick volatility as a fraction of price. The default suits an option premium, where a
# 0.25% step on Rs 100 is a few paise. It is badly wrong for an index level: 0.25% of 24,000
# is 60 points per tick, which would make any EMA/VWAP signal fire on pure noise. Callers
# pricing an underlying pass `sigma_pct` explicitly.
OPTION_SIGMA_PCT = 0.0025
INDEX_SIGMA_PCT = 0.0002


def step_live_tick_fields(
    symbol: str,
    state: dict,
    *,
    lot_size: int = 25,
    sigma_pct: float = OPTION_SIGMA_PCT,
) -> dict:
    """Advance `state` one random-walk step in place; return a real-SDK-shaped tick dict.

    Field names match what `breeze_connect`'s own `parse_data()` produces for
    a quotes-type tick (see `backend/tests/mock_breeze_server.py` and
    `app/services/ws_tick_normalize.py`), so callers of `on_ticks` can't tell
    this apart from a real tick's field names.
    """
    sigma = max(0.05, abs(state["last"]) * sigma_pct)
    delta = random.gauss(0, sigma)
    floor, ceiling = state["prev_close"] * 0.7, state["prev_close"] * 1.3
    state["last"] = round(min(max(state["last"] + delta, floor), ceiling), 2)
    state["high"] = round(max(state["high"], state["last"]), 2)
    state["low"] = round(min(state["low"], state["last"]), 2)
    traded_qty = random.randint(1, 50) * lot_size
    state["ttq"] += traded_qty
    # Accumulate turnover from the same quantity at the tick's own price, so that
    # `ttv / ttq` remains a genuine volume-weighted average rather than a free variable.
    state["ttv"] = state.get("ttv", 0.0) + traded_qty * state["last"]
    spread = max(0.05, state["last"] * 0.001)
    return {
        "symbol": symbol, "open": state["open"], "last": state["last"],
        "high": state["high"], "low": state["low"],
        "change": round(state["last"] - state["prev_close"], 2),
        "bPrice": round(state["last"] - spread, 2), "bQty": random.randint(50, 800),
        "sPrice": round(state["last"] + spread, 2), "sQty": random.randint(50, 800),
        "ltq": random.randint(1, 100) * lot_size,
        "avgPrice": round(state["ttv"] / state["ttq"], 2) if state["ttq"] else state["last"],
        "ttq": state["ttq"], "totalBuyQt": int(state["ttq"] * 0.55), "totalSellQ": int(state["ttq"] * 0.45),
        # Crore-suffixed string, matching the real captures rather than a bare number.
        "ttv": f"{state['ttv'] / 1e7:.2f}C", "trend": "+" if delta >= 0 else "-",
        "lowerCktLm": round(state["prev_close"] * 0.9, 2), "upperCktLm": round(state["prev_close"] * 1.1, 2),
        "ltt": int(time.time()), "close": state["prev_close"],
        "OI": random.randint(1_000_000, 20_000_000), "CHNGOI": random.randint(-50_000, 50_000),
    }
