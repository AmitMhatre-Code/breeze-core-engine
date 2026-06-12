"""Strategy calculator registry by category."""
from __future__ import annotations

from typing import Callable

from icici_breeze_backend.app.services.options_strategy_engine.strategies.directional import (
    calc_bear_put_spread,
    calc_bull_call_spread,
    calc_long_call,
    calc_long_put,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income import (
    calc_bear_call_spread,
    calc_bull_put_spread,
    calc_iron_butterfly,
    calc_iron_condor,
    calc_naked_ce_short,
    calc_naked_pe_short,
    calc_short_straddle,
    calc_short_strangle,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.volatility import (
    calc_long_butterfly,
    calc_long_condor,
    calc_long_straddle,
    calc_long_strangle,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, StrategyCategory, StrategyResult

CATEGORY_CALCULATORS: dict[StrategyCategory, list[Callable[[EngineContext], StrategyResult]]] = {
    "income": [
        calc_naked_ce_short,
        calc_naked_pe_short,
        calc_iron_condor,
        calc_iron_butterfly,
        calc_short_strangle,
        calc_short_straddle,
        calc_bull_put_spread,
        calc_bear_call_spread,
    ],
    "directional": [
        calc_bull_call_spread,
        calc_bear_put_spread,
        calc_long_call,
        calc_long_put,
    ],
    "volatility": [
        calc_long_straddle,
        calc_long_strangle,
        calc_long_butterfly,
        calc_long_condor,
    ],
}
