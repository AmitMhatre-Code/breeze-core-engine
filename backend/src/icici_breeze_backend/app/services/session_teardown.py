"""What logging out actually tears down — and, crucially, what it must not.

Two very different things used to arrive at the same code path. A user clicking "Log
out" means "end my session"; the frontend's automatic 401 handler
(`auth-session-expired.ts`) means "your app JWT lapsed, sign in again". Both POSTed
`/auth/logout`, and both cleared the persisted broker session token — the one thing that
lets PB/SL square-off dispatch reach the broker with no HTTP request in scope (see
`app/repositories/broker_session.py`). So an app-JWT expiry in a background tab silently
disarmed the user's stop-losses, and they only found out at the breach, when the exit
orders failed to place.

Deliberate logout still clears everything. A session expiry now clears cookies only: the
persisted broker token (and the warm Breeze session behind it) survives until its own
midnight-IST expiry, so armed rules keep firing headless exactly as they do when the
browser is simply closed.

The security shape of that is worth stating: the retained token is server-side only and
is never handed back to a caller — a signed-out browser still has to log in from
scratch. All it buys is the background engine's ability to finish the trading day the
user already armed.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from icici_breeze_backend.app.repositories import squareoff_rules as repo
from icici_breeze_backend.app.services import telegram_alerts

_logger = logging.getLogger(__name__)

#: Session-expiry teardown can arrive several times over — every open tab's 401 handler
#: fires its own POST — so the alert is deduped per user. Deliberate logout is a single
#: user action and is never deduped.
_EXPIRY_ALERT_COOLDOWN_SECONDS = 15 * 60
_expiry_alert_lock = threading.Lock()
_expiry_alert_sent_at: dict[str, float] = {}


def _should_send_expiry_alert(user_id: str) -> bool:
    now = time.monotonic()
    with _expiry_alert_lock:
        last = _expiry_alert_sent_at.get(user_id)
        if last is not None and now - last < _EXPIRY_ALERT_COOLDOWN_SECONDS:
            return False
        _expiry_alert_sent_at[user_id] = now
        return True


def _live_rules(user_id: str) -> list[Any]:
    try:
        return repo.list_monitoring_rules(user_id)
    except Exception:  # noqa: BLE001 — never let a rules lookup break logging out
        _logger.exception("Could not list live PB/SL rules for user_id=%s", user_id)
        return []


def teardown_session(user_id: str, broker_token: str, *, deliberate: bool) -> None:
    """Release this user's server-side session state for a logout of `deliberate` kind.

    Best-effort throughout: a user must always end up logged out of the browser, whatever
    fails in here.
    """
    from icici_breeze_backend.app.repositories.broker_session import clear_broker_session_token
    from icici_breeze_backend.app.services.breeze_session_cache import evict
    from icici_breeze_backend.app.services.broker_snapshot_cache import evict as evict_snapshot
    from icici_breeze_backend.app.services.customer_details_cache import (
        evict as evict_customer_details,
    )

    rules = _live_rules(user_id)

    if not deliberate:
        # Keep the broker token AND the warm caches: they are what the P&L engine's
        # square-off dispatch runs on, and re-creating a Breeze session costs an ICICI
        # round trip we have no reason to spend here.
        if rules and _should_send_expiry_alert(user_id):
            try:
                telegram_alerts.notify_session_expired_with_live_rules(user_id, rules)
            except Exception:  # noqa: BLE001
                _logger.exception("Session-expiry PB/SL alert failed for user_id=%s", user_id)
        _logger.info(
            "Session expired for user_id=%s; broker session retained (live PB/SL rules=%d)",
            user_id,
            len(rules),
        )
        return

    try:
        evict(user_id, broker_token or "")
        evict_snapshot(user_id, broker_token or "")
        evict_customer_details(user_id, broker_token or "")
        clear_broker_session_token(user_id)
    except Exception:  # noqa: BLE001
        _logger.exception("Broker session teardown failed for user_id=%s", user_id)

    if rules:
        try:
            telegram_alerts.notify_logout_stopped_monitoring(user_id, rules)
        except Exception:  # noqa: BLE001
            _logger.exception("Logout PB/SL alert failed for user_id=%s", user_id)
        _logger.info(
            "User %s logged out with %d live PB/SL rule(s); monitoring stopped", user_id, len(rules)
        )
