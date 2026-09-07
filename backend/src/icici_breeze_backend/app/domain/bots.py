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

BotType = Literal[
    "holdings_writer",
    "expiry_index_writer",
    "momentum_long_scalper",
    "iron_fly_scalper",
]

# How a run was started. `schedule` is the bot's own timer, `manual` a user-pressed scan,
# `session_arrival` Bot 2 firing late because the broker session only just appeared.
# `session` is the scalpers': one run row covering a whole trading session, with its
# cycles in `bot_cycles`. It is not a fourth way of starting a run so much as a different
# unit of work -- see `docs/bots-scalping-plan.md` section 9.
BotRunTrigger = Literal["schedule", "manual", "session_arrival", "session"]

# Terminal run states. `proposed` is Bot 1 finishing successfully with something for the
# user to approve -- distinct from `completed`, which means orders were actually placed.
BotRunStatus = Literal["running", "completed", "proposed", "skipped", "failed"]

ProposalStatus = Literal["pending", "approved", "rejected", "expired", "superseded", "placed"]

# How an unattended run commits. `auto` places straight away; `telegram` sends the priced
# proposal to the user's linked chat and places nothing until they tap Approve. Silence
# never trades. A freshly created bot defaults to `telegram` -- arming a bot that will
# place real orders with no human in the loop should be a deliberate choice, not the
# out-of-the-box state. A config a build older than this wrote and never re-saved omits the
# field and so inherits the new default on read; anything saved through the settings drawer
# carries an explicit value and is left exactly as the user left it.
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

    # Scalper outcomes (docs/bots-scalping-plan.md). Cycle-level codes live on
    # `bot_cycles.exit_reason_code`; session-level ones on `bot_runs` like every other bot.
    SIGNAL_NO_TRADE = "signal_no_trade"
    NOT_WARM = "not_warm"
    OUTLAY_BELOW_ONE_LOT = "outlay_below_one_lot"
    ENTRY_UNFILLED = "entry_unfilled"
    ENTRY_PARTIAL_UNWOUND = "entry_partial_unwound"
    SG_RULE_CONFLICT = "sg_rule_conflict"
    COOLDOWN_ACTIVE = "cooldown_active"
    API_BUDGET_LOW = "api_budget_low"
    TERMINATED_FOR_DAY = "terminated_for_day"
    OUTSIDE_SESSION_WINDOW = "outside_session_window"
    REENTRY_GATE_CLOSED = "reentry_gate_closed"
    # The tick feed went quiet while a position was open. Entries freeze the moment the
    # stream is stale; an exit only fires once it has stayed stale, so a WS blip cannot
    # flatten a good position at the cost of a round trip of friction.
    STALE_FEED = "stale_feed"
    # Cycle exits
    TARGET_HIT = "target_hit"
    TRAILING_STOP = "trailing_stop"
    STOP_LOSS = "stop_loss"
    TIME_INVALIDATION = "time_invalidation"
    DRIFT_STOP = "drift_stop"
    CREDIT_DECAY_TARGET = "credit_decay_target"
    SQUARE_OFF = "square_off"

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
    approval_mode: ApprovalMode = "telegram"


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
    approval_mode: ApprovalMode = "telegram"

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


# --------------------------------------------------------------------------------------
# Bots 3 & 4 -- intraday scalpers (docs/bots-scalping-plan.md)
#
# Shaped as nested blocks rather than one flat namespace because there are ~25 knobs and the
# grouping is load-bearing: the pure decision layer takes only the block it needs, so the
# signal evaluator is structurally incapable of reading a risk limit and vice versa.
# --------------------------------------------------------------------------------------


ScalperMode = Literal["paper", "live"]


class SessionWindow(BaseModel):
    """A trading window in IST. Both bots take a list; overlapping windows are the user's
    business, since the two bots are deliberately independent (plan section 5.2)."""

    start: str = Field(pattern=r"^[0-2]\d:[0-5]\d$")
    end: str = Field(pattern=r"^[0-2]\d:[0-5]\d$")

    @model_validator(mode="after")
    def _ordered(self) -> "SessionWindow":
        if self.start >= self.end:
            raise ValueError("A session window must end after it starts.")
        return self


