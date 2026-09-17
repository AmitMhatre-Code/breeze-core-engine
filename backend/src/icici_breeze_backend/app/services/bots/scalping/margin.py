"""Margin for a mixed long/short structure (docs/bots-scalping-plan.md section 4.2).

Separate from `expiry_index_writer.margin_for_legs`, which hardcodes `action: SELL` on every
leg because Bot 2's shapes are short-only. An iron fly has two BUY legs, and those wings
exist precisely to *reduce* margin -- sending them as sells would price the structure as a
short strangle plus two more short legs and return a number several times too high, which
would then refuse every lot count against the ceiling.

All four legs go in ONE call. Pricing them separately and adding would discard the exchange's
netting, which on a hedged four-leg structure is most of the margin benefit -- the same
reasoning that made Bot 2 price a strangle's two sides together.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

import icici_breeze_backend.app.core.config as cfg

_logger = logging.getLogger(__name__)


def margin_for_mixed_legs(
    proc: Any,
    user_id: str,
    *,
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    legs: Sequence[tuple[str, float, int, str]],
) -> Optional[float]:
    """SPAN for `legs` as (right, strike, quantity, action) priced together.

    Returns None on any failure. Callers must treat that as "cannot price", never as zero --
    a zero would size an unlimited position against a margin ceiling.
    """
    from icici_breeze_backend.app.core.strike import strike_for_broker
    from icici_breeze_backend.app.services.processor import _expiry_display_to_api

    if not legs:
        return None
    try:
        breeze = proc.get_session_breeze(user_id)
        expiry_api = _expiry_display_to_api(expiry_display)
        payload = [
            {
                "strike_price": strike_for_broker(strike),
                "quantity": int(quantity),
                "product": cfg.OPTIONS,
                "action": action,
                "expiry_date": expiry_api,
                "stock_code": stock_code,
                "right": right,
            }
            for right, strike, quantity, action in legs
        ]
        out = breeze.margin_calculator(payload, exchange_code=exchange_code)
    except Exception:  # noqa: BLE001
        _logger.warning("iron fly: margin_calculator failed for %s", stock_code, exc_info=True)
        return None
    if not isinstance(out, dict) or out.get("Status") != 200:
        # Logged, not just returned: a silent refusal is how a stale session passed for
        # "cannot price" on Bot 2 for a whole morning.
        _logger.warning(
            "iron fly: margin_calculator refused for %s %s status=%s error=%r",
            stock_code,
            exchange_code,
            out.get("Status") if isinstance(out, dict) else type(out).__name__,
            out.get("Error") if isinstance(out, dict) else None,
        )
        evict = getattr(proc, "_maybe_evict_session", None)
        if callable(evict) and isinstance(out, dict):
            evict(user_id, out)
        return None
    raw = (out.get("Success") or {}).get("span_margin_required")
    try:
        value = float(raw or 0)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:
        _logger.warning(
            "iron fly: margin_calculator span_margin_required=%r for %s %s", raw, stock_code, exchange_code
        )
        return None
    return value
