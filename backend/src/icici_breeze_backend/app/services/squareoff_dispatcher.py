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
from dataclasses import dataclass
from typing import Any, Callable

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import strike_key
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


def _current_leg_ltp(leg: dict[str, Any]) -> float | None:
    """Freshest cached LTP for this contract, read at dispatch time.

    One pipelined Redis read of the WS quote hash — never an ICICI call, so this
    stays clear of the broker's per-minute budget no matter how many legs a group
    holds. Returns None when the contract has no cached quote at all.
    """
    scrip_key = leg.get("scrip_key")
    if not scrip_key:
        return None
    try:
        from icici_breeze_backend.app.services.portfolio_pnl_engine import (
            _fetch_quotes,
            _parse_quote_fields,
        )

        ltp, _ts = _parse_quote_fields(_fetch_quotes([scrip_key]).get(scrip_key))
        return ltp if ltp and ltp > 0 else None
    except Exception:  # noqa: BLE001 — pricing must never fail the square-off
        _logger.debug("Could not re-read LTP for leg=%s", scrip_key, exc_info=True)
        return None


def _leg_limit_price(leg: dict[str, Any], *, reason: str, payload: dict[str, Any]) -> float:
    """Marketable limit price for a closing leg: a Buy is placed at a premium
    to LTP, a Sell at a discount, using whichever of the rule's two
    user-configured percentages matches why it fired (profit-booking vs
    stop-loss) — so the order is priced to fill without being a raw,
    unbounded-slippage MARKET order.

    The LTP is re-read here rather than taken from the snapshot that tripped the
    rule. Rules may fire on a quote up to `PNL_RULE_MAX_QUOTE_AGE_SECONDS` old —
    deliberately, so a stalled feed can't leave a stop-loss unarmed — but pricing a
    limit order off a two-minute-old quote is how that order sits unfilled. Deciding
    to exit on an old price and pricing the exit on the best price available are
    separate questions; this answers the second one.
    """
    pct = (
        payload["target_premium_pct"]
        if reason == "group_target_hit"
        else payload["stop_loss_premium_pct"]
    )
    ltp = _current_leg_ltp(leg)
    if ltp is None:
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
        if row["status"] == "triggered":
            # Only a dispatch that died with the process leaves this at startup (the
            # dispatcher is registered after hydration). Settle it; never re-arm or pin it.
            try:
                _recover_interrupted_dispatch(user_id, str(row["id"]))
            except Exception:  # noqa: BLE001 — one rule must not block the rest
                _logger.exception("Could not recover interrupted SG %s", row["id"])
            continue
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
                target_option_price=(
                    float(row["target_option_price"])
                    if row.get("target_option_price") is not None
                    else None
                ),
            )
        rule = repo.get_rule(str(row["id"]))
        if rule is not None:
            sg.pin_subscription(user_id, rule)