class ScalperRiskConfig(BaseModel):
    """Per-bot circuit breakers. Separate stops per bot were chosen deliberately, so the
    combined daily downside is the SUM of the two -- surfaced in the UI rather than left for
    the user to multiply (plan section 5.2)."""

    cumulative_stop_inr: float = Field(
        10000.0, gt=0, description="Realized + unrealized + charges; terminal for the day"
    )
    consecutive_loss_limit: int = Field(3, ge=1, le=20)
    cooldown_minutes: int = Field(30, ge=1, le=240)
    # Chosen over a static max-cycles cap: it reacts to what the account is actually
    # spending rather than to a number guessed in advance. The reserve is what scalping must
    # leave for the dashboard, the other bots, and above all a manual square-off.
    api_budget_reserve_calls: int = Field(25, ge=0, le=90)


class ScalperExecutionConfig(BaseModel):
    """Order mechanics shared by both bots. Every price is a bounded limit; nothing here can
    produce a raw market order, matching `squareoff_dispatcher._leg_limit_price`."""

    entry_limit_tolerance_pct: float = Field(1.0, gt=0, le=10)
    entry_fill_timeout_seconds: int = Field(3, ge=1, le=60)
    entry_retries: int = Field(2, ge=0, le=5)
    exit_limit_band_pct: float = Field(1.0, gt=0, le=20)


class MomentumSignalConfig(BaseModel):
    """The 1-minute NIFTY *futures* signal.

    Futures, not the index: an index has no traded volume, so neither the volume filter nor
    VWAP is computable against it (plan section 3.2). VWAP itself needs no warm-up -- it
    comes from the tick's cumulative `avgPrice` -- so only these two periods gate arming.
    """

    candle_seconds: int = Field(60, ge=60, le=300)
    ema_period: int = Field(9, ge=2, le=200)
    volume_ma_period: int = Field(20, ge=2, le=200)
    volume_multiplier: float = Field(1.5, gt=0, le=10)
    require_vwap: bool = True


class TrailingLadderConfig(BaseModel):
    """The three-level ladder. `target_pts` is a *trailing trigger*, not a take-profit:
    reaching it starts the runner rather than closing the trade, which is the "ride the
    winners" behaviour this bot exists for.

    Points are absolute option points, chosen over percentage-of-premium with the trade-off
    recorded (plan section 3.3): 10 points is a 9% move on a Rs 110 option and 33% on a
    Rs 30 one. Paper mode reports both so the choice can be revisited from evidence.
    """

    target_pts: float = Field(10.0, gt=0)
    stop_loss_pts: float = Field(6.0, gt=0)
    time_invalidation_seconds: int = Field(90, ge=5, le=3600)
    time_invalidation_min_move_pts: float = Field(3.0, ge=0)
    level_1_trigger_pts: float = Field(5.0, gt=0)
    level_1_lock_pts: float = Field(1.2, description="Locked above cost; covers fees")
    level_2_trigger_pts: float = Field(8.0, gt=0)
    level_2_lock_pts: float = Field(5.0)
    level_3_runner_step_pts: float = Field(3.0, gt=0)

    @model_validator(mode="after")
    def _ladder_is_monotonic(self) -> "TrailingLadderConfig":
        """A ladder that steps backwards would move a stop *away* from locked profit.

        Validated here rather than trusted at runtime: the exit loop's invariant is that a
        stop only ever ratchets up, and the cheapest place to guarantee that is to refuse a
        config that cannot satisfy it.
        """
        if self.level_2_trigger_pts <= self.level_1_trigger_pts:
            raise ValueError("Level 2 must trigger above level 1.")
        if self.target_pts <= self.level_2_trigger_pts:
            raise ValueError("The runner target must sit above level 2's trigger.")
        if self.level_2_lock_pts <= self.level_1_lock_pts:
            raise ValueError("Level 2 must lock in more than level 1.")
        if self.level_1_lock_pts >= self.level_1_trigger_pts:
            raise ValueError("Level 1 cannot lock in more than it has gained.")
        if self.level_2_lock_pts >= self.level_2_trigger_pts:
            raise ValueError("Level 2 cannot lock in more than it has gained.")
        return self


