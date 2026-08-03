"""Fire-and-forget Telegram alert on a group square-off rule fire.

`_handle_group_rule_hit` (the caller) runs inside a worker thread — `run_pnl_tick`
is invoked via `asyncio.to_thread` from `run_pnl_loop`, so there is no running
event loop on that thread to hand an `asyncio.create_task` to. A plain daemon
thread is used instead of `asyncio.run_coroutine_threadsafe` so this never
blocks order placement and doesn't need a reference to the main event loop.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from icici_breeze_backend.app.repositories.user_telegram import get_status
from icici_breeze_backend.app.services.telegram_client import send_message_sync

logger = logging.getLogger(__name__)

_REASON_LABEL = {
    "group_target_hit": ("🎯", "Profit Booking Triggered"),
    "group_stop_loss_hit": ("🛑", "Stop-Loss Triggered"),
}


def _format_message(
    reason: str,
    payload: dict[str, Any],
    leg_results: list[dict[str, Any]],
    *,
    failed: bool,
) -> str:
    emoji, label = _REASON_LABEL.get(reason, ("🔔", "Square-Off Rule Fired"))
    stock_code = payload.get("stock_code", "")
    expiry_display = payload.get("expiry_display", "")
    lines = [f"{emoji} *{label}* — {stock_code} · {expiry_display}"]

    for leg in leg_results:
        ok = leg.get("status") == "success"
        mark = "✅" if ok else "❌"
        action = leg.get("action", "")
        right = leg.get("right", "")
        strike = leg.get("strike_price", "")
        qty = leg.get("quantity", "")
        if ok:
            price = leg.get("price", "")
            lines.append(f"{mark} {action} {stock_code} {strike} {right} ×{qty} @ ₹{price} — Filled")
        else:
            error = leg.get("error") or "Order rejected"
            lines.append(f"{mark} {action} {stock_code} {strike} {right} ×{qty} — {error}")

    total_pnl = payload.get("total_pnl")
    if total_pnl is not None:
        sign = "+" if total_pnl >= 0 else ""
        lines.append(f"\nTotal P&L: ₹{sign}{total_pnl:,.2f}")

    if failed:
        # "check the app" was an invitation to go place the leg manually on ICICI's
        # portal, which is exactly how a user ends up opening a contra position against
        # an exit that was still in flight. Name the risk instead of implying an action.
        lines.insert(
            1,
            "⚠️ _One or more legs did not go through. Automatic retries are finished — "
            "review the position before placing anything yourself, since any leg that DID "
            "fill has already changed it._",
        )

    return "\n".join(lines)


def _format_retrying_message(reason: str, payload: dict[str, Any], seconds: int) -> str:
    """Sent the moment a throttle forces the first retry, not after the last one.

    The whole point is to reach the user during the wait. A user who sees no exit appear
    and has been told nothing will reasonably go and place it on ICICI's app — and that
    manual fill is what creates the contra-position risk. Telling them we are retrying,
    and that we will detect their action and stop, removes the reason to intervene.
    """
    emoji, label = _REASON_LABEL.get(reason, ("🔔", "Square-Off Rule Fired"))
    stock_code = payload.get("stock_code", "")
    expiry_display = payload.get("expiry_display", "")
    return "\n".join(
        [
            f"⏳ *Retrying exit orders* — {stock_code} · {expiry_display}",
            "",
            f"{label} fired, but ICICI is rate-limiting us. Retrying automatically for up "
            f"to about {seconds} seconds.",
            "",
            "*You don't need to do anything.* If you place these orders yourself in the "
            "meantime we'll detect it and stop retrying, so you won't get a duplicate.",
            "",
            "You'll get a final confirmation here either way.",
        ]
    )


def notify_squareoff_retrying(
    user_id: str, *, reason: str, payload: dict[str, Any], seconds: int
) -> None:
    _notify(user_id, _format_retrying_message(reason, payload, seconds), kind="squareoff retry")


def notify_squareoff_fired(
    user_id: str,
    *,
    reason: str,
    payload: dict[str, Any],
    leg_results: list[dict[str, Any]],
    failed: bool = False,
) -> None:
    try:
        status = get_status(user_id)
    except Exception:  # noqa: BLE001
        logger.exception("telegram alert: status lookup failed for user_id=%s", user_id)
        return
    if not status["alerts_enabled"] or not status["telegram_chat_id"]:
        return

    text = _format_message(reason, payload, leg_results, failed=failed)
    _send_async(status["telegram_chat_id"], text)


def _send_async(chat_id: str, text: str) -> None:
    threading.Thread(
        target=send_message_sync,
        args=(chat_id, text),
        daemon=True,
        name="telegram-alert-send",
    ).start()


def _format_reset_message(rule: Any, reason: str, orphans: list[Any]) -> str:
    """A Reset means protection the user was relying on has stopped.

    The hard part is that Reset stops *monitoring* but does not stop *orders* — so
    "monitoring stopped" alone actively misleads: it reads as "nothing automated will
    happen to my positions", while orphaned exit orders keep working. Anything still live
    has to be said out loud, and the contra case ("this will OPEN a position") loudest of
    all.
    """
    contra = [o for o in orphans if getattr(o, "opens_contra_position", False)]
    header = "⚠️ *PB/SL Reset — action needed*" if contra else "🔔 *PB/SL Reset*"
    lines = [header, f"{rule.stock_code} · {rule.expiry_display}", "", f"Monitoring stopped: {reason}"]

    if orphans:
        lines.append("")
        lines.append(
            f"*{len(orphans)} exit order(s) from this rule are still live and may still execute:*"
        )
        for o in orphans:
            leg = f"{o.stock_code} {o.strike_price} {o.right}"
            price = f" @ ₹{o.price}" if o.price else ""
            lines.append(f"• {o.action} {leg} ×{o.quantity}{price}")
            if o.opens_contra_position:
                lines.append(
                    f"  ⚠️ {leg} is already closed — if this fills it will OPEN a new "
                    f"{o.action} position."
                )
        lines.append("")
        lines.append("Cancel them in the app, or let them fill and accept the outcome.")
    else:
        lines.append("")
        lines.append("No exit orders are outstanding. Set the rule again to resume.")
    return "\n".join(lines)


def _rule_lines(rules: list[Any]) -> list[str]:
    return [f"• {r.stock_code} · {r.expiry_display}" for r in rules]


def _format_session_expired_message(rules: list[Any]) -> str:
    """App session gone, broker session still good.

    The honest version of this is counter-intuitive and has to be said plainly: being
    logged out of the app does *not* switch PB/SL off. The stored broker session keeps
    the engine firing headless until it expires at midnight IST. If we said "monitoring
    stopped" here the user might re-arm on top of rules that are still live, or worse,
    manually close positions that automation is also about to close.
    """
    n = len(rules)
    return "\n".join(
        [
            "⚠️ *App session expired — PB/SL still armed*",
            "",
            f"You were signed out of the app, but {n} rule(s) are still being monitored:",
            *_rule_lines(rules),
            "",
            "Monitoring continues on your stored broker session until midnight IST — "
            "exit orders can still be placed without you being signed in.",
            "",
            "Log back in to see and control them. After midnight IST the broker session "
            "expires and monitoring stops.",
        ]
    )


def _format_logout_message(rules: list[Any]) -> str:
    """Deliberate logout: the broker session is cleared, so this really is the end of
    monitoring. Unlike a Reset, no exit orders were placed, so there is nothing live to
    warn about — the whole message is "you are now unprotected"."""
    n = len(rules)
    return "\n".join(
        [
            "🛑 *PB/SL monitoring stopped — you logged out*",
            "",
            f"{n} rule(s) are no longer being monitored:",
            *_rule_lines(rules),
            "",
            "No exit orders will be placed for these. Log back in and re-arm to resume.",
        ]
    )


def notify_session_expired_with_live_rules(user_id: str, rules: list[Any]) -> None:
    """Alert when the app session lapses while SGs are live — see
    `_format_session_expired_message` for why this is reassurance, not a warning."""
    _notify(user_id, _format_session_expired_message(rules), kind="session expired")


def notify_logout_stopped_monitoring(user_id: str, rules: list[Any]) -> None:
    """Alert when a deliberate logout ends monitoring.

    The logout confirm dialog already warned in-app and the user proceeded anyway, so
    this is not the first notice — it is the durable one. Ending protection is exactly
    the kind of thing a user does in a hurry and forgets by the next move in the market.
    """
    _notify(user_id, _format_logout_message(rules), kind="logout")


def _format_protection_suspended_message(rules: list[Any], *, first: bool) -> str:
    """Armed on paper, evaluating nothing — the engine has no positions to measure against
    because there is no usable broker session.

    This is the opposite case to `_format_session_expired_message` and the distinction is
    the entire message. There, monitoring genuinely continues on a stored broker session.
    Here it does not: the rules exist, the app will show them as Armed, and nothing will
    fire. Saying "still armed" would be the most dangerous possible wording, so the words
    "not being monitored" have to come before anything else.
    """
    n = len(rules)
    header = (
        "🛑 *PB/SL protection suspended*"
        if first
        else "🛑 *PB/SL protection still suspended*"
    )
    return "\n".join(
        [
            header,
            "",
            f"{n} rule(s) are *not being monitored* right now:",
            *_rule_lines(rules),
            "",
            "The app cannot reach your broker session, so no profit-booking or stop-loss "
            "exit orders can be placed — even though these rules still show as Armed.",
            "",
            "*Log back in to restore protection.* You'll get a confirmation here the "
            "moment monitoring resumes.",
        ]
    )


def _format_protection_resumed_message(rules: list[Any]) -> str:
    n = len(rules)
    return "\n".join(
        [
            "✅ *PB/SL monitoring resumed*",
            "",
            f"Your broker session is live again and {n} rule(s) are being monitored:",
            *_rule_lines(rules),
        ]
    )


def notify_protection_suspended(user_id: str, rules: list[Any], *, first: bool) -> None:
    """Recurring alert while live SGs cannot be evaluated. `first` only changes the
    wording — the repeat has to read as a continuing state, not a fresh incident, or a
    user who has been unprotected for two hours cannot tell it from a new one."""
    _notify(
        user_id,
        _format_protection_suspended_message(rules, first=first),
        kind="protection suspended",
    )


def notify_protection_resumed(user_id: str, rules: list[Any]) -> None:
    """Sent once, when a previously-suspended user's positions warm successfully.

    Closing the loop is not a nicety: we told the user protection was off and asked them
    to act, so leaving them to guess whether it worked would make them distrust the
    suspended alert itself next time.
    """
    _notify(user_id, _format_protection_resumed_message(rules), kind="protection resumed")


def _notify(user_id: str, text: str, *, kind: str) -> None:
    try:
        status = get_status(user_id)
    except Exception:  # noqa: BLE001
        logger.exception("telegram %s alert: status lookup failed for user_id=%s", kind, user_id)
        return
    if not status["alerts_enabled"] or not status["telegram_chat_id"]:
        return
    _send_async(status["telegram_chat_id"], text)


def notify_squareoff_reset(user_id: str, rule: Any, reason: str, orphans: list[Any] | None = None) -> None:
    """Alert on every Reset — not optional.

    Arming pins its own WS subscription so PB/SL runs headless, which means a Reset can
    happen with nobody watching, and that is exactly when it is most likely (EOD expiry,
    an overnight-adjacent broker rejection, a manual trade from the ICICI app). In that
    window every in-app surface is decorative and this is the only channel that reaches
    the user.
    """
    try:
        status = get_status(user_id)
    except Exception:  # noqa: BLE001
        logger.exception("telegram reset alert: status lookup failed for user_id=%s", user_id)
        return
    if not status["alerts_enabled"] or not status["telegram_chat_id"]:
        return
    _send_async(status["telegram_chat_id"], _format_reset_message(rule, reason, orphans or []))