def _recover_interrupted_dispatch(user_id: str, rule_id: str) -> None:
    """Settle an SG the app stopped in the middle of firing (B-13): it restarted, or was
    killed, while the exit orders were going out.

    The SG is Reset, never re-fired. Prices have moved since it tripped, and the user may
    have acted meanwhile; sending the rest of the exits minutes later, unattended, is a
    new trade nobody chose. What was sent is kept on the row so the orphan warning and bulk
    cancel can act on it. An order that went out with no answer is looked up in the order
    book first, so it is not lost from that list.
    """
    from icici_breeze_backend.app.services import portfolio_pnl_engine
    from icici_breeze_backend.app.services import strategy_group_lifecycle as sg
    from icici_breeze_backend.app.services.bots.scalping import order_intents
    from icici_breeze_backend.app.services.telegram_alerts import notify_squareoff_reset

    rule = repo.get_rule(rule_id)
    if rule is None or rule.status != "triggered":
        return
    records = repo.exit_order_record(rule_id)
    breeze = processor()
    unanswered = 0
    for rec in records:
        rec.setdefault("order_ids", [])
        sending = rec.pop("sending", None)
        if rec.get("status") != "placing":
            continue  # this leg had finished before the stop
        if isinstance(sending, dict):
            intent = _intent(
                {**rec, "exchange_code": rule.exchange_code, "expiry_display": rule.expiry_display},
                int(sending.get("quantity") or 0),
                float(sending.get("price") or 0),
                float(sending.get("sent_at") or 0),
                str(sending.get("tag") or "") or None,
            )
            claimed = {oid for r in records for oid in r.get("order_ids") or []}
            found = order_intents.locate_order(breeze, user_id, intent, claimed=claimed)
            if found.state == "found" and found.order_id:
                rec["order_ids"].append(str(found.order_id))
                rec["placed_quantity"] = int(rec.get("placed_quantity") or 0) + intent["quantity"]
            elif found.state != "absent":
                unanswered += 1
        placed = int(rec.get("placed_quantity") or 0)
        if rec["order_ids"] and placed >= int(float(rec.get("quantity") or 0)):
            rec["status"] = "success"
        else:
            rec["status"] = "partial" if rec["order_ids"] else "failed"
            rec["error"] = "The app stopped before this leg's exit orders were all sent."

    reason = (
        "the app restarted while it was placing this group's exit orders, so it stopped "
        "and sent nothing more."
    )
    if not records:
        reason += " It has no record of which orders went out — check the Order Book."
    elif unanswered:
        reason += (
            f" The broker's answer to {unanswered} exit order(s) was lost — check the "
            f"Order Book before placing anything yourself."
        )
    if not repo.mark_fire_failed(rule_id, records, reason):
        return
    _logger.warning("SG %s was left mid-dispatch by a restart; reset: %s", rule_id, reason)

    after = repo.get_rule(rule_id) or rule
    orphans: list[Any] = []
    order_ids = repo.order_ids_for_rule(after)
    if order_ids:
        statuses = sg._order_statuses(
            breeze, user_id, order_ids, exchanges=[after.exchange_code] if after.exchange_code else None
        )
        if statuses:
            open_keys = {
                leg.scrip_key
                for leg in portfolio_pnl_engine.group_legs_for_user(
                    user_id, after.stock_code, after.expiry_display
                )
            }
            orphans = sg.live_orphans(breeze, user_id, after, open_keys, status_by_id=statuses)
        else:
            # Without the book, the alert's "no exit orders are outstanding" would be a guess.
            reason += " Its exit orders could not be checked — look at the Order Book."
    try:
        notify_squareoff_reset(user_id, after, reason, orphans)
    except Exception:  # noqa: BLE001
        _logger.exception("Telegram reset alert failed for SG %s", rule_id)


# ~50s of patience, against ICICI's minute-scale throttle cooldown. The old behaviour gave
# up after the transport layer's ~4s of backoff, which is an order of magnitude short — a
# leg that could have filled 20 seconds later was abandoned, leaving the position half
# unwound with no automatic second chance (the rule is popped from the registry before
# dispatch, so no later tick re-evaluates it).
_RETRY_DELAYS_SEC = (5.0, 10.0, 15.0, 20.0)


def _is_retryable_throttle(response: Any) -> bool:
    """Only an explicit ICICI throttle is retryable.

    Deliberately narrow. A throttle is a *refusal*: ICICI tells us it did not accept the
    order, so re-sending cannot duplicate it. A timeout, a transport error or a 503 carries
    no such guarantee — the order may well have reached the exchange — so those are marked
    `outcome_unknown` and looked up in the order book instead (`_look_up_lost_order`),
    never re-sent.

    A day-limit exhaustion is a throttle we also refuse to retry: it will not clear before
    midnight IST, so waiting 50s only delays the bad news.
    """
    if not isinstance(response, dict):
        return False
    if response.get("outcome_unknown"):
        return False
    if response.get("daily_limit_exhausted"):
        return False
    if response.get("advisory_shed"):
        return False  # never applies to order placement, but be explicit
    if response.get("icici_throttled"):
        return True
    return is_breeze_rate_limited(response.get("Status"), response.get("Error"))


