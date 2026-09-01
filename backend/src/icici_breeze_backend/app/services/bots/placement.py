"""Order placement shared by the bots.

Freeze-quantity chunking is the reason this exists rather than a bare `place_order` call:
the exchange rejects a single order above the contract's freeze limit outright, so a large
lot count has to go out as several orders. `squareoff_dispatcher` learned this the hard way
and its `_leg_qty_per_order` is mirrored here, including its fallback — a scrip-master gap
must not newly block an order that would otherwise have gone through unchunked.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.aggressive_limit import round_to_tick

_logger = logging.getLogger(__name__)


@dataclass
class PlacementResult:
    stock_code: str
    right: str
    strike_price: float
    expiry_display: str
    quantity: int
    limit_price: float
    order_ids: list[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.order_ids)


def qty_per_order(proc: Any, stock_code: str, expiry_display: str, exchange_code: str, total_qty: int) -> int:
    """Largest lot-aligned quantity that fits under the contract's freeze limit."""
    try:
        limit = proc.fetch_qty_limits(stock_code, exchange_code=exchange_code)
        lot = proc.fetch_lot_size(stock_code, expiry_display, exchange_code=exchange_code)
        if not limit or not lot:
            return total_qty
        per_order = (max(1, int(limit)) // int(lot)) * int(lot)
        return per_order if per_order > 0 else total_qty
    except Exception:  # noqa: BLE001 -- see module docstring
        _logger.warning(
            "bots: could not resolve freeze-qty limit for %s %s; placing unchunked",
            stock_code,
            expiry_display,
            exc_info=True,
        )
        return total_qty


def sell_limit_price(premium_per_share: float, tolerance_pct: float) -> float:
    """Undercut the bid slightly so the order actually fills, bounded by tolerance.

    A limit exactly at the bid frequently sits unfilled; a market order on a thin stock
    option can fill far away. This is the same bounded-aggression trade-off
    `aggressive_limit.py` documents, applied to the sell side.
    """
    price = float(premium_per_share) * (1 - max(0.0, tolerance_pct) / 100)
    return max(round_to_tick(price), round_to_tick(cfg.AGGRESSIVE_LIMIT_TICK_SIZE))


def place_short_legs(
    proc: Any,
    user_id: str,
    legs: list[dict[str, Any]],
    *,
    tolerance_pct: float,
) -> list[PlacementResult]:
    """Sell each leg, chunked under the freeze limit.

    Placement is best-effort per leg and never all-or-nothing: a rejection on one scrip must
    not cancel orders already working on another. Partial outcomes are reported per leg so
    the caller can record exactly what got to the exchange.
    """
    results: list[PlacementResult] = []
    for leg in legs:
        total = int(leg["quantity"])
        price = sell_limit_price(float(leg["premium_per_share"]), tolerance_pct)
        result = PlacementResult(
            stock_code=leg["stock_code"],
            right=leg["right"],
            strike_price=float(leg["strike_price"]),
            expiry_display=leg["expiry_display"],
            quantity=total,
            limit_price=price,
        )
        chunk = qty_per_order(
            proc, leg["stock_code"], leg["expiry_display"], leg["exchange_code"], total
        )
        remaining = total
        while remaining > 0:
            this_chunk = min(chunk, remaining)
            try:
                response = proc.place_order(
                    user_id,
                    cfg.OPTIONS,
                    leg["stock_code"],
                    cfg.SELL,
                    leg["strike_price"],
                    cfg.CALL if leg["right"] == "call" else cfg.PUT,
                    str(price),
                    leg["expiry_display"],
                    this_chunk,
                    exchange_code=leg["exchange_code"],
                )
            except Exception as e:  # noqa: BLE001
                _logger.warning("bots: place_order raised for %s: %s", leg["stock_code"], e, exc_info=True)
                result.error = "Broker call failed while placing this order."
                break
            if isinstance(response, dict) and response.get("Status") == 200:
                order_id = (response.get("Success") or {}).get("order_id")
                if order_id:
                    result.order_ids.append(str(order_id))
                else:
                    result.error = "Broker did not return an order id."
                    break
            else:
                result.error = str((response or {}).get("Error") or "Broker rejected the order.")
                break
            remaining -= this_chunk
        # A leg that placed some chunks and then failed is a real, reportable state -- say
        # so rather than presenting it as a clean success or a clean failure.
        if result.error and result.order_ids:
            result.error = f"Partially placed ({len(result.order_ids)} order(s)): {result.error}"
        results.append(result)
    return results
