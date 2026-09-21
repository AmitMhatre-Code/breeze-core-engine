"""The vocabulary every signal speaks: its states and the reasons it gives for them.

"unavailable" is a state of its own and never "neutral" (docs/design-decisions.md #30). Neutral is
a reading -- the market was looked at and nothing fired. Unavailable means there is no reading:
the session is shut, the feed is stale, the baseline is not built yet. A consumer deciding whether
to trade must be able to tell those apart, so anything but "bullish"/"bearish" is no trade.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

SignalState = Literal["bullish", "bearish", "neutral", "unavailable"]
DirectionalState = Literal["bullish", "bearish", "neutral"]
DIRECTIONAL: tuple[str, ...] = ("bullish", "bearish")

# Why there is no reading at all -- each of these publishes `unavailable`.
REASON_MARKET_CLOSED = "market_closed"
REASON_OUTSIDE_SESSION = "outside_session"
REASON_WARMING_UP = "warming_up"
REASON_NO_BARS = "no_bars"
REASON_STALE = "stale"
REASON_EXCLUDED_SESSION = "excluded_session"
REASON_NO_OI = "no_open_interest"
REASON_VOLUME_UNKNOWN = "volume_unavailable"
REASON_ANCHOR_NOT_TRADED = "anchor_not_traded"
REASON_VWAP_UNKNOWN = "vwap_unavailable"
REASON_NOT_PUBLISHED = "not_published"

# A reading that did not call -- each of these publishes `neutral`.
REASON_NO_EXPANSION = "no_expansion"
REASON_UNWIND = "unwind"
REASON_VOLUME_LOW = "volume_below_threshold"
REASON_NO_CONFLUENCE = "no_confluence"

NO_READING_REASONS = frozenset(
    {
        REASON_MARKET_CLOSED,
        REASON_OUTSIDE_SESSION,
        REASON_WARMING_UP,
        REASON_NO_BARS,
        REASON_STALE,
        REASON_EXCLUDED_SESSION,
        REASON_NO_OI,
        REASON_VOLUME_UNKNOWN,
        REASON_ANCHOR_NOT_TRADED,
        REASON_VWAP_UNKNOWN,
        REASON_NOT_PUBLISHED,
    }
)


@dataclass(frozen=True)
class Evaluation:
    """What a mechanism said when a bar (or a candle) completed. `side` None is no call, and
    `reason` then says whether that is a quiet market (neutral) or no reading (unavailable)."""

    side: Optional[str]
    strength: Optional[float]
    components: dict[str, Any] = field(default_factory=dict)
    reason: Optional[str] = None
