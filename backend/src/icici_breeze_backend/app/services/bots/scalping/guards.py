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


def switch_off(user_id: str, bot_type: str, reason_text: str) -> bool:
    """Switch the bot off so it opens nothing new, without telling anyone. Returns success.

    Silent on purpose: every caller sends the one alert that says *why*. The stuck-position
    paths (B-01) each send "... needs checking ... The bot has been disarmed", and used to
    go through `disarm_bot`, which added a second message claiming the bot "hit its
    cumulative daily loss limit" -- wrong, and the one a user would act on.
    """
    from icici_breeze_backend.app.repositories import bots as repo

    try:
        repo.update_bot(user_id, bot_type, enabled=False)
    except Exception:  # noqa: BLE001
        _logger.exception("scalping: could not switch %s off (%s)", bot_type, reason_text)
        return False
    _logger.warning("scalping: %s switched off -- %s", bot_type, reason_text)
    return True


def disarm_bot(user_id: str, bot_type: str, reason_text: str, *, paper: bool) -> None:
    """Switch the bot off after a daily-stop breach, and say so on Telegram.

    Only for the daily stop -- its message says the loss limit was hit. Anything else that
    stands a bot down uses `switch_off` and sends its own alert.

    Decided 2026-09-06: a bot that has lost its daily limit does not trade again until a
    human has looked at why. The cost is real and worth stating -- one bad day stops the bot
    for every subsequent day until it is re-enabled by hand, which is a silence that has to be
    noticed. The run log and the Telegram alert are what make it noticeable.

    `paper` is required, not defaulted, on purpose: a paper day can breach its own simulated
    stop, so this alert can fire for a bot that has placed no real orders at all. A silent
    default here is exactly how a Paper-mode loss reads as real money.
    """
    from icici_breeze_backend.app.services.telegram_alerts import _BOT_LABEL, _notify

    if not switch_off(user_id, bot_type, f"daily loss limit: {reason_text}"):
        return
    display_name = _BOT_LABEL.get(bot_type, bot_type)
    banner = (
        "\U0001f9ea *SIMULATION (Paper mode) — no real money is involved.*\n\n"
        if paper else ""
    )
    try:
        _notify(
            user_id,
            f"{banner}"
            "🛑 *Scalping bot stopped*\n\n"
            f"*{display_name}* hit its cumulative daily loss limit and has been "
            f"*disabled*.\n\n{reason_text}\n\n"
            "It will not trade again until you re-enable it.",
            kind="scalping_daily_stop",
        )
    except Exception:  # noqa: BLE001
        _logger.exception("scalping: could not send the daily-stop alert")


def has_unresolved_intent(user_id: str, bot_type: str) -> bool:
    """True while a pending row remains. Blocks new cycles.

    Trading around an order you cannot account for is worse than not trading. The block
    lasts only as long as the question does: `order_intents.resolve_pending` settles a
    pending row from the broker at startup and on every pass, so a row is left pending only
    while the broker genuinely cannot answer (B-02).
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
    user_id: str,
    bot_type: str,
    run_id: str,
    *,
    reason_code: str,
    reason_text: str,
    extra_detail: Optional[dict[str, Any]] = None,
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
            **(extra_detail or {}),
            "cycles": totals.cycles,
            "net_pnl": totals.realized_net_pnl,
            "friction": totals.friction,
            "consecutive_losses": totals.consecutive_losses,
        },
    )
