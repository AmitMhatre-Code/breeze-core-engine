"""Bot 2 — Expiry-Day Index Writer (docs/bots-mvp-plan.md section 4).

This bot trades unattended, so it is split in two on purpose:

  * `decide()` is pure. Given the clock, the config, whether anything expires today and
    whether a broker session exists, it returns what should happen — and nothing else.
    Every awkward case (the session arriving at 11:47, the cutoff passing with no login,
    two indices expiring on the same day) is decided here, where it can be tested without
    a broker, a market, or a clock that has to be the real one.
  * `fire()` does the IO, and only ever runs because `decide()` said so.

The reliability problem this bot exists around is not the strategy — it is that the ICICI
session lapses overnight, so an unattended 09:30 entry depends on a human having logged in
that morning. Hence the nag window, and hence a cutoff that ends the day cleanly rather
than leaving the bot half-armed.
"""
from __future__ import annotations

import datetime
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import parse_strike
from icici_breeze_backend.app.domain.bots import (
    ExpiryIndexWriterConfig,
    IndexWriterLeg,
    ReasonCode,
)

_logger = logging.getLogger(__name__)

# ICICI codes. SENSEX trades on BFO; NIFTY on NFO.
INDEX_EXCHANGE = {"NIFTY": cfg.NFO, "BSESEN": cfg.BFO}
INDEX_LABEL = {"NIFTY": "NIFTY", "BSESEN": "SENSEX"}

TickAction = Literal["idle", "nag", "fire", "skip"]


@dataclass(frozen=True)
class TickContext:
    now: datetime.datetime
    app_started_at: datetime.datetime
    config: ExpiryIndexWriterConfig
    # index code -> expiry_display, for indices whose contracts expire *today*.
    expiring_today: dict[str, str]
    has_session: bool
    ran_today: bool
    last_nag_at: Optional[datetime.datetime] = None


@dataclass(frozen=True)
class TickDecision:
    action: TickAction
    reason_code: Optional[str] = None
    reason_text: Optional[str] = None
    # Index codes to trade, already ordered by the user's priority.
    indices: tuple[str, ...] = ()


def _at(now: datetime.datetime, hhmm: str) -> datetime.datetime:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _ordered_indices(config: ExpiryIndexWriterConfig, expiring: dict[str, str]) -> tuple[str, ...]:
    """Enabled indices expiring today, lowest priority number first.

    Priority only ever matters on a day both expire, which currently never happens — but
    the per-index margin caps already bound that case, so this is a tie-break, not the
    safety mechanism.
    """
    candidates = [
        (leg.priority, code)
        for code, leg in config.indices.items()
        if leg.enabled and code in expiring
    ]
    return tuple(code for _, code in sorted(candidates))


def decide(ctx: TickContext) -> TickDecision:
    """What this tick should do. Pure — no IO, no globals, no wall clock."""
    config = ctx.config

    if ctx.ran_today:
        # Terminal for the day either way: a fired bot must not fire twice, and a day
        # already logged as skipped must not re-log on every subsequent tick.
        return TickDecision("idle")

    indices = _ordered_indices(config, ctx.expiring_today)
    if not indices:
        if not ctx.expiring_today:
            return TickDecision(
                "skip",
                ReasonCode.NOT_AN_EXPIRY_DAY,
                "No NIFTY or SENSEX expiry today.",
            )
        return TickDecision(
            "skip",
            ReasonCode.NOTHING_ELIGIBLE,
            "An index expires today, but none of the ones you enabled.",
        )

    cutoff = _at(ctx.now, config.cutoff_ist)
    entry = _at(ctx.now, config.entry_time_ist)
    # The nag cannot start before the app is up to send it, so a deployment powered on at
    # 09:10 starts nagging then rather than pretending it nagged from 08:00.
    nag_start = max(_at(ctx.now, config.nag_start_ist), ctx.app_started_at)

    if ctx.now >= cutoff:
        if not ctx.has_session:
            return TickDecision(
                "skip",
                ReasonCode.NO_BROKER_SESSION,
                f"No ICICI session by the {config.cutoff_ist} cut-off, so nothing was traded.",
            )
        return TickDecision(
            "skip",
            ReasonCode.CUTOFF_PASSED,
            f"The {config.cutoff_ist} cut-off passed before this could enter.",
        )

    if not ctx.has_session:
        if ctx.now < nag_start:
            return TickDecision("idle")
        due = ctx.last_nag_at is None or (
            ctx.now - ctx.last_nag_at
        ) >= datetime.timedelta(minutes=config.nag_interval_minutes)
        if not due:
            return TickDecision("idle")
        return TickDecision(
            "nag",
            ReasonCode.NO_BROKER_SESSION,
            (
                f"{', '.join(INDEX_LABEL.get(i, i) for i in indices)} expires today and your "
                f"bot is armed, but your ICICI session has lapsed. Log in before "
                f"{config.cutoff_ist} or the bot will skip today."
            ),
            indices,
        )

    if ctx.now < entry:
        return TickDecision("idle")

    # A session that only appears at 11:12 fires immediately rather than waiting for a
    # scheduled time that has already passed.
    return TickDecision("fire", None, None, indices)


