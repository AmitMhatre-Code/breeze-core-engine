"""Builds the `squareoff_watch` list reported in the portal heartbeat -- lets
breeze-saas-portal remind a user over Telegram to re-login before market open
when their ICICI broker session has lapsed overnight and they still have an
armed PB/SL rule, even if this deployment is powered off at the time (the
portal acts on the last value it received). See docs/architecture.md and
breeze-saas-portal's squareoff reminder scheduler.

Scoped to exactly the users who currently have an armed rule (usually 0 or 1,
occasionally more if a deployment is shared) rather than every locally
configured user, keeping the payload minimal and avoiding a single/multi-user
assumption either way.
"""
from __future__ import annotations

from typing import Any

from icici_breeze_backend.app.repositories import squareoff_rules as squareoff_repo
from icici_breeze_backend.app.repositories.broker_session import get_broker_session_expiry
from icici_breeze_backend.app.repositories.user_telegram import get_status


def build_squareoff_watch() -> list[dict[str, Any]]:
    user_ids = {str(row["user_id"]) for row in squareoff_repo.list_all_armed_rules()}
    watch: list[dict[str, Any]] = []
    for user_id in sorted(user_ids):
        try:
            status = get_status(user_id)
        except Exception:  # noqa: BLE001 -- best-effort telemetry, never block heartbeat
            continue
        if not status.get("alerts_enabled") or not status.get("telegram_chat_id"):
            continue
        watch.append(
            {
                "icici_user_id": user_id,
                "telegram_chat_id": status["telegram_chat_id"],
                "broker_session_valid_until": get_broker_session_expiry(user_id),
            }
        )
    return watch
