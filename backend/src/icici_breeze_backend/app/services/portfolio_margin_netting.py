"""Shared primitives for netting Strategy Builder margin against a user's open
option positions in the same scrip.

See docs/strategy-builder-portfolio-margin-plan.md for the full design (D1-D10).
Reuses processor._netted_span_for_legs -- the same netted, composition-cached
margin_calculator call already powering the Portfolio page -- rather than
introducing a second netting implementation.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from icici_breeze_backend.app.services.processor import _safe_int


def normalize_stock(raw: str) -> str:
    """Canonical stock-code comparison key. Moved here from route_hedge so both
    the hedge bucket filter and the margin-netting position filter share one
    definition."""
    return str(raw or "").strip().upper().split()[0]


@dataclass(frozen=True)
class PositionSet:
    """Open option positions for one scrip + exchange, across all expiries.

    `available=False` means the positions fetch itself failed (broker session
    down, exception, non-200) -- callers must fall back to standalone margin
    (D7), never size against an offset that could not be verified. An empty
    `rows` list with `available=True` is a valid "no open positions" answer and
    must NOT trigger the D7 fallback warning.
    """

    rows: list[dict[str, Any]] = field(default_factory=list)
    fingerprint: str = ""
    expiries: list[str] = field(default_factory=list)
    available: bool = True
    error: str | None = None


def _positions_fingerprint(rows: list[dict[str, Any]]) -> str:
    """Stable hash of leg composition, matching the identity format used by
    processor._portfolio_netted_cache_key (stock:expiry:strike:right:action:qty),
    so a position set that would hit that 24h cache also produces a stable
    fingerprint here. Used to key engine-side margin caches (see helpers.margin_key
    / sizing.structural_margin_key) so a stale netted span can never leak across
    builds after the user's positions change."""
    if not rows:
        return ""
    ident = sorted(
        f"{r.get('stock_code')}:{r.get('expiry_date')}:{r.get('strike_price')}:"
        f"{r.get('right')}:{r.get('action')}:{r.get('quantity')}"
        for r in rows
    )
    return hashlib.sha256("|".join(ident).encode("utf-8")).hexdigest()[:16]


def positions_for_underlying(
    proc: Any, user_id: str, stock_code: str, exchange_code: str
) -> PositionSet:
    """Open option positions for one scrip on one exchange, across all open
    expiries (D1 netting universe). Futures are already excluded upstream by
    get_positions() (D9 -- options-only for this release)."""
    try:
        raw = proc.get_positions(user_id)
    except Exception as exc:  # noqa: BLE001 - any failure here must fall back, not raise
        return PositionSet(available=False, error=str(exc))

    if not isinstance(raw, dict) or raw.get("Status") != 200:
        err = str((raw or {}).get("Error") or "Unable to load portfolio positions")
        return PositionSet(available=False, error=err)

    target = normalize_stock(stock_code)
    rows: list[dict[str, Any]] = []
    for row in raw.get("Success") or []:
        if not isinstance(row, dict):
            continue
        if normalize_stock(str(row.get("stock_code") or "")) != target:
            continue
        if str(row.get("exchange_code") or "") != exchange_code:
            continue
        if _safe_int(row.get("quantity")) == 0:
            continue
        rows.append(row)

    expiries = sorted({str(r["expiry_date"]) for r in rows if r.get("expiry_date")})
    return PositionSet(
        rows=rows,
        fingerprint=_positions_fingerprint(rows),
        expiries=expiries,
        available=True,
        error=None,
    )


def positions_to_margin_input(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert raw get_positions() rows to margin_calculator leg dicts, each
    keeping its OWN expiry_date -- unlike helpers.legs_to_margin_input, which
    hardcodes one expiry for every leg and therefore cannot represent a
    cross-expiry position set. Field shape mirrors
    processor._netted_span_for_legs exactly."""
    return [
        {
            "strike_price": r["strike_price"],
            "quantity": r["quantity"],
            "right": r["right"],
            "action": r["action"],
            "product": r["product_type"],
            "expiry_date": r["expiry_date"],
            "stock_code": r["stock_code"],
            "cover_order_flow": "N",
            "fresh_order_type": "N",
            "cover_limit_rate": "0",
            "cover_sltp_price": "0",
            "fresh_limit_rate": "0",
            "open_quantity": "0",
        }
        for r in rows
    ]


def existing_span(
    proc: Any,
    breeze: Any,
    user_id: str,
    exchange_code: str,
    position_set: PositionSet,
) -> float | None:
    """M(P): netted SPAN of the existing position set alone. Returns 0.0 (no
    call made) when there are no positions, and None when the netted
    margin_calculator call itself fails -- callers must treat None as
    "netting unavailable" (D7), not as zero margin."""
    if not position_set.rows:
        return 0.0
    return proc._netted_span_for_legs(breeze, user_id, exchange_code, position_set.rows)