def _contract(leg: dict[str, Any]) -> tuple[str, str]:
    """(strike key, "call"/"put") — how an order notification names the same contract."""
    right = "call" if str(leg.get("right") or "").strip().lower().startswith("c") else "put"
    return strike_key(leg.get("strike_price")) or "", right


def _closes_short(leg: dict[str, Any]) -> bool:
    """A closing Buy: this leg is a short being bought back."""
    return str(leg.get("action") or "").strip().lower() == cfg.BUY.lower()


def _leg_label(leg: dict[str, Any]) -> str:
    strike, right = _contract(leg)
    return f"{leg.get('stock_code', '')} {strike} {'CE' if right == 'call' else 'PE'}".strip()


class _Dispatch:
    """One fire's exit orders, saved on the rule row as they go out (B-03, B-13).

    `records` is the `leg_results` list the final write stores. While the rule is
    `triggered` each record is `placing`, carries every order id the moment ICICI returns
    it, and carries `sending` for the one order that is out with no answer yet — which is
    what a restart needs to look that order up (`_recover_interrupted_dispatch`).
    """

    def __init__(self, rule_id: str, legs: list[dict[str, Any]]) -> None:
        self.rule_id = rule_id
        self.records: list[dict[str, Any]] = [
            {
                # Strings, as `SquareOffRuleLegResult` reads them back: the rule is re-read
                # before every send, and a row that fails to parse would blind that check.
                "scrip_key": str(leg["scrip_key"]),
                "stock_code": str(leg["stock_code"]),
                "strike_price": str(leg["strike_price"]),
                "right": str(leg["right"]),
                "quantity": str(leg["quantity"]),
                "status": "placing",
                "error": None,
                "order_ids": [],
                "action": leg["action"],
                "price": None,
                "placed_quantity": 0,
            }
            for leg in legs
        ]
        self.errors: list[list[str]] = [[] for _ in legs]
        # Contracts with an order whose answer was lost and which the book could not find.
        self.unanswered: list[tuple[str, str]] = []
        self.stand_down: str | None = None

    def our_ids(self) -> set[str]:
        return {oid for rec in self.records for oid in rec["order_ids"]}

    def save(self) -> None:
        """Best-effort. A failed save must not stop an exit: the in-memory record still
        drives this dispatch and its final write; only a crash would miss it."""
        try:
            repo.record_exit_orders(self.rule_id, self.records)
        except Exception:  # noqa: BLE001
            _logger.exception("Could not save exit orders for rule_id=%s", self.rule_id)

    def sending(self, index: int, quantity: int, price: float, sent_at: float, tag: str) -> None:
        self.records[index]["sending"] = {
            "quantity": int(quantity), "price": float(price), "sent_at": float(sent_at),
            "tag": tag,
        }
        self.save()

    def answered(self, index: int, quantity: int, order_id: str | None, price: float) -> None:
        rec = self.records[index]
        rec.pop("sending", None)
        if order_id:
            rec["order_ids"].append(str(order_id))
            rec["placed_quantity"] += int(quantity)
            rec["price"] = str(price)
        self.save()

    def settle_leg(self, index: int) -> None:
        rec, errors = self.records[index], self.errors[index]
        rec.pop("sending", None)
        if rec["order_ids"] and not errors:
            rec["status"] = "success"
        elif rec["order_ids"]:
            rec["status"] = "partial"
        else:
            rec["status"] = "failed"
        rec["error"] = "; ".join(errors) if errors else None

    def results(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in rec.items() if k != "sending"} for rec in self.records]


