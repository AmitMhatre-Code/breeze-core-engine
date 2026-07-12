"""Excludes broker orders spawned by a Profit Booking / Stop Loss exit rule from the
main Order Book list (`route_book.py`), so they can be surfaced instead under the
Orders page's dedicated Profit Booking / Stop Loss table with their owning rule.

Group rules: exact match via `order_id`, captured at dispatch time
(`squareoff_dispatcher.py`) into `SquareOffRuleLegResult.order_id` — fully reliable.

Leg·GTT rules: ICICI's GTT placement/list APIs don't confirm a resulting order_id for
the order a trigger eventually produces (unverified against real ICICI — the mock
broker tracks a `fresh_order_id` field but nothing ever populates it, and there's no
other corroboration in the vendored SDK). Falls back to a conservative contract+action
match: only excludes an order when it's the single unambiguous match for a live GTT
bracket's contract and one of its two leg actions. Ambiguous or no match leaves the
order visible in Order Book — bias toward showing too much, never hiding a real order
incorrectly.
"""
from __future__ import annotations

from typing import Any

from icici_breeze_backend.app.core.strike import parse_strike
from icici_breeze_backend.app.domain.gtt_exit_order import GttExitOrderRowRecord
from icici_breeze_backend.app.domain.squareoff_rule import SquareOffRuleRecord
from icici_breeze_backend.app.services.reference_data.scrip_master_sql import normalize_expiry_display


def _expiry_matches(a: Any, b: Any) -> bool:
    try:
        return normalize_expiry_display(str(a or "")) == normalize_expiry_display(str(b or ""))
    except (ValueError, TypeError):
        return str(a or "").strip().lower() == str(b or "").strip().lower()


def _right_matches(a: Any, b: Any) -> bool:
    norm = lambda r: "call" if str(r or "").strip().lower().startswith("c") else "put"
    return norm(a) == norm(b)


def split_rule_spawned_orders(
    squareoff_rules: list[SquareOffRuleRecord],
    gtt_rows: list[GttExitOrderRowRecord],
    raw_orders: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (kept, excluded). `excluded` entries are the original order dicts plus
    `exit_rule_source` ('squareoff_rule' | 'gtt_exit_order') and `exit_rule_id`."""
    known: dict[str, tuple[str, str]] = {}

    for rule in squareoff_rules:
        for leg in rule.leg_results or []:
            if leg.order_id:
                known[leg.order_id] = ("squareoff_rule", rule.id)

    unmatched = [o for o in raw_orders if o.get("order_id") not in known]
    for gtt in gtt_rows:
        if not gtt.gtt_order_id:
            continue
        leg_actions = {str(leg.action or "").strip().lower() for leg in gtt.legs if leg.action}
        if not leg_actions:
            continue
        candidates = [
            o
            for o in unmatched
            if str(o.get("stock_code") or "").strip().upper() == str(gtt.stock_code or "").strip().upper()
            and str(o.get("exchange_code") or "") == str(gtt.exchange_code or "")
            and _expiry_matches(o.get("expiry_date"), gtt.expiry_display)
            and parse_strike(o.get("strike_price")) == parse_strike(gtt.strike_price)
            and _right_matches(o.get("right"), gtt.right)
            and str(o.get("action") or "").strip().lower() in leg_actions
        ]
        if len(candidates) == 1:
            oid = candidates[0].get("order_id")
            if oid:
                known[oid] = ("gtt_exit_order", gtt.gtt_order_id)

    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for o in raw_orders:
        tag = known.get(o.get("order_id") or "")
        if tag:
            source, rule_id = tag
            excluded.append({**o, "exit_rule_source": source, "exit_rule_id": rule_id})
        else:
            kept.append(o)
    return kept, excluded