# --------------------------------------------------------------------------------------
# Candidates -- pricing each shortlisted strategy so they can be compared
# --------------------------------------------------------------------------------------


@dataclass
class CandidateLeg:
    right: str  # cfg.CALL / cfg.PUT
    strike_price: float
    bid: float


@dataclass
class Candidate:
    """One shortlisted strategy, priced for a single lot so shapes compare like for like."""

    strategy: str
    legs: list[CandidateLeg]
    premium_per_lot: float
    margin_per_lot: float

    @property
    def margin_yield(self) -> float:
        """Premium collected per rupee of margin committed.

        This, not absolute premium, is how the shortlist is ranked -- and the distinction is
        the whole reason the ranking needs stating. A strangle is both legs, so on absolute
        premium it wins every time it is shortlisted, which would quietly retire naked CE and
        naked PE the moment a user ticked all three. Per rupee of margin it has to earn the
        extra capital it ties up, and the exchange's own netting of the two sides is what
        gives it a fair chance of doing so.
        """
        return self.premium_per_lot / self.margin_per_lot if self.margin_per_lot > 0 else 0.0

    @property
    def label(self) -> str:
        return STRATEGY_LABEL.get(self.strategy, self.strategy)


STRATEGY_LABEL = {
    "naked_ce": "Naked CE",
    "naked_pe": "Naked PE",
    "short_strangle": "Short strangle",
}

# Which sides each shortlisted shape sells.
STRATEGY_RIGHTS: dict[str, tuple[str, ...]] = {
    "naked_ce": (cfg.CALL,),
    "naked_pe": (cfg.PUT,),
    "short_strangle": (cfg.CALL, cfg.PUT),
}


def _chain_rows(proc: Any, user_id: str, index_code: str, exchange: str, expiry: str, right: str):
    from icici_breeze_backend.app.services.quote_source_router import (
        fetch_chain_side_icici_response,
    )

    chain = fetch_chain_side_icici_response(proc, user_id, index_code, exchange, expiry, right)
    if (chain or {}).get("Status") != 200 or not chain.get("Success"):
        return []
    return [r for r in chain["Success"] if isinstance(r, dict)]


def _spot_from(rows: list[dict]) -> float:
    for r in rows:
        try:
            spot = float(r.get("spot_price") or 0)
        except (TypeError, ValueError):
            continue
        if spot > 0:
            return spot
    return 0.0


def _bid(row: dict) -> float:
    try:
        return float(row.get("best_bid_price") or 0)
    except (TypeError, ValueError):
        return 0.0


