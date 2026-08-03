"""Wires `portfolio_pnl_engine`'s group-rule-hit event to real broker orders.

`portfolio_pnl_engine.register_rule_hit_listener` has existed since that
module was written but nothing ever called it — this is the first (and only)
listener. It only acts on `group_target_hit` / `group_stop_loss_hit`; the
per-leg and whole-portfolio tiers are dead code paths (nothing arms them) and
are ignored defensively rather than assumed unreachable.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.repositories import squareoff_rules as repo
from icici_breeze_backend.app.services.deployment_license_status import trading_mutations_allowed
from icici_breeze_backend.app.services.icici_api_pacing import is_breeze_rate_limited
from icici_breeze_backend.app.services.portfolio_pnl_engine import (
    register_rule_hit_listener,
    set_group_rule,
)
from icici_breeze_backend.app.services.processor import processor
from icici_breeze_backend.app.services.telegram_alerts import (
    notify_squareoff_fired,
    notify_squareoff_retrying,
)
from icici_breeze_backend.audit.logger import AuditLogger, OperationType

_logger = logging.getLogger(__name__)
_GROUP_REASONS = {"group_target_hit", "group_stop_loss_hit"}
_NFO_TICK_SIZE = 0.05


def _round_to_tick(price: float) -> float:
    return round(round(price / _NFO_TICK_SIZE) * _NFO_TICK_SIZE, 2)


def _leg_qty_per_order(breeze, leg: dict[str, Any]) -> int:
    """Freeze-aligned max quantity per single order for this leg's contract.

    Falls back to the leg's full quantity (i.e. no chunking) if the scrip
    master doesn't have freeze-limit/lot-size data for this contract, so a
    lookup gap doesn't newly block a square-off that used to work unchunked.
    """
    total_qty = int(leg["quantity"])
    try:
        qty_limits = breeze.fetch_qty_limits(leg["stock_code"], exchange_code=leg["exchange_code"])
        lot_size = breeze.fetch_lot_size(leg["stock_code"], leg["expiry_display"], exchange_code=leg["exchange_code"])
        if not qty_limits or not lot_size:
            return total_qty
        per_order = (max(1, int(qty_limits)) // int(lot_size)) * int(lot_size)
        return per_order if per_order > 0 else total_qty
    except Exception:
        _logger.warning(
            "Could not resolve freeze-qty limit for square-off leg=%s; placing unchunked",
            leg.get("scrip_key"),
            exc_info=True,
        )
        return total_qty


def _split_into_chunks(total_qty: int, qty_per_order: int) -> list[int]:
    if qty_per_order <= 0 or qty_per_order >= total_qty:
        return [total_qty]
    iterations = total_qty // qty_per_order
    remainder = total_qty % qty_per_order
    chunks = [qty_per_order] * iterations
    if remainder:
        chunks.append(remainder)
    return chunks


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
    """Re-arm every persisted live SG into the in-memory engine, and re-pin its WS chain
    subscription, so a restart doesn't silently drop a user's protection.

    The re-pin matters as much as the re-arm: nothing else in the app creates a
    server-side subscription holder (every other one comes from a browser request), so
    without it a restarted instance would hold armed SGs that can never fire — their
    quotes would go stale and the engine would never see a breach.
    """
    from icici_breeze_backend.app.services import strategy_group_lifecycle as sg
    from icici_breeze_backend.app.services.squareoff_protection_guard import (
        warm_positions_for_user,
    )

    armed_order_feed: set[str] = set()
    warmed_positions: set[str] = set()
    for row in repo.list_all_live_rules():
        user_id = str(row["user_id"])
        # Re-arming the rule is not enough to make it fire. `run_pnl_tick` iterates the
        # position registry and returns early when it is empty, and `_evaluate_rules` is
        # called from inside that per-user loop — so without warming positions here the
        # SGs below are restored, pinned, visible in the UI as Armed, and evaluate
        # nothing until someone opens the Portfolio page. Cost is one call per user.
        if user_id not in warmed_positions:
            warmed_positions.add(user_id)
            if not warm_positions_for_user(user_id):
                # Expected when the instance restarts outside a live broker session. The
                # guard loop retries every tick and alerts the user; do not block
                # hydration of the remaining rules on it.
                _logger.warning(
                    "Could not warm positions for user_id=%s during hydration; "
                    "PB/SL evaluation is suspended until a broker session is available",
                    user_id,
                )
        # Arm the account-wide order feed once per user with a live SG, independent of the
        # per-rule chain pins below — the SG lifecycle can't reach Completed / Reset without
        # it, and it must not hinge on a chain subscription happening to bring the WS up.
        if user_id not in armed_order_feed:
            armed_order_feed.add(user_id)
            try:
                from icici_breeze_backend.app.services.breeze_websocket_manager import (
                    ensure_order_feed,
                )
                from icici_breeze_backend.app.services.processor import processor

                ensure_order_feed(processor(), user_id)
            except Exception:  # noqa: BLE001 — never block hydration on a WS hiccup
                _logger.exception("Could not arm order feed for user_id=%s", user_id)
        if row["status"] == "armed":
            set_group_rule(
                user_id,
                str(row["id"]),
                stock_code=str(row["stock_code"]),
                expiry_display=str(row["expiry_display"]),
                exchange_code=str(row.get("exchange_code") or "NFO"),
                target_pnl=float(row["profit_target_pnl"]),
                stop_loss_pnl=float(row["loss_limit_pnl"]),
                target_premium_pct=int(row["target_premium_pct"]),
                stop_loss_premium_pct=int(row["stop_loss_premium_pct"]),
            )
        rule = repo.get_rule(str(row["id"]))
        if rule is not None:
            sg.pin_subscription(user_id, rule)


# ~50s of patience, against ICICI's minute-scale throttle cooldown. The old behaviour gave
# up after the transport layer's ~4s of backoff, which is an order of magnitude short — a
# leg that could have filled 20 seconds later was abandoned, leaving the position half
# unwound with no automatic second chance (the rule is popped from the registry before
# dispatch, so no later tick re-evaluates it).
_RETRY_DELAYS_SEC = (5.0, 10.0, 15.0, 20.0)


def _is_retryable_throttle(response: Any) -> bool:
    """Only an explicit ICICI throttle is retryable.

    Deliberately narrow. A throttle is a *refusal*: ICICI tells us it did not accept the
    order, so re-sending cannot duplicate it. A timeout or transport error carries no such
    guarantee — the order may well have reached the exchange — and re-sending one risks
    opening a second position. Those are surfaced to the user instead of retried, which is
    why this never needs an order-book round-trip to stay safe.

    A day-limit exhaustion is a throttle we also refuse to retry: it will not clear before
    midnight IST, so waiting 50s only delays the bad news.
    """
    if not isinstance(response, dict):
        return False
    if response.get("daily_limit_exhausted"):
        return False
    if response.get("advisory_shed"):
        return False  # never applies to order placement, but be explicit
    if response.get("icici_throttled"):
        return True
    return is_breeze_rate_limited(response.get("Status"), response.get("Error"))


def _still_firing(rule_id: str) -> bool:
    """True while this rule is still the one we are placing exits for.

    `_handle_group_rule_hit` marks the rule `triggered` before placing. Anything else —
    most importantly a Reset from `_handle_foreign_order`, which fires when the WS order
    feed sees a fill the user made from the ICICI website or app — means our exit is no
    longer wanted. Re-checking this between retries is what makes a long retry safe: the
    user can act during the wait and we stand down instead of racing them into a contra
    position.
    """
    try:
        rule = repo.get_rule(rule_id)
    except Exception:  # noqa: BLE001 — a DB blip must not silently abandon an exit
        _logger.exception("Could not re-read rule %s mid-retry; continuing", rule_id)
        return True
    return rule is not None and rule.status == "triggered"


def _place_chunk_with_retry(
    breeze,
    *,
    user_id: str,
    rule_id: str,
    leg: dict[str, Any],
    chunk_qty: int,
    limit_price: float,
    on_first_retry: Callable[[], None],
) -> tuple[str | None, str | None]:
    """Place one chunk, retrying only through ICICI throttles. Returns (order_id, error)."""
    last_error: str | None = None
    notified = False
    for attempt in range(len(_RETRY_DELAYS_SEC) + 1):
        if attempt:
            time.sleep(_RETRY_DELAYS_SEC[attempt - 1])
            if not _still_firing(rule_id):
                return None, (
                    "Not retried — this rule was reset while we were waiting out an ICICI "
                    "throttle (the position was acted on elsewhere)."
                )
        response = breeze.place_order(
            user_id=user_id,
            product_type=leg["product_type"],
            stock_code=leg["stock_code"],
            action=leg["action"],
            strike_price=leg["strike_price"],
            right=leg["right"],
            price=str(limit_price),
            expiry_date=leg["expiry_display"],
            quantity=chunk_qty,
            exchange_code=leg["exchange_code"],
            aggressive_limit=False,
        )
        if isinstance(response, dict) and response.get("Status") == 200:
            order_id = (response.get("Success") or {}).get("order_id")
            if order_id:
                return str(order_id), None
            return None, "Broker did not return an order id"

        last_error = str((response or {}).get("Error") or "Broker rejected the order")
        if not _is_retryable_throttle(response):
            return None, last_error
        if not notified:
            notified = True
            on_first_retry()
        _logger.warning(
            "Exit leg throttled for rule_id=%s leg=%s; retry %d/%d",
            rule_id,
            leg.get("scrip_key"),
            attempt + 1,
            len(_RETRY_DELAYS_SEC),
        )
    return None, last_error


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
        repo.mark_fire_failed(
            rule_id,
            leg_results,
            "trading is in read-only mode (licence not active), so no exit orders "
            "could be placed.",
        )
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

    announced = {"retry": False}

    def _announce_retry() -> None:
        """Tell the user we are retrying, the moment it starts — not 50s later.

        Without this the only signal is the final alert, so a user watching an exit not
        appear has every reason to go place it themselves on ICICI's app. Saying we are
        retrying *and* that we will stand down if they act is what makes the wait
        tolerable; the stand-down is real (`_still_firing`), so this is a promise the code
        actually keeps.
        """
        if announced["retry"]:
            return
        announced["retry"] = True
        try:
            notify_squareoff_retrying(
                user_id,
                reason=reason,
                payload=payload,
                seconds=int(sum(_RETRY_DELAYS_SEC)),
            )
        except Exception:  # noqa: BLE001 — an alert must never break placement
            _logger.exception("Retry alert failed for rule_id=%s", rule_id)

    for leg in legs:
        order_ids: list[str] = []
        chunk_errors: list[str] = []
        limit_price: float | None = None
        try:
            limit_price = _leg_limit_price(leg, reason=reason, payload=payload)
            qty_per_order = _leg_qty_per_order(breeze, leg)
            chunks = _split_into_chunks(int(leg["quantity"]), qty_per_order)
            for chunk_qty in chunks:
                order_id, chunk_error = _place_chunk_with_retry(
                    breeze,
                    user_id=user_id,
                    rule_id=rule_id,
                    leg=leg,
                    chunk_qty=chunk_qty,
                    limit_price=limit_price,
                    on_first_retry=_announce_retry,
                )
                if order_id:
                    order_ids.append(order_id)
                else:
                    chunk_errors.append(chunk_error or "Broker rejected the order")
        except Exception as exc:  # defensive: one leg's failure must not stop the rest
            _logger.exception(
                "Square-off order placement raised for rule_id=%s leg=%s", rule_id, leg.get("scrip_key")
            )
            chunk_errors.append(str(exc))

        if order_ids and not chunk_errors:
            leg_status = "success"
        elif order_ids:
            leg_status = "partial"
        else:
            leg_status = "failed"
        all_ok = all_ok and leg_status == "success"
        leg_results.append(
            {
                "scrip_key": leg["scrip_key"],
                "stock_code": leg["stock_code"],
                "strike_price": leg["strike_price"],
                "right": leg["right"],
                "quantity": leg["quantity"],
                "status": leg_status,
                "error": "; ".join(chunk_errors) if chunk_errors else None,
                "order_ids": order_ids,
                "action": leg["action"],
                "price": str(limit_price) if order_ids and limit_price is not None else None,
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
        # "failed" legs placed zero chunks; "partial" legs placed some but not all --
        # both mean the leg did not fully square off, so both count against the reset reason.
        not_fully_placed = [r for r in leg_results if r["status"] in ("failed", "partial")]
        # A placement failure is one flavour of Reset: monitoring has stopped and the user
        # must re-arm. Any legs that DID get an order placed before the failure are now
        # live orphans -- they are NOT cancelled here (Reset withdraws future automation,
        # it does not retract orders already placed), so they surface in the Order Book
        # and in the SG's orphan warning.
        repo.mark_fire_failed(
            rule_id,
            leg_results,
            f"{len(not_fully_placed)} of {len(leg_results)} exit orders could not be fully placed.",
        )
        AuditLogger(None).log_operation(
            user_id,
            OperationType.SQUAREOFF_RULE_FIRE_FAILED,
            "PortfolioSquareOffRule",
            rule_id,
            action_status="failure",
            error_details=f"{len(not_fully_placed)}/{len(leg_results)} leg(s) not fully placed",
        )
        notify_squareoff_fired(user_id, reason=reason, payload=payload, leg_results=leg_results, failed=True)


def register_squareoff_dispatcher() -> None:
    register_rule_hit_listener(_handle_group_rule_hit)
