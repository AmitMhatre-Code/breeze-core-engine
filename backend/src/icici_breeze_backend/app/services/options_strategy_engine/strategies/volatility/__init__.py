"""Volatility strategy calculators."""
from __future__ import annotations

from .long_butterfly import calc_long_butterfly
from .long_condor import calc_long_condor
from .long_straddle import calc_long_straddle
from .long_strangle import calc_long_strangle

__all__ = [
    "calc_long_butterfly",
    "calc_long_condor",
    "calc_long_straddle",
    "calc_long_strangle",
]
