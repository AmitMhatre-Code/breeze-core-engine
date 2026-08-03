"""Keeps the P&L engine's position registry warm for users holding live SGs, and tells
them when it cannot.

The defect this exists for: `_legs_by_user` (the registry `run_pnl_tick` iterates) has
exactly one writer — `sync_positions_from_response`, reached only from `GET
/portfolio/data`. `hydrate_group_rules_on_startup` restores rules, WS pins and the order
feed after a restart but *not* positions, and `run_pnl_tick` returns early on an empty
registry. So a restarted instance showed SGs as Armed while evaluating nothing at all,
until a human happened to open the Portfolio page. Portal-approved upgrades restart the
container mid-session, so this was a live exposure, not a theoretical one.

Two jobs, one loop, because they are the same broker call:
  * warm the registry (retry after a restart, until a broker session exists);
  * refresh composition periodically, since prices arrive over WS but the *set* of legs
    only ever changed on a Portfolio page load.

Deliberately NOT seeding the registry from the rule's arm-time `legs_snapshot`. That would
resume evaluation instantly, but against a composition that may have changed while we were
down — and firing real exit orders on a stale leg set is a worse failure than a gap the
user has been told about. So: stay inert, and make the gap loud.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import IST, now_ist
from icici_breeze_backend.app.repositories import squareoff_protection as state_repo
from icici_breeze_backend.app.repositories import squareoff_rules as repo

_logger = logging.getLogger(__name__)

_TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S"


def _tick_interval_seconds() -> float:
    """How often to refresh positions for users with live SGs.

    60s is a deliberate budget decision, not an arbitrary default. One user with an SG
    armed for a full session costs ~375 calls/day against ICICI's 5000 cap (~7.5%) — the
    measured cost of the bug this replaces was 4236 calls in ~71 minutes, so this is
    roughly two orders of magnitude cheaper for strictly better coverage.
    """
    try:
        return max(15.0, float(getattr(cfg, "SQUAREOFF_GUARD_INTERVAL_SECONDS", 60.0)))
    except (TypeError, ValueError):
        return 60.0


def _reminder_interval_seconds() -> float:
    try:
        return max(60.0, float(getattr(cfg, "SQUAREOFF_REMINDER_INTERVAL_SECONDS", 1800.0)))
    except (TypeError, ValueError):
        return 1800.0


def _parse_ist(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:19], _TIMESTAMP_FMT).replace(tzinfo=IST)
    except ValueError:
        return None


def warm_positions_for_user(user_id: str) -> bool:
    """Refresh this user's tracked legs from the broker. True iff the fetch succeeded.

    Success is judged on the *broker response*, not on whether any legs came back. A user
    can legitimately hold zero open positions for a moment, and treating that as failure
    would report protection as suspended when the session is perfectly healthy.

    Critically, `sync_positions_from_response` calls `clear_positions` for any response it
    cannot read as a position list — including error payloads. Handing it a failed fetch
    would therefore *wipe* a registry that was already warm, turning one transient broker
    hiccup into exactly the inert state this module exists to prevent. So the Status check
    happens here, before the sync, and a bad response is left to the caller as False.
    """
    try:
        from icici_breeze_backend.app.services.portfolio_pnl_engine import (
            sync_positions_from_response,
        )
        from icici_breeze_backend.app.services.processor import processor

        data = processor().get_positions(user_id)
        if not isinstance(data, dict) or data.get("Status") != 200:
            return False
        sync_positions_from_response(user_id, data)
        return True
    except Exception:  # noqa: BLE001 — a warm failure must never break the loop
        _logger.exception("Position warm failed for user_id=%s", user_id)
        return False


def _users_with_live_rules() -> list[str]:
    seen: list[str] = []
    for row in repo.list_all_live_rules():
        uid = str(row["user_id"])
        if uid not in seen:
            seen.append(uid)
    return seen


def _should_remind(state: dict | None, now: datetime) -> bool:
    """First reminder fires immediately — the protection gap starts at the restart, not
    30 minutes after it, and a user who reboots into an unprotected state needs to know
    before the market moves, not after."""
    if state is None:
        return True
    last = _parse_ist(state.get("last_reminder_at"))
    if last is None:
        return True
    return now - last >= timedelta(seconds=_reminder_interval_seconds())


def _handle_suspended(user_id: str, now: datetime) -> None:
    from icici_breeze_backend.app.services.telegram_alerts import notify_protection_suspended

    state = state_repo.get_state(user_id)
    first = state is None
    if first:
        state_repo.mark_suspended(user_id)
        _logger.warning(
            "PB/SL protection suspended for user_id=%s: positions could not be warmed",
            user_id,
        )
    if not _should_remind(state, now):
        return
    rules = repo.list_monitoring_rules(user_id)
    if not rules:
        return
    notify_protection_suspended(user_id, rules, first=first)
    state_repo.mark_reminder_sent(user_id)


def reconcile_fully_closed_groups(user_id: str) -> int:
    """Reset armed SGs whose every leg has been closed outside the rule.

    This is the one foreign-fill case the drift check cannot reach, and the reason is
    structural rather than a bug in it. `_evaluate_rules` skips a group rule when no
    tracked leg matches it (`if not matching: continue`), and `run_pnl_tick` only iterates
    users who appear in the position registry at all — so a user who closes *everything*
    manually is dropped from the registry entirely and their armed SG is never evaluated
    again. It sits `armed` forever: the user believes they are protected on a position they
    no longer hold, and the one-live-SG-per-key index blocks them re-arming that group.

    A partial change is already handled — `check_armed_drift` compares the refreshed
    registry against `legs_snapshot` on the normal tick — so this deliberately covers only
    the empty case rather than duplicating that logic.

    MUST only be called right after a *successful* position warm. "No legs" and "we could
    not read your positions" are the same observation from here, and resetting on the
    second would tear down live protection over a broker hiccup — the same fail-safe
    reasoning `check_armed_drift` uses when the registry is cold.
    """
    from icici_breeze_backend.app.services import portfolio_pnl_engine as engine
    from icici_breeze_backend.app.services import strategy_group_lifecycle as sg

    reset_count = 0
    for rule in repo.list_monitoring_rules(user_id):
        # `armed` only. A `triggered` rule is mid-placement and its own exit orders are not
        # recorded against it yet; a `fired` rule's own exits legitimately empty the group,
        # which is what `reconcile_fired_rules_for_user` resolves into Completed. Resetting
        # either here would fight the code that is already handling them.
        if rule.status != "armed":
            continue
        if not rule.legs_snapshot:
            continue  # nothing was recorded at arm time — cannot conclude anything
        if engine.group_legs_for_user(user_id, rule.stock_code, rule.expiry_display):
            continue
        try:
            sg.reset_rule(user_id, rule, sg.reason_group_closed_elsewhere())
            reset_count += 1
            _logger.info(
                "SG %s reset: group fully closed outside the rule (user=%s)", rule.id, user_id
            )
        except Exception:  # noqa: BLE001 — one rule must not stop the rest
            _logger.exception("Could not reset fully-closed SG %s", rule.id)
    return reset_count


def _handle_recovered(user_id: str) -> None:
    from icici_breeze_backend.app.services.telegram_alerts import notify_protection_resumed

    if not state_repo.clear_suspension(user_id):
        return  # was never suspended — nothing to announce
    _logger.info("PB/SL protection resumed for user_id=%s", user_id)
    rules = repo.list_monitoring_rules(user_id)
    if rules:
        notify_protection_resumed(user_id, rules)


def protection_guard_tick() -> None:
    """One sweep over every user holding live SGs.

    Skipped entirely while the market is closed. That single gate does double duty: it
    keeps the loop from spending broker budget overnight, and it satisfies the requirement
    that re-login reminders never fire at 2am — a reminder the user cannot act on and does
    not need is how a channel gets muted, which would cost us the alerts that matter.
    """
    from icici_breeze_backend.app.services.market_calendar import is_market_open

    if not is_market_open():
        return
    now = now_ist()
    for user_id in _users_with_live_rules():
        try:
            if warm_positions_for_user(user_id):
                _handle_recovered(user_id)
                # Only after a successful warm: an empty group is now a fact, not an
                # unknown. Costs no extra broker call — it reads the registry the warm
                # just refreshed.
                reconcile_fully_closed_groups(user_id)
            else:
                _handle_suspended(user_id, now)
        except Exception:  # noqa: BLE001 — one user must not stop the sweep
            _logger.exception("Protection guard tick failed for user_id=%s", user_id)


async def run_protection_guard_loop() -> None:
    """Mirrors the heartbeat / pnl-loop idiom: infinite loop cancelled only by the
    lifespan's `task.cancel()`, `CancelledError` re-raised, everything else logged and
    swallowed so one bad tick never kills the task."""
    _logger.info("PB/SL protection guard loop started")
    while True:
        try:
            await asyncio.sleep(_tick_interval_seconds())
            await asyncio.to_thread(protection_guard_tick)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _logger.exception("Protection guard tick failed")
