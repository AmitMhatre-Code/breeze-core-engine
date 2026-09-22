"""Schemas for the Bots section (docs/bots-mvp-plan.md).

Config is persisted as a JSON blob (see `db/bots_migrate`); these models are where its
typing actually lives, so every read and write goes through them rather than touching raw
dicts. Defaults here ARE the agreed policy -- a freshly created bot is already configured
the way the design says it should be, and a config that omits a field inherits the policy
rather than a zero.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

BotType = Literal[
    "holdings_writer",
    "expiry_index_writer",
    "momentum_long_scalper",
    "iron_fly_scalper",
    "cas_bingo",
]

# How a run was started. `schedule` is the bot's own timer, `manual` a user-pressed scan,
# `session_arrival` Bot 2 firing late because the broker session only just appeared.
# `session` is the scalpers': one run row covering a whole trading session, with its
# cycles in `bot_cycles`. It is not a fourth way of starting a run so much as a different
# unit of work -- see `docs/bots-scalping-plan.md` section 9.
# "backtest" is a replay, never a trade. It shares the Activity table with live runs so one
# surface shows everything a bot has done, but it MUST be excluded from every guard that asks
# "has this bot already acted today?" -- see `repositories/bots.LIVE_RUNS_ONLY` (#35).
# "telegram" is a HITL approval tapped on a phone rather than in the app -- see
# `services/bots/proposals.approve`'s `trigger` docstring.
BotRunTrigger = Literal["schedule", "manual", "session_arrival", "session", "backtest", "telegram"]

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
    # Bot 3 fired, but on the same signal run that opened its last trade. It re-enters only
    # after the signal has switched off and fired again (plan section 3.4).
    SIGNAL_NOT_FRESH = "signal_not_fresh"
    # Bot 4's entry filter (#38): VIX rising, or an expansion call live.
    ENTRY_FILTER_CLOSED = "entry_filter_closed"
    # The user set the bot Off (or back to Paper) while a real position was still open. The
    # loop keeps ticking it so the exit path runs -- section 5.5's "a gate that blocks
    # entering never blocks leaving", extended past the arming switch itself -- but nothing
    # new may be opened. Distinct from BOT_DISABLED, which means the bot is doing nothing
    # at all; this one is a bot that is still working, on its way out.
    ENTRIES_SUSPENDED = "entries_suspended"
    # The tick feed went quiet while a position was open. Entries freeze the moment the
    # stream is stale; an exit only fires once it has stayed stale, so a WS blip cannot
    # flatten a good position at the cost of a round trip of friction.
    STALE_FEED = "stale_feed"
    # Cycle exits
    TARGET_HIT = "target_hit"
    TRAILING_STOP = "trailing_stop"
    STOP_LOSS = "stop_loss"
    TIME_INVALIDATION = "time_invalidation"
    # Retired: the call behind a signal trade ran out. A signal trade is no longer closed by that
    # clock -- see `scalping/signal.call_reversed`. Kept so old cycles still read back.
    SIGNAL_WINDOW_ENDED = "signal_window_ended"
    # The signal fired the other way while the trade was open.
    SIGNAL_REVERSED = "signal_reversed"
    DRIFT_STOP = "drift_stop"
    CREDIT_DECAY_TARGET = "credit_decay_target"
    SQUARE_OFF = "square_off"

    # CAS Bingo (docs/bots-cas-bingo-plan.md). `signal_not_ready` is the readiness gate on an
    # Autonomous spread entry -- distinct from SIGNAL_NO_TRADE, which means the signal is
    # trusted and simply has not triggered.
    SIGNAL_NOT_READY = "signal_not_ready"
    DAY_OPEN_UNAVAILABLE = "day_open_unavailable"
    MARGIN_INSUFFICIENT = "margin_insufficient"
    # Liquidation ran (or would have) and still could not cover the shortfall. The buy-backs
    # that did fill stand: each was profitable on its own terms.
    LIQUIDATION_INSUFFICIENT = "liquidation_insufficient"
    TARGET_REACHED = "target_reached"
    # Neither exit fired, so the contract expired. P&L is an estimate at intrinsic value
    # against the last index level; the official settlement price is published later.
    EXPIRED_SETTLED = "expired_settled"

    # Failures -- something went wrong
    INTERRUPTED = "interrupted"
    CHAIN_NOT_READY = "chain_not_ready"
    QUOTE_UNAVAILABLE = "quote_unavailable"
    MARGIN_LOOKUP_FAILED = "margin_lookup_failed"
    ORDER_REJECTED = "order_rejected"
    # Orders FILLED, but the exit rule that protects them did not arm. The opposite of
    # ORDER_REJECTED in every way that matters to the reader: there is a live position,
    # money is at risk, and it needs a stop set by hand. Reporting it as a rejection told
    # the user nothing had happened while a naked short sat open.
    EXIT_ARM_FAILED = "exit_arm_failed"
    # Orders are out but some are still working at the exchange, so the stop cannot be armed
    # yet (the arm guard refuses while any order for the expiry is live). Not a failure:
    # `bots/exit_arming` arms it the moment the order feed reports every order done, and
    # rewrites the run's reason when it does.
    EXIT_ARM_PENDING = "exit_arm_pending"
    BROKER_ERROR = "broker_error"
    RATE_LIMITED = "rate_limited"
    INTERNAL_ERROR = "internal_error"


# Outcomes that mean "not yet", not "not today".
#
# The scheduled bots resolve a day once and then go quiet, which is right for a real
# decision -- a skipped day must not re-log on every thirty-second tick, and a fired day
# must never fire twice. It is wrong for a *pricing* miss. A chain that has not finished
# warming, a strike whose first tick has not arrived, a margin call that blipped: none of
# those are findings about the market, and treating one as terminal costs the whole
# remaining window. This is exactly how an expiry morning was lost -- a single
# "No spot price available" at 09:30:07 stood the bot down until the 12:00 cutoff.
#
# Membership is deliberately narrow. Anything that is a genuine answer -- no session, not
# an expiry day, margin cap too small, nothing eligible, an order rejected -- stays
# terminal, because re-asking a question already answered is its own kind of broken.
TRANSIENT_REASON_CODES: frozenset[str] = frozenset(
    {
        ReasonCode.CHAIN_NOT_READY,
        ReasonCode.QUOTE_UNAVAILABLE,
        ReasonCode.MARGIN_LOOKUP_FAILED,
        ReasonCode.BROKER_ERROR,
        ReasonCode.RATE_LIMITED,
    }
)

# How long one of those stands a bot down before it tries again. Short, because what it is
# waiting for -- a chain finishing its warm-up, a strike's first tick -- clears in seconds to
# a minute, and the entry window is finite: waiting a full nag interval would spend a sixth
# of an expiry morning on a condition that had already passed. Not as short as the tick
# either, so a genuinely dead feed leaves a readable handful of run-log rows rather than one
# every thirty seconds. It lives here rather than in the scheduler because `hitl.next_action`
# is the second gate in the same series and must agree with it; importing the scheduler from
# `hitl` would be a cycle.
TRANSIENT_RETRY_MINUTES = 2.0


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


# HH:MM in IST, 00:00-23:59. Tighter than the `[0-2]\d` this used to carry, which accepted
# "25:00" and "29:59" -- times that pass the model and then compare as ordinary strings
# against the clock, so a window bounded by one is simply never open.
HHMM_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"

# NSE's cash/derivatives session. Windows outside it can only ever log
# `outside_session_window`, so they are refused rather than saved.
MARKET_OPEN_IST = "09:15"
# Fallback only. The live value comes from `market_close_ist()` below, which reads the
# operator-editable exchange calendar -- so a session the exchange lengthens is a
# Settings change, not a code change, and a window saved against the new close is not
# rejected by a constant that still remembers the old one.
MARKET_CLOSE_IST = "15:30"


def market_close_ist() -> str:
    """Configured market close as zero-padded HH:MM, for string comparison."""
    try:
        from icici_breeze_backend.app.services.market_calendar import get_calendar_config

        cal = get_calendar_config()
        return f"{cal.close_hour:02d}:{cal.close_minute:02d}"
    except Exception:  # noqa: BLE001 — validation must not fail on a calendar read
        return MARKET_CLOSE_IST

# The earliest a scalper can usefully start. At the default signal settings the volume MA
# needs 20 one-minute bars built from live ticks -- there is no historical backfill -- so
# nothing before this can return anything but `not_warm`. It is a floor, not the whole
# truth: Bot 3's signal periods are user-editable, and a slower one pushes the real warm-up
# later still. The UI warns about that case; blocking on it would make the Signal tab
# invalidate windows saved on the Schedule tab.
EARLIEST_SESSION_START_IST = "09:35"

# Enough for a morning, an afternoon and a split around a known event, without turning the
# gate stack into a list walk or the drawer into a form nobody can read.
MAX_SESSION_WINDOWS = 4


class SessionWindow(BaseModel):
    """A trading window in IST.

    Overlaps *between* the two bots are the user's business -- they are deliberately
    independent (plan section 5.2) -- but overlaps within one bot's own list are refused by
    `validate_session_windows`, because `in_window` returns the first match and a config
    whose second window can never be reached is not one anybody means.
    """

    start: str = Field(pattern=HHMM_PATTERN)
    end: str = Field(pattern=HHMM_PATTERN)

    @model_validator(mode="after")
    def _ordered(self) -> "SessionWindow":
        if self.start >= self.end:
            raise ValueError("A session window must end after it starts.")
        return self


def validate_session_windows(
    sessions: List["SessionWindow"], hard_square_off_ist: str
) -> List["SessionWindow"]:
    """Shared window rules for both scalpers. Raises ValueError with a user-facing message.

    Zero-padded HH:MM compares correctly as a string, which is why every bound here is a
    plain comparison and there is no time parsing anywhere on this path.

    The messages are the UI's error text: `PATCH /bots/config` surfaces them verbatim, so
    they name the offending window and say what would happen, not just what is disallowed.
    """
    if not sessions:
        raise ValueError("A bot needs at least one trading window.")
    if len(sessions) > MAX_SESSION_WINDOWS:
        raise ValueError(f"At most {MAX_SESSION_WINDOWS} trading windows.")

    for w in sessions:
        span = f"{w.start}-{w.end}"
        if w.start < EARLIEST_SESSION_START_IST:
            raise ValueError(
                f"Window {span} starts before {EARLIEST_SESSION_START_IST}. The indicators "
                f"are built from live ticks with no backfill, so nothing before that can do "
                f"anything but warm up."
            )
        market_close = market_close_ist()
        if w.end > market_close:
            raise ValueError(
                f"Window {span} runs past the {market_close} market close."
            )
        if w.end > hard_square_off_ist:
            raise ValueError(
                f"Window {span} ends after the {hard_square_off_ist} square-off. The bot "
                f"would open a position and flatten it on the next pass, paying a round "
                f"trip of friction for nothing."
            )

    ordered = sorted(sessions, key=lambda w: w.start)
    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt.start < prev.end:
            raise ValueError(
                f"Windows {prev.start}-{prev.end} and {nxt.start}-{nxt.end} overlap."
            )
    return sessions


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


SignalMechanism = Literal["expansion", "momentum"]
SignalDuration = Literal[1, 5, 15]
SignalDirection = Literal["follow", "fade"]


class SignalChoice(BaseModel):
    """Which signal a bot trades on (docs/signals-streamline-plan.md section 7).

    A cell of the fixed grid -- a mechanism and a duration -- plus the bot's own direction:
    `follow` trades the call, `fade` trades against it. The duration is both the window the
    signal reads and how long a call stands, so a bot trading a call holds it until the call
    ends. The index is the bot's, not the signal's. A combination is usable only once a signal
    backtest has covered 30 days (`index_signal.gate`), which the save and arm paths enforce.
    """

    mechanism: SignalMechanism = "expansion"
    duration: SignalDuration = 15
    direction: SignalDirection = "follow"

    def series_id(self, index: str) -> str:
        return f"{index.lower()}:{self.mechanism}:{self.duration}m"

    def label(self) -> str:
        name = {"expansion": "Volume expansion", "momentum": "Momentum"}[self.mechanism]
        return f"{name} {self.duration}m" + (" · fade" if self.direction == "fade" else "")


# Retired signal-variant ids (#38) and what each became on the grid. A user-created id is read
# from its own parameters (`_legacy_signal`).
_LEGACY_VARIANT = re.compile(r"^[a-z]+-w(\d+)-(?:oi\d+|nooi)-h\d+-(follow|fade)$")


def _nearest_duration(minutes: int) -> int:
    return min((1, 5, 15), key=lambda d: (abs(d - minutes), -d))


def _legacy_signal(entry_signal: Any, default: dict[str, Any]) -> dict[str, Any]:
    """The grid cell a stored pre-grid choice maps to: Bot 3's own `momentum` (1-minute
    candles) or a variant id (its price window and direction)."""
    raw = str(entry_signal or "").strip().lower()
    if raw == "momentum":
        return {"mechanism": "momentum", "duration": 1, "direction": "follow"}
    m = _LEGACY_VARIANT.match(raw)
    if m:
        return {"mechanism": "expansion", "duration": _nearest_duration(int(m.group(1))),
                "direction": m.group(2)}
    return dict(default)


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


_BOT3_DEFAULT_SIGNAL = {"mechanism": "expansion", "duration": 15, "direction": "fade"}


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
    # 09:35 by default. The signal itself is warm from the open (its volume ranking carries
    # across the night); a 15-minute momentum reading needs nine of today's candles, so it
    # first speaks at 11:30 whatever the window says.
    sessions: List[SessionWindow] = Field(
        default_factory=lambda: [
            SessionWindow(start="09:35", end="11:30"),
            SessionWindow(start="13:30", end="15:10"),
        ],
        min_length=1,
        max_length=MAX_SESSION_WINDOWS,
    )
    hard_square_off_ist: str = Field("15:15", pattern=HHMM_PATTERN)
    # The literal reading of "a fixed premium outlay": one position at a time, so this is the
    # maximum capital *deployed* at any instant.
    #
    # 25,000 rather than the 10,000 the source conversation suggested, decided 2026-09-06
    # after the backtest refused to trade at all. That figure assumed an ATM premium of
    # 80-140 on a lot of 65 (5,200-9,100 per lot); the lot was 75 when this was decided (it is
    # 65 again now), and 80-140 describes an option one or two days from expiry rather than a
    # Monday one. At 10,000 the bot could not afford a single ATM lot beyond about 3 days to
    # expiry and stood down for half of most weeks -- see docs/bots-scalping-plan.md 8.5.
    #
    # Known and accepted consequence: because lots = floor(outlay / cost) and an ATM option
    # gets cheaper as expiry approaches, position size rises as expiry nears -- roughly 1 lot
    # at 6 DTE and 4 at 1 DTE. Risk per stop-out moves with it (450 to 1,800 at the default
    # 6-point stop), so the largest positions sit where gamma is highest. Every cycle records
    # `risk_per_stop_inr` so that swing is visible in the run log rather than implicit.
    premium_outlay_inr: float = Field(25000.0, gt=0)
    # The signal that opens a trade (docs/signals-streamline-plan.md section 7). A trade is held
    # until the call that opened it ends -- the stop and the ladder still apply -- and it is one
    # trade per call.
    #
    # Defaults to the 15-minute expansion FADE, the setting Bot 3 was running in Simulation
    # when the grid replaced the variants (2026-09-19); the per-signal backtest now shows how
    # every other cell would have done.
    signal: SignalChoice = Field(default_factory=lambda: SignalChoice(**_BOT3_DEFAULT_SIGNAL))
    exits: TrailingLadderConfig = Field(default_factory=TrailingLadderConfig)
    execution: ScalperExecutionConfig = Field(default_factory=ScalperExecutionConfig)
    risk: ScalperRiskConfig = Field(default_factory=ScalperRiskConfig)

    @model_validator(mode="before")
    @classmethod
    def _map_retired_signal(cls, data: Any) -> Any:
        """Stored configs from before the grid carry `entry_signal` (a variant id or
        "momentum") and a `signal` block of EMA/volume settings. Map them onto the grid."""
        if not isinstance(data, dict):
            return data
        signal = data.get("signal")
        if isinstance(signal, dict) and "mechanism" in signal:
            return data
        out = {k: v for k, v in data.items() if k != "entry_signal"}
        if "entry_signal" in data or isinstance(signal, dict):
            out["signal"] = _legacy_signal(data.get("entry_signal"), _BOT3_DEFAULT_SIGNAL)
        return out

    @model_validator(mode="after")
    def _windows_are_tradeable(self) -> "MomentumLongScalperConfig":
        validate_session_windows(self.sessions, self.hard_square_off_ist)
        return self


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

    **The flat stop ships off** (2026-09-13). A fly opens about a point down on the bid-ask
    spread of its four legs alone, and time decay earns that back only over tens of minutes.
    On the 10-11 Sep paper days a 1,500 flat stop on ~13 lots was under two points: flies
    were stopped out 14 and 37 seconds after entry, and the results said nothing about the
    strategy. The share-of-credit stop and the drift stop now govern; the fly otherwise holds
    to the end of its window, which is where its time decay is actually earned.
    """

    target_decay_pct: float = Field(15.0, gt=0, le=100)
    hard_stop_loss_inr: Optional[float] = Field(None, gt=0)
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


