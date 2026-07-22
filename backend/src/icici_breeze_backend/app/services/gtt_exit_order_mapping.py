"""Shared mapping from ICICI's raw `gtt_order_book` rows to `GttExitOrderRowRecord`.

Used both by the Orders page's "list all GTT exit orders" endpoint
(`route_gtt_exit_orders.py`) and the exit-rule order-matching service
(`exit_rule_orders.py`), so the two paths can't drift out of sync.
"""
from __future__ import annotations

import datetime
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import today_ist_date
from icici_breeze_backend.app.domain.gtt_exit_order import GttExitOrderLeg, GttExitOrderRowRecord
from icici_breeze_backend.app.services.reference_data.scrip_master_sql import normalize_expiry_display


def fetch_all_gtt_raw_rows(breeze, user_id: str) -> list[dict[str, Any]]:
    """Raw `gtt_order_book` rows across both exchanges, one year back. Best-effort: a
    failure on one exchange doesn't raise, just contributes nothing from that exchange
    (callers include a read path — Order Book — that shouldn't break over this)."""
    from_date = str(today_ist_date() - datetime.timedelta(days=365)) + "T06:00:00.000Z"
    to_date = str(today_ist_date()) + "T06:00:00.000Z"
    raw_rows: list[dict[str, Any]] = []
    for exchange_code in (cfg.NFO, cfg.BFO):
        response = breeze.get_gtt_order_book(user_id, exchange_code, from_date, to_date)
        if (response or {}).get("Error"):
            continue
        for row in (response or {}).get("Success") or []:
            row.setdefault("exchange_code", exchange_code)
            raw_rows.append(row)
    return raw_rows


def is_fully_cancelled(row: dict[str, Any]) -> bool:
    """True once every leg reads "Cancelled" — the one status string ICICI's SDK docs
    confirm (the rest of the vocabulary isn't documented)."""
    legs = row.get("order_details") or []
    return bool(legs) and all(
        str(leg.get("status") or "").strip().lower() == "cancelled" for leg in legs
    )


def map_gtt_order_book_rows(raw_rows: list[dict[str, Any]]) -> list[GttExitOrderRowRecord]:
    """Maps + dedupes by the resulting `gtt_order_id`. Deduping matters because
    `fetch_all_gtt_raw_rows` queries per-exchange and at least the mock broker (and
    possibly real ICICI, unverified) returns its full book regardless of the
    exchange_code filter, so the same bracket can otherwise show up once per
    exchange queried."""
    rows: list[GttExitOrderRowRecord] = []
    seen_ids: set[str] = set()
    for row in raw_rows:
        order_details = row.get("order_details") or []
        if not order_details:
            continue
        gtt_order_id = str(order_details[0].get("gtt_order_id") or "")
        if gtt_order_id and gtt_order_id in seen_ids:
            continue
        if gtt_order_id:
            seen_ids.add(gtt_order_id)
        legs = [
            GttExitOrderLeg(
                gtt_leg_type=leg.get("gtt_leg_type"),
                action=leg.get("action"),
                trigger_price=leg.get("trigger_price"),
                limit_price=leg.get("limit_price"),
                status=leg.get("status"),
            )
            for leg in order_details
        ]
        try:
            expiry_display = normalize_expiry_display(str(row.get("expiry_date") or ""))
        except (ValueError, TypeError):
            expiry_display = row.get("expiry_date")
        rows.append(
            GttExitOrderRowRecord(
                gtt_order_id=gtt_order_id,
                stock_code=row.get("stock_code"),
                exchange_code=row.get("exchange_code"),
                expiry_display=expiry_display,
                strike_price=row.get("strike_price"),
                right=row.get("right"),
                quantity=row.get("quantity"),
                product_type=row.get("product_type"),
                gtt_type=row.get("gtt_type"),
                order_datetime=row.get("order_datetime"),
                legs=legs,
                is_cancelled=is_fully_cancelled(row),
            )
        )
    return rows
