"""Core types for the options strategy engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from icici_breeze_backend.app.core.strike import Strike

Right = Literal["Call", "Put"]
Side = Literal["Buy", "Sell"]
StrategyCategory = Literal["income", "bullish", "bearish", "volatility"]
RiskRewardProfile = Literal["conservative", "moderate", "aggressive"]
StrategyId = str

STRATEGY_CATALOG: list[tuple[str, str]] = [
    ("naked_ce_short", "Naked CE Short"),
    ("naked_pe_short", "Naked PE Short"),
    ("bull_call_spread", "Bull Call Spread"),
    ("bear_put_spread", "Bear Put Spread"),
    ("bear_call_spread", "Bear Call Spread"),
    ("bull_put_spread", "Bull Put Spread"),
    ("long_call", "Long Call"),
    ("long_put", "Long Put"),
    ("long_straddle", "Long Straddle"),
    ("short_straddle", "Short Straddle"),
    ("short_strangle", "Short Strangle"),
    ("long_strangle", "Long Strangle"),
    ("long_butterfly", "Long Butterfly"),
    ("long_condor", "Long Condor"),
    ("iron_condor", "Iron Condor"),
    ("iron_butterfly", "Iron Butterfly"),
]

UNDEFINED_RISK_STRATEGIES: frozenset[str] = frozenset(
    {"naked_ce_short", "naked_pe_short", "short_strangle", "short_straddle"}
)

TOP_K_SHORT_STRIKES = 8
TOP_M_WING_STRIKES = 3
MAX_CANDIDATES_PER_STRATEGY = 30
POP_TOLERANCE_PCT = 7.0
POP_PRE_FILTER_TOLERANCE = 2.0


@dataclass
class QuoteRow:
    strike: Strike
    right: Right
    ltp: float
    best_bid_price: float
    best_offer_price: float
    total_buy_qty: int
    total_sell_qty: int
    buy_sell_ratio: float | str
    spot_price: float | None = None
    oi: int = 0
    iv: float | None = None
    delta: float | None = None
    liquidity_score: float = 0.0

    @property
    def liquid(self) -> bool:
        return self.total_buy_qty > 0 and self.total_sell_qty > 0

    @property
    def mid_price(self) -> float:
        if self.best_bid_price > 0 and self.best_offer_price > 0:
            return (self.best_bid_price + self.best_offer_price) / 2.0
        return self.ltp

    @property
    def spread(self) -> float:
        if self.best_bid_price > 0 and self.best_offer_price > 0:
            return max(0.0, self.best_offer_price - self.best_bid_price)
        return 0.0


@dataclass
class TradeLeg:
    right: Right
    side: Side
    strike: Strike
    quantity: int
    premium_per_unit: float

    def to_out(self, cache: dict[tuple[Strike, Right], QuoteRow]) -> dict[str, Any]:
        q = cache.get((self.strike, self.right))
        return {
            "right": self.right,
            "side": self.side,
            "strike": self.strike,
            "quantity": self.quantity,
            "premium_per_unit": round(self.premium_per_unit, 4),
            "ltp": q.ltp if q else None,
            "best_bid_price": q.best_bid_price if q else None,
            "best_offer_price": q.best_offer_price if q else None,
            "total_buy_qty": q.total_buy_qty if q else None,
            "total_sell_qty": q.total_sell_qty if q else None,
            "buy_sell_ratio": q.buy_sell_ratio if q else None,
        }


@dataclass
class TileMetric:
    label: str
    value: str


@dataclass
class StrategyResult:
    strategy_id: str
    strategy_name: str
    status: Literal["ok", "skipped"] = "skipped"
    skip_reason: str | None = None
    structure_modified: bool = False
    net_premium: float | None = None
    max_loss: float | None = None
    annualized_return_pct: float | None = None
    risk_reward_ratio: str | None = None
    pop_pct: float | None = None
    legs: list[TradeLeg] = field(default_factory=list)
    margin_key: tuple | None = None
    span_margin: float | None = None
    elm_requirement: float | None = None
    variant_rank: int | None = None
    engine_score: float | None = None
    ranking_summary: str | None = None
    score_breakdown: dict | None = None
    conviction_profile: RiskRewardProfile | None = None
    hero_metric: TileMetric | None = None
    secondary_metrics: list[TileMetric] = field(default_factory=list)
    badges: list[str] = field(default_factory=list)
    compliance: Literal["recommended", "relaxed"] = "recommended"
    constraint_violations: list[str] = field(default_factory=list)


@dataclass
class EngineContext:
    processor: Any
    user_id: str
    stock_code: str
    exchange_code: str
    expiry_display: str
    margin_rupees: float
    max_loss_rupees: float | None
    min_pop_pct: float
    provision_elm: bool
    strategy_category: StrategyCategory
    lot_size: int
    strikes: list[Strike]
    strike_step: float
    search_interval: float
    spot: float
    atm_strike: Strike
    allow_infinite_loss: bool = False
    risk_reward_profile: RiskRewardProfile = "moderate"
    min_ann_return_pct: float = 5.0
    atm_iv: float | None = None
    cache: dict[tuple[Strike, Right], QuoteRow] = field(default_factory=dict)
    structure_modified: bool = False
    halted: bool = False
    halt_reason: str | None = None
    audit: Any | None = None
    audit_collector: Any | None = None
    chain_backoff: Any | None = None
    range_lower: float = 0.0
    range_upper: float = 0.0
    unit_span_by_structure: dict[tuple, float] = field(default_factory=dict)
    progress: Any | None = None

    def effective_max_loss_budget(self) -> float | None:
        if self.allow_infinite_loss or self.max_loss_rupees is None:
            return None
        return self.max_loss_rupees

    def effective_loss_sizing_budget(self) -> float:
        if self.max_loss_rupees is None:
            return self.margin_rupees
        return min(self.margin_rupees, self.max_loss_rupees)

    @property
    def liquid_ce_strikes(self) -> list[Strike]:
        return sorted(
            s for s in self.strikes if (s, "Call") in self.cache and self.cache[(s, "Call")].liquid
        )

    @property
    def liquid_pe_strikes(self) -> list[Strike]:
        return sorted(
            s for s in self.strikes if (s, "Put") in self.cache and self.cache[(s, "Put")].liquid
        )

    @property
    def t_years(self) -> float:
        from icici_breeze_backend.app.services.options_strategy_engine.helpers import years_to_expiry

        return years_to_expiry(self.expiry_display)
