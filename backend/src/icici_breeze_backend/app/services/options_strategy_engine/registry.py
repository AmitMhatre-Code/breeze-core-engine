"""Strategy calculator registry by category."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Union

from icici_breeze_backend.app.services.options_strategy_engine.strategies.directional.bear_put_spread import (
    calc_bear_put_spread,
    prefetch_bear_put_spread,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.directional.bull_call_spread import (
    calc_bull_call_spread,
    prefetch_bull_call_spread,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.directional.long_call import (
    calc_long_call,
    prefetch_long_call,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.directional.long_put import (
    calc_long_put,
    prefetch_long_put,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.bear_call_spread import (
    calc_bear_call_spread,
    prefetch_bear_call_spread,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.bull_put_spread import (
    calc_bull_put_spread,
    prefetch_bull_put_spread,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.iron_butterfly import (
    calc_iron_butterfly,
    prefetch_iron_butterfly,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.iron_condor import (
    calc_iron_condor,
    prefetch_iron_condor_strikes,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.naked_ce_short import (
    calc_naked_ce_short,
    prefetch_naked_ce_short,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.naked_pe_short import (
    calc_naked_pe_short,
    prefetch_naked_pe_short,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.short_straddle import (
    calc_short_straddle,
    prefetch_short_straddle,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.short_strangle import (
    calc_short_strangle,
    prefetch_short_strangle,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.volatility.long_butterfly import (
    calc_long_butterfly,
    prefetch_long_butterfly,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.volatility.long_condor import (
    calc_long_condor,
    prefetch_long_condor,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.volatility.long_straddle import (
    calc_long_straddle,
    prefetch_long_straddle,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.volatility.long_strangle import (
    calc_long_strangle,
    prefetch_long_strangle,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    Right,
    StrategyCategory,
    StrategyResult,
)

StrategyCalcResult = Union[StrategyResult, list[StrategyResult]]
StrategyCalcFn = Callable[[EngineContext], StrategyCalcResult | Awaitable[StrategyCalcResult]]
PrefetchFn = Callable[[EngineContext], set[tuple[int, Right]]]


@dataclass(frozen=True)
class StrategySpec:
    calc: StrategyCalcFn
    prefetch: PrefetchFn | None = None


CATEGORY_STRATEGIES: dict[StrategyCategory, list[StrategySpec]] = {
    "income": [
        StrategySpec(calc_naked_ce_short, prefetch_naked_ce_short),
        StrategySpec(calc_naked_pe_short, prefetch_naked_pe_short),
        StrategySpec(calc_iron_condor, prefetch_iron_condor_strikes),
        StrategySpec(calc_iron_butterfly, prefetch_iron_butterfly),
        StrategySpec(calc_short_strangle, prefetch_short_strangle),
        StrategySpec(calc_short_straddle, prefetch_short_straddle),
        StrategySpec(calc_bull_put_spread, prefetch_bull_put_spread),
        StrategySpec(calc_bear_call_spread, prefetch_bear_call_spread),
    ],
    "bullish": [
        StrategySpec(calc_bull_call_spread, prefetch_bull_call_spread),
        StrategySpec(calc_long_call, prefetch_long_call),
    ],
    "bearish": [
        StrategySpec(calc_bear_put_spread, prefetch_bear_put_spread),
        StrategySpec(calc_long_put, prefetch_long_put),
    ],
    "volatility": [
        StrategySpec(calc_long_straddle, prefetch_long_straddle),
        StrategySpec(calc_long_strangle, prefetch_long_strangle),
        StrategySpec(calc_long_butterfly, prefetch_long_butterfly),
        StrategySpec(calc_long_condor, prefetch_long_condor),
    ],
}

CATEGORY_CALCULATORS: dict[StrategyCategory, list[StrategyCalcFn]] = {
    category: [spec.calc for spec in specs]
    for category, specs in CATEGORY_STRATEGIES.items()
}


def prefetch_for_category(category: StrategyCategory) -> list[PrefetchFn]:
    return [spec.prefetch for spec in CATEGORY_STRATEGIES[category] if spec.prefetch is not None]
