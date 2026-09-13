"""CAS Bingo's entry triggers (docs/bots-cas-bingo-plan.md section 3). Pure: no I/O, no clock.

Every read of the signal's *history* comes from the shadow log's rows (`index_signal_log`,
oldest first), which the publisher writes on every state change and once a minute. Reading the
durable log rather than watching the reader in-process is what lets a restart at 15:18 still
know about the flip at 15:12.

**What a flip is** is the shadow log's own definition, deliberately: a transition into
`bullish` or `bearish` from a *live* reading (neutral or the other side). A transition out of
`unavailable` is the signal waking up -- warm-up, coverage back, the auction books refilling
after 15:20 -- not the order books changing their mind, and the readiness verdict that gates
Autonomous never scored those. Trading on a definition the evidence did not measure would make
the gate meaningless.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from icici_breeze_backend.app.core.timezone import IST

DIRECTIONAL = ("bullish", "bearish")
_FLIP_FROM = frozenset({"neutral", "bullish", "bearish"})

# Cash stocks take auction orders from 15:20 (none 15:15-15:20); only then do constituents
# publish equilibrium prices, so only then is the index's indicative value a settlement
# estimate. SEBI's 2026-09-12 consultation may change the session -- re-read plan section 0.
AUCTION_ORDER_ENTRY_IST = "15:20"


@dataclass(frozen=True)
class Flip:
    ts: float
    state: str  # "bullish" | "bearish"
    signal: Optional[float]
    spot: Optional[float]


@dataclass(frozen=True)
class Trigger:
    right: str  # "call" | "put" -- the side the structure goes on
    text: str
    flip: Optional[Flip] = None
    # The index level the trigger judged against, when strikes must be measured from that
    # same reading (the auction rule's indicative index) rather than a fresh one.
    level: Optional[float] = None


@dataclass(frozen=True)
class Verdict:
    """`trigger` is None when nothing fires; `reason` then says why, for the run log."""

    trigger: Optional[Trigger]
    reason: str


def _f(raw: Any) -> Optional[float]:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def hhmm(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts, IST).strftime("%H:%M")


def in_windows(ts: float, windows: Sequence[tuple[str, str]]) -> bool:
    """Zero-padded HH:MM compares correctly as a string, as everywhere else in the bots."""
    t = hhmm(ts)
    return any(start <= t < end for start, end in windows)


def last_flip(rows: Sequence[dict[str, Any]]) -> tuple[Optional[Flip], str]:
    """The current episode's flip, or (None, why there is none).

    Only the LAST transition matters: an earlier flip that has since been reversed or dropped
    back to neutral is a call the signal withdrew.
    """
    prev_state: Optional[str] = None
    last: Optional[dict[str, Any]] = None
    before_last: Optional[str] = None
    for row in rows:
        if row.get("kind") == "transition":
            last, before_last = row, prev_state
        prev_state = str(row.get("state") or "")
    if last is None:
        return None, "No signal flip today yet."
    state = str(last.get("state") or "")
    if state not in DIRECTIONAL:
        return None, f"The signal's latest move was to {state or 'nothing'} at {hhmm(float(last['ts']))}."
    if before_last not in _FLIP_FROM:
        return None, (
            f"The signal woke up {state} at {hhmm(float(last['ts']))} (from "
            f"{before_last or 'no reading'}); that is not a flip."
        )
    return (
        Flip(
            ts=float(last["ts"]),
            state=state,
            signal=_f(last.get("signal")),
            spot=_f(last.get("spot")),
        ),
        "",
    )


def _peak_since(rows: Sequence[dict[str, Any]], flip: Flip, live_signal: Optional[float]) -> float:
    """Largest |signal| seen on the flipped side since the flip, the live reading included."""
    peak = abs(flip.signal) if flip.signal is not None else 0.0
    for row in rows:
        if float(row.get("ts") or 0) < flip.ts or row.get("state") != flip.state:
            continue
        value = _f(row.get("signal"))
        if value is not None:
            peak = max(peak, abs(value))
    if live_signal is not None:
        peak = max(peak, abs(float(live_signal)))
    return peak


def _live_matches(flip: Flip, live_state: str) -> Optional[str]:
    if live_state not in DIRECTIONAL:
        return f"The signal is {live_state} now."
    if live_state != flip.state:
        # Only possible between the reader and the log disagreeing for one publish.
        return f"The signal is {live_state} now, not the {flip.state} it flipped to."
    return None


def evaluate_debit(
    rows: Sequence[dict[str, Any]],
    *,
    live_state: str,
    live_signal: Optional[float],
    now_ts: float,
    windows: Sequence[tuple[str, str]],
    strong_threshold: float,
    sustain_seconds: float,
) -> Verdict:
    """Section 3.1: a strong flip, then the same side held for the sustain period."""
    flip, why = last_flip(rows)
    if flip is None:
        return Verdict(None, why)
    mismatch = _live_matches(flip, live_state)
    if mismatch:
        return Verdict(None, mismatch)
    if not in_windows(flip.ts, windows):
        return Verdict(None, f"The {flip.state} flip at {hhmm(flip.ts)} was outside the entry windows.")
    peak = _peak_since(rows, flip, live_signal)
    if peak < strong_threshold:
        return Verdict(
            None,
            f"{flip.state.capitalize()} since {hhmm(flip.ts)}, but the signal has peaked at "
            f"{peak:.2f}, short of the {strong_threshold:.2f} strong threshold.",
        )
    held = now_ts - flip.ts
    if held < sustain_seconds:
        return Verdict(
            None,
            f"Strong {flip.state} flip at {hhmm(flip.ts)}; held {held / 60:.1f} of "
            f"{sustain_seconds / 60:.1f} minutes.",
        )
    right = "call" if flip.state == "bullish" else "put"
    return Verdict(
        Trigger(
            right=right,
            flip=flip,
            text=(
                f"Strong {flip.state} flip at {hhmm(flip.ts)} (peak {peak:.2f}), held "
                f"{held / 60:.1f} min: {right} debit spread."
            ),
        ),
        "",
    )


def evaluate_credit(
    rows: Sequence[dict[str, Any]],
    *,
    live_state: str,
    now_ts: float,
    windows: Sequence[tuple[str, str]],
    day_open: Optional[float],
    move_trigger_pct: float,
) -> Verdict:
    """Section 3.2: the index has moved from the open, then the signal flips against it.

    The move is read at the flip itself, from the spot the shadow log recorded with it -- the
    question is whether the reversal came *after* the move, not whether the move is still
    there by the time this pass runs.
    """
    del now_ts  # the flip carries its own time; kept for a symmetric call shape
    if not day_open or day_open <= 0:
        return Verdict(None, "The day's open is not known, so the move cannot be measured.")
    flip, why = last_flip(rows)
    if flip is None:
        return Verdict(None, why)
    mismatch = _live_matches(flip, live_state)
    if mismatch:
        return Verdict(None, mismatch)
    if not in_windows(flip.ts, windows):
        return Verdict(None, f"The {flip.state} flip at {hhmm(flip.ts)} was outside the entry windows.")
    if not flip.spot:
        return Verdict(None, f"The flip at {hhmm(flip.ts)} carries no index level to measure against.")
    move = (flip.spot - day_open) / day_open * 100.0
    if move >= move_trigger_pct and flip.state == "bearish":
        right = "call"
    elif move <= -move_trigger_pct and flip.state == "bullish":
        right = "put"
    else:
        return Verdict(
            None,
            f"{flip.state.capitalize()} flip at {hhmm(flip.ts)} with the index {move:+.2f}% "
            f"from the open; needs a {move_trigger_pct:.2f}% move the other way first.",
        )
    sold = "CE" if right == "call" else "PE"
    return Verdict(
        Trigger(
            right=right,
            flip=flip,
            text=(
                f"Index {move:+.2f}% from the open, then a {flip.state} flip at "
                f"{hhmm(flip.ts)}: sell a {sold} credit spread."
            ),
        ),
        "",
    )


def evaluate_auction_credit(
    *, indicative: Optional[float], day_open: Optional[float], now_ts: float
) -> Verdict:
    """Section 3.2b: inside the auction, the credit side is the side the index has moved to.

    No flip is read: from 15:20 the depth feed shows auction books the readiness evidence never
    scored. Whether the chosen side is actually worth selling -- still priced well above what
    it would settle at -- is `plan.build_plan(auction=True)`'s question, because it needs the
    chain.
    """
    if not indicative or indicative <= 0:
        return Verdict(None, "No fresh indicative index level yet.")
    if not day_open or day_open <= 0:
        return Verdict(None, "The day's open is not known, so the auction's direction cannot be told.")
    move = (indicative - day_open) / day_open * 100.0
    right = "call" if move >= 0 else "put"
    sold = "CE" if right == "call" else "PE"
    where = "above" if right == "call" else "below"
    return Verdict(
        Trigger(
            right=right,
            level=float(indicative),
            text=(
                f"Indicative index {indicative:,.2f} at {hhmm(now_ts)} ({move:+.2f}% from the "
                f"open): sell a {sold} credit spread {where} it if it is still priced."
            ),
        ),
        "",
    )


def strangle_due(now_ts: float, entry_time_ist: str, windows: Sequence[tuple[str, str]]) -> Verdict:
    """Section 3.3: the clock, from `entry_time_ist` until the window it sits in closes."""
    t = hhmm(now_ts)
    if t < entry_time_ist:
        return Verdict(None, f"The long strangle fires at {entry_time_ist}.")
    for start, end in windows:
        if start <= entry_time_ist < end:
            if t >= end:
                return Verdict(None, f"The {start}-{end} window closed before the strangle could enter.")
            return Verdict(Trigger(right="both", text=f"{entry_time_ist}: long strangle."), "")
    return Verdict(None, f"The strangle's entry time {entry_time_ist} is outside every window.")