IronFlyEntryFilterKind = Literal["none", "vix_not_rising", "signal_quiet"]


class IronFlyEntryFilterConfig(BaseModel):
    """An extra condition on opening a fly, on top of the re-entry gate.

    A fly earns its credit when the market moves less than implied volatility priced in, so
    both filters try to skip the moments that premise is visibly failing:

    * `vix_not_rising` -- India VIX has not risen more than `vix_max_rise_pct` over the last
      `vix_lookback_minutes`. Rising implied volatility marks every short leg up at once.
    * `signal_quiet` -- the chosen signal has no live call. A call of either side is a move the
      signal thinks is under way: the opposite of the quiet a fly wants. Direction is
      irrelevant here, so `signal.direction` is ignored.

    Both fail closed: a VIX series or a signal that cannot be read holds the entry.
    """

    kind: IronFlyEntryFilterKind = "none"
    vix_lookback_minutes: int = Field(15, ge=1, le=120)
    vix_max_rise_pct: float = Field(2.0, ge=0, le=50)
    signal: SignalChoice = Field(default_factory=SignalChoice)

    @model_validator(mode="before")
    @classmethod
    def _map_retired_filter(cls, data: Any) -> Any:
        """`expansion_neutral` on a variant id (#38) is `signal_quiet` on its grid cell."""
        if not isinstance(data, dict):
            return data
        out = {k: v for k, v in data.items() if k != "variant"}
        if data.get("kind") == "expansion_neutral":
            out["kind"] = "signal_quiet"
        if "variant" in data and not isinstance(data.get("signal"), dict):
            out["signal"] = _legacy_signal(data.get("variant"), {"mechanism": "expansion", "duration": 15})
        return out


