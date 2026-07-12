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
    chat_id = status["telegram_chat_id"]
    threading.Thread(
        target=send_message_sync,
        args=(chat_id, text),
        daemon=True,
        name="telegram-alert-send",
    ).start()
