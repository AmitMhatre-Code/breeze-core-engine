"""Strategy Group (SG) state machine.

An SG is every open leg for one (user_id, stock_code, expiry_display). Its lifecycle:

    Active ──arm──► Armed ──breach──► Fired ──all exits executed──► Completed
       ▲              │                 │
       │              └─composition─────┴─manual intervention──► Reset
       │                changed                 / order failed     │
       └──────────────────── user re-arms (a NEW SG) ──────────────┘

The one hard problem this module exists to solve
------------------------------------------------
Spec section 6 says Fired persists until every exit order finishes; sections 9/10 say a
composition change Resets the SG. Those collide: when a Fired SG's OWN exit orders fill,
legs vanish — read literally, section 9 would Reset the SG at the exact moment it should
be reaching Completed.

The discriminator is order identity. An execution whose `orderReference` is in the SG's
stored `order_ids` is *our* exit working as intended; one that isn't is the user trading
elsewhere. This is why `orderReference == REST order_id` is load-bearing: without it
there is no principled way to tell "our exit filled" from "user squared off manually",
and the section 6 / section 10 boundary collapses into guesswork.

Consequences that follow, and are easy to get wrong later:
  * While `fired`, NEVER diff positions against `legs_snapshot` — our own exits
    legitimately change legs, and a snapshot diff cannot attribute a fill. Use order
    identity only. Drift-diffing is safe only while `armed`.
  * Reset does NOT cancel outstanding exit orders. It withdraws future automation; it
    does not retract actions already taken. Auto-cancelling would, in the stop-loss case,
    kill three working exits because a fourth leg was rejected — leaving the user
    unprotected in a moving market. Orphans surface in the Order Book plus an explicit
    bulk-cancel action.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from icici_breeze_backend.app.core.strike import strike_key
from icici_breeze_backend.app.domain.squareoff_rule import (
    ResetHazardTier,
    SquareOffRuleOrphanOrder,
    SquareOffRuleRecord,
)
from icici_breeze_backend.app.repositories import squareoff_rules as repo
from icici_breeze_backend.app.services import portfolio_pnl_engine
from icici_breeze_backend.app.services.order_notifications import OrderNotification

_logger = logging.getLogger(__name__)

# Non-terminal broker statuses, lowercased. An order in one of these is still live at the
# exchange and can still fill.
LIVE_ORDER_STATUSES = {"requested", "queued", "ordered", "partially executed", "freezed"}


def _fmt_leg(stock_code: str, strike: Any, right: str) -> str:
    """"NIFTY 26000 CE" — how the user thinks of a leg. Reset reasons must name the leg
    in the user's own terms, never dump internals like a scrip_key.

    `strike_key` rather than str(): Strike is a bare float, so str() would render
    "26000.0". strike_key is the canonical form already used for keys/wire format.
    """
    r = "CE" if str(right or "").strip().lower().startswith("c") else "PE"
    return f"{stock_code} {strike_key(strike) or strike} {r}".strip()


# ---------------------------------------------------------------- reasons (user-facing)

def reason_manual_intervention(leg_label: str) -> str:
    return f"you traded {leg_label} outside this rule, so the group no longer matches what was armed."


def reason_composition_changed(detail: str) -> str:
    return detail


def reason_order_failed(leg_label: str, status: str) -> str:
    verb = {
        "cancelled": "was cancelled",
        "rejected": "was rejected by the broker",
        "expired": "expired at market close",
        "partially executed and cancelled": "only partly filled before being cancelled",
        "partially executed and expired": "only partly filled before expiring",
    }.get(status, f"ended as {status}")
    return f"the exit order for {leg_label} {verb}."


# ------------------------------------------------------------------- drift (section 9)

def snapshot_from_legs(legs: Iterable[Any]) -> dict[str, int]:
    """scrip_key -> quantity for the SG's currently-open legs."""
    return {leg.scrip_key: int(leg.quantity) for leg in legs}


def describe_drift(before: dict[str, int], after: dict[str, int]) -> str | None:
    """Human description of a composition change, or None if identical.

    Quantity-sensitive on purpose: the thresholds were set against a specific exposure,
    so a partial add/reduce makes the configured numbers mean something the user never
    agreed to. Any qty change invalidates.
    """
    if before == after:
        return None
    added = [k for k in after if k not in before]
    removed = [k for k in before if k not in after]
    resized = [k for k in after if k in before and after[k] != before[k]]
    if added:
        return "a new leg was added to this group."
    if removed:
        return "a leg in this group was closed."
    if resized:
        return "the quantity on a leg in this group changed."
    return "this group changed."


