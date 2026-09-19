"""The three-level trailing ladder (docs/bots-scalping-plan.md section 3.5).

Pure, and deliberately the only place a stop is allowed to move. The invariant the whole
exit path rests on is that **a stop only ever ratchets up** -- `TrailingLadderConfig`
refuses a configuration that could not satisfy it, and `advance` enforces it with a max()
on every transition, so neither a config change mid-session nor an out-of-order price can
walk a stop back down.

Priced off the **bid**, never the LTP: the bid is what a long position can actually be sold
at, and on a wide option book the difference between the two is most of the edge.

State is persisted, not just held. The portal recreates the container on every version
upgrade, so a restart mid-trade is routine rather than exceptional; without persistence an
upgrade would silently un-ratchet a stop that had already locked in gains and turn a banked
winner into a loser. `advance` reports when the stop moved so the caller writes only on a
real transition -- a handful of writes per cycle, not one per tick.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Optional

from icici_breeze_backend.app.domain.bots import ReasonCode, TrailingLadderConfig

# Ladder rungs. 0 is the initial stop; 3 is the runner, where the stop trails the peak.
LEVEL_INITIAL = 0
LEVEL_BREAK_EVEN = 1
LEVEL_LOCK_PROFIT = 2
LEVEL_RUNNER = 3


@dataclass(frozen=True)
class LadderState:
    entry_price: float
    peak_price: float
    stop_price: float
    level: int
    opened_at_epoch: float

    @property
    def peak_gain(self) -> float:
        return self.peak_price - self.entry_price

    def gain_at(self, bid: float) -> float:
        return float(bid) - self.entry_price

    def to_detail(self) -> dict[str, Any]:
        return {
            "entry_price": self.entry_price,
            "peak_price": self.peak_price,
            "stop_price": self.stop_price,
            "level": self.level,
            "opened_at_epoch": self.opened_at_epoch,
        }

    @classmethod
    def from_detail(cls, raw: Any) -> Optional["LadderState"]:
        """Rebuild after a restart, or None if the stored blob is unusable.

        Returns None rather than raising or guessing: the caller adopting an open position
        needs to distinguish "resume exactly" from "this cycle has no ladder to resume",
        and a half-parsed ladder is more dangerous than an absent one.
        """
        if not isinstance(raw, dict):
            return None
        try:
            return cls(
                entry_price=float(raw["entry_price"]),
                peak_price=float(raw["peak_price"]),
                stop_price=float(raw["stop_price"]),
                level=int(raw["level"]),
                opened_at_epoch=float(raw["opened_at_epoch"]),
            )
        except (KeyError, TypeError, ValueError):
            return None


def open_ladder(entry_price: float, now_epoch: float, config: TrailingLadderConfig) -> LadderState:
    entry = float(entry_price)
    return LadderState(
        entry_price=entry,
        peak_price=entry,
        stop_price=entry - config.stop_loss_pts,
        level=LEVEL_INITIAL,
        opened_at_epoch=float(now_epoch),
    )


def advance(
    state: LadderState, bid: float, config: TrailingLadderConfig
) -> tuple[LadderState, bool]:
    """Ratchet the ladder against a new bid. Returns (state, stop_moved).

    `stop_moved` is what gates persistence: the peak updates constantly in a rising market,
    the stop only on a real transition, and only the stop changes what the position will do.
    """
    price = float(bid)
    peak = max(state.peak_price, price)
    gain = price - state.entry_price
    level = state.level
    stop = state.stop_price

    # Levels are evaluated against the CURRENT gain and applied in ascending order, so a
    # single large jump promotes through every rung it has earned rather than one per tick.
    if gain >= config.level_1_trigger_pts and level < LEVEL_BREAK_EVEN:
        level = LEVEL_BREAK_EVEN
    if gain >= config.level_2_trigger_pts and level < LEVEL_LOCK_PROFIT:
        level = LEVEL_LOCK_PROFIT
    if gain >= config.target_pts and level < LEVEL_RUNNER:
        level = LEVEL_RUNNER

    if level >= LEVEL_BREAK_EVEN:
        stop = max(stop, state.entry_price + config.level_1_lock_pts)
    if level >= LEVEL_LOCK_PROFIT:
        stop = max(stop, state.entry_price + config.level_2_lock_pts)
    if level >= LEVEL_RUNNER:
        # The runner trails the PEAK, not the current price, so a pullback tightens nothing
        # and only a new high moves the stop.
        stop = max(stop, peak - config.level_3_runner_step_pts)

    moved = stop > state.stop_price
    return replace(state, peak_price=peak, stop_price=stop, level=level), moved


def exit_decision(
    state: LadderState,
    bid: float,
    now_epoch: float,
    config: TrailingLadderConfig,
    *,
    hold_seconds: Optional[float] = None,
) -> Optional[tuple[str, str]]:
    """(reason_code, reason_text) when this ladder says close, else None.

    The stop is checked before the time stop: a trade that has both blown its stop and gone
    nowhere is a stop-out, and reporting it as a timeout would understate what happened.

    `hold_seconds` is set for a trade opened on a signal variant (#38): the variant's call is
    a statement about the next N minutes, so the trade is closed when those minutes are up, in
    place of the momentum signal's "went nowhere in 90 seconds" test. The stop and the ladder
    still apply throughout.
    """
    price = float(bid)
    if price <= state.stop_price:
        if state.level >= LEVEL_BREAK_EVEN:
            return (
                ReasonCode.TRAILING_STOP,
                f"Trailed out at {price:.2f}, stop was {state.stop_price:.2f} "
                f"(level {state.level}, peak {state.peak_price:.2f}).",
            )
        return (
            ReasonCode.STOP_LOSS,
            f"Stopped out at {price:.2f}, stop was {state.stop_price:.2f}.",
        )

    held = float(now_epoch) - state.opened_at_epoch
    if hold_seconds is not None:
        if held >= hold_seconds:
            return (
                ReasonCode.SIGNAL_WINDOW_ENDED,
                f"The signal's {hold_seconds / 60:.0f}-minute window is over "
                f"(best gain {state.peak_gain:+.2f} points).",
            )
        return None
    if held >= config.time_invalidation_seconds:
        # Measured against the PEAK gain, not the current one: "did not achieve +N points"
        # means it never got there, so a trade that ran up and came back has not timed out --
        # it is the trailing stop's business, and the ladder will have moved by then anyway.
        if state.peak_gain < config.time_invalidation_min_move_pts:
            return (
                ReasonCode.TIME_INVALIDATION,
                f"Went nowhere: best gain {state.peak_gain:+.2f} points in "
                f"{held:.0f}s, needed {config.time_invalidation_min_move_pts:+.2f}.",
            )
    return None
