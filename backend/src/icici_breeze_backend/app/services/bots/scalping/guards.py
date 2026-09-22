"""Safety guards that gate live enablement (docs/bots-scalping-plan.md sections 2.2, 6).

Everything here answers a question the shared gate stack asks but cannot answer itself,
because it needs the P&L engine, the repository or Telegram. `decide.py` stays pure; this is
where the world gets consulted.

Two of these exist because of hazards that are invisible until they bite:

* **The Strategy Group collision.** A group rule is keyed on `(stock_code, expiry_display)`
  alone and squares off *every* leg in that group. A scalper trading NIFTY's weekly expiry
  shares that key with any rule armed on it -- Bot 2's, or one armed by hand from the PB/SL
  screen -- so the rule would absorb the scalper's legs into its own P&L and close them with
  it, at a price it chose.
* **The daily stop must survive a restart.** It is recomputed from the day's cycle rows on
  every pass rather than held in memory, because an in-process counter plus a container
  upgrade is how a bot that has already lost its limit quietly resumes trading.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.domain.bots import ReasonCode
from icici_breeze_backend.app.services.bots.scalping.momentum_bot import (
    INDEX_STOCK_CODE,
    nearest_expiry,
)

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SgConflict:
    expiry_display: str
    rule_id: str
    other_legs: int  # legs in the group that are NOT this bot's


def find_sg_conflict(
    proc: Any, user_id: str, *, bot_leg_count: int = 0
) -> Optional[SgConflict]:
    """A live group rule on the expiry this bot would trade, or None.

    `other_legs` matters for the alert, not the decision: it is how the user is told whether
    disarming the rule leaves any of their *own* positions unprotected.
    """
    from icici_breeze_backend.app.services.portfolio_pnl_engine import (
        group_legs_for_user,
        group_rule_for,
    )

    expiry = nearest_expiry(proc)
    if not expiry:
        return None
    rule = group_rule_for(user_id, INDEX_STOCK_CODE, expiry)
    if rule is None:
        return None
    try:
        legs = group_legs_for_user(user_id, INDEX_STOCK_CODE, expiry)
    except Exception:  # noqa: BLE001
        legs = []
    return SgConflict(
        expiry_display=expiry,
        rule_id=str(getattr(rule, "rule_id", "") or ""),
        other_legs=max(0, len(legs) - int(bot_leg_count)),
    )


def disarm_conflicting_rule(user_id: str, conflict: SgConflict) -> None:
    """Clear the rule and tell the user exactly what was disarmed.

    **This disarms a protection the user set up**, which is why the alert is not optional and
    names the group. Chosen deliberately (2026-09-06): the alternative is leaving a rule
    armed that will square off the bot's legs at a moment and price of its own choosing,
    after which the bot manages a position that no longer exists.

    The hazard being accepted, and the reason the alert spells it out: the rule covers the
    whole `(stock_code, expiry)` group, so if the user holds other positions on that expiry
    -- a Strategy Builder trade, say -- those lose their stop too. `other_legs` is how the
    message says so.
    """
    from icici_breeze_backend.app.services.portfolio_pnl_engine import clear_group_rule
    from icici_breeze_backend.app.services.telegram_alerts import _notify

    clear_group_rule(user_id, INDEX_STOCK_CODE, conflict.expiry_display)
    _logger.warning(
        "scalping: disarmed PB/SL rule %s on %s %s -- it would have squared off this bot's "
        "legs (%d other leg(s) in that group)",
        conflict.rule_id,
        INDEX_STOCK_CODE,
        conflict.expiry_display,
        conflict.other_legs,
    )

    lines = [
        "⚠️ *PB/SL rule disabled by a scalping bot*",
        "",
        f"A profit/stop-loss rule on *{INDEX_STOCK_CODE} {conflict.expiry_display}* has "
        f"been switched off.",
        "",
        "That rule applies to _every_ leg on this stock and expiry, so it would have "
        "squared off the bot's position along with its own — at its price, not the bot's.",
    ]
    if conflict.other_legs > 0:
        lines += [
            "",
            f"⚠️ *{conflict.other_legs} other leg(s)* on this expiry were covered by that "
            f"rule and are now *unprotected*.",
        ]
    lines += [
        "",
        "If you want a PB/SL rule on this expiry, *stop the scalping bot first*, then "
        "arm the rule.",
    ]
    try:
        _notify(user_id, "\n".join(lines), kind="scalping_sg_conflict")
    except Exception:  # noqa: BLE001 -- an unreachable user must not stop the bot trading
        _logger.exception("scalping: could not send the PB/SL disarm alert")


def unrealized_pnl(cycle: Any, mark_value: Optional[float]) -> float:
    """Open-position P&L at current marks, or 0.0 when it cannot be priced.

    Zero, not an exception: the cumulative stop's realized half is durable and must keep
    working when a quote is missing. The stale-feed gate is what escalates a feed that stays
    dark, and it is a different concern from this one.
    """
    if cycle is None or mark_value is None:
        return 0.0
    entry = float(getattr(cycle, "entry_value", 0) or 0)
    structure = str(getattr(cycle, "structure", ""))
    if structure == "iron_fly":
        # A credit structure: entry_value is the credit received, mark_value the cost to buy
        # it back, so profit is what is left of the credit.
        return round(entry - float(mark_value), 2)
    return round(float(mark_value) - entry, 2)


def stop_breached_including_open(
    realized_net_pnl: float, unrealized: float, cumulative_stop_inr: float
) -> bool:
    """The section 6.1 test: realized + unrealized against the cap.

    Unrealized is included so a bot cannot sit deep underwater on an open position and go on
    opening more; realized alone would let exactly that happen.
    """
    return (float(realized_net_pnl) + float(unrealized)) <= -abs(float(cumulative_stop_inr))


def disarm_bot(user_id: str, bot_type: str, reason_text: str) -> None:
    """Switch the bot off after a daily-stop breach, so it cannot resume unattended.

    Decided 2026-09-06: a bot that has lost its daily limit does not trade again until a
    human has looked at why. The cost is real and worth stating -- one bad day stops the bot
    for every subsequent day until it is re-enabled by hand, which is a silence that has to be
    noticed. The run log and the Telegram alert are what make it noticeable.
    """
    from icici_breeze_backend.app.repositories import bots as repo
    from icici_breeze_backend.app.services.telegram_alerts import _BOT_LABEL, _notify

    try:
        repo.update_bot(user_id, bot_type, enabled=False)
    except Exception:  # noqa: BLE001
        _logger.exception("scalping: could not disarm %s after its daily stop", bot_type)
        return
    _logger.warning("scalping: %s disarmed after breaching its daily loss limit", bot_type)
    display_name = _BOT_LABEL.get(bot_type, bot_type)
    try:
        _notify(
            user_id,
            "🛑 *Scalping bot stopped*\n\n"
            f"*{display_name}* hit its cumulative daily loss limit and has been "
            f"*disabled*.\n\n{reason_text}\n\n"
            "It will not trade again until you re-enable it.",
            kind="scalping_daily_stop",
        )
    except Exception:  # noqa: BLE001
        _logger.exception("scalping: could not send the daily-stop alert")


def reconcile_pending_cycles(proc: Any, user_id: str, bot_type: str) -> int:
    """Resolve intent rows left by a crash mid-placement. Returns how many were touched.

    A pending row means an order MAY have gone out. Resolving it is a question with two
    honest answers and no third:

    * the broker shows a fill -> adopt it as a real open position, so the exit loop takes
      over and the position is managed;
    * the broker shows nothing filled -> abandon the row, having traded nothing.

    Anything the broker cannot answer is escalated rather than guessed. Assuming "no fill"
    would silently strand a live position with no stop behind it, which is the exact failure
    this whole mechanism exists to prevent.
    """
    from icici_breeze_backend.app.repositories import bots as repo
    from icici_breeze_backend.app.services.bots.scalping import live

    pending = repo.pending_cycles(user_id, bot_type)
    if not pending:
        return 0

    resolved = 0
    for cycle in pending:
        detail = dict(cycle.detail or {})
        order_ids = [str(o) for o in (detail.get("order_ids") or []) if o]
        if not order_ids:
            # The row was written but no order id was ever recorded, so either the call never
            # reached the broker or its answer was lost. The order book is the only place
            # that knows, and it is not something to guess at.
            _alert_orphan(user_id, bot_type, cycle, "no order id was recorded")
            resolved += 1
            continue

        filled = 0
        unknown = False
        # Looked up on the exchange the legs trade on. A SENSEX (BFO) order asked about on NFO
        # is simply not found, which would read as "nothing filled" and abandon a real fill.
        exchange = str(((cycle.legs or [{}])[0] or {}).get("exchange_code") or cfg.NFO)
        for order_id in order_ids:
            state = live._rest_order_state(proc, user_id, order_id, exchange)
            if not state:
                unknown = True
                continue
            filled += int(state.get("executed") or 0)

        if unknown:
            _alert_orphan(user_id, bot_type, cycle, "the broker did not answer for its order")
            resolved += 1
            continue

        if filled > 0:
            # **A multi-leg structure is never adopted from a total.** Bot 3 holds one leg,
            # so "some units filled" fully describes what is live. Bot 4's fly has four, and
            # a sum cannot say *which* -- 75 units could be one wing, which is not a fly and
            # is not something any exit rule in `iron_fly_bot` describes. Adopting it would
            # hand the exit loop a structure it would then misprice and mis-sequence.
            #
            # So a multi-leg cycle is adopted only when every leg is accounted for; anything
            # short of that goes to a human, which is the same fail-closed answer `unknown`
            # already gets.
            expected_legs = [l for l in (cycle.legs or []) if int(l.get("quantity") or 0) > 0]
            if len(expected_legs) > 1:
                wanted = sum(int(l.get("quantity") or 0) for l in expected_legs)
                if filled < wanted or len(order_ids) < len(expected_legs):
                    _alert_orphan(
                        user_id, bot_type, cycle,
                        f"only {filled} of {wanted} units across "
                        f"{len(order_ids)}/{len(expected_legs)} legs can be accounted for, "
                        f"so what is open is not the structure the bot intended",
                    )
                    resolved += 1
                    continue
            detail["pending"] = False
            detail["reconciled"] = True
            repo.mark_cycle_placed(cycle.id, order_ids=order_ids, detail=detail)
            _logger.warning(
                "scalping: adopted cycle %s after a restart -- %d units are live",
                cycle.cycle_no, filled,
            )
        else:
            repo.abandon_cycle(
                cycle.id,
                reason_code=ReasonCode.ENTRY_UNFILLED,
                reason_text="Interrupted before the order filled; nothing was traded.",
            )
        resolved += 1
    return resolved


def _alert_orphan(user_id: str, bot_type: str, cycle: Any, why: str) -> None:
    """Surface a position we cannot account for, and stop guessing.

    Deliberately does not close or adopt the row: an unresolved intent is a fact for a human,
    and a bot that guesses here is a bot that either abandons a live position or invents one.
    """
    from icici_breeze_backend.app.services.telegram_alerts import _BOT_LABEL, _notify

    _logger.error(
        "scalping: cannot reconcile cycle %s for %s -- %s", cycle.id, bot_type, why
    )
    display_name = _BOT_LABEL.get(bot_type, bot_type)
    try:
        _notify(
            user_id,
            "\u26a0\ufe0f *Scalping bot needs checking*\n\n"
            f"*{display_name}* was interrupted while placing an order and {why}.\n\n"
            "Check the Order Book for an unexpected position. The bot will not open anything "
            "new until this is resolved.",
            kind="scalping_orphan",
        )
    except Exception:  # noqa: BLE001
        _logger.exception("scalping: could not send the orphan alert")


def has_unresolved_intent(user_id: str, bot_type: str) -> bool:
    """True while a pending row remains. Blocks new cycles.

    One crash at a bad moment stops the bot for the session, which is the intended trade:
    trading around an order you cannot account for is worse than not trading.
    """
    from icici_breeze_backend.app.repositories import bots as repo

    return bool(repo.pending_cycles(user_id, bot_type))


def session_is_over(config: Any, now: Any) -> bool:
    """True once the day's last configured window has passed.

    Used to finalise the session run. Without it the row stays `running`, its heartbeat
    stops at end of day, and `reap_stale_runs` marks a perfectly normal trading day as
    "Interrupted before it finished" about half an hour later.
    """
    windows = getattr(config, "sessions", None) or []
    if not windows:
        return True
    latest = max(w.end for w in windows)
    return now.strftime("%H:%M") >= max(latest, config.hard_square_off_ist)


def finalise_session(
    user_id: str, bot_type: str, run_id: str, *, reason_code: str, reason_text: str
) -> None:
    """Close the day's run with a summary of what it actually did."""
    from icici_breeze_backend.app.repositories import bots as repo

    totals = repo.scalper_day_totals(user_id, bot_type)
    repo.finish_run(
        run_id,
        status="completed",
        reason_code=reason_code,
        reason_text=reason_text,
        detail={
            "cycles": totals.cycles,
            "net_pnl": totals.realized_net_pnl,
            "friction": totals.friction,
            "consecutive_losses": totals.consecutive_losses,
        },
    )
