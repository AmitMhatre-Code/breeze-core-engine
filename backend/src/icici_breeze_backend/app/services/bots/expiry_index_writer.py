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
from icici_breeze_backend.app.domain.bots import ExpiryIndexWriterConfig, ReasonCode

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
# Execution
# --------------------------------------------------------------------------------------


@dataclass
class FireResult:
    index_code: str
    exchange_code: str
    expiry_display: str
    right: str
    strike_price: Optional[float] = None
    lots: int = 0
    quantity: int = 0
    entry_price: Optional[float] = None
    span_per_lot: Optional[float] = None
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
    """Size and place one index's short leg, then arm its exit."""
    from icici_breeze_backend.app.services.bots import placement
    from icici_breeze_backend.app.services.quote_source_router import (
        fetch_chain_side_icici_response,
    )

    leg_cfg = config.indices[index_code]
    exchange = INDEX_EXCHANGE.get(index_code, cfg.NFO)
    right = cfg.CALL if leg_cfg.right == "call" else cfg.PUT
    result = FireResult(
        index_code=index_code,
        exchange_code=exchange,
        expiry_display=expiry_display,
        right=leg_cfg.right,
    )

    budget = available_margin * leg_cfg.margin_pct_cap / 100.0
    result.budget = round(budget, 2)

    chain = fetch_chain_side_icici_response(
        proc, user_id, index_code, exchange, expiry_display, right
    )
    if (chain or {}).get("Status") != 200 or not chain.get("Success"):
        result.reason_code = ReasonCode.CHAIN_NOT_READY
        result.error = "No option chain available."
        return result
    rows = [r for r in chain["Success"] if isinstance(r, dict)]

    spot = 0.0
    for r in rows:
        try:
            spot = float(r.get("spot_price") or 0)
        except (TypeError, ValueError):
            spot = 0.0
        if spot > 0:
            break
    if spot <= 0:
        result.reason_code = ReasonCode.QUOTE_UNAVAILABLE
        result.error = "No spot price available."
        return result

    row = _pick_strike(rows, spot, right, leg_cfg.safety_pct)
    if row is None:
        result.reason_code = ReasonCode.QUOTE_UNAVAILABLE
        result.error = f"No strike at least {leg_cfg.safety_pct}% from spot."
        return result
    strike = float(parse_strike(row.get("strike_price")) or 0)
    result.strike_price = strike

    try:
        bid = float(row.get("best_bid_price") or 0)
    except (TypeError, ValueError):
        bid = 0.0
    if bid <= 0:
        # Unlike Bot 1, there is no indicative fallback here: this bot only ever runs
        # during market hours, so a missing bid means the book really is empty.
        result.reason_code = ReasonCode.QUOTE_UNAVAILABLE
        result.error = f"No bid on the {strike:g} strike."
        return result
    result.entry_price = bid

    lot_size = proc.fetch_lot_size(index_code, expiry_display, exchange_code=exchange)
    try:
        lot_size = int(lot_size or 0)
    except (TypeError, ValueError):
        lot_size = 0
    if lot_size <= 0:
        result.reason_code = ReasonCode.INTERNAL_ERROR
        result.error = "No lot size in the scrip master."
        return result

    # Estimate from the baseline first -- one cheap lookup rather than a margin call per
    # candidate lot count.
    margin, _warnings = proc._resolve_leg_margin_with_source(
        user_id=user_id, exchange_code=exchange, stock_code=index_code,
        expiry_display=expiry_display, strike_price=strike, right=right,
        quantity=lot_size, margin_source=margin_source,
        action=cfg.SELL, product=cfg.OPTIONS,
    )
    if (margin or {}).get("Status") != 200:
        result.reason_code = ReasonCode.MARGIN_LOOKUP_FAILED
        result.error = "Could not price the margin for one lot."
        return result
    span_per_lot = float(margin["Success"]["span_margin_required"])
    if span_per_lot <= 0:
        result.reason_code = ReasonCode.MARGIN_LOOKUP_FAILED
        result.error = "Margin for one lot came back as zero."
        return result
    result.span_per_lot = round(span_per_lot, 2)

    lots = int(math.floor(budget / span_per_lot))
    if lots < 1:
        result.reason_code = ReasonCode.MARGIN_CAP_TOO_SMALL
        result.error = (
            f"One lot needs about Rs {span_per_lot:,.0f}, above the "
            f"Rs {budget:,.0f} this index is allowed."
        )
        return result

    # Verify the estimate against a real margin call before committing capital. The
    # baseline is a per-contract lookup; the real number can differ, and over-committing an
    # unattended trade is exactly what the cap exists to prevent.
    verified, _ = proc._resolve_leg_margin_with_source(
        user_id=user_id, exchange_code=exchange, stock_code=index_code,
        expiry_display=expiry_display, strike_price=strike, right=right,
        quantity=lots * lot_size, margin_source=margin_source,
        action=cfg.SELL, product=cfg.OPTIONS,
    )
    if (verified or {}).get("Status") == 200:
        verified_total = float(verified["Success"]["span_margin_required"])
        while lots > 1 and verified_total > budget:
            lots -= 1
            verified_total = span_per_lot * lots
        if verified_total > budget:
            result.reason_code = ReasonCode.MARGIN_CAP_TOO_SMALL
            result.error = (
                f"Verified margin Rs {verified_total:,.0f} for one lot exceeds the "
                f"Rs {budget:,.0f} cap."
            )
            return result

    result.lots = lots
    result.quantity = lots * lot_size

    placed = placement.place_short_legs(
        proc,
        user_id,
        [{
            "stock_code": index_code,
            "exchange_code": exchange,
            "right": leg_cfg.right,
            "expiry_display": expiry_display,
            "strike_price": strike,
            "quantity": result.quantity,
            "premium_per_share": bid,
        }],
        tolerance_pct=float(cfg.AGGRESSIVE_LIMIT_DEFAULT_TOLERANCE_PCT),
    )
    leg_result = placed[0]
    result.order_ids = list(leg_result.order_ids)
    if leg_result.error:
        result.reason_code = ReasonCode.ORDER_REJECTED
        result.error = leg_result.error
        if not result.order_ids:
            return result

    result.rule_id = _arm_exit(
        proc, user_id, result, config=config, exchange=exchange, expiry_display=expiry_display
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
    `loss_limit_pnl`. The profit target is an absolute option price and is passed through
    as `target_option_price` — never converted — because the engine's P&L uses the broker's
    average_price, which need not equal the price we just sold at.
    """
    from icici_breeze_backend.app.repositories import squareoff_rules as sq_repo
    from icici_breeze_backend.app.services import portfolio_pnl_engine
    from icici_breeze_backend.app.services.strategy_group_arm_guard import (
        ArmPreconditionError,
        assert_can_arm,
    )

    premium_collected = float(result.entry_price or 0) * result.quantity
    loss_limit = config.loss_limit_premium_multiple * premium_collected
    if loss_limit <= 0:
        return None
    try:
        breeze = proc.get_session_breeze(user_id)
        assert_can_arm(breeze, user_id, result.index_code, expiry_display)
        rule = sq_repo.arm_rule(
            user_id,
            stock_code=result.index_code,
            expiry_display=expiry_display,
            exchange_code=exchange,
            # `profit_target_pnl` is NOT NULL and must stay positive, but the real profit
            # exit is the price target below. Set it beyond anything reachable so it never
            # front-runs the price target; the two are alternatives, not a pair.
            profit_target_pnl=max(premium_collected * 100.0, 1.0),
            loss_limit_pnl=loss_limit,
            target_premium_pct=5,
            stop_loss_premium_pct=5,
            target_option_price=config.profit_target_option_price,
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