class MomentumLongScalperConfig(BaseModel):
    """Bot 3 -- buys one ATM option on a momentum signal and manages it with a ladder.

    `mode` ships as `paper` for the same reason Bot 2's `approval_mode` ships as `telegram`:
    arming something that fires unattended orders should be a deliberate act, not the
    out-of-the-box state.
    """

    index: Literal["NIFTY"] = "NIFTY"
    expiry_preference: Literal["nearest_weekly"] = "nearest_weekly"
    trade_on_expiry_day: bool = False
    mode: ScalperMode = "paper"
    # 09:35, not 09:20: the candle builder starts at market open and needs 20 bars before the
    # volume MA exists, so an earlier window would only log `not_warm` (plan section 3.2).
    sessions: List[SessionWindow] = Field(
        default_factory=lambda: [
            SessionWindow(start="09:35", end="11:30"),
            SessionWindow(start="13:30", end="15:10"),
        ]
    )
    hard_square_off_ist: str = Field("15:15", pattern=r"^[0-2]\d:[0-5]\d$")
    # The literal reading of "a fixed premium outlay": one position at a time, so this is the
    # maximum capital *deployed* at any instant.
    #
    # 25,000 rather than the 10,000 the source conversation suggested, decided 2026-09-06
    # after the backtest refused to trade at all. That figure assumed an ATM premium of
    # 80-140 on a lot of 65 (5,200-9,100 per lot); the lot is now 75, and 80-140 describes an
    # option one or two days from expiry rather than a Monday one. At 10,000 the bot could
    # not afford a single ATM lot beyond about 3 days to expiry and stood down for half of
    # most weeks -- see docs/bots-scalping-plan.md section 8.5.
    #
    # Known and accepted consequence: because lots = floor(outlay / cost) and an ATM option
    # gets cheaper as expiry approaches, position size rises as expiry nears -- roughly 1 lot
    # at 6 DTE and 4 at 1 DTE. Risk per stop-out moves with it (450 to 1,800 at the default
    # 6-point stop), so the largest positions sit where gamma is highest. Every cycle records
    # `risk_per_stop_inr` so that swing is visible in the run log rather than implicit.
    premium_outlay_inr: float = Field(25000.0, gt=0)
    signal: MomentumSignalConfig = Field(default_factory=MomentumSignalConfig)
    exits: TrailingLadderConfig = Field(default_factory=TrailingLadderConfig)
    execution: ScalperExecutionConfig = Field(default_factory=ScalperExecutionConfig)
    risk: ScalperRiskConfig = Field(default_factory=ScalperRiskConfig)


class IronFlyStructureConfig(BaseModel):
    """Wing placement. 150 points is the stated NIFTY sweet spot: narrower collapses the net
    credit, wider adds max loss without buying further margin relief."""

    wing_width_points: float = Field(150.0, gt=0, le=2000)
    widen_above_vix: Optional[float] = Field(
        None, gt=0, le=100, description="VIX above which to use the widened width; None = off"
    )
    widened_wing_width_points: float = Field(200.0, gt=0, le=2000)


class IronFlyExitConfig(BaseModel):
    """Whichever fires first. The drift stop is the one that matters: by the time a short ATM
    leg has moved this far, gamma is expanding on the tested side and waiting for a P&L stop
    is waiting for a number that arrives faster than an exit can be placed.

    **Two loss stops, both live, the tighter one binding** (decided 2026-09-06). They answer
    different questions and neither subsumes the other:

    * `hard_stop_loss_inr` is an absolute rupee cap, easy to reason about against the daily
      limit. On its own it does not scale: at three lots a flat 1,500 is 500 a lot, roughly
      seven points of combined premium, which ordinary intraday movement clears.
    * `stop_loss_credit_pct` is a share of the credit actually collected -- symmetric with
      `target_decay_pct`, and scale-invariant across lots, DTE and volatility. It is the same
      idiom `ExpiryIndexWriterConfig.loss_limit_premium_multiple` already uses, for the same
      reason.

    Either can be set to None to switch it off. Both None means the position has no P&L stop
    at all and relies on the drift stop and the wings, which is a deliberate choice a user has
    to make explicitly rather than reach by accident.
    """

    target_decay_pct: float = Field(15.0, gt=0, le=100)
    hard_stop_loss_inr: Optional[float] = Field(1500.0, gt=0)
    stop_loss_credit_pct: Optional[float] = Field(20.0, gt=0, le=500)
    max_spot_drift_pct: float = Field(0.35, gt=0, le=10)

    def loss_limit_inr(self, net_credit_inr: float) -> Optional[float]:
        """The binding stop in rupees, or None when both are switched off.

        The tighter of the two, because each exists to catch something the other misses and
        honouring only the looser would silently retire the stricter one.
        """
        limits = [
            limit
            for limit in (
                self.hard_stop_loss_inr,
                (
                    abs(float(net_credit_inr)) * self.stop_loss_credit_pct / 100.0
                    if self.stop_loss_credit_pct is not None
                    else None
                ),
            )
            if limit is not None
        ]
        return min(limits) if limits else None