class IronFlyScalperConfig(BaseModel):
    """Bot 4 -- ATM iron fly, booked on credit decay, re-entered behind a gate."""

    index: Literal["NIFTY"] = "NIFTY"
    expiry_preference: Literal["nearest_weekly"] = "nearest_weekly"
    trade_on_expiry_day: bool = False
    mode: ScalperMode = "paper"
    sessions: List[SessionWindow] = Field(
        default_factory=lambda: [SessionWindow(start="11:30", end="13:30")],
        min_length=1,
        max_length=MAX_SESSION_WINDOWS,
    )
    hard_square_off_ist: str = Field("15:15", pattern=HHMM_PATTERN)
    # A rupee ceiling rather than a lot count (adapts to VIX-driven margin changes) or a
    # share of free margin (which would drift with the day's P&L). The bot takes the largest
    # whole-lot fly that fits, verified through `margin_calculator` on all four legs at once
    # so the exchange's netting is real.
    #
    # 25,000 is about three lots: a hedged fly needed roughly 7,000-7,700 a lot in the
    # 10-11 Sep 2026 paper sessions. The original 1,00,000 default bought ~13 lots, which
    # put the loss stops -- and the 10,000 daily limit -- inside the entry spread's noise.
    margin_ceiling_inr: float = Field(25000.0, gt=0)
    min_lots: int = Field(1, ge=1, le=100)
    structure: IronFlyStructureConfig = Field(default_factory=IronFlyStructureConfig)
    exits: IronFlyExitConfig = Field(default_factory=IronFlyExitConfig)
    reentry: IronFlyReentryConfig = Field(default_factory=IronFlyReentryConfig)
    entry_filter: IronFlyEntryFilterConfig = Field(default_factory=IronFlyEntryFilterConfig)
    execution: ScalperExecutionConfig = Field(default_factory=ScalperExecutionConfig)
    risk: ScalperRiskConfig = Field(default_factory=ScalperRiskConfig)

    @model_validator(mode="after")
    def _windows_are_tradeable(self) -> "IronFlyScalperConfig":
        validate_session_windows(self.sessions, self.hard_square_off_ist)
        return self

    @model_validator(mode="after")
    def _widened_is_wider(self) -> "IronFlyScalperConfig":
        s = self.structure
        if s.widen_above_vix is not None and s.widened_wing_width_points <= s.wing_width_points:
            raise ValueError("The widened wing width must exceed the normal one.")
        return self


