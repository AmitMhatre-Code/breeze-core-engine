"""The scalpers' gate stack (docs/bots-scalping-plan.md sections 5, 6).

Pure. Given a snapshot of the world it returns one action and the reason for it. Every
judgement about *whether* to act lives here, mirroring `expiry_index_writer.decide()`; the
driver in `runtime.py` only gathers inputs and carries out the verdict.

The ordering is the design, and it is the same shape for both bots:

    1. exits first, always -- a gate that blocks *entering* must never block *leaving*
    2. among exits: obligations before opinions (square-off, stop, stale feed, then ladder)
    3. entries last, cheapest and most certain gates before the expensive ones

Rule 1 is the one that matters. Read-only mode, an expiry day, a closed session window, a
cooldown and an exhausted API budget are all reasons not to open a position; none of them is
a reason to abandon one that is already open. A stack that checked them uniformly would
strand a live position with no stop running the moment a licence lapsed.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Optional

from icici_breeze_backend.app.domain.bots import ReasonCode, ScalperDayTotals, SessionWindow

if TYPE_CHECKING:  # imported for typing only -- `signal` imports this module's Candle type
    from icici_breeze_backend.app.services.bots.scalping.signal import SignalResult

Action = Literal["idle", "enter", "exit", "stand_down"]


@dataclass(frozen=True)
class FeedHealth:
    """What the tick stream is doing. `warm` covers indicators, `stale` covers liveness."""

    warm: bool
    stale: bool
    stale_seconds: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Snapshot:
    """Everything the decision needs, gathered once by the driver.

    Passed as one frozen object rather than a long argument list so a test can build the
    exact world it wants to assert on, and so adding an input cannot silently change the
    meaning of an existing call.
    """

    now_ist: datetime.datetime
    trading_allowed: bool          # licence: `deployment_license_status`
    is_trading_day: bool
    is_expiry_day: bool
    feed: FeedHealth
    totals: ScalperDayTotals
    has_open_position: bool
    api_calls_remaining: int
    sg_rule_conflict: bool = False
    # Set by the bot-specific layer: Bot 3's ladder verdict, or Bot 4's decay/drift verdict.
    position_exit: Optional[tuple[str, str]] = None
    # Bot 4 exits when its window closes; Bot 3 lets a runner run to the hard square-off.
    exit_at_window_end: bool = False
    # Mark-to-market on the open position. Kept separate from `totals.realized_net_pnl`
    # because the realized half is durable and survives a restart, while this half is only
    # knowable with a live quote -- folding them together would make the terminal-for-the-day
    # verdict depend on the feed being up.
    unrealized_pnl: float = 0.0
    # The bot has been switched Off (or back to Paper) while a real position is still open,
    # so the driver is ticking it purely to run the exit path. Entries are refused; the exit
    # half of the stack is untouched, which is the whole point -- see `_decide_entry`.
    entries_suspended: bool = False
    # The bot's entry signal, for bots that have one. `None` means "this bot has no signal
    # gate" (Bot 4 sizes a fly whenever the gates are clear) and leaves the stack unchanged.
    #
    # It is gathered here rather than inside the executor so that a signal that did NOT fire
    # is a *verdict* -- carrying a reason code and detail onto the run row like every other
    # stand-down -- instead of an early `return` visible only in a DEBUG log. A quiet day
    # that produced no trades has to be able to say which quiet day it was.
    signal: Optional["SignalResult"] = None


@dataclass(frozen=True)
class Decision:
    action: Action
    reason_code: str
    reason_text: str
    detail: dict[str, Any] = field(default_factory=dict)


def _hhmm(now: datetime.datetime) -> str:
    return now.strftime("%H:%M")


def in_window(now: datetime.datetime, windows: list[SessionWindow]) -> Optional[SessionWindow]:
    current = _hhmm(now)
    for w in windows:
        if w.start <= current < w.end:
            return w
    return None


def stop_breached(
    totals: ScalperDayTotals, cumulative_stop_inr: float, unrealized: float = 0.0
) -> bool:
    """Realized + unrealized against the cap (plan section 6.1).

    Unrealized is included so a bot cannot sit deep underwater on an open position and go on
    opening more. It defaults to zero, so a caller with no live mark -- or no position --
    still gets the durable realized-only test rather than an error.
    """
    return (totals.realized_net_pnl + float(unrealized)) <= -abs(float(cumulative_stop_inr))


def in_cooldown(
    totals: ScalperDayTotals,
    now: datetime.datetime,
    *,
    consecutive_loss_limit: int,
    cooldown_minutes: int,
) -> bool:
    if totals.consecutive_losses < consecutive_loss_limit:
        return False
    if not totals.last_closed_at:
        return True
    try:
        last = datetime.datetime.strptime(str(totals.last_closed_at)[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        # An unparseable timestamp must not silently release the cooldown: the streak is
        # real either way, and holding is the conservative reading.
        return True
    elapsed = (now.replace(tzinfo=None) - last).total_seconds()
    return elapsed < cooldown_minutes * 60


def decide(snapshot: Snapshot, config: Any, *, stale_exit_seconds: float = 60.0) -> Decision:
    """One action for this pass. See the module docstring for why the order is what it is."""
    if snapshot.has_open_position:
        return _decide_exit(snapshot, config, stale_exit_seconds=stale_exit_seconds)
    return _decide_entry(snapshot, config)


def _decide_exit(snapshot: Snapshot, config: Any, *, stale_exit_seconds: float) -> Decision:
    now = snapshot.now_ist
    risk = config.risk

    if _hhmm(now) >= config.hard_square_off_ist:
        return Decision(
            "exit",
            ReasonCode.SQUARE_OFF,
            f"Hard square-off at {config.hard_square_off_ist}.",
        )

    if stop_breached(snapshot.totals, risk.cumulative_stop_inr, snapshot.unrealized_pnl):
        total = snapshot.totals.realized_net_pnl + snapshot.unrealized_pnl
        return Decision(
            "exit",
            ReasonCode.TERMINATED_FOR_DAY,
            f"Daily loss limit reached ({total:,.0f} including the open position, against a "
            f"{risk.cumulative_stop_inr:,.0f} cap). Closing and standing down.",
        )

    if snapshot.feed.stale and snapshot.feed.stale_seconds >= stale_exit_seconds:
        # A short blip must never flatten a position -- that costs a round trip of friction,
        # the binding constraint. A sustained blackout is different: the stop is not being
        # evaluated against anything, so the position is running unmanaged.
        return Decision(
            "exit",
            ReasonCode.STALE_FEED,
            f"No ticks for {snapshot.feed.stale_seconds:.0f}s; the stop cannot be "
            f"evaluated, so the position is being closed.",
        )

    if snapshot.exit_at_window_end and in_window(now, config.sessions) is None:
        return Decision(
            "exit", ReasonCode.SQUARE_OFF, "Session window closed; flattening."
        )

    if snapshot.position_exit is not None:
        code, text = snapshot.position_exit
        return Decision("exit", code, text)

    return Decision("idle", "holding", "Position open; no exit condition met.")


def _decide_entry(snapshot: Snapshot, config: Any) -> Decision:
    now = snapshot.now_ist
    risk = config.risk

    # First, because it is the most direct answer to "why is this not opening anything": the
    # user switched it off. Everything below is a reason the bot itself found; this is the
    # one the user created, and it outranks them all.
    #
    # Reached only while the last position is being closed out -- once flat, the driver stops
    # ticking a disarmed bot entirely and this gate is unreachable.
    if snapshot.entries_suspended:
        return Decision(
            "idle",
            ReasonCode.ENTRIES_SUSPENDED,
            "Switched off; closing the open position and opening nothing new.",
        )

    # Terminal for the day comes first so that everything after it can assume the bot is
    # still allowed to trade at all.
    if stop_breached(snapshot.totals, risk.cumulative_stop_inr, snapshot.unrealized_pnl):
        return Decision(
            "stand_down",
            ReasonCode.TERMINATED_FOR_DAY,
            f"Daily loss limit reached ({snapshot.totals.realized_net_pnl:,.0f}). "
            f"No further trades today.",
        )

    if not snapshot.trading_allowed:
        return Decision(
            "stand_down",
            ReasonCode.TRADING_READ_ONLY,
            "Read-only mode: the deployment's licence does not currently allow trading.",
        )

    if not snapshot.is_trading_day:
        return Decision("idle", ReasonCode.MARKET_CLOSED, "Not a trading day.")

    if snapshot.is_expiry_day and not config.trade_on_expiry_day:
        return Decision(
            "idle",
            ReasonCode.NOT_A_FIRING_DAY,
            "Expiry day, and this bot is configured not to trade one.",
        )

    window = in_window(now, config.sessions)
    if window is None:
        return Decision(
            "idle",
            ReasonCode.OUTSIDE_SESSION_WINDOW,
            f"{_hhmm(now)} is outside every configured session window.",
        )

    if snapshot.sg_rule_conflict:
        # A Strategy Group rule is keyed on (stock_code, expiry) alone and sweeps every leg
        # in the group, so an open scalper position would be absorbed into its P&L and
        # squared off with it. Skip the cycle rather than hand it a leg to take.
        return Decision(
            "idle",
            ReasonCode.SG_RULE_CONFLICT,
            "A profit/stop-loss rule is armed on this index and expiry; it would square "
            "off this bot's legs along with its own.",
        )

    if in_cooldown(
        snapshot.totals,
        now,
        consecutive_loss_limit=risk.consecutive_loss_limit,
        cooldown_minutes=risk.cooldown_minutes,
    ):
        return Decision(
            "idle",
            ReasonCode.COOLDOWN_ACTIVE,
            f"{snapshot.totals.consecutive_losses} losses in a row; pausing for "
            f"{risk.cooldown_minutes} minutes.",
            {"consecutive_losses": snapshot.totals.consecutive_losses},
        )

    if snapshot.api_calls_remaining < risk.api_budget_reserve_calls:
        return Decision(
            "idle",
            ReasonCode.API_BUDGET_LOW,
            f"Only {snapshot.api_calls_remaining} broker calls left in the window; holding "
            f"{risk.api_budget_reserve_calls} back for the dashboard and manual exits.",
            {"api_calls_remaining": snapshot.api_calls_remaining},
        )

    if snapshot.feed.stale:
        return Decision(
            "idle",
            ReasonCode.STALE_FEED,
            f"Tick feed is stale ({snapshot.feed.stale_seconds:.0f}s); not opening anything.",
        )

    if not snapshot.feed.warm:
        return Decision(
            "idle",
            ReasonCode.NOT_WARM,
            "Indicators are still warming up.",
            dict(snapshot.feed.detail),
        )

    # The last gate, for bots that have an entry signal. A signal that did not fire is a
    # stand-down like any other and says so on the run row; a bot with no signal gate
    # (`signal is None`) falls through unchanged.
    if snapshot.signal is not None and not snapshot.signal.fired:
        return Decision(
            "idle",
            ReasonCode.SIGNAL_NO_TRADE,
            f"Gates clear, but no entry signal: {snapshot.signal.reason}",
            dict(snapshot.signal.values or {}),
        )

    # Everything shared is satisfied. What to buy is the bot-specific layer's call.
    return Decision("enter", "gates_clear", "All entry gates clear.")
