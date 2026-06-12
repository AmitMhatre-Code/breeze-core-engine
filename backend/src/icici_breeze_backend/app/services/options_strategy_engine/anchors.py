"""Anchor strike indexing and strategy windows (OpenAI §5.2, §6)."""
from __future__ import annotations

from dataclasses import dataclass

from icici_breeze_backend.app.services.options_strategy_engine.helpers import nearest_atm
from icici_breeze_backend.app.services.options_strategy_engine.types import StrategyId


@dataclass
class AnchorIndex:
    atm: int
    step: int
    spot: float
    ce_by_strike: dict[int, int]  # strike -> OTM step (positive = OTM)
    pe_by_strike: dict[int, int]
    otm_ce: dict[int, int]  # step -> strike
    otm_pe: dict[int, int]
    itm_ce: dict[int, int]
    itm_pe: dict[int, int]


SHORT_PREMIUM_MAX_STEPS = 5
LONG_PREMIUM_MAX_STEPS = 8
SPREAD_MAX_STEPS = 6
CONDOR_MAX_STEPS = 5


def build_anchor_index(strikes: list[int], spot: float, step: int) -> AnchorIndex:
    atm = nearest_atm(strikes, spot)
    ce_above = sorted(s for s in strikes if s > atm)
    ce_below = sorted((s for s in strikes if s < atm), reverse=True)
    pe_below = sorted((s for s in strikes if s < atm), reverse=True)
    pe_above = sorted(s for s in strikes if s > atm)

    otm_ce: dict[int, int] = {}
    itm_ce: dict[int, int] = {}
    ce_by_strike: dict[int, int] = {}
    for i, s in enumerate(ce_above, start=1):
        otm_ce[i] = s
        ce_by_strike[s] = i
    for i, s in enumerate(ce_below, start=1):
        itm_ce[i] = s
        ce_by_strike[s] = -i

    otm_pe: dict[int, int] = {}
    itm_pe: dict[int, int] = {}
    pe_by_strike: dict[int, int] = {}
    for i, s in enumerate(pe_below, start=1):
        otm_pe[i] = s
        pe_by_strike[s] = i
    for i, s in enumerate(pe_above, start=1):
        itm_pe[i] = s
        pe_by_strike[s] = -i

    ce_by_strike[atm] = 0
    pe_by_strike[atm] = 0

    return AnchorIndex(
        atm=atm,
        step=step,
        spot=spot,
        ce_by_strike=ce_by_strike,
        pe_by_strike=pe_by_strike,
        otm_ce=otm_ce,
        otm_pe=otm_pe,
        itm_ce=itm_ce,
        itm_pe=itm_pe,
    )


def max_steps_for_strategy(strategy_id: StrategyId) -> int:
    if strategy_id in {
        "short_straddle",
        "short_strangle",
        "naked_ce_short",
        "naked_pe_short",
        "iron_condor",
        "iron_butterfly",
        "bull_put_spread",
        "bear_call_spread",
    }:
        return SHORT_PREMIUM_MAX_STEPS
    if strategy_id in {"long_straddle", "long_strangle", "long_call", "long_put"}:
        return LONG_PREMIUM_MAX_STEPS
    if strategy_id in {"long_butterfly", "long_condor", "iron_condor", "iron_butterfly"}:
        return CONDOR_MAX_STEPS
    return SPREAD_MAX_STEPS


def strikes_in_window(
    liquid_strikes: list[int],
    anchors: AnchorIndex,
    right: str,
    max_steps: int,
) -> list[int]:
    by_strike = anchors.ce_by_strike if right == "Call" else anchors.pe_by_strike
    out: list[int] = []
    for s in liquid_strikes:
        step_off = abs(by_strike.get(s, 99))
        if step_off <= max_steps:
            out.append(s)
    if anchors.atm in liquid_strikes and anchors.atm not in out:
        out.append(anchors.atm)
    return sorted(out)


STRANGLE_OTM_PAIRS: tuple[tuple[int, int], ...] = (
    (2, 2),
    (3, 3),
    (2, 3),
    (3, 2),
)