# --------------------------------------------------------------------------------------
# Bot 5 -- CAS Bingo, the expiry-day closing-auction bot (docs/bots-cas-bingo-plan.md)
# --------------------------------------------------------------------------------------


# `simulation` runs the full logic on live prices and places nothing; `live` is the card's
# Autonomous. Manual is simply `enabled=False` -- the run sheet works in every mode.
CasBingoMode = Literal["simulation", "live"]
CasBingoStrategy = Literal["credit_spread", "debit_spread", "long_strangle"]

CAS_BINGO_INDICES = ("NIFTY", "BSESEN")


class CasBingoIndex(BaseModel):
    enabled: bool = True


def _outer_beyond_inner(inner_pct: float, outer_pct: float, what: str) -> None:
    # A spread whose far leg sits inside its near leg is a different structure (or none).
    if outer_pct <= inner_pct:
        raise ValueError(
            f"The {what}'s outer leg must sit further out than its inner leg "
            f"({outer_pct}% is not beyond {inner_pct}%)."
        )


class CasBingoCreditConfig(BaseModel):
    """Credit spread: after the index has moved `move_trigger_pct` from the day's open, any
    signal flip against that move sells the inner leg (CE after a rise, PE after a drop) with
    the outer leg bought first as the hedge.

    Strikes are measured from the **day's open**, not from spot at entry -- spot can swing
    2-3% inside CAS, and the thesis is a reversal back toward the open. So a sold leg can be in
    the money versus spot at entry; the user confirmed that is the bet.

    **Inside the auction (from 15:20) the rule is different** (2026-09-13, plan section 3.2b).
    The order-book signal cannot be read there, so no flip is consulted: the sold leg sits
    `auction_gap_pct` beyond the exchange's *indicative* index -- roughly where expiry would
    settle now -- on the side the index has moved from the open, and it is sold only while the
    spread still pays `auction_min_credit_pct` of its width. An option that should expire
    worthless but still costs that much is the spike being faded. The hedge sits one spread
    width further out, the width being `outer_pct - inner_pct`.
    """

    margin_lakhs: float = Field(2.0, gt=0, le=1000)
    move_trigger_pct: float = Field(0.5, gt=0, le=10)
    inner_pct: float = Field(0.5, ge=0, le=20)
    outer_pct: float = Field(1.0, gt=0, le=25)
    # The user asked for the gap as a setting: SENSEX has swung 2-3% inside the auction on
    # expiry days, so how much room to leave is a judgement Simulation should inform.
    auction_gap_pct: float = Field(1.0, gt=0, le=10)
    auction_min_credit_pct: float = Field(
        10.0, gt=0, le=100, description="Net credit as a share of the spread's width"
    )
    target_pct: float = Field(
        80.0, gt=0, le=100, description="Book once this share of the credit is captured"
    )
    stop_loss_pct: float = Field(
        100.0, gt=0, le=1000, description="Stop once the loss reaches this share of the credit"
    )

    @model_validator(mode="after")
    def _legs_ordered(self) -> "CasBingoCreditConfig":
        _outer_beyond_inner(self.inner_pct, self.outer_pct, "credit spread")
        return self


