"""Bot 3's entry, read from the signal grid (docs/signals-streamline-plan.md section 7).

The bot computes no signal of its own any more: momentum and volume expansion are shared
mechanisms published by `index_signal`, and the bot names one cell of the grid plus a direction
(`SignalChoice`). This module turns that cell's reading into an entry verdict, and answers the
two questions a held trade asks of it: is the call that opened me still the live call, and has
it ended.

Pure: the caller reads the payload (`index_signal.reader.get_signal`, already turned the bot's
way) and passes it in, live and in replay alike.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

SignalSide = Literal["bullish", "bearish"]

# A bullish signal buys the ATM call, a bearish one the ATM put -- the bot is long-only, so
# "bearish" is a view expressed by buying a put, never by selling anything.
SIDE_TO_RIGHT: dict[str, str] = {"bullish": "call", "bearish": "put"}


@dataclass(frozen=True)
class SignalResult:
    side: Optional[SignalSide]
    reason: str
    values: dict[str, Any] = field(default_factory=dict)

    @property
    def fired(self) -> bool:
        return self.side is not None

    @property
    def right(self) -> Optional[str]:
        return SIDE_TO_RIGHT.get(self.side or "")


def evaluate_reading(payload: dict[str, Any], series_id: str) -> SignalResult:
    """A published reading (turned the bot's way) as an entry verdict.

    `values["candle_start"]` carries when the call began: the fresh-signal rule and the day
    totals key "one trade per call" on it. Anything that is not a call is no trade, and
    `unavailable` says why rather than reading as a quiet market (#30)."""
    state = str(payload.get("state") or "unavailable")
    values: dict[str, Any] = {
        "source": "signal",
        "series": series_id,
        "mechanism": payload.get("mechanism"),
        "duration_minutes": payload.get("duration_minutes"),
        "direction": payload.get("direction"),
        "state": state,
        "source_state": payload.get("source_state"),
        "strength": payload.get("signal"),
        "components": payload.get("components") or {},
        "call_started_at": payload.get("call_started_at"),
        "held_until": payload.get("held_until"),
    }
    started = payload.get("call_started_at")
    if state in SIDE_TO_RIGHT and started is not None:
        values["candle_start"] = int(float(started))
        return SignalResult(state, "signal_call", values)  # type: ignore[arg-type]
    if state == "unavailable":
        return SignalResult(None, f"signal_unavailable:{payload.get('reason') or 'unknown'}", values)
    return SignalResult(None, "no_call", values)


def call_unbroken(payload: dict[str, Any], *, entry_candle_start: int, side: str) -> bool:
    """True while the call that opened the last trade is still the live call.

    A call is one run by construction: it starts when it fires, a re-fire while it stands
    extends it, and it ends when it lapses or turns (`index_signal.series`). So the check is
    only whether the live call is that one."""
    started = payload.get("call_started_at")
    if str(payload.get("state") or "") != side or started is None:
        return False
    return int(float(started)) == int(entry_candle_start)


def call_reversed(payload: dict[str, Any], *, side: str) -> bool:
    """True when the live reading is a call the *other* way from the trade that is open.

    This is what closes a signal trade now. It used to be closed when its call simply ran out
    (`call_ended`, decision 5), which welded how long a trade may be held to the length of the
    window that produced it: a one-minute call meant a one-minute maximum hold, and the trailing
    stop -- the whole mechanism for letting a winner run -- never got a chance to move. Measured
    over 117 sessions, 89% of one-minute calls ended by simply lapsing, so nearly every trade was
    closed by that clock rather than by anything about the trade.

    A call merely lapsing to quiet is now nothing: the stop and the trailing stop decide, and the
    hard square-off still flattens everything before the close (`decide`). But a call fired the
    other way is not the clock running out, it is the signal saying the opposite of what opened
    the trade, and holding through that would be holding a position the bot's own signal is now
    against. `unavailable` is not a reversal and never closes anything (#30).

    `payload` has already been turned the bot's way, so for a bot set to fade, "the other way"
    means the other way round for the bot, not for the mechanism (`index_signal.series`)."""
    state = str(payload.get("state") or "unavailable")
    return state in ("bullish", "bearish") and state != side


def call_ended(
    payload: dict[str, Any], *, started_at: float, side: str, known_until: Optional[float], now: float
) -> tuple[bool, Optional[float]]:
    """(ended, the call's latest known end) for a trade opened on the call begun at `started_at`.

    The call still standing extends `known_until`. A reading that shows anything else -- quiet,
    the other side, a new call -- ends it. An *unreadable* reading (a feed blip, a stale
    publisher) is not evidence either way, so the trade is held until the call would have
    lapsed on its own, and closed then."""
    state = str(payload.get("state") or "unavailable")
    if call_unbroken(payload, entry_candle_start=int(started_at), side=side):
        until = payload.get("held_until")
        try:
            return False, float(until) if until is not None else known_until
        except (TypeError, ValueError):
            return False, known_until
    if state == "unavailable":
        return (known_until is not None and now >= known_until), known_until
    return True, known_until