def _stand_down_reason(dispatch: _Dispatch) -> str | None:
    """Why this dispatch must stop sending, or None while its exits are still wanted.

    Asked before every order, not only before a throttle retry. Two things end it: the rule
    stopped being `triggered`, or a fill arrived that none of our exit orders explains —
    the user trading the group while its exits go out (`held_fill_not_ours`). Standing down
    is what makes a long retry safe: the user can act during the wait and we stop instead
    of racing them into a contra position.
    """
    from icici_breeze_backend.app.services import strategy_group_lifecycle as sg

    try:
        rule = repo.get_rule(dispatch.rule_id)
    except Exception:  # noqa: BLE001 — a DB blip must not silently abandon an exit
        _logger.exception("Could not re-read rule %s mid-dispatch; continuing", dispatch.rule_id)
        rule = None
    else:
        if rule is None or rule.status != "triggered":
            return (
                "this rule was reset while its exit orders were going out (the position "
                "was acted on elsewhere)."
            )
    foreign = sg.held_fill_not_ours(dispatch.rule_id, dispatch.our_ids(), dispatch.unanswered)
    if foreign is not None:
        return sg.reason_manual_intervention(sg._fmt_leg(foreign.stock_code, foreign.strike, foreign.right))
    return None


@dataclass
class _Chunk:
    order_id: str | None = None
    error: str | None = None
    stood_down: bool = False


def _look_up_lost_order(
    breeze,
    *,
    user_id: str,
    dispatch: _Dispatch,
    leg: dict[str, Any],
    chunk_qty: int,
    limit_price: float,
    sent_at: float,
    tag: str,
    error: str,
) -> _Chunk:
    """An order whose answer said nothing — the SDK raised, the body was garbled, a 200
    came without an id, or the gateway answered 503 — may still have been accepted.
    Re-sending it could double the exit into a contra position, and forgetting it would
    leave its fill unexplained (B-03 by another route). So the day's order book is read
    once, exactly as the bots do (#54)."""
    from icici_breeze_backend.app.services.bots.scalping import order_intents

    time.sleep(order_intents.LOCATE_SETTLE_SECONDS)
    found = order_intents.locate_order(
        breeze, user_id, _intent(leg, chunk_qty, limit_price, sent_at, tag), claimed=dispatch.our_ids()
    )
    if found.state == "found" and found.order_id:
        _logger.warning(
            "Exit leg for rule_id=%s gave no usable answer, but order %s is in the order book",
            dispatch.rule_id,
            found.order_id,
        )
        return _Chunk(order_id=str(found.order_id))
    if found.state == "absent":
        return _Chunk(error=f"{error} The order book shows it was not placed.")
    dispatch.unanswered.append(_contract(leg))
    return _Chunk(
        error=(
            f"{error} The order book could not say whether it was placed ({found.why}) — "
            f"check the Order Book before placing anything yourself."
        )
    )


def _intent(
    leg: dict[str, Any], quantity: int, price: float, sent_at: float, tag: str | None = None
) -> dict[str, Any]:
    return {
        "tag": tag,
        "stock_code": str(leg["stock_code"]),
        "exchange_code": str(leg.get("exchange_code") or cfg.NFO),
        "right": str(leg["right"]),
        "strike_price": float(leg["strike_price"]),
        "expiry_display": str(leg["expiry_display"]),
        "action": str(leg["action"]),
        "quantity": int(quantity),
        "price": float(price),
        "sent_at": float(sent_at),
    }