class CasBingoDebitConfig(BaseModel):
    """Debit spread: a signal flip, held for `sustain_minutes`, buys the inner leg (0% = ATM)
    and sells the outer one, in the direction of the flip (against it when the bot's signal is
    set to fade). Strikes are measured from spot at the moment of deploying.

    There is no strength threshold: it was a W-OBI-scale number, and on the grid every
    expansion call is already a top-fifth move and momentum has no strength at all."""

    premium_budget_inr: float = Field(10000.0, gt=0, le=10_000_000)
    sustain_minutes: float = Field(3.0, ge=0, le=60)
    inner_pct: float = Field(0.0, ge=0, le=20)
    outer_pct: float = Field(0.5, gt=0, le=25)
    target_pct: float = Field(100.0, gt=0, le=1000, description="Gain as a share of the debit")
    stop_loss_pct: float = Field(50.0, gt=0, le=100, description="Loss as a share of the debit")

    @model_validator(mode="after")
    def _legs_ordered(self) -> "CasBingoDebitConfig":
        _outer_beyond_inner(self.inner_pct, self.outer_pct, "debit spread")
        return self


class CasBingoStrangleConfig(BaseModel):
    """Long strangle: no signal -- it buys both sides at `entry_time_ist`."""

    premium_budget_inr: float = Field(10000.0, gt=0, le=10_000_000)
    entry_time_ist: str = Field("15:15", pattern=HHMM_PATTERN)
    call_pct: float = Field(0.5, ge=0, le=20)
    put_pct: float = Field(0.5, ge=0, le=20)
    target_pct: float = Field(100.0, gt=0, le=1000, description="Gain as a share of the debit")
    stop_loss_pct: float = Field(50.0, gt=0, le=100, description="Loss as a share of the debit")