def check_armed_drift(user_id: str, rule: SquareOffRuleRecord) -> str | None:
    """Reset reason if an ARMED SG's composition drifted, else None.

    Only valid while `armed`/`triggered`. Never call this on a `fired` SG — its own exit
    fills change legs by design, and a position diff cannot tell whose fill it was.
    """
    if rule.status not in ("armed", "triggered"):
        return None
    if not rule.legs_snapshot:
        return None
    legs = portfolio_pnl_engine.group_legs_for_user(
        user_id, rule.stock_code, rule.expiry_display
    )
    if not legs:
        # Registry not warmed yet (no portfolio fetch this session) is indistinguishable
        # from "everything closed". Staying silent is the fail-safe read: a spurious Reset
        # would tear down real protection the user is relying on.
        return None
    return describe_drift(rule.legs_snapshot, snapshot_from_legs(legs))


# ------------------------------------------------- order notifications (sections 6/8/10)

def on_order_notification(n: OrderNotification) -> None:
    """Route one normalized order event into the SG state machine. Best-effort: this runs
    on the SDK's WS callback thread, so it must never raise."""
    try:
        rule = repo.get_active_rule_for_group(n.user_id, n.stock_code, n.expiry_display)
        if rule is None:
            return  # no live SG for this contract's group — nothing to do
        if n.order_id in repo.order_ids_for_rule(rule):
            _handle_own_exit_order(n.user_id, rule, n)
        else:
            _handle_foreign_order(n.user_id, rule, n)
    except Exception:  # noqa: BLE001 — must not break the shared tick callback
        _logger.exception("SG lifecycle failed for order_id=%s", n.order_id)


def _handle_own_exit_order(
    user_id: str, rule: SquareOffRuleRecord, n: OrderNotification
) -> None:
    """An order this SG placed. Its fills are expected and drive Completed — they must
    never be mistaken for the manual intervention of section 10."""
    if rule.status != "fired":
        return
    label = _fmt_leg(n.stock_code, n.strike, n.right)

    if n.is_terminal_failure:
        # Spec section 8: cancelled / rejected / expired / failed. The two partial-fill
        # terminals land here on their own terms (they contain a cancel/expiry) and leave
        # residual open quantity, which the orphan warning surfaces.
        _reset(user_id, rule, reason_order_failed(label, n.status))
        return

    if not n.is_terminal_success:
        # Requested/Queued/Ordered/Partially Executed/Freezed. Fired is a patient state:
        # a resting exit order keeps the SG waiting rather than resolving it. This
        # self-resolves at EOD — an unfilled Day order goes Expired, which IS a Reset.
        return

    _maybe_complete(user_id, rule)


def _maybe_complete(
    user_id: str,
    rule: SquareOffRuleRecord,
    *,
    status_by_id: dict[str, str] | None = None,
) -> None:
    """Completed requires BOTH conditions of spec section 7: every exit order executed,
    and no open positions left in the group.

    `status_by_id` lets a caller that already holds the order book (order_id -> lowercased
    status) pass it in so this doesn't issue a second `get_orders` round-trip — used by the
    REST reconcile path, which reconciles many fired SGs off one already-fetched book. When
    omitted, the status is read fresh from the book (the live WS-callback path)."""
    if _open_leg_count(user_id, rule) > 0:
        return
    order_ids = repo.order_ids_for_rule(rule)
    if not order_ids:
        return
    if status_by_id is not None:
        # Mirror `_order_statuses`: only include order_ids actually present in the book, so
        # `all(...)` has identical semantics on both paths.
        statuses = {oid: status_by_id[oid] for oid in order_ids if oid in status_by_id}
    else:
        from icici_breeze_backend.app.services.processor import processor

        statuses = _order_statuses(processor(), user_id, order_ids)
    if not statuses:
        return  # couldn't read the book — leave Fired; reconcile will retry
    if all(s == "executed" for s in statuses.values()):
        if repo.mark_completed(rule.id):
            _release_subscription(rule.id)
            _logger.info("SG %s completed", rule.id)