def _place_chunk_with_retry(
    breeze,
    *,
    user_id: str,
    dispatch: _Dispatch,
    index: int,
    leg: dict[str, Any],
    chunk_qty: int,
    limit_price: float,
    on_first_retry: Callable[[], None],
) -> _Chunk:
    """Place one chunk, retrying only through ICICI throttles."""
    from icici_breeze_backend.app.services.bots.scalping import order_intents

    last_error: str | None = None
    notified = False
    for attempt in range(len(_RETRY_DELAYS_SEC) + 1):
        if attempt:
            time.sleep(_RETRY_DELAYS_SEC[attempt - 1])
        why = _stand_down_reason(dispatch)
        if why:
            dispatch.stand_down = dispatch.stand_down or why
            return _Chunk(error=f"Not sent — {why}", stood_down=True)
        sent_at = time.time()
        # A fresh tag per send: it goes out as the order's `user_remark`, and is how the
        # order is found in the book if its answer is lost.
        tag = order_intents.new_tag()
        dispatch.sending(index, chunk_qty, limit_price, sent_at, tag)
        try:
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
                user_remark=tag,
            )
        except Exception as exc:  # noqa: BLE001 — the request may still have reached ICICI
            _logger.exception("Exit order placement raised for rule_id=%s", dispatch.rule_id)
            response = {
                "Status": None,
                "Error": f"Broker call failed while placing: {exc}",
                "outcome_unknown": True,
            }
        if isinstance(response, dict) and response.get("Status") == 200:
            order_id = (response.get("Success") or {}).get("order_id")
            if order_id:
                return _Chunk(order_id=str(order_id))
            response = {**response, "Error": "Broker did not return an order id.", "outcome_unknown": True}

        last_error = str((response or {}).get("Error") or "Broker rejected the order")
        if isinstance(response, dict) and response.get("outcome_unknown"):
            return _look_up_lost_order(
                breeze,
                user_id=user_id,
                dispatch=dispatch,
                leg=leg,
                chunk_qty=chunk_qty,
                limit_price=limit_price,
                sent_at=sent_at,
                tag=tag,
                error=last_error,
            )
        if not _is_retryable_throttle(response):
            return _Chunk(error=last_error)
        if not notified:
            notified = True
            on_first_retry()
        _logger.warning(
            "Exit leg throttled for rule_id=%s leg=%s; retry %d/%d",
            dispatch.rule_id,
            leg.get("scrip_key"),
            attempt + 1,
            len(_RETRY_DELAYS_SEC),
        )
    return _Chunk(error=last_error)