class CasBingoLiquidationConfig(BaseModel):
    """Buying back profitable shorts on the same index and expiry to free margin.

    `min_captured_pct` is the share of the original premium that must already be captured:
    80 means a short sold at 100 is bought back only at 20 or less, and the buy-back limit is
    capped there -- so a liquidation can never be a losing trade.
    """

    enabled: bool = True
    min_captured_pct: float = Field(80.0, gt=0, lt=100)
    # Added on top of the shortfall before the local estimate is trusted: the in-app SPAN
    # engine under-prices short calls against ICICI by up to ~22% (margin harness, 2026-09).
    safety_buffer_pct: float = Field(10.0, ge=0, le=100)


class CasBingoConfig(BaseModel):
    """CAS Bingo -- trades today's expiry inside two windows around the closing auction.

    Ships in `simulation` for the same reason the scalpers ship in paper: arming something
    that fires unattended orders should be a deliberate act.
    """

    mode: CasBingoMode = "simulation"
    indices: Dict[str, CasBingoIndex] = Field(
        default_factory=lambda: {code: CasBingoIndex() for code in CAS_BINGO_INDICES}
    )
    # Both are ENTRY windows (the user rejected "pre-CAS observes, CAS enters"). 15:29, not
    # 15:30: derivatives stop at the close and an entry needs time to fill.
    pre_cas_window: SessionWindow = Field(
        default_factory=lambda: SessionWindow(start="14:30", end="15:15")
    )
    cas_window: SessionWindow = Field(
        default_factory=lambda: SessionWindow(start="15:15", end="15:29")
    )
    strategy: CasBingoStrategy = "debit_spread"
    # The signal both spreads read their flips from. `direction` applies to the debit spread
    # only: the credit spread's rule is already "a flip against the day's move".
    signal: SignalChoice = Field(default_factory=SignalChoice)
    credit: CasBingoCreditConfig = Field(default_factory=CasBingoCreditConfig)
    debit: CasBingoDebitConfig = Field(default_factory=CasBingoDebitConfig)
    strangle: CasBingoStrangleConfig = Field(default_factory=CasBingoStrangleConfig)
    liquidation: CasBingoLiquidationConfig = Field(default_factory=CasBingoLiquidationConfig)
    execution: ScalperExecutionConfig = Field(default_factory=ScalperExecutionConfig)

    @field_validator("indices")
    @classmethod
    def _known_indices(cls, v: Dict[str, CasBingoIndex]) -> Dict[str, CasBingoIndex]:
        unknown = set(v) - set(CAS_BINGO_INDICES)
        if unknown:
            raise ValueError(f"Unsupported index code(s): {', '.join(sorted(unknown))}")
        return v

    @model_validator(mode="after")
    def _windows_are_tradeable(self) -> "CasBingoConfig":
        pre, cas = self.pre_cas_window, self.cas_window
        if pre.start < MARKET_OPEN_IST:
            raise ValueError(f"The pre-CAS window cannot start before the {MARKET_OPEN_IST} open.")
        close = market_close_ist()
        if cas.end > close:
            raise ValueError(f"The CAS window runs past the {close} market close.")
        if pre.end > cas.start:
            raise ValueError(
                f"The pre-CAS window ({pre.start}-{pre.end}) must end by the time the CAS "
                f"window starts ({cas.start})."
            )
        t = self.strangle.entry_time_ist
        if not any(w.start <= t < w.end for w in (pre, cas)):
            raise ValueError(
                f"The long strangle's entry time {t} is outside both windows, so it could "
                f"never fire."
            )
        return self

    def windows(self) -> List[SessionWindow]:
        return [self.pre_cas_window, self.cas_window]

    def enabled_indices(self) -> List[str]:
        return [code for code in CAS_BINGO_INDICES if self.indices.get(code, CasBingoIndex(enabled=False)).enabled]


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
    #: Filename of the audit trail covering this run's trading day, when one was written.
    #: Resolved per *day*, not per run: a day split across a dozen interrupted session rows
    #: is one continuous record, and linking each fragment to its own slice would rebuild
    #: exactly the fragmentation the trail exists to see past.
    audit_log: Optional[str] = None


