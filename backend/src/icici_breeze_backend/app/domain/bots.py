"""Schemas for the Bots section (docs/bots-mvp-plan.md).

Config is persisted as a JSON blob (see `db/bots_migrate`); these models are where its
typing actually lives, so every read and write goes through them rather than touching raw
dicts. Defaults here ARE the agreed policy -- a freshly created bot is already configured
the way the design says it should be, and a config that omits a field inherits the policy
rather than a zero.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

BotType = Literal["holdings_writer", "expiry_index_writer"]

# How a run was started. `schedule` is the bot's own timer, `manual` a user-pressed scan,
# `session_arrival` Bot 2 firing late because the broker session only just appeared.
BotRunTrigger = Literal["schedule", "manual", "session_arrival"]

# Terminal run states. `proposed` is Bot 1 finishing successfully with something for the
# user to approve -- distinct from `completed`, which means orders were actually placed.
BotRunStatus = Literal["running", "completed", "proposed", "skipped", "failed"]

ProposalStatus = Literal["pending", "approved", "rejected", "expired", "superseded", "placed"]

# How an unattended run commits. `auto` places straight away; `telegram` sends the priced
# proposal to the user's linked chat and places nothing until they tap Approve. Silence
# never trades -- which is the whole point, so the default stays `auto` and an upgraded
# deployment keeps the behaviour it already had.
ApprovalMode = Literal["auto", "telegram"]


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
    NOT_A_FIRING_DAY = "not_a_firing_day"
    MARGIN_EXHAUSTED = "margin_exhausted"
    # Semi-autonomous (`approval_mode="telegram"`) outcomes. A proposal sent and still
    # unanswered is deliberately NOT a terminal state -- see `has_committed_run_today`.
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_TIMEOUT = "approval_timeout"
    # Asked to ask, but with no way of asking: Telegram unlinked or alerts switched off.
    # A bot that cannot reach the user must say so rather than look like a quiet day.
    APPROVAL_UNREACHABLE = "approval_unreachable"

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
    """Bot 1 config. CE is capped by holdings; PE is capped by delivery cash.

    The clock fields mirror Bot 2's, deliberately: on its firing day this bot has the same
    problem -- the ICICI session lapses overnight, so an unattended entry depends on a human
    having logged in. `nag_start_ist` doubles as the entry time (there is no separate one),
    so with a session in hand it fires at that moment, and without one it nags until the
    session appears or `cutoff_ist` ends the day.
    """

    default_safety_pct_ce: float = Field(
        5.0, gt=0, le=50, description="Default distance above spot for written calls"
    )
    default_safety_pct_pe: float = Field(
        5.0, gt=0, le=50, description="Default distance below spot for written puts"
    )
    # The PE side has no natural cap from holdings -- assignment buys shares, so the real
    # constraint is cash. One global ceiling; in manual mode the user allocates it across
    # scrips, and in autonomous mode per-scrip priority spends it in order.
    delivery_cash_budget: float = Field(
        0.0, ge=0, description="Rupee ceiling on total PE assignment exposure"
    )
    expiry_preference: Literal["current", "next"] = "current"
    proposal_ttl_minutes: int = Field(
        15, ge=1, le=240, description="How long a priced proposal stays valid"
    )

    # --- autonomous firing -------------------------------------------------------------
    # Counted in TRADING days against the monthly stock-option expiry, not calendar days:
    # calendar arithmetic drifts onto weekends and holidays and would fire into a closed
    # market. 0 means the expiry day itself.
    fire_days_before_expiry: int = Field(
        3, ge=0, le=30, description="Trading days before expiry on which to fire"
    )
    nag_start_ist: str = Field("09:20", pattern=r"^[0-2]\d:[0-5]\d$")
    cutoff_ist: str = Field("12:00", pattern=r"^[0-2]\d:[0-5]\d$")
    nag_interval_minutes: int = Field(15, ge=5, le=120)
    # `nag_interval_minutes` doubles as the re-proposal cadence in `telegram` mode: an
    # unanswered proposal goes stale long before `cutoff_ist`, so it is re-sent at fresh
    # prices rather than surrendering the rest of the window on one missed message.
    approval_mode: ApprovalMode = "auto"


class ScripPref(BaseModel):
    """Per-scrip deviation from policy. Absence of a row means policy defaults apply.

    Both lot fields are None by default, meaning "whatever the bot did before this field
    existed" -- every covered lot for calls, one lot for puts -- so a deployment upgrading
    into this keeps its behaviour without a backfill. A number is a *target*, and for calls
    it is still hard-capped by coverage: asking for 5 lots on a 3-lot holding writes 3 and
    says so, rather than refusing outright. 0 means write nothing on that side.
    """

    stock_code: str
    ce_enabled: bool = True
    pe_enabled: bool = False
    ce_lots: Optional[int] = Field(None, ge=0, le=999)
    pe_lots: Optional[int] = Field(None, ge=0, le=999)
    safety_pct_ce: Optional[float] = Field(None, gt=0, le=50)
    safety_pct_pe: Optional[float] = Field(None, gt=0, le=50)
    # Order in which scrips are funded when free margin or the delivery budget cannot cover
    # everything. Lower goes first.
    priority: int = Field(1, ge=1, le=999)

    @property
    def writes_ce(self) -> bool:
        return self.ce_enabled and (self.ce_lots is None or self.ce_lots > 0)

    @property
    def writes_pe(self) -> bool:
        return self.pe_enabled and (self.pe_lots is None or self.pe_lots > 0)


# --------------------------------------------------------------------------------------
# Bot 2 -- Expiry-Day Index Writer
# --------------------------------------------------------------------------------------


IndexStrategy = Literal["naked_ce", "naked_pe", "short_strangle"]


class IndexWriterLeg(BaseModel):
    """Per-index settings. NIFTY and SENSEX are configured independently so that a
    same-day expiry collision is bounded by construction rather than arbitrated at
    runtime; `priority` only breaks the tie on the day they do collide.

    `strategies` is a shortlist, not a single choice: with more than one entry the bot
    prices all of them and trades whichever yields the most premium **per rupee of margin**.
    Ranking on absolute premium instead would be no contest at all -- a strangle is both
    legs, so it always collects more than either alone, and shortlisting it would silently
    retire the other two options.
    """

    enabled: bool = False
    strategies: List[IndexStrategy] = Field(default_factory=lambda: ["naked_pe"])
    # Separate distances per side: index skew means the same distance rarely fetches
    # comparable premium on a call and a put.
    safety_pct_ce: float = Field(2.0, gt=0, le=50)
    safety_pct_pe: float = Field(2.0, gt=0, le=50)
    margin_pct_cap: float = Field(
        30.0, gt=0, le=100, description="Share of free margin this index may consume"
    )
    priority: int = Field(1, ge=1, le=9, description="Lower fires first on a collision")

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_shape(cls, data: Any) -> Any:
        """Carry a pre-rework config forward instead of silently resetting it.

        Configs written before this change carry `right` and a single `safety_pct`. The
        repository validates stored blobs on read, so without this an existing user's chosen
        side would quietly become the default one the first time their bot loaded.
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "strategies" not in data and "right" in data:
            data["strategies"] = ["naked_ce" if data.get("right") == "call" else "naked_pe"]
        legacy_pct = data.pop("safety_pct", None)
        if legacy_pct is not None:
            data.setdefault("safety_pct_ce", legacy_pct)
            data.setdefault("safety_pct_pe", legacy_pct)
        data.pop("right", None)
        return data

    @field_validator("strategies")
    @classmethod
    def _at_least_one(cls, v: List[str]) -> List[str]:
        # De-duplicate but keep the user's order, so the UI round-trips what they picked.
        seen: List[str] = []
        for s in v:
            if s not in seen:
                seen.append(s)
        if not seen:
            raise ValueError("Pick at least one of naked CE, naked PE or short strangle.")
        return seen


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
    # See `HoldingsWriterConfig.approval_mode`. On this bot the window is the expiry
    # morning itself, so the re-proposal loop runs from `entry_time_ist` to `cutoff_ist`.
    approval_mode: ApprovalMode = "auto"

    # Exit policy, both sides now expressed against the premium collected.
    #
    # The loss limit is genuinely a rupee P&L quantity and maps onto the SG rule's
    # `loss_limit_pnl` on the group -- which is the right shape for a strangle, where the
    # risk is net across both legs and a per-leg price stop would fire on the losing side
    # while the winning side was paying for it.
    #
    # Profit booking used to be an absolute option price (the "paisa limit"). It is now a
    # share of the premium, which the engine still evaluates as a per-leg PRICE target of
    # `entry x (1 - pct/100)` -- never as rupees, because `_evaluate_user_pnl` computes P&L
    # from the BROKER's average_price, which need not equal the price the bot sold at.
    # 100% is the special case: the target price is zero, which no limit order can reach, so
    # no profit exit is armed at all and the position is left to expire worthless with only
    # the stop-loss live. See `profit_target_price_for`.
    loss_limit_premium_multiple: float = Field(
        1.0, gt=0, le=10, description="Stop at N x the premium collected"
    )
    profit_book_premium_pct: float = Field(
        50.0,
        gt=0,
        le=100,
        description="Book once this share of the premium is captured; 100 = let it expire",
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_exit(cls, data: Any) -> Any:
        """Drop the retired absolute price target rather than failing validation on it."""
        if isinstance(data, dict) and "profit_target_option_price" in data:
            data = dict(data)
            data.pop("profit_target_option_price", None)
        return data

    def profit_target_price_for(self, entry_price: float) -> Optional[float]:
        """The per-leg buy-back price this policy implies, or None to let it expire.

        Returns None at 100% because there is no such thing as a limit order at zero. The
        tick floor makes anything near it degenerate too, so the honest reading of "book
        100% of the premium" is "hold to expiry", not "chase the last five paise".
        """
        if self.profit_book_premium_pct >= 100:
            return None
        target = float(entry_price) * (1 - self.profit_book_premium_pct / 100.0)
        return round(target, 2) if target > 0 else None

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
    # Cross-BOT ordering, distinct from the per-index and per-scrip priorities inside
    # config. On a day both bots fire, the lower number sizes and places first and the
    # other sizes against whatever margin and delivery cash is left.
    priority: int = 1
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
    # Funding order. Copied onto the leg at scan time so the proposal can be read and
    # allocated top-down without re-joining against the prefs table.
    scrip_priority: int = 1
    selected: bool = True
    note: Optional[str] = None
    # Bot 2 only. `strategy` is the shortlisted shape this leg was priced for and
    # `group_key` ties a strangle's two legs together, so selecting one selects both --
    # half a strangle is a naked short, which is not what the user picked.
    strategy: Optional[str] = None
    group_key: Optional[str] = None
    margin_yield: Optional[float] = Field(
        None, description="Premium per rupee of margin -- how strategies are ranked"
    )


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
    priority: Optional[int] = Field(None, ge=1, le=99)
    config: Optional[Dict[str, Any]] = None


class UpdateScripPrefsRequest(BaseModel):
    prefs: List[ScripPref] = Field(default_factory=list)


class LegEdit(BaseModel):
    """A user's change to one proposed leg, made in the manual review before executing.

    Only size and strike are editable. Changing the underlying or the side would make it a
    different trade from the one the bot proposed, at which point Strategy Builder is the
    right screen -- and a bot's run log would be recording something the bot never decided.
    """

    lots: Optional[int] = Field(None, ge=1, le=999)
    strike_price: Optional[float] = Field(None, gt=0)


class ApproveProposalRequest(BaseModel):
    """Approval names the legs to place. Anything omitted is dropped, which is how the
    manual delivery-cash allocation is expressed -- the user keeps what fits.

    `edits` is keyed by the same index, so an edited leg is still identifiably the leg the
    bot proposed rather than a free-form order.
    """

    leg_indexes: List[int] = Field(..., description="Indexes into the proposal's legs list")
    edits: Dict[int, LegEdit] = Field(default_factory=dict)


class RepriceRequest(BaseModel):
    """Re-price the proposal with the user's edits applied, without placing anything."""

    leg_indexes: List[int] = Field(default_factory=list)
    edits: Dict[int, LegEdit] = Field(default_factory=dict)


class HoldingRow(BaseModel):
    """One row of the settings drawer's scrip list.

    Read live from the broker on every open, never from a stored list: what the user holds
    changes without the bot being told, and a stale list would offer to write calls against
    stock that has since been sold.
    """

    stock_code: str
    # A holding splits three ways, exhaustively: available + blocked + pledged = quantity.
    # Only `blocked` is excluded from call coverage -- it is already earmarked and not the
    # user's to deliver. Pledged stock IS coverage; it just has to be unpledged before
    # expiry, which is an obligation to surface, not a reason to leave it unwritten.
    quantity: int
    available_quantity: int = 0
    blocked_quantity: int = 0
    pledged_quantity: int = 0
    deliverable_quantity: int = 0
    lot_size: Optional[int] = None
    lots_held: int = 0
    available_lots: int = 0
    blocked_lots: int = 0
    pledged_lots: int = 0
    deliverable_lots: int = 0
    existing_short_ce_lots: int = 0
    existing_short_pe_lots: int = 0
    fno_eligible: bool = True
    ineligible_reason: Optional[str] = None
    current_market_price: Optional[float] = None
