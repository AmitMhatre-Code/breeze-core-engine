"""Income strategy calculators — ATM delta-anchored."""
from __future__ import annotations

from .bear_call_spread import calc_bear_call_spread
from .bull_put_spread import calc_bull_put_spread
from .iron_butterfly import calc_iron_butterfly
from .iron_condor import calc_iron_condor
from .naked_ce_short import calc_naked_ce_short
from .naked_pe_short import calc_naked_pe_short
from .short_straddle import calc_short_straddle
from .short_strangle import calc_short_strangle

__all__ = [
    "calc_bear_call_spread",
    "calc_bull_put_spread",
    "calc_iron_butterfly",
    "calc_iron_condor",
    "calc_naked_ce_short",
    "calc_naked_pe_short",
    "calc_short_straddle",
    "calc_short_strangle",
]