def margin_for_legs(
    proc: Any,
    user_id: str,
    *,
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    legs: list[tuple[str, float, int]],
) -> Optional[float]:
    """SPAN for a set of short legs priced together in ONE margin_calculator call.

    Sending both sides of a strangle in a single call is what makes the comparison honest:
    the exchange nets the two, and pricing each side alone and adding them would overstate a
    strangle's cost by the whole netting benefit -- biasing the yield ranking against the
    very shape the netting exists to reward.

    `legs` is (right, strike, quantity). Returns None on any failure; callers treat that as
    "cannot price", never as zero.
    """
    from icici_breeze_backend.app.core.strike import strike_for_broker
    from icici_breeze_backend.app.services.processor import _expiry_display_to_api

    if not legs:
        return None
    try:
        breeze = proc.get_session_breeze(user_id)
        expiry_api = _expiry_display_to_api(expiry_display)
        payload = [
            {
                "strike_price": strike_for_broker(strike),
                "quantity": int(quantity),
                "product": cfg.OPTIONS,
                "action": cfg.SELL,
                "expiry_date": expiry_api,
                "stock_code": stock_code,
                "right": right,
            }
            for right, strike, quantity in legs
        ]
        out = breeze.margin_calculator(payload, exchange_code=exchange_code)
    except Exception:  # noqa: BLE001 -- an unpriceable shape drops out of the shortlist
        _logger.warning("bot2: margin_calculator failed for %s", stock_code, exc_info=True)
        return None
    if not isinstance(out, dict) or out.get("Status") != 200:
        return None
    try:
        value = float((out.get("Success") or {}).get("span_margin_required") or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def build_candidates(
    proc: Any,
    user_id: str,
    index_code: str,
    *,
    exchange: str,
    expiry_display: str,
    leg_cfg: IndexWriterLeg,
    lot_size: int,
) -> tuple[list[Candidate], Optional[str]]:
    """Price every shortlisted strategy for one lot. Returns (candidates, error)."""
    rights_needed = {r for s in leg_cfg.strategies for r in STRATEGY_RIGHTS.get(s, ())}
    rows_by_right: dict[str, list[dict]] = {}
    spot = 0.0
    for right in rights_needed:
        rows = _chain_rows(proc, user_id, index_code, exchange, expiry_display, right)
        if not rows:
            return [], "No option chain available."
        rows_by_right[right] = rows
        spot = spot or _spot_from(rows)
    if spot <= 0:
        return [], "No spot price available."

    # Pick each side once and reuse it: a strangle's call leg is the same contract the
    # naked-CE candidate would sell, so pricing it twice would only invite them to drift.
    picked: dict[str, CandidateLeg] = {}
    for right in rights_needed:
        safety = leg_cfg.safety_pct_ce if right == cfg.CALL else leg_cfg.safety_pct_pe
        row = _pick_strike(rows_by_right[right], spot, right, safety)
        if row is None:
            continue
        strike = float(parse_strike(row.get("strike_price")) or 0)
        bid = _bid(row)
        # Unlike Bot 1 there is no indicative fallback: this bot only runs during market
        # hours, so a missing bid means the book really is empty.
        if strike <= 0 or bid <= 0:
            continue
        picked[right] = CandidateLeg(right=right, strike_price=strike, bid=bid)

    candidates: list[Candidate] = []
    for strategy in leg_cfg.strategies:
        rights = STRATEGY_RIGHTS.get(strategy, ())
        legs = [picked[r] for r in rights if r in picked]
        if len(legs) != len(rights):
            continue
        margin = margin_for_legs(
            proc,
            user_id,
            exchange_code=exchange,
            stock_code=index_code,
            expiry_display=expiry_display,
            legs=[(leg.right, leg.strike_price, lot_size) for leg in legs],
        )
        if margin is None:
            continue
        candidates.append(
            Candidate(
                strategy=strategy,
                legs=legs,
                premium_per_lot=round(sum(leg.bid for leg in legs) * lot_size, 2),
                margin_per_lot=round(margin, 2),
            )
        )
    if not candidates:
        return [], "None of the shortlisted strategies could be priced."
    return candidates, None


def choose(candidates: list[Candidate]) -> Optional[Candidate]:
    """Best premium per rupee of margin. Ties break towards the cheaper shape."""
    if not candidates:
        return None
    return max(candidates, key=lambda c: (c.margin_yield, -c.margin_per_lot))


# --------------------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------------------


@dataclass
class FireResult:
    index_code: str
    exchange_code: str
    expiry_display: str
    right: str
    strategy: Optional[str] = None
    strike_price: Optional[float] = None
    legs: list[dict] = field(default_factory=list)
    lots: int = 0
    quantity: int = 0
    entry_price: Optional[float] = None
    span_per_lot: Optional[float] = None
    margin_total: Optional[float] = None
    premium_total: Optional[float] = None
    margin_yield: Optional[float] = None
    considered: list[dict] = field(default_factory=list)
    budget: Optional[float] = None
    order_ids: list[str] = field(default_factory=list)
    rule_id: Optional[str] = None
    reason_code: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.order_ids)