class IronFlyReentryConfig(BaseModel):
    """Both conditions must hold. The cooldown alone re-centres into an ongoing move and gets
    stopped again; the range test alone re-fires immediately in a chop that keeps clearing the
    band. Requiring both means re-entering only when the market has been quiet *and* some time
    has passed since whatever went wrong last."""

    cooldown_minutes: int = Field(15, ge=0, le=240)
    range_window_minutes: int = Field(10, ge=1, le=120)
    max_range_pct: float = Field(0.15, gt=0, le=10)


class IronFlyScalperConfig(BaseModel):
    """Bot 4 -- ATM iron fly, booked on credit decay, re-entered behind a gate."""

    index: Literal["NIFTY"] = "NIFTY"
    expiry_preference: Literal["nearest_weekly"] = "nearest_weekly"
    trade_on_expiry_day: bool = False
    mode: ScalperMode = "paper"
    sessions: List[SessionWindow] = Field(
        default_factory=lambda: [SessionWindow(start="11:30", end="13:30")]
    )
    hard_square_off_ist: str = Field("15:15", pattern=r"^[0-2]\d:[0-5]\d$")
    # A rupee ceiling rather than a lot count (adapts to VIX-driven margin changes) or a
    # share of free margin (which would drift with the day's P&L). The bot takes the largest
    # whole-lot fly that fits, verified through `margin_calculator` on all four legs at once
    # so the exchange's netting is real.
    margin_ceiling_inr: float = Field(100000.0, gt=0)
    min_lots: int = Field(1, ge=1, le=100)
    structure: IronFlyStructureConfig = Field(default_factory=IronFlyStructureConfig)
    exits: IronFlyExitConfig = Field(default_factory=IronFlyExitConfig)
    reentry: IronFlyReentryConfig = Field(default_factory=IronFlyReentryConfig)
    execution: ScalperExecutionConfig = Field(default_factory=ScalperExecutionConfig)
    risk: ScalperRiskConfig = Field(default_factory=ScalperRiskConfig)

    @model_validator(mode="after")
    def _widened_is_wider(self) -> "IronFlyScalperConfig":
        s = self.structure
        if s.widen_above_vix is not None and s.widened_wing_width_points <= s.wing_width_points:
            raise ValueError("The widened wing width must exceed the normal one.")
        return self


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


class TradingCharges(BaseModel):
    """The shared round-trip cost model, as the API exposes it.

    Deployment-wide, not per bot: both scalpers and the backtest harness must price the same
    trade identically. Mirrors `services/bots/scalping/charges.ChargesModel`; that module owns
    the arithmetic and the caveats, this is only its wire shape.
    """

    brokerage_per_order_inr: float
    brokerage_pct_of_premium: float
    brokerage_cap_inr: Optional[float] = None
    stt_sell_pct: float
    exchange_txn_pct: float
    exchange_txn_pct_bse: float
    sebi_pct: float
    ipft_pct: float
    stamp_buy_pct: float
    gst_pct: float
    slippage_spread_fraction: float