def reconcile_fired_rules_from_orders(
    user_id: str, raw_orders: Iterable[dict[str, Any]]
) -> None:
    """REST backstop for the fired→Completed transition, driven from an order book the
    caller already fetched (no extra ICICI round-trip).

    The WS order feed drives Completed in real time (`on_order_notification`), but a
    dropped or late notification would strand a fully-exited SG on `fired` forever — the
    Orders page would show every exit order Executed while the SG badge stayed Fired. This
    re-runs the same guarded completion check from authoritative REST state, so a missed
    event self-heals on the next book fetch.

    Deliberately completion-only. A `fired` SG must NEVER be Reset from a position diff
    (see module docstring): its own exits legitimately change legs, so a REST snapshot
    cannot attribute a change. `_maybe_complete`'s open-leg guard already keeps a leftover
    leg (e.g. a manual add whose foreign-fill WS event was itself missed) from producing a
    *false* Completed — the fail-safe outcome there is to stay Fired, not to guess Reset.
    Best-effort: never raises, so it can't break the book fetch it hangs off.
    """
    try:
        status_by_id = {
            oid: str(o.get("status") or "").strip().lower()
            for o in raw_orders
            if isinstance(o, dict)
            for oid in (str(o.get("order_id") or ""),)
            if oid
        }
        for rule in repo.list_fired_rules_for_user(user_id):
            _maybe_complete(user_id, rule, status_by_id=status_by_id)
    except Exception:  # noqa: BLE001 — reconcile is best-effort; never break the book fetch
        _logger.exception("Fired-SG reconcile failed for user_id=%s", user_id)


def _handle_foreign_order(
    user_id: str, rule: SquareOffRuleRecord, n: OrderNotification
) -> None:
    """An order this SG did NOT place — the user trading from anywhere (this app, the
    ICICI website, the mobile app; `channel` is not a reliable discriminator, order
    identity is).

    Only a *fill* counts. A merely resting manual order changes no composition until it
    executes, so it must not tear down a rule the user is still relying on.
    """
    if not n.moved_position:
        return
    label = _fmt_leg(n.stock_code, n.strike, n.right)
    _reset(user_id, rule, reason_manual_intervention(label))


def _reset(user_id: str, rule: SquareOffRuleRecord, reason: str) -> None:
    if not repo.mark_reset(rule.id, reason):
        return  # already resolved by a concurrent event
    _release_subscription(rule.id)
    _logger.info("SG %s reset: %s", rule.id, reason)
    try:
        from icici_breeze_backend.app.services.telegram_alerts import notify_squareoff_reset

        notify_squareoff_reset(user_id, rule, reason)
    except Exception:  # noqa: BLE001
        _logger.exception("Telegram reset alert failed for SG %s", rule.id)


def reset_rule(user_id: str, rule: SquareOffRuleRecord, reason: str) -> None:
    """Public entry point for Reset from outside the WS path (drift detection, the arm
    route). Same side effects: release the pin, alert the user."""
    _reset(user_id, rule, reason)


# ------------------------------------------------------------- subscription pinning

def _sg_holder_id(rule_id: str) -> str:
    """A server-side WS subscription holder owned by the SG itself, independent of any
    browser. Without this the chain goes cold the moment the user closes their tab, the
    quote cache goes stale, and PB/SL silently stops protecting."""
    return f"sg:{rule_id}"


def pin_subscription(user_id: str, rule: SquareOffRuleRecord) -> None:
    """Hold a WS chain subscription for as long as this SG is live, independent of any
    browser session. Nothing else in the app does this — every other holder is created
    from a frontend request — so without it an armed SG stops being protected the moment
    the user closes their tab, and a restarted instance holds armed SGs that physically
    cannot fire."""
    try:
        from icici_breeze_backend.app.services import breeze_websocket_manager as ws
        from icici_breeze_backend.app.services.processor import processor

        ws.sync_holder_chain_subscriptions(
            processor(),
            user_id,
            _sg_holder_id(rule.id),
            rule.stock_code,
            rule.exchange_code,
            rule.expiry_display,
        )
    except Exception:  # noqa: BLE001 — never block arming on a WS hiccup
        _logger.exception("Could not pin WS subscription for SG %s", rule.id)


