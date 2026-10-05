"""Dynamic Iron Condor campaign settings (docs/dynamic-iron-condor-plan.md).

One model serves the Portfolio campaign, the backtest and the bot, so a setting means the
same thing in all three. Every default here is a starting point the backtest must calibrate
(plan section 3), not a finding.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from icici_breeze_backend.app.domain.bots import HHMM_PATTERN

CondorUnderlying = Literal["NIFTY"]
# `monthly` keeps to each month's last listed expiry, where far-month liquidity sits; `any`
# allows weeklies.
CondorExpiryKind = Literal["monthly", "any"]
CondorCheckKind = Literal["sod", "eod"]


class CondorSettings(BaseModel):
    underlying: CondorUnderlying = "NIFTY"
    expiry_kind: CondorExpiryKind = "monthly"

    # Cycle clock, in calendar days to expiry. Tranches enter between entry and cut-off; the
    # cycle exits (or time-rolls) at exit.
    entry_dte: int = Field(45, ge=0, le=120)
    tranche_cutoff_dte: int = Field(30, ge=0, le=120)
    exit_dte: int = Field(21, ge=0, le=120)

    # Shorts are chosen by |delta|, so one rule works at any DTE (plan section 1). Wings sit the
    # same distance beyond each short, as a % of spot (#69): equal widths keep the max loss and
    # the margin the same on both sides, where equal-delta wings put the widest spread, and the
    # biggest loss, on the put side, often beyond the strikes ICICI lists.
    short_delta: float = Field(0.20, gt=0, lt=0.5)
    wing_width_pct: float = Field(4.5, gt=0, le=25)

    tranches: int = Field(3, ge=1, le=10)
    entry_check: CondorCheckKind = "sod"
    # Start of day ignores prices until the morning has settled; end of day runs after the
    # closing auction, while options still trade (to 15:40).
    sod_check_ist: str = Field("10:30", pattern=HHMM_PATTERN)
    eod_check_ist: str = Field("15:31", pattern=HHMM_PATTERN)

    # Roll triggers -- whichever fires first. The leg rule reads the untested side; the block
    # rule reads net delta per lot, in option-delta units (0.15 = 15 delta).
    leg_rule_delta_floor: float = Field(0.10, gt=0, lt=0.5)
    leg_rule_decay_pct: float = Field(80.0, gt=0, le=100)
    net_delta_band_per_lot: float = Field(0.15, gt=0, le=1.0)
    # A roll that adds less than this, per unit and net of charges, is skipped.
    min_roll_credit_points: float = Field(20.0, ge=0)
    # A roll due within this many days of the exit DTE is reported, not done: the new short
    # would be held a day or two, so the roll mostly pays the spread. 0 (the default) keeps
    # the rules as first agreed; the backtest compares the two (decided 2026-10-03).
    no_roll_within_days_of_exit: int = Field(0, ge=0, le=30)

    # The campaign stop, evaluated only at the scheduled checks (the user's choice). Both
    # forms may be set; the tighter binds. At least one is required.
    max_loss_inr: Optional[float] = Field(None, gt=0)
    max_loss_pct_of_ceiling: Optional[float] = Field(5.0, gt=0, le=100)

    margin_ceiling_inr: float = Field(..., gt=0)

    @model_validator(mode="after")
    def _clock_is_ordered(self) -> "CondorSettings":
        if not (self.entry_dte >= self.tranche_cutoff_dte >= self.exit_dte):
            raise ValueError("Entry DTE must be ≥ tranche cut-off DTE, which must be ≥ exit DTE.")
        if self.tranche_cutoff_dte == self.exit_dte and self.tranches > 1:
            raise ValueError("Tranches need a cut-off DTE above the exit DTE.")
        return self

    @model_validator(mode="after")
    def _has_a_stop(self) -> "CondorSettings":
        if self.max_loss_inr is None and self.max_loss_pct_of_ceiling is None:
            raise ValueError("Set a max-loss in rupees, as a % of the margin ceiling, or both.")
        return self

    def max_loss_limit_inr(self) -> float:
        """The binding stop in rupees: the tighter of the two forms that are set."""
        limits = []
        if self.max_loss_inr is not None:
            limits.append(float(self.max_loss_inr))
        if self.max_loss_pct_of_ceiling is not None:
            limits.append(self.margin_ceiling_inr * float(self.max_loss_pct_of_ceiling) / 100.0)
        return min(limits)


CondorBotMode = Literal["paper", "telegram", "auto"]


def _default_bot_campaign() -> "CondorSettings":
    return CondorSettings(margin_ceiling_inr=10_00_000)


class DynamicCondorBotConfig(BaseModel):
    """The Dynamic Iron Condor bot (plan section 7): a campaign's settings plus how it acts.

    `paper` (Simulation on the card) simulates every action at live quotes and places nothing;
    `telegram` (Semi-auto) asks on Telegram before each action; `auto` acts. Simulation is open
    from day 1 and the other two unlock in that order (`services/condor/bot.py`, #70).
    """

    mode: CondorBotMode = "paper"
    campaign: CondorSettings = Field(default_factory=_default_bot_campaign)
    # What exit DTE, and the break-even exit at the straddle cap, do.
    exit_action: Literal["time_roll", "close"] = "time_roll"
    # None sizes each tranche from today's margin (ceiling / tranches / one lot's margin).
    lots_per_tranche: Optional[int] = Field(None, ge=1, le=500)
    proposal_ttl_minutes: int = Field(15, ge=2, le=60)
    # Set when a manual ticket runs on the bot's campaign: the bot then decides nothing until
    # the user resumes it, so it never undoes the change at its next check (plan 6a).
    paused: bool = False
    paused_reason: Optional[str] = None
