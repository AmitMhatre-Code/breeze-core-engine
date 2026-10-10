"""The gate's verdict: one rule for every bot, live and replayed (docs/premium-gate-plan.md)."""
from __future__ import annotations

from typing import Any, Literal, Optional

from icici_breeze_backend.app.domain.bots import ReasonCode
from icici_breeze_backend.app.services.premium_gate.reading import Reading


def refusal(reading: Reading, gate: Any, side: Literal["sell", "buy"]) -> Optional[tuple[str, str]]:
    """(reason code, text) when the gate stops this trade, else None. An off gate never stops
    one; no reading always does."""
    if gate is None or not gate.enabled or reading.allows(side, gate.threshold):
        return None
    if not reading.available:
        return ReasonCode.PREMIUM_UNREADABLE, f"Holding off: {reading.describe()}."
    if side == "sell":
        return (ReasonCode.PREMIUM_NOT_RICH,
                f"Premium not rich enough to sell: {reading.describe()}; the gate needs "
                f"{gate.threshold:.2f}x.")
    return (ReasonCode.PREMIUM_NOT_CHEAP,
            f"Premium not cheap enough to buy: {reading.describe()}; the gate needs "
            f"{gate.threshold:.2f}x or less.")