def _available_margin(proc: Any, user_id: str) -> Optional[float]:
    situation = proc.get_margin_situation(user_id, 0)
    if (situation or {}).get("Status") != 200:
        return None
    success = situation.get("Success") or {}
    for key in ("actual_margin_avl", "limits", "cash_limit"):
        try:
            value = float(success.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _pick_strike(rows: list[dict], spot: float, right: str, safety_pct: float) -> Optional[dict]:
    """Same rule as Bot 1: at or beyond the safety distance, rounded away from spot."""
    if spot <= 0:
        return None
    target = spot * (1 + safety_pct / 100) if right == cfg.CALL else spot * (1 - safety_pct / 100)
    best, best_distance = None, float("inf")
    for row in rows:
        strike = parse_strike(row.get("strike_price"))
        if strike is None:
            continue
        s = float(strike)
        if right == cfg.CALL and s < target:
            continue
        if right == cfg.PUT and s > target:
            continue
        if abs(s - target) < best_distance:
            best, best_distance = row, abs(s - target)
    return best


def plan_index(
    proc: Any,
    user_id: str,
    index_code: str,
    *,
    expiry_display: str,
    config: ExpiryIndexWriterConfig,
    available_margin: float,
    margin_source: str,
) -> FireResult:
    """Decide *what* to trade and *how big*, without placing anything.

    Split out from `fire_index` so the manual run and the unattended run size identically --
    a manual review that showed different numbers from what the bot would have done on its
    own would be worse than no review at all.
    """
    leg_cfg = config.indices[index_code]
    exchange = INDEX_EXCHANGE.get(index_code, cfg.NFO)
    result = FireResult(
        index_code=index_code,
        exchange_code=exchange,
        expiry_display=expiry_display,
        right="",
    )
    budget = available_margin * leg_cfg.margin_pct_cap / 100.0
    result.budget = round(budget, 2)

    lot_size = proc.fetch_lot_size(index_code, expiry_display, exchange_code=exchange)
    try:
        lot_size = int(lot_size or 0)
    except (TypeError, ValueError):
        lot_size = 0
    if lot_size <= 0:
        result.reason_code = ReasonCode.INTERNAL_ERROR
        result.error = "No lot size in the scrip master."
        return result

    candidates, error = build_candidates(
        proc,
        user_id,
        index_code,
        exchange=exchange,
        expiry_display=expiry_display,
        leg_cfg=leg_cfg,
        lot_size=lot_size,
    )
    if error:
        result.reason_code = (
            ReasonCode.CHAIN_NOT_READY
            if "chain" in error.lower()
            else ReasonCode.QUOTE_UNAVAILABLE
        )
        result.error = error
        return result

    # Every shortlisted shape is recorded, not just the winner. A user who ticked three
    # strategies and got a strangle needs to see what the other two would have yielded,
    # otherwise the choice is unexplainable after the fact.
    result.considered = [
        {
            "strategy": c.strategy,
            "label": c.label,
            "premium_per_lot": c.premium_per_lot,
            "margin_per_lot": c.margin_per_lot,
            "margin_yield": round(c.margin_yield, 6),
        }
        for c in candidates
    ]

    best = choose(candidates)
    if best is None:
        result.reason_code = ReasonCode.QUOTE_UNAVAILABLE
        result.error = "No strategy could be priced."
        return result

    result.strategy = best.strategy
    result.span_per_lot = best.margin_per_lot
    result.margin_yield = round(best.margin_yield, 6)
    # `right` and `strike_price` describe the single-leg case and stay populated for it;
    # `legs` is the full picture and is what placement and the exit arming read.
    result.right = "call" if best.legs[0].right == cfg.CALL else "put"
    result.strike_price = best.legs[0].strike_price if len(best.legs) == 1 else None

    lots = int(math.floor(budget / best.margin_per_lot))
    if lots < 1:
        result.reason_code = ReasonCode.MARGIN_CAP_TOO_SMALL
        result.error = (
            f"One lot of {best.label} needs about Rs {best.margin_per_lot:,.0f}, above the "
            f"Rs {budget:,.0f} this index is allowed."
        )
        return result

    # Verify against a real margin call at the full size before committing capital. The
    # per-lot figure does not always scale linearly, and over-committing an unattended trade
    # is exactly what the cap exists to prevent.
    verified = margin_for_legs(
        proc,
        user_id,
        exchange_code=exchange,
        stock_code=index_code,
        expiry_display=expiry_display,
        legs=[(leg.right, leg.strike_price, lots * lot_size) for leg in best.legs],
    )
    if verified is not None:
        while lots > 1 and verified > budget:
            lots -= 1
            verified = best.margin_per_lot * lots
        if verified > budget:
            result.reason_code = ReasonCode.MARGIN_CAP_TOO_SMALL
            result.error = (
                f"Verified margin Rs {verified:,.0f} for one lot of {best.label} exceeds "
                f"the Rs {budget:,.0f} cap."
            )
            return result
        result.margin_total = round(verified, 2)
    else:
        result.margin_total = round(best.margin_per_lot * lots, 2)

    result.lots = lots
    result.quantity = lots * lot_size
    result.premium_total = round(best.premium_per_lot * lots, 2)
    result.entry_price = best.legs[0].bid if len(best.legs) == 1 else None
    result.legs = [
        {
            "right": "call" if leg.right == cfg.CALL else "put",
            "strike_price": leg.strike_price,
            "bid": leg.bid,
            "quantity": result.quantity,
            "premium_total": round(leg.bid * result.quantity, 2),
        }
        for leg in best.legs
    ]
    return result


def fire_index(
    proc: Any,
    user_id: str,
    index_code: str,
    *,
    expiry_display: str,
    config: ExpiryIndexWriterConfig,
    available_margin: float,
    margin_source: str,
) -> FireResult:
    """Size and place one index's short position, then arm its exit."""
    result = plan_index(
        proc,
        user_id,
        index_code,
        expiry_display=expiry_display,
        config=config,
        available_margin=available_margin,
        margin_source=margin_source,
    )
    if result.error or not result.legs:
        return result
    return execute_plan(proc, user_id, result, config=config)


def execute_plan(
    proc: Any, user_id: str, result: FireResult, *, config: ExpiryIndexWriterConfig
) -> FireResult:
    """Place a plan's legs and arm the exit. Shared by the scheduler and the manual run."""
    from icici_breeze_backend.app.services.bots import placement

    exchange = result.exchange_code
    placed = placement.place_short_legs(
        proc,
        user_id,
        [
            {
                "stock_code": result.index_code,
                "exchange_code": exchange,
                "right": leg["right"],
                "expiry_display": result.expiry_display,
                "strike_price": leg["strike_price"],
                "quantity": leg["quantity"],
                "premium_per_share": leg["bid"],
            }
            for leg in result.legs
        ],
        tolerance_pct=float(cfg.AGGRESSIVE_LIMIT_DEFAULT_TOLERANCE_PCT),
    )
    errors = []
    for leg_result in placed:
        result.order_ids.extend(leg_result.order_ids)
        if leg_result.error:
            errors.append(leg_result.error)
    if errors:
        result.reason_code = ReasonCode.ORDER_REJECTED
        result.error = "; ".join(errors)
        if not result.order_ids:
            return result

    result.rule_id = _arm_exit(
        proc,
        user_id,
        result,
        config=config,
        exchange=exchange,
        expiry_display=result.expiry_display,
    )
    return result


def _arm_exit(
    proc: Any,
    user_id: str,
    result: FireResult,
    *,
    config: ExpiryIndexWriterConfig,
    exchange: str,
    expiry_display: str,
) -> Optional[str]:
    """Arm the SG that will close this position.

    The loss limit genuinely is a rupee P&L (N x the premium collected) so it maps onto
    `loss_limit_pnl` on the group -- the right shape for a strangle, whose risk is net
    across both legs.

    Profit booking is a share of the premium, applied as a per-leg PRICE target of
    `entry x (1 - pct/100)` and never converted into rupees: the engine's P&L uses the
    broker's `average_price`, which need not equal the price the bot sold at. At 100% the
    target price is zero, which no limit order can reach, so no profit target is armed at
    all and only the stop-loss stands -- the honest reading of "let it expire worthless".
    """
    from icici_breeze_backend.app.repositories import squareoff_rules as sq_repo
    from icici_breeze_backend.app.services import portfolio_pnl_engine
    from icici_breeze_backend.app.services.strategy_group_arm_guard import (
        ArmPreconditionError,
        assert_can_arm,
    )

    premium_collected = float(result.premium_total or 0)
    loss_limit = config.loss_limit_premium_multiple * premium_collected
    if loss_limit <= 0:
        return None

    # One target price for the group, so the rule fires only when EVERY short leg is at or
    # below it. On a strangle the cheapest leg would otherwise book the whole group and
    # leave the other side naked, which is strictly worse than holding both.
    leg_targets = [
        config.profit_target_price_for(float(leg.get("bid") or 0)) for leg in result.legs
    ]
    target_option_price = (
        min(t for t in leg_targets if t is not None)
        if leg_targets and all(t is not None for t in leg_targets)
        else None
    )

    try:
        breeze = proc.get_session_breeze(user_id)
        assert_can_arm(breeze, user_id, result.index_code, expiry_display)
        rule = sq_repo.arm_rule(
            user_id,
            stock_code=result.index_code,
            expiry_display=expiry_display,
            exchange_code=exchange,
            # `profit_target_pnl` is NOT NULL and must stay positive. Where a price target
            # exists the two are alternatives, not a pair, so this is pushed out of reach so
            # it cannot front-run it. Where the user asked to let the position expire, it is
            # pushed out of reach for the same reason -- the stop-loss is the only live exit.
            profit_target_pnl=max(premium_collected * 100.0, 1.0),
            loss_limit_pnl=loss_limit,
            target_premium_pct=5,
            stop_loss_premium_pct=5,
            target_option_price=target_option_price,
        )
        portfolio_pnl_engine.set_group_rule(
            user_id,
            rule.id,
            stock_code=result.index_code,
            expiry_display=expiry_display,
            exchange_code=exchange,
            target_pnl=rule.profit_target_pnl,
            stop_loss_pnl=rule.loss_limit_pnl,
            target_premium_pct=rule.target_premium_pct,
            stop_loss_premium_pct=rule.stop_loss_premium_pct,
            target_option_price=rule.target_option_price,
        )
        return rule.id
    except ArmPreconditionError as e:
        # The position is open and unprotected. Say so loudly rather than reporting a
        # clean fire -- this is the single worst state this bot can leave behind.
        result.error = f"Position is OPEN but its stop could not be armed: {e}"
        result.reason_code = ReasonCode.ORDER_REJECTED
        return None
    except Exception as e:  # noqa: BLE001
        _logger.exception("bot2: could not arm exit for %s", result.index_code)
        result.error = f"Position is OPEN but its stop could not be armed: {e}"
        result.reason_code = ReasonCode.ORDER_REJECTED
        return None
