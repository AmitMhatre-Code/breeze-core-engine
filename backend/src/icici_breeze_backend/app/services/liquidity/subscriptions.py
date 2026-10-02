"""Which depth rooms to subscribe (decision 1 of docs/liquidity-checks-plan.md).

Every strike of every streamed chain gets its depth room beside its quote room, under the same
holder, so the two come and go together. Whether ICICI caps subscriptions per session is
unobserved (B-41). If it refuses a depth batch while accepting the chain's quotes, depth is
**capped** for the rest of the IST day: chains then keep depth only for the
`NEAR_MONEY_STRIKES` strikes either side of the money, and a ticket's own legs are pinned as
lookups. Everything else falls back to the top of book, which the check labels.

A refusal that ICICI reports as success, with no depth ever arriving, is not detected here; those
contracts simply have no depth and fall back the same way.
"""
from __future__ import annotations

import datetime
import logging
import threading
from typing import Any, Optional

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.liquidity.book import depth_symbol

_logger = logging.getLogger(__name__)

NEAR_MONEY_STRIKES = 10

_lock = threading.Lock()
_capped_on: Optional[datetime.date] = None
_cap_error: Optional[str] = None


def _today() -> datetime.date:
    return datetime.datetime.now(IST).date()


def is_capped() -> bool:
    with _lock:
        return _capped_on == _today()


def mark_capped(error: str) -> None:
    global _capped_on, _cap_error
    with _lock:
        first = _capped_on != _today()
        _capped_on = _today()
        _cap_error = error
    if first:
        _logger.warning(
            "liquidity: ICICI refused depth subscriptions (%s); depth limited to %d strikes "
            "either side of the money for today, the rest checked from the top of book",
            error, NEAR_MONEY_STRIKES,
        )


def status() -> dict[str, Any]:
    with _lock:
        return {"capped": _capped_on == _today(), "cap_error": _cap_error if _capped_on == _today() else None}


def _band(strikes_symbols: list[tuple[float, str]], spot: Optional[float]) -> list[tuple[float, str]]:
    strikes = sorted({s for s, _ in strikes_symbols})
    if not strikes:
        return []
    if spot is None or spot <= 0:
        centre = len(strikes) // 2
    else:
        centre = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
    keep = set(strikes[max(0, centre - NEAR_MONEY_STRIKES): centre + NEAR_MONEY_STRIKES + 1])
    return [(s, sym) for s, sym in strikes_symbols if s in keep]


def _spot(exchange: str, stock: str) -> Optional[float]:
    try:
        from icici_breeze_backend.app.services.quote_source_router import spot_reading_cached

        reading = spot_reading_cached(exchange, stock)
        return reading.spot if reading is not None else None
    except Exception:  # noqa: BLE001
        return None


def depth_tokens_for_chain(exchange: str, stock: str, expiry: str, quote_tokens: list[str]) -> list[str]:
    """The depth rooms a chain should hold now, given its quote rooms: one for each, or only
    the near-money band's once capped."""
    quotes = set(quote_tokens)
    if is_capped():
        from icici_breeze_backend.app.services.reference_data.ws_token_index import (
            list_ws_tokens_with_strikes,
        )

        pairs = [p for p in list_ws_tokens_with_strikes(exchange, stock, expiry) if p[1] in quotes]
        quotes = {sym for _strike, sym in _band(pairs, _spot(exchange, stock))}
    return sorted({d for d in (depth_symbol(q) for q in quotes) if d})


def subscribe_contract_depth(
    proc: Any, user_id: str, exchange: str, stock: str, expiry: str, strike: float, right: str
) -> bool:
    """Pin one contract's depth room as a lookup (released after 15 idle minutes)."""
    from icici_breeze_backend.app.services.breeze_websocket_manager import subscribe_lookup_symbol
    from icici_breeze_backend.app.services.liquidity.check import resolve_token
    from icici_breeze_backend.app.services.reference_data.ws_token_index import format_ws_stock_token

    token = resolve_token(exchange, stock, expiry, strike, right)
    if token is None:
        return False
    d = depth_symbol(format_ws_stock_token(exchange, token))
    if not d:
        return False
    return subscribe_lookup_symbol(proc, user_id, d)


def reset_state_for_tests() -> None:
    global _capped_on, _cap_error
    with _lock:
        _capped_on = None
        _cap_error = None
