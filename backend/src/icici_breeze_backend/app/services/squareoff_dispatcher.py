"""Wires `portfolio_pnl_engine`'s group-rule-hit event to real broker orders.

`portfolio_pnl_engine.register_rule_hit_listener` has existed since that
module was written but nothing ever called it — this is the first (and only)
listener. It only acts on `group_target_hit` / `group_stop_loss_hit`; the
per-leg and whole-portfolio tiers are dead code paths (nothing arms them) and
are ignored defensively rather than assumed unreachable.
"""
from __future__ import annotations

import logging
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.repositories import squareoff_rules as repo
from icici_breeze_backend.app.services.deployment_license_status import trading_mutations_allowed
from icici_breeze_backend.app.services.portfolio_pnl_engine import (
    register_rule_hit_listener,
    set_group_rule,
)
from icici_breeze_backend.app.services.processor import processor
from icici_breeze_backend.app.services.telegram_alerts import notify_squareoff_fired
from icici_breeze_backend.audit.logger import AuditLogger, OperationType

_logger = logging.getLogger(__name__)
_GROUP_REASONS = {"group_target_hit", "group_stop_loss_hit"}
_NFO_TICK_SIZE = 0.05


def _round_to_tick(price: float) -> float:
    return round(round(price / _NFO_TICK_SIZE) * _NFO_TICK_SIZE, 2)


def _leg_limit_price(leg: dict[str, Any], *, reason: str, payload: dict[str, Any]) -> float:
    """Marketable limit price for a closing leg: a Buy is placed at a premium
    to LTP, a Sell at a discount, using whichever of the rule's two
    user-configured percentages matches why it fired (profit-booking vs
    stop-loss) — so the order is priced to fill without being a raw,
    unbounded-slippage MARKET order."""
    pct = (
        payload["target_premium_pct"]
        if reason == "group_target_hit"
        else payload["stop_loss_premium_pct"]
    )
    ltp = float(leg["ltp"])
    factor = 1 + pct / 100 if leg["action"] == cfg.BUY else 1 - pct / 100
    return _round_to_tick(ltp * factor)


def hydrate_group_rules_on_startup() -> None:
    """Re-arm every persisted 'armed' rule into the in-memory engine so a
    restart doesn't silently drop a user's live protection."""
    for row in repo.list_all_armed_rules():
        set_group_rule(
            str(row["user_id"]),
            str(row["id"]),
            stock_code=str(row["stock_code"]),
            expiry_display=str(row["expiry_display"]),
            exchange_code=str(row.get("exchange_code") or "NFO"),
            target_pnl=float(row["profit_target_pnl"]),
            stop_loss_pnl=float(row["loss_limit_pnl"]),
            target_premium_pct=int(row["target_premium_pct"]),
            stop_loss_premium_pct=int(row["stop_loss_premium_pct"]),
        )


def _handle_group_rule_hit(payload: dict[str, Any]) -> None:
    reason = payload.get("reason")
    if reason not in _GROUP_REASONS:
        return

    user_id = str(payload["user_id"])
    rule_id = str(payload["rule_id"])
    legs = payload.get("legs") or []

    # Short-lived marker: this poll cycle detected the breach and is about to dispatch
    # close orders. Real, if brief, since each leg below is a synchronous network call.
    repo.mark_triggered(rule_id)

    if not trading_mutations_allowed():
        leg_results = [
            {
                "scrip_key": leg["scrip_key"],
                "stock_code": leg["stock_code"],
                "strike_price": leg["strike_price"],
                "right": leg["right"],
                "quantity": leg["quantity"],
                "status": "failed",
                "error": "Trading is read-only (license not active) — no orders were placed.",
            }
            for leg in legs
        ]
        repo.mark_fire_failed(rule_id, leg_results)
        AuditLogger(None).log_operation(
            user_id,
            OperationType.SQUAREOFF_RULE_FIRE_FAILED,
            "PortfolioSquareOffRule",
            rule_id,
            action_status="failure",
            error_details="Trading read-only at fire time (license not active)",
        )
        notify_squareoff_fired(user_id, reason=reason, payload=payload, leg_results=leg_results, failed=True)
        return

    breeze = processor()
    leg_results: list[dict[str, Any]] = []
    all_ok = True
    for leg in legs:
        try:
            limit_price = _leg_limit_price(leg, reason=reason, payload=payload)
            response = breeze.place_order(
                user_id=user_id,
                product_type=leg["product_type"],
                stock_code=leg["stock_code"],
                action=leg["action"],
                strike_price=leg["strike_price"],
                right=leg["right"],
                price=str(limit_price),
                expiry_date=leg["expiry_display"],
                quantity=leg["quantity"],
                exchange_code=leg["exchange_code"],
                aggressive_limit=False,
            )
            ok = isinstance(response, dict) and response.get("Status") == 200
            error = None if ok else str((response or {}).get("Error") or "Broker rejected the order")
            order_id = (response or {}).get("Success", {}).get("order_id") if ok else None
        except Exception as exc:  # defensive: one leg's failure must not stop the rest
            _logger.exception(
                "Square-off order placement raised for rule_id=%s leg=%s", rule_id, leg.get("scrip_key")
            )
            ok = False
            error = str(exc)
            order_id = None
        all_ok = all_ok and ok
        leg_results.append(
            {
                "scrip_key": leg["scrip_key"],
                "stock_code": leg["stock_code"],
                "strike_price": leg["strike_price"],
                "right": leg["right"],
                "quantity": leg["quantity"],
                "status": "success" if ok else "failed",
                "error": error,
                "order_id": str(order_id) if order_id else None,
                "action": leg["action"],
                "price": str(limit_price) if ok else None,
            }
        )

    if all_ok:
        repo.mark_fired(rule_id, leg_results)
        AuditLogger(None).log_operation(
            user_id,
            OperationType.SQUAREOFF_RULE_FIRED,
            "PortfolioSquareOffRule",
            rule_id,
            action_status="success",
            error_details=f"reason={reason} total_pnl={payload.get('total_pnl')}",
        )
        notify_squareoff_fired(user_id, reason=reason, payload=payload, leg_results=leg_results, failed=False)
    else:
        repo.mark_fire_failed(rule_id, leg_results)
        failed = [r for r in leg_results if r["status"] == "failed"]
        AuditLogger(None).log_operation(
            user_id,
            OperationType.SQUAREOFF_RULE_FIRE_FAILED,
            "PortfolioSquareOffRule",
            rule_id,
            action_status="failure",
            error_details=f"{len(failed)}/{len(leg_results)} leg(s) failed to place",
        )
        notify_squareoff_fired(user_id, reason=reason, payload=payload, leg_results=leg_results, failed=True)


def register_squareoff_dispatcher() -> None:
    register_rule_hit_listener(_handle_group_rule_hit)
