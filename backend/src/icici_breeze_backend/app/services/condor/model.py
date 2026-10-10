"""Value types the condor engine reads and returns. No I/O lives here or in the engine."""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Literal, Optional

from icici_breeze_backend.app.services.condor.pricing import ChainRow, Quote, Right

Side = Literal["Buy", "Sell"]
CheckKind = Literal["sod", "eod", "on_demand"]


@dataclass(frozen=True)
class Leg:
    """One open contract of the campaign. `quantity` is in units and always positive; `side`
    says whether it is held long (Buy) or short (Sell). `avg_price` is what the broker says
    was paid or received (#58)."""

    strike: float
    right: Right
    side: Side
    quantity: int
    avg_price: float

    @property
    def is_short(self) -> bool:
        return self.side == "Sell"

    @property
    def signed_qty(self) -> int:
        return self.quantity if self.side == "Buy" else -self.quantity


@dataclass(frozen=True)
class OrderLeg:
    """One order the engine or a ticket wants placed. `opening` is True when it adds to a
    position (a new long or a new short) and False when it reduces one."""

    strike: float
    right: Right
    action: Side
    quantity: int
    opening: bool
    # Price the decision was costed at: the ask for a buy, the bid for a sell.
    price: Optional[float] = None
    # None means the campaign's open cycle. A time roll's second half names the next expiry.
    expiry: Optional[datetime.date] = None


@dataclass(frozen=True)
class CampaignState:
    expiry: datetime.date
    lot_size: int
    legs: tuple[Leg, ...]
    # Every rupee in and out over the campaign's whole life, net of charges: sells add,
    # buys subtract. This is the plan's "total net credit" (section 1, credit basis).
    ledger_cash_inr: float
    tranches_entered: int = 0


@dataclass(frozen=True)
class MarketSnapshot:
    now: datetime.datetime
    spot: Optional[float]
    # True only when a tick set the spot (#50). A stand-in spot is never decided on.
    spot_live: bool
    # False when a feed the decision needs is down (#52).
    feeds_ok: bool
    chain: tuple[ChainRow, ...]
    # Strikes a new leg may use; None means every chain row. A replay sets it to the range ICICI
    # lists today (#69), so it never trades a strike live trading could not, while every quoted
    # row still feeds the smile and prices the held legs.
    listed: Optional[tuple[float, ...]] = None
    # The premium gate's forecast (#78): the variance of NIFTY's log return from `now` to the
    # cycle's expiry close, from the cash index's own recent sessions; None with `forecast_reason`
    # when it cannot be made. Only a tranche entry with the gate on reads it.
    forecast_variance: Optional[float] = None
    forecast_reason: Optional[str] = None

    def quote(self, strike: float, right: Right) -> Optional[Quote]:
        for row in self.chain:
            if row.strike == strike:
                return row.side(right)
        return None

    @property
    def strikes(self) -> list[float]:
        rows = (row.strike for row in self.chain)
        if self.listed is None:
            return sorted(rows)
        allowed = set(self.listed)
        return sorted(k for k in rows if k in allowed)


Action = Literal[
    "no_action",
    "unavailable",
    "close_all",
    "exit_or_roll",
    "roll_untested",
    "recentre",
    "enter_tranche",
]


@dataclass(frozen=True)
class Metrics:
    """What the card shows, whatever the decision. None means it could not be computed."""

    dte: Optional[int] = None
    call_short_abs_delta: Optional[float] = None
    put_short_abs_delta: Optional[float] = None
    net_delta_per_lot: Optional[float] = None
    tested_side: Optional[Right] = None
    untested_decay_pct: Optional[float] = None
    open_value_inr: Optional[float] = None
    campaign_pnl_inr: Optional[float] = None
    max_loss_limit_inr: Optional[float] = None
    lower_breakeven: Optional[float] = None
    upper_breakeven: Optional[float] = None
    at_cap: bool = False
    worst_loss_at_wings_inr: Optional[float] = None


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str
    text: str
    orders: tuple[OrderLeg, ...] = ()
    metrics: Metrics = field(default_factory=Metrics)
    # Net credit of the proposed orders per unit of the moved quantity, after charges.
    # Present on rolls, including those skipped for falling below the minimum.
    roll_credit_points: Optional[float] = None
    # For enter_tranche and exit_or_roll: the target structure, sized by the caller.
    tranche_strikes: Optional[dict[str, float]] = None