class BotRunBundle(BaseModel):
    """Back-to-back runs of one bot sharing a day, trigger and outcome, collapsed to one row.

    "Back to back" is per bot: another bot's run in between does not split a bundle, only
    this bot's own next run with a different trigger or outcome does. The frontend bundles
    short ranges itself with the same rule (`frontend/src/lib/bot-run-bundles.ts`); keep the
    two in step. A bundle's runs are exactly this bot's runs with this trigger and status
    between `first_started_at` and `last_started_at`, which is how they are fetched on
    expand."""

    bot_type: BotType
    trigger: BotRunTrigger
    status: BotRunStatus
    date: str
    count: int
    first_started_at: Optional[str] = None
    last_started_at: Optional[str] = None
    latest: BotRunRecord
    #: Distinct reasons across the bundle; the row shows the latest and "+N other reasons".
    distinct_reasons: int = 1
    #: The bot's full-day audit trail for a live bundle. A backtest's trail is per run, so a
    #: bundle of several backtests carries none here and each run keeps its own link.
    audit_log: Optional[str] = None


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
    # Today's most recent entry signal (Bot 3): the candle it fired on and its side. Read from
    # the cycle rows like everything else here, so the fresh-signal rule survives a restart.
    last_entry_candle_start: Optional[int] = None
    last_entry_side: Optional[str] = None