class TradingChargesUpdate(BaseModel):
    """A partial edit. Every field optional, so the drawer can PATCH one rate at a time.

    Bounds rather than free numbers because these are percentages of turnover: a stray
    decimal turns a 0.1% levy into 10% and quietly makes every backtest and every paper
    session unrecognisable.
    """

    brokerage_per_order_inr: Optional[float] = Field(None, ge=0, le=10000)
    brokerage_pct_of_premium: Optional[float] = Field(None, ge=0, le=10)
    brokerage_cap_inr: Optional[float] = Field(None, ge=0, le=100000)
    stt_sell_pct: Optional[float] = Field(None, ge=0, le=5)
    exchange_txn_pct: Optional[float] = Field(None, ge=0, le=5)
    exchange_txn_pct_bse: Optional[float] = Field(None, ge=0, le=5)
    sebi_pct: Optional[float] = Field(None, ge=0, le=5)
    ipft_pct: Optional[float] = Field(None, ge=0, le=5)
    stamp_buy_pct: Optional[float] = Field(None, ge=0, le=5)
    gst_pct: Optional[float] = Field(None, ge=0, le=100)
    slippage_spread_fraction: Optional[float] = Field(None, ge=0, le=2)


class BotCycleRecord(BaseModel):
    """One scalper round trip, hanging off a session's `bot_runs` row.

    `friction` is a first-class field rather than something derived at read time because it
    is the constraint that actually binds this strategy: ~80 cycles a day is comfortable for
    the ICICI call budget and burns roughly Rs 8,000 of friction against a Rs 10,000 daily
    stop (plan section 6.4). A number that decides whether the bot is viable has to be
    visible in the log, not recomputed by whoever happens to ask.
    """

    id: str
    run_id: str
    bot_type: BotType
    cycle_no: int
    structure: str  # "long_ce" | "long_pe" | "iron_fly"
    legs: List[Dict[str, Any]] = Field(default_factory=list)
    lots: Optional[int] = None
    opened_at: Optional[str] = None
    closed_at: Optional[str] = None
    entry_value: Optional[float] = None
    exit_value: Optional[float] = None
    gross_pnl: Optional[float] = None
    friction: Optional[float] = None
    net_pnl: Optional[float] = None
    exit_reason_code: Optional[str] = None
    exit_reason_text: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None
    paper: bool = True

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    @property
    def is_loss(self) -> bool:
        """Closed and net-negative. An open cycle is not yet a loss, and a cycle that never
        opened a position (an aborted entry) is not one either -- which is why the
        consecutive-loss counter reads this rather than counting rows."""
        return self.closed_at is not None and (self.net_pnl or 0.0) < 0


class ScalperDayTotals(BaseModel):
    """Today's running totals for one scalper, recomputed before every cycle decision."""

    cycles: int = 0
    open_cycles: int = 0
    realized_net_pnl: float = 0.0
    friction: float = 0.0
    consecutive_losses: int = 0
    last_closed_at: Optional[str] = None


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
    # Underlying price the strike was picked against, shown next to the strike so a user
    # changing the distance % can see what it is a percentage *of*.
    spot: Optional[float] = None
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
    # Estimated brokerage and statutory charges on WRITING this leg, and the premium that
    # actually lands after them. Entry only: whether the option is later bought back or left
    # to expire is not knowable at proposal time, so charging a round trip would overstate a
    # leg that expires worthless. Shown as an estimate, never as a settled figure.
    estimated_charges: Optional[float] = None
    net_premium_total: Optional[float] = None


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

    Size and the distance from spot are editable; the side and the underlying are not --
    changing those would make it a different trade from the one the bot proposed, at which
    point Strategy Builder is the right screen, and a bot's run log would be recording
    something the bot never decided.

    `distance_pct` re-derives the strike from the *current* spot, `strike x (1 -/+ pct/100)`
    snapped away from spot, exactly as the scan does. It is how the manual UI moves a
    strike: an absolute `strike_price` is still accepted (older clients, tests) but the two
    are alternatives, and `distance_pct` wins if both arrive.
    """

    lots: Optional[int] = Field(None, ge=1, le=999)
    strike_price: Optional[float] = Field(None, gt=0)
    distance_pct: Optional[float] = Field(None, gt=0, le=50)


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
