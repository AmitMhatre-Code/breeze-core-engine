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
        lines.insert(1, "⚠️ _One or more legs failed to place — check the app._")

    return "\n".join(lines)


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