class PaperEvidenceDay(BaseModel):
    """One completed paper trading day, as the confirmation dialog shows it."""

    trading_day: str
    reason_code: Optional[str] = None
    reason_text: Optional[str] = None
    cycles: int = 0
    closed_cycles: int = 0
    wins: int = 0
    losses: int = 0
    net_pnl: float = 0.0
    friction: float = 0.0


class BacktestEvidence(BaseModel):
    """The latest completed backtest on the settings being armed, if there is one.

    Deliberately NOT part of the gate: `unlocked` stays one completed Simulation day. This
    travels beside it because the two answer different questions — a Simulation day proves the
    bot runs end to end against live plumbing and real fills, while a replay over months is the
    only thing that says anything about edge. One quiet Simulation day on its own is a thin
    basis for judging whether a strategy makes money.

    The backtest's own limits ride along rather than being hidden: `price_source` says whether
    it was priced off real traded candles or Black-Scholes, `days_awaiting_data` how much of the
    range is missing from the totals, and `compare_*` whether its fills were ever checked
    against a Simulation day (its spread is modelled, so that check is the only fill evidence).
    """

    run_id: str
    created_at: str
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    price_source: str = ""
    days_replayed: int = 0
    days_awaiting_data: int = 0
    cycles: int = 0
    win_rate_pct: Optional[float] = None
    net_pnl: float = 0.0
    friction: float = 0.0
    compare_day: Optional[str] = None
    compare_median_entry_gap: Optional[float] = None
    compare_pairs: Optional[int] = None


class LiveEligibility(BaseModel):
    """Whether a scalper may be set `live`, and the evidence the user judges it on.

    `unlocked` is the gate's answer and nothing more: one completed paper trading day on the
    current settings. Whether that day was GOOD is the user's call, which is why every number
    behind it travels with the verdict rather than being reduced to a boolean here.
    """

    bot_type: BotType
    unlocked: bool = False
    config_hash: str = ""
    days: int = 0
    cycles: int = 0
    closed_cycles: int = 0
    wins: int = 0
    losses: int = 0
    net_pnl: float = 0.0
    friction: float = 0.0
    sessions: List[PaperEvidenceDay] = Field(default_factory=list)
    # Present only when locked, so the card can say what is missing rather than just
    # disabling a segment with no explanation.
    blocked_reason: Optional[str] = None
    # The other half of the picture, when a backtest exists on these exact settings. Never
    # consulted by the gate — see BacktestEvidence.
    backtest: Optional[BacktestEvidence] = None


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
    # Placement only: whether THIS leg's orders reached the exchange. Whether the position
    # got its stop is a property of the index, not the leg, and lives on `ApprovalResult.stops`
    # -- folding the two together is what once reported two placed legs as "0 of 2 placed".
    error: Optional[str] = None
    # What the order feed has confirmed filled so far; None when it has not reported yet.
    filled_quantity: Optional[int] = None


class ExitStopResult(BaseModel):
    """The PB/SL state of one index's position once the approval has placed it."""

    stock_code: str
    expiry_display: str
    # "armed" | "pending" (waiting for the orders to finish filling) | "failed"
    status: str
    rule_id: Optional[str] = None
    # The `bot_pending_exits` row arming it later, when it was not armed at placement.
    pending_exit_id: Optional[str] = None
    detail: Optional[str] = None


class ApprovalResult(BaseModel):
    proposal_id: str
    placed: List[PlacedLegResult] = Field(default_factory=list)
    # Every leg reached the exchange. Says nothing about the stop -- see `stops`.
    all_succeeded: bool
    stops: List[ExitStopResult] = Field(default_factory=list)


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
