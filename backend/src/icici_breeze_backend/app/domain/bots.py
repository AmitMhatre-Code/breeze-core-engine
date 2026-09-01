"""Schemas for the Bots section (docs/bots-mvp-plan.md).

Config is persisted as a JSON blob (see `db/bots_migrate`); these models are where its
typing actually lives, so every read and write goes through them rather than touching raw
dicts. Defaults here ARE the agreed policy -- a freshly created bot is already configured
the way the design says it should be, and a config that omits a field inherits the policy
rather than a zero.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

BotType = Literal["holdings_writer", "expiry_index_writer"]

# How a run was started. `schedule` is the bot's own timer, `manual` a user-pressed scan,
# `session_arrival` Bot 2 firing late because the broker session only just appeared.
BotRunTrigger = Literal["schedule", "manual", "session_arrival"]

# Terminal run states. `proposed` is Bot 1 finishing successfully with something for the
# user to approve -- distinct from `completed`, which means orders were actually placed.
BotRunStatus = Literal["running", "completed", "proposed", "skipped", "failed"]

ProposalStatus = Literal["pending", "approved", "rejected", "expired", "superseded", "placed"]


class ReasonCode:
    """Stable machine-readable run outcomes.

    These are persisted and asserted on in tests, so treat them as an API: add freely,
    never repurpose. Every one of them must be distinguishable in the run log -- a user
    looking at a no-trade day needs to know *which* no-trade it was.
    """

    # Success
    ORDERS_PLACED = "orders_placed"
    PROPOSAL_READY = "proposal_ready"

    # Skips -- expected, non-error outcomes
    NOTHING_ELIGIBLE = "nothing_eligible"
    NO_BROKER_SESSION = "no_broker_session"
    CUTOFF_PASSED = "cutoff_passed"
    NOT_AN_EXPIRY_DAY = "not_an_expiry_day"
    MARKET_CLOSED = "market_closed"
    MARGIN_CAP_TOO_SMALL = "margin_cap_too_small"
    BUDGET_EXHAUSTED = "budget_exhausted"
    ALREADY_RAN_TODAY = "already_ran_today"
    BOT_DISABLED = "bot_disabled"
    TRADING_READ_ONLY = "trading_read_only"

    # Failures -- something went wrong
    INTERRUPTED = "interrupted"
    CHAIN_NOT_READY = "chain_not_ready"
    QUOTE_UNAVAILABLE = "quote_unavailable"
    MARGIN_LOOKUP_FAILED = "margin_lookup_failed"
    ORDER_REJECTED = "order_rejected"
    BROKER_ERROR = "broker_error"
    RATE_LIMITED = "rate_limited"
    INTERNAL_ERROR = "internal_error"


# --------------------------------------------------------------------------------------
# Bot 1 -- Holdings Option Writer
# --------------------------------------------------------------------------------------


class HoldingsWriterConfig(BaseModel):
    """Bot 1 config. CE is capped by holdings; PE is capped by delivery cash."""

    default_safety_pct_ce: float = Field(
        5.0, gt=0, le=50, description="Default distance above spot for written calls"
    )
    default_safety_pct_pe: float = Field(
        5.0, gt=0, le=50, description="Default distance below spot for written puts"
    )
    # The PE side has no natural cap from holdings -- assignment buys shares, so the real
    # constraint is cash. One global ceiling, allocated manually at approval time.
    delivery_cash_budget: float = Field(
        0.0, ge=0, description="Rupee ceiling on total PE assignment exposure"
    )
    expiry_preference: Literal["current", "next"] = "current"
    proposal_ttl_minutes: int = Field(
        15, ge=1, le=240, description="How long a priced proposal stays valid"
    )


class ScripPref(BaseModel):
    """Per-scrip deviation from policy. Absence of a row means policy defaults apply."""

    stock_code: str
    ce_enabled: bool = True
    pe_enabled: bool = False
    safety_pct_ce: Optional[float] = Field(None, gt=0, le=50)
    safety_pct_pe: Optional[float] = Field(None, gt=0, le=50)


# --------------------------------------------------------------------------------------
# Bot 2 -- Expiry-Day Index Writer
# --------------------------------------------------------------------------------------


class IndexWriterLeg(BaseModel):
    """Per-index settings. NIFTY and SENSEX are configured independently so that a
    same-day expiry collision is bounded by construction rather than arbitrated at
    runtime; `priority` only breaks the tie on the day they do collide."""

    enabled: bool = False
    right: Literal["call", "put"] = "put"
    safety_pct: float = Field(2.0, gt=0, le=50)
    margin_pct_cap: float = Field(
        30.0, gt=0, le=100, description="Share of free margin this index may consume"
    )
    priority: int = Field(1, ge=1, le=9, description="Lower fires first on a collision")


class ExpiryIndexWriterConfig(BaseModel):
    """Bot 2 config.

    The three clock values encode the session-availability policy: nag from
    max(app start, `nag_start_ist`) every `nag_interval_minutes` until `cutoff_ist`,
    entering at `entry_time_ist` if a session exists by then, otherwise the moment one
    appears -- up to the cutoff, which is both the last nag and the last trade.
    """

    indices: Dict[str, IndexWriterLeg] = Field(
        default_factory=lambda: {
            "NIFTY": IndexWriterLeg(priority=1),
            "BSESEN": IndexWriterLeg(priority=2),
        }
    )
    entry_time_ist: str = Field("09:30", pattern=r"^[0-2]\d:[0-5]\d$")
    nag_start_ist: str = Field("08:00", pattern=r"^[0-2]\d:[0-5]\d$")
    cutoff_ist: str = Field("12:00", pattern=r"^[0-2]\d:[0-5]\d$")
    nag_interval_minutes: int = Field(15, ge=5, le=120)

    # Exit policy. The loss limit is genuinely a P&L quantity and maps onto the SG rule's
    # rupee `loss_limit_pnl`. The profit target is an absolute OPTION PRICE and must not be
    # converted into rupees -- see docs/bots-mvp-plan.md section 4.
    loss_limit_premium_multiple: float = Field(
        1.0, gt=0, le=10, description="Stop at N x the premium collected"
    )
    profit_target_option_price: float = Field(
        0.10, gt=0, le=100, description="Exit when the option trades at or below this price"
    )

    @field_validator("indices")
    @classmethod
    def _known_indices(cls, v: Dict[str, IndexWriterLeg]) -> Dict[str, IndexWriterLeg]:
        allowed = {"NIFTY", "BSESEN"}
        unknown = set(v) - allowed
        if unknown:
            raise ValueError(f"Unsupported index code(s): {', '.join(sorted(unknown))}")
        return v


# --------------------------------------------------------------------------------------
# Records and requests
# --------------------------------------------------------------------------------------


class BotRecord(BaseModel):
    id: str
    bot_type: BotType
    enabled: bool
    config: Dict[str, Any]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class BotRunRecord(BaseModel):
    id: str
    bot_type: BotType
    trigger: BotRunTrigger
    status: BotRunStatus
    reason_code: Optional[str] = None
    reason_text: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class ProposalLeg(BaseModel):
    """One writable contract in a Bot 1 proposal, priced at scan time."""

    stock_code: str
    exchange_code: str = "NFO"
    right: Literal["call", "put"]
    expiry_display: str
    strike_price: float
    lots: int = Field(..., ge=1)
    lot_size: int = Field(..., ge=1)
    quantity: int = Field(..., ge=1)
    premium_per_share: float = Field(..., ge=0, description="Bid where one exists -- we are selling")
    premium_total: float = Field(..., ge=0)
    # A bhavcopy carries no order book, so outside market hours there is no bid at all
    # (0 of ~30k NFO rows have one). `ltp_indicative` marks a premium priced off the last
    # trade so the user can *plan* off-market; placement still requires a real bid.
    premium_basis: Literal["bid", "ltp_indicative"] = "bid"
    span_margin: Optional[float] = None
    elm_margin: Optional[float] = None
    # PE only: strike x quantity, the cash needed if assigned. Drives the delivery budget.
    delivery_exposure: Optional[float] = None
    # Holdings context, shown so the user can see why the cap landed where it did.
    held_quantity: Optional[int] = None
    pledged_quantity: Optional[int] = None
    existing_short_lots: int = 0
    selected: bool = True
    note: Optional[str] = None


class ProposalRecord(BaseModel):
    id: str
    run_id: str
    bot_type: BotType
    status: ProposalStatus
    legs: List[ProposalLeg] = Field(default_factory=list)
    totals: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    expires_at: Optional[str] = None
    resolved_at: Optional[str] = None
    resolution_note: Optional[str] = None


class PlacedLegResult(BaseModel):
    stock_code: str
    right: str
    strike_price: float
    expiry_display: str
    quantity: int
    limit_price: float
    order_ids: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class ApprovalResult(BaseModel):
    proposal_id: str
    placed: List[PlacedLegResult] = Field(default_factory=list)
    all_succeeded: bool


class ScanResponse(BaseModel):
    """A scan reports what it *declined* as well as what it found.

    `skipped` is the load-bearing half: a user whose portfolio yields two proposals wants
    to know why the other nine holdings produced none, and "not F&O eligible" versus
    "under one lot" versus "already fully written" are different answers.
    """

    run_id: str
    proposal: Optional[ProposalRecord] = None
    skipped: List[Dict[str, str]] = Field(default_factory=list)
    warnings: List[Dict[str, Any]] = Field(default_factory=list)


class UpdateBotRequest(BaseModel):
    enabled: Optional[bool] = None
    config: Optional[Dict[str, Any]] = None


class UpdateScripPrefsRequest(BaseModel):
    prefs: List[ScripPref] = Field(default_factory=list)


class ApproveProposalRequest(BaseModel):
    """Approval names the legs to place. Anything omitted is dropped, which is how the
    manual delivery-cash allocation is expressed -- the user keeps what fits."""

    leg_indexes: List[int] = Field(..., description="Indexes into the proposal's legs list")
