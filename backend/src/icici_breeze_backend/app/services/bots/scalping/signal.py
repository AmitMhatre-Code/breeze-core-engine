"""The momentum entry signal (docs/bots-scalping-plan.md section 3.2).

Pure. Given completed candles, a session VWAP and the config, it returns a side or None
along with every number it used -- the numbers matter as much as the verdict, because an
unexplained no-trade day is the failure the run log exists to prevent, and "no signal" is
the most common thing a scalper does all session.

Evaluated on the **last completed candle**, never the bar in progress. A signal read off a
partial bar changes as the bar fills and would fire, unfire and re-fire within the same
minute against an EMA that has not moved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Sequence

from icici_breeze_backend.app.domain.bots import MomentumSignalConfig
from icici_breeze_backend.app.services.bots.scalping.candles import Candle, ema_of

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


def evaluate_momentum(
    candles: Sequence[Candle],
    session_vwap: Optional[float],
    config: MomentumSignalConfig,
) -> SignalResult:
    """Bullish when the last close is above both the EMA and VWAP on above-average volume.

    All three conditions are required in the same direction. The volume filter is what keeps
    this from firing on a drifting close in a dead market, and it is also the reason the
    signal runs on futures rather than the index -- an index has no volume to compare.
    """
    completed = list(candles)
    required = max(config.ema_period, config.volume_ma_period)
    if len(completed) < required:
        return SignalResult(
            None,
            "not_enough_candles",
            {"candles": len(completed), "candles_required": required},
        )

    last = completed[-1]
    ema = ema_of([c.close for c in completed], config.ema_period)
    if ema is None:
        return SignalResult(None, "ema_unavailable", {"candles": len(completed)})

    window = [c.volume for c in completed[-config.volume_ma_period :]]
    if any(v is None for v in window):
        # An unknown bar is not a zero-volume bar -- see `candles.Candle.volume`. Refusing
        # here is what stops a feed gap from manufacturing a volume surge out of a low mean.
        return SignalResult(None, "volume_unavailable", {"candles": len(completed)})
    volume_ma = sum(int(v) for v in window if v is not None) / float(config.volume_ma_period)

    if config.require_vwap and session_vwap is None:
        return SignalResult(None, "vwap_unavailable", {"candles": len(completed)})

    threshold = volume_ma * config.volume_multiplier
    last_volume = last.volume
    values: dict[str, Any] = {
        "close": last.close,
        "ema": round(ema, 4),
        "session_vwap": round(session_vwap, 4) if session_vwap is not None else None,
        "volume": last_volume,
        "volume_ma": round(volume_ma, 2),
        "volume_threshold": round(threshold, 2),
        "candle_start": last.start,
    }

    if last_volume is None or last_volume <= threshold:
        return SignalResult(None, "volume_below_threshold", values)

    above_vwap = session_vwap is None or last.close > session_vwap
    below_vwap = session_vwap is None or last.close < session_vwap
    if last.close > ema and above_vwap:
        return SignalResult("bullish", "confluence", values)
    if last.close < ema and below_vwap:
        return SignalResult("bearish", "confluence", values)
    return SignalResult(None, "no_confluence", values)


# Verdicts that mean the signal is genuinely off. The data-gap reasons (not_enough_candles,
# ema_unavailable, volume_unavailable, vwap_unavailable) are deliberately absent: not knowing
# whether the signal fired is not evidence that it stopped.
_DECISIVE_OFF = frozenset({"volume_below_threshold", "no_confluence"})


def signal_run_unbroken(
    candles: Sequence[Candle],
    config: MomentumSignalConfig,
    *,
    entry_candle_start: int,
    side: str,
) -> bool:
    """True while the run of candles that opened the last trade has not ended.

    One trade per signal run (docs/bots-scalping-plan.md section 3.4). The signal is a
    *state* -- close above EMA and VWAP on volume stays true minute after minute -- so without
    this rule a stopped-out position is re-bought two seconds later on the same reading. On
    the 10-11 Sep paper days that happened seven times: one winner, -Rs 3,706 net.

    The run ends at the first completed candle after the entry candle that decisively did not
    fire `side`: low volume, no confluence, or the opposite side. A candle that went off
    *while the position was still held* counts -- the 11 Sep 13:55 re-entry (+Rs 1,769) came
    after the signal lapsed mid-hold and fired again, and is exactly the trade to keep.

    Each candle is replayed with its own VWAP (`Candle.vwap`), so the verdict is what the
    signal read at that minute, not a re-reading against today's latest VWAP. After a restart
    the candles from before it are gone; the run is then held unbroken until the rebuilt
    history shows an off-candle, which is the conservative reading.
    """
    completed = list(candles)
    for k, candle in enumerate(completed):
        if candle.start <= entry_candle_start:
            continue
        verdict = evaluate_momentum(completed[: k + 1], candle.vwap, config)
        if verdict.side is not None and verdict.side != side:
            return False
        if verdict.side is None and verdict.reason in _DECISIVE_OFF:
            return False
    return True


# --------------------------------------------------------------------------------------
# Signal variants (#38): the entry read from a published variant instead of the candles
# --------------------------------------------------------------------------------------


def evaluate_variant(payload: dict[str, Any], variant_id: str) -> SignalResult:
    """A signal variant's published reading as an entry verdict.

    The payload's state is already the *traded* side -- a fade variant has swapped it -- so
    this only translates. `values["candle_start"]` carries when the call began, which is the
    same field the momentum signal uses for its signal run: that is what lets the fresh-signal
    rule and the day totals treat "one trade per call" and "one trade per run" identically.
    Anything that is not a call is no trade, and `unavailable` says why rather than reading as
    a quiet market (#30).
    """
    state = str(payload.get("state") or "unavailable")
    values: dict[str, Any] = {
        "source": "variant",
        "variant_id": variant_id,
        "variant_name": payload.get("variant_name"),
        "direction": payload.get("direction"),
        "state": state,
        "source_state": payload.get("source_state"),
        "strength": payload.get("signal"),
        "components": payload.get("components") or {},
        "hold_minutes": payload.get("hold_minutes"),
        "held_until": payload.get("held_until"),
    }
    started = payload.get("call_started_at")
    if state in SIDE_TO_RIGHT and started is not None:
        values["candle_start"] = int(float(started))
        return SignalResult(state, "variant_call", values)  # type: ignore[arg-type]
    if state == "unavailable":
        return SignalResult(None, f"signal_unavailable:{payload.get('reason') or 'unknown'}", values)
    return SignalResult(None, "no_call", values)


def variant_call_unbroken(
    payload: dict[str, Any], *, entry_candle_start: int, side: str
) -> bool:
    """True while the call that opened the last trade is still the live call.

    A variant's call is one run by construction: it starts when it fires and ends when it
    lapses or turns, and a re-fire while it is held extends it rather than starting another
    (`expansion.ExpansionEngine`). So the check is only whether the live call is that one.
    """
    started = payload.get("call_started_at")
    if str(payload.get("state") or "") != side or started is None:
        return False
    return int(float(started)) == int(entry_candle_start)
