"""Which signal a bot trades on, and whether it may (docs/signals-streamline-plan.md sections 5, 7).

One place answers "does this bot, as configured, read a signal -- and which?", so the save path,
the arm path, the runtime and the backtests can never disagree about it:

* Bot 3 (momentum scalper) always trades its `signal`.
* Bot 4 (iron fly) reads one only when its entry filter is `signal_quiet`.
* CAS Bingo reads one for its debit and credit spreads; the long strangle reads none.
* Bots 1 and 2 read none.

A signal is available to bots once a signal backtest has covered 30 days on its current version
(`index_signal.gate`). The gate applies to Simulation and Live alike; backtests are never gated.
"""
from __future__ import annotations

from typing import Any, Optional

from icici_breeze_backend.app.db.bots_migrate import (
    BOT_CAS_BINGO,
    BOT_IRON_FLY_SCALPER,
    BOT_MOMENTUM_LONG_SCALPER,
)


def signal_in_use(bot_type: str, config: Any) -> Optional[Any]:
    """The bot's `SignalChoice` when its current settings read one, else None."""
    if bot_type == BOT_MOMENTUM_LONG_SCALPER:
        return getattr(config, "signal", None)
    if bot_type == BOT_IRON_FLY_SCALPER:
        f = getattr(config, "entry_filter", None)
        return f.signal if f is not None and f.kind == "signal_quiet" else None
    if bot_type == BOT_CAS_BINGO:
        if getattr(config, "strategy", None) in ("debit_spread", "credit_spread"):
            return getattr(config, "signal", None)
    return None


def refusal(bot_type: str, config: Any) -> Optional[str]:
    """None when the bot may act on its signal (or reads none); otherwise why not."""
    choice = signal_in_use(bot_type, config)
    if choice is None:
        return None
    from icici_breeze_backend.app.services.index_signal import gate

    reason = gate.refusal(choice.mechanism)
    if reason is None:
        return None
    return f"{choice.label()} is not yet available to bots. {reason}"
