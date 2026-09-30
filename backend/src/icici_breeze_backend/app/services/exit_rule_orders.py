"""Excludes broker orders spawned by a Profit Booking / Stop Loss exit rule from the
main Order Book list (`route_book.py`), so they can be surfaced instead under the
Orders page's dedicated Profit Booking / Stop Loss table with their owning rule.

Group rules: exact match via `order_ids`, captured at dispatch time
(`squareoff_dispatcher.py`) into `SquareOffRuleLegResult.order_ids` — fully reliable
(a leg can have more than one order_id when its quantity was split across multiple
freeze-quantity-capped chunk orders).

Leg·GTT rules: ICICI's GTT placement/list APIs don't confirm a resulting order_id for
the order a trigger eventually produces (unverified against real ICICI — the mock
broker tracks a `fresh_order_id` field but nothing ever populates it, and there's no
other corroboration in the vendored SDK), and the GTT APIs take no remark. So a GTT's
order is never moved out of the Order Book (B-51). Every order the app places carries an
app tag (`order_intents.is_app_tag`) and is never a candidate. An untagged order that is
the single match for a live GTT bracket's contract and one of its leg actions stays in the
Order Book with `possible_gtt_exit_id`: it is either the GTT's, or one placed on ICICI's
own app, and nothing here can tell which.
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
    known: dict[str, tuple[str, str, str | None]] = {}

    for rule in squareoff_rules:
        for leg in rule.leg_results or []:
            for oid in leg.order_ids:
                known[oid] = ("squareoff_rule", rule.id, leg.scrip_key)

    from icici_breeze_backend.app.services.bots.scalping.order_intents import is_app_tag

    # An order the app placed carries its tag and cannot be one a GTT fired (B-51).
    unmatched = [
        o for o in raw_orders
        if o.get("order_id") not in known and not is_app_tag(o.get("user_remark"))
    ]
    possible: dict[str, str] = {}
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
                # Not moved out of the Order Book: an untagged order is either the GTT's or
                # one placed on ICICI's own app, and nothing here can tell which. It stays,
                # marked, rather than a manual order disappearing (B-51).
                possible[oid] = gtt.gtt_order_id

    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for o in raw_orders:
        tag = known.get(o.get("order_id") or "")
        if tag:
            source, rule_id, scrip_key = tag
            entry = {**o, "exit_rule_source": source, "exit_rule_id": rule_id}
            if scrip_key:
                entry["exit_rule_scrip_key"] = scrip_key
            excluded.append(entry)
        elif (o.get("order_id") or "") in possible:
            kept.append({**o, "possible_gtt_exit_id": possible[o.get("order_id") or ""]})
        else:
            kept.append(o)
    return kept, excluded
