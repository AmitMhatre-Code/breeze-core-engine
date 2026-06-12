"""Options strategy engine package (Strategy Builder New)."""
from __future__ import annotations

from typing import Any

from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
    floor_lots,
    strategy_boundary_strikes,
    strike_window,
    tail_strikes_needed,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    STRATEGY_CATALOG,
    EngineContext,
    QuoteRow,
    StrategyResult,
    TradeLeg,
)
from icici_breeze_backend.app.services.user_rate_limit_prefs import get_icici_rate_limit_pause_seconds

_LAZY_EXPORTS = {
    "CATEGORY_CALCULATORS",
    "attach_margins_and_returns",
    "run_propose_trades",
    "build_liquidity_cache",
    "expand_chain_to_liquidity_boundary",
    "fetch_full_chain_side",
    "calc_bear_call_spread",
    "calc_bear_put_spread",
    "calc_bull_call_spread",
    "calc_bull_put_spread",
    "calc_iron_butterfly",
    "calc_iron_condor",
    "calc_long_butterfly",
    "calc_long_call",
    "calc_long_condor",
    "calc_long_put",
    "calc_long_straddle",
    "calc_long_strangle",
    "calc_naked_ce_short",
    "calc_naked_pe_short",
    "calc_short_straddle",
    "calc_short_strangle",
    "_attach_margins_and_returns",
    "_build_liquidity_cache",
    "_expand_chain_to_liquidity_boundary",
    "_fetch_full_chain_side",
    "_floor_lots",
    "_strategy_boundary_strikes",
    "_strike_window",
    "_tail_strikes_needed",
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        return _resolve_lazy(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _resolve_lazy(name: str) -> Any:
    if name == "CATEGORY_CALCULATORS":
        from icici_breeze_backend.app.services.options_strategy_engine.registry import CATEGORY_CALCULATORS

        return CATEGORY_CALCULATORS
    if name in ("attach_margins_and_returns", "_attach_margins_and_returns"):
        from icici_breeze_backend.app.services.options_strategy_engine.orchestrator import attach_margins_and_returns

        return attach_margins_and_returns
    if name == "run_propose_trades":
        from icici_breeze_backend.app.services.options_strategy_engine.orchestrator import run_propose_trades

        return run_propose_trades
    if name in ("build_liquidity_cache", "_build_liquidity_cache"):
        from icici_breeze_backend.app.services.options_strategy_engine.universe import build_liquidity_cache

        return build_liquidity_cache
    if name in ("expand_chain_to_liquidity_boundary", "_expand_chain_to_liquidity_boundary"):
        from icici_breeze_backend.app.services.options_strategy_engine.universe import expand_chain_to_liquidity_boundary

        return expand_chain_to_liquidity_boundary
    if name in ("fetch_full_chain_side", "_fetch_full_chain_side"):
        from icici_breeze_backend.app.services.options_strategy_engine.universe import fetch_full_chain_side

        return fetch_full_chain_side
    if name == "_floor_lots":
        return floor_lots
    if name == "_strategy_boundary_strikes":
        return strategy_boundary_strikes
    if name == "_strike_window":
        return strike_window
    if name == "_tail_strikes_needed":
        return tail_strikes_needed

    calc_map = {
        "calc_bear_call_spread": "icici_breeze_backend.app.services.options_strategy_engine.strategies.income",
        "calc_bull_put_spread": "icici_breeze_backend.app.services.options_strategy_engine.strategies.income",
        "calc_iron_butterfly": "icici_breeze_backend.app.services.options_strategy_engine.strategies.income",
        "calc_iron_condor": "icici_breeze_backend.app.services.options_strategy_engine.strategies.income",
        "calc_naked_ce_short": "icici_breeze_backend.app.services.options_strategy_engine.strategies.income",
        "calc_naked_pe_short": "icici_breeze_backend.app.services.options_strategy_engine.strategies.income",
        "calc_short_straddle": "icici_breeze_backend.app.services.options_strategy_engine.strategies.income",
        "calc_short_strangle": "icici_breeze_backend.app.services.options_strategy_engine.strategies.income",
        "calc_bull_call_spread": "icici_breeze_backend.app.services.options_strategy_engine.strategies.directional",
        "calc_bear_put_spread": "icici_breeze_backend.app.services.options_strategy_engine.strategies.directional",
        "calc_long_call": "icici_breeze_backend.app.services.options_strategy_engine.strategies.directional",
        "calc_long_put": "icici_breeze_backend.app.services.options_strategy_engine.strategies.directional",
        "calc_long_straddle": "icici_breeze_backend.app.services.options_strategy_engine.strategies.volatility",
        "calc_long_strangle": "icici_breeze_backend.app.services.options_strategy_engine.strategies.volatility",
        "calc_long_butterfly": "icici_breeze_backend.app.services.options_strategy_engine.strategies.volatility",
        "calc_long_condor": "icici_breeze_backend.app.services.options_strategy_engine.strategies.volatility",
    }
    if name in calc_map:
        import importlib

        mod = importlib.import_module(calc_map[name])
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "STRATEGY_CATALOG",
    "CATEGORY_CALCULATORS",
    "EngineContext",
    "QuoteRow",
    "StrategyResult",
    "TradeLeg",
    "attach_margins_and_returns",
    "build_liquidity_cache",
    "calc_bear_call_spread",
    "calc_bear_put_spread",
    "calc_bull_call_spread",
    "calc_bull_put_spread",
    "calc_iron_butterfly",
    "calc_iron_condor",
    "calc_long_butterfly",
    "calc_long_call",
    "calc_long_condor",
    "calc_long_put",
    "calc_long_straddle",
    "calc_long_strangle",
    "calc_naked_ce_short",
    "calc_naked_pe_short",
    "calc_short_straddle",
    "calc_short_strangle",
    "expand_chain_to_liquidity_boundary",
    "fetch_full_chain_side",
    "floor_lots",
    "get_icici_rate_limit_pause_seconds",
    "run_propose_trades",
    "strategy_boundary_strikes",
    "strike_window",
    "tail_strikes_needed",
    "_attach_margins_and_returns",
    "_build_liquidity_cache",
    "_expand_chain_to_liquidity_boundary",
    "_fetch_full_chain_side",
    "_floor_lots",
    "_strategy_boundary_strikes",
    "_strike_window",
    "_tail_strikes_needed",
]