def _shorts_first(legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Buy back shorts before selling longs (B-14). If the sends stop partway — a
    rejection, a throttle that never clears, a stand-down — what is left open is then a
    long, or a hedged pair, never a short whose wing has already been sold."""
    return sorted(legs, key=lambda leg: 0 if _closes_short(leg) else 1)


def _short_left_open(dispatch: _Dispatch, legs: list[dict[str, Any]], index: int) -> str | None:
    """For a long being sold: the label of a same-right short in this group whose buy-back
    was not fully placed. Selling the wing then would leave that short naked, so the wing
    is held back — the same rule the bots close by (#53)."""
    leg = legs[index]
    if _closes_short(leg):
        return None
    _, right = _contract(leg)
    for other, rec in zip(legs, dispatch.records):
        if _closes_short(other) and _contract(other)[1] == right and rec["status"] != "success":
            return _leg_label(other)
    return None


def _handle_group_rule_hit(payload: dict[str, Any]) -> None:
    from icici_breeze_backend.app.services import strategy_group_lifecycle as sg

    reason = payload.get("reason")
    if reason not in _GROUP_REASONS:
        return

    user_id = str(payload["user_id"])
    rule_id = str(payload["rule_id"])
    legs = _shorts_first(list(payload.get("legs") or []))

    # Short-lived marker: this poll cycle detected the breach and is about to dispatch
    # close orders. While it holds, order notifications for this group are held rather
    # than judged (`strategy_group_lifecycle.finish_dispatch`).
    if not repo.mark_triggered(rule_id):
        _logger.warning(
            "Rule %s was not armed when its breach was dispatched; no exits sent", rule_id
        )
        return

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
        written, _held = sg.finish_dispatch(
            user_id,
            rule_id,
            lambda: repo.mark_fire_failed(
                rule_id,
                leg_results,
                "trading is in read-only mode (licence not active), so no exit orders "
                "could be placed.",
            ),
        )
        if written:
            sg.release_subscription(rule_id)
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
    dispatch = _Dispatch(rule_id, legs)
    dispatch.save()

    announced = {"retry": False}

    def _announce_retry() -> None:
        """Tell the user we are retrying, the moment it starts — not 50s later.

        Without this the only signal is the final alert, so a user watching an exit not
        appear has every reason to go place it themselves on ICICI's app. Saying we are
        retrying *and* that we will stand down if they act is what makes the wait
        tolerable; the stand-down is real (`_stand_down_reason`), so this is a promise the
        code actually keeps.
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

    for index, leg in enumerate(legs):
        errors = dispatch.errors[index]
        if dispatch.stand_down:
            errors.append(f"Not sent — {dispatch.stand_down}")
            dispatch.settle_leg(index)
            continue
        open_short = _short_left_open(dispatch, legs, index)
        if open_short:
            errors.append(
                f"Held back — the buy-back of {open_short} did not go through, and selling "
                f"this wing would leave that short naked. Close them together yourself."
            )
            dispatch.settle_leg(index)
            continue
        try:
            limit_price = _leg_limit_price(leg, reason=reason, payload=payload)
            qty_per_order = _leg_qty_per_order(breeze, leg)
            for chunk_qty in _split_into_chunks(int(leg["quantity"]), qty_per_order):
                chunk = _place_chunk_with_retry(
                    breeze,
                    user_id=user_id,
                    dispatch=dispatch,
                    index=index,
                    leg=leg,
                    chunk_qty=chunk_qty,
                    limit_price=limit_price,
                    on_first_retry=_announce_retry,
                )
                dispatch.answered(index, chunk_qty, chunk.order_id, limit_price)
                if not chunk.order_id:
                    errors.append(chunk.error or "Broker rejected the order")
                if chunk.stood_down:
                    break
        except Exception as exc:  # defensive: one leg's failure must not stop the rest
            _logger.exception(
                "Square-off order placement raised for rule_id=%s leg=%s", rule_id, leg.get("scrip_key")
            )
            errors.append(str(exc))
        dispatch.settle_leg(index)

    leg_results = dispatch.results()
    all_ok = not dispatch.stand_down and all(r["status"] == "success" for r in leg_results)

    if all_ok:
        written, held = sg.finish_dispatch(
            user_id, rule_id, lambda: repo.mark_fired(rule_id, leg_results)
        )
        if not written:
            _logger.warning("Rule %s was no longer triggered when its exits finished", rule_id)
        AuditLogger(None).log_operation(
            user_id,
            OperationType.SQUAREOFF_RULE_FIRED,
            "PortfolioSquareOffRule",
            rule_id,
            action_status="success",
            error_details=f"reason={reason} total_pnl={payload.get('total_pnl')}",
        )
        notify_squareoff_fired(user_id, reason=reason, payload=payload, leg_results=leg_results, failed=False)
        # Only now, with every exit order's id on the row, is it safe to judge what the
        # order feed reported while they were going out.
        sg.replay_held(user_id, rule_id, held)
        return

    # "failed" legs placed zero chunks; "partial" legs placed some but not all --
    # both mean the leg did not fully square off, so both count against the reset reason.
    not_fully_placed = [r for r in leg_results if r["status"] in ("failed", "partial")]
    if dispatch.stand_down:
        fail_reason = dispatch.stand_down
    else:
        fail_reason = (
            f"{len(not_fully_placed)} of {len(leg_results)} exit orders could not be fully placed."
        )
    if dispatch.unanswered:
        fail_reason += (
            f" The broker's answer to {len(dispatch.unanswered)} exit order(s) was lost, so "
            f"check the Order Book before placing anything yourself."
        )
    # A placement failure is one flavour of Reset: monitoring has stopped and the user
    # must re-arm. Any legs that DID get an order placed before the failure are now
    # live orphans -- they are NOT cancelled here (Reset withdraws future automation,
    # it does not retract orders already placed), so they surface in the Order Book
    # and in the SG's orphan warning.
    written, _held = sg.finish_dispatch(
        user_id, rule_id, lambda: repo.mark_fire_failed(rule_id, leg_results, fail_reason)
    )
    if written:
        sg.release_subscription(rule_id)
    else:
        _logger.warning("Rule %s was no longer triggered when its exits finished", rule_id)
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