def release_subscription(rule_id: str) -> None:
    """Drop this SG's WS pin. Called on every terminal transition (completed / reset /
    disarmed) — a pin outliving its SG would keep a chain hot for nothing."""
    try:
        from icici_breeze_backend.app.services.breeze_websocket_manager import release_holder

        release_holder(_sg_holder_id(rule_id))
    except Exception:  # noqa: BLE001
        _logger.debug("Could not release WS subscription for SG %s", rule_id, exc_info=True)


_release_subscription = release_subscription  # internal alias


# ------------------------------------------------------ orphans / hazard tier (5.13)

def _open_leg_count(user_id: str, rule: SquareOffRuleRecord) -> int:
    return len(
        portfolio_pnl_engine.group_legs_for_user(
            user_id, rule.stock_code, rule.expiry_display
        )
    )


def _order_statuses(breeze, user_id: str, order_ids: set[str]) -> dict[str, str]:
    """order_id -> lowercased broker status, read from the REST order book.

    REST-authoritative on purpose: a dropped WS notification must never be able to make
    an orphaned order *look* terminal, because that would unblock re-arming and let a
    second exit order stack on top of a live one.
    """
    from icici_breeze_backend.app.services.strategy_group_arm_guard import today_order_window

    try:
        start, end = today_order_window()
        resp = breeze.get_orders(user_id, start=start, end=end)
    except Exception:  # noqa: BLE001
        _logger.exception("Could not read order book for user_id=%s", user_id)
        return {}
    if not isinstance(resp, dict) or resp.get("Status") != 200:
        return {}
    rows = resp.get("Success")
    if not isinstance(rows, list):
        return {}
    out: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        oid = str(row.get("order_id") or "")
        if oid in order_ids:
            out[oid] = str(row.get("status") or "").strip().lower()
    return out


def live_orphans(
    breeze, user_id: str, rule: SquareOffRuleRecord, open_leg_keys: set[str]
) -> list[SquareOffRuleOrphanOrder]:
    """This SG's exit orders that are still live at the exchange.

    `opens_contra_position` is the sharp one: the order was placed to CLOSE a leg, but
    that leg is gone (the user closed it manually in the interim). If it fills it no
    longer closes anything — it opens a brand-new position the user never asked for.
    """
    order_ids = repo.order_ids_for_rule(rule)
    if not order_ids:
        return []
    statuses = _order_statuses(breeze, user_id, order_ids)
    out: list[SquareOffRuleOrphanOrder] = []
    for leg in rule.leg_results or []:
        for oid in leg.order_ids:
            if statuses.get(oid) not in LIVE_ORDER_STATUSES:
                continue
            out.append(
                SquareOffRuleOrphanOrder(
                    order_id=oid,
                    stock_code=leg.stock_code,
                    strike_price=leg.strike_price,
                    right=leg.right,
                    action=leg.action or "",
                    quantity=leg.quantity,
                    price=leg.price,
                    opens_contra_position=leg.scrip_key not in open_leg_keys,
                )
            )
    return out


def hazard_tier(orphans: list[SquareOffRuleOrphanOrder]) -> ResetHazardTier:
    """Tier by what is actually at stake, not by state.

    Tier 3 is not a louder tier 2 — different verb, different stake. Tier 2 says "your
    exit may still complete"; tier 3 says "you may accidentally open a trade". Merging
    them is how the dangerous one gets skimmed past.
    """
    if not orphans:
        return "settled"
    if any(o.opens_contra_position for o in orphans):
        return "contra_risk"
    return "orders_live"


def attach_reset_details(user_id: str, breeze, rules: list[SquareOffRuleRecord]) -> None:
    """Populate the derived hazard fields on every `reset` SG.

    Derived server-side, never stored: the order book and the position registry already
    live here, and computing it in the frontend would duplicate the join and invent a
    second opinion about the same facts.
    """
    for rule in rules:
        if rule.status != "reset":
            continue
        open_keys = {
            leg.scrip_key
            for leg in portfolio_pnl_engine.group_legs_for_user(
                user_id, rule.stock_code, rule.expiry_display
            )
        }
        orphans = live_orphans(breeze, user_id, rule, open_keys)
        rule.orphan_orders = orphans
        rule.hazard_tier = hazard_tier(orphans)
        # Same condition gates re-arming and dismissal: a live orphan means a re-arm
        # would stack a duplicate exit order, and dismissing would hide real risk.
        rule.rearm_blocked = bool(orphans)
