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
    PremiumGateConfig,
    ReasonCode,
)
from icici_breeze_backend.app.services.premium_gate import reading as premium

_logger = logging.getLogger(__name__)

# How many smaller sizes are re-checked with ICICI when the first verified margin is over
# the cap (B-26). Each is one margin call.
_MARGIN_RECHECKS = 4

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
    # "sell" for a short; "buy" for a hedged shape's wing, priced at `ask`.
    action: str = "sell"
    ask: float = 0.0

    @property
    def price(self) -> float:
        """What this leg trades at: the bid we sell at, or the ask we buy a wing at."""
        return self.bid if self.action == "sell" else self.ask


@dataclass
class Candidate:
    """One shortlisted strategy, priced for a single lot so shapes compare like for like.
    `premium_per_lot` is net: sold bids less bought asks."""

    strategy: str
    legs: list[CandidateLeg]
    premium_per_lot: float
    margin_per_lot: float

    @property
    def hedged(self) -> bool:
        return self.strategy in HEDGED

    def lots_for(self, budget: float) -> int:
        return int(math.floor(budget / self.margin_per_lot)) if self.margin_per_lot > 0 else 0

    def premium_for(self, budget: float) -> float:
        """Premium receivable at the size `budget` buys: the user's ranking (2026-10-10)."""
        return self.premium_per_lot * self.lots_for(budget)

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
    "bear_call_spread": "Bear call spread",
    "bull_put_spread": "Bull put spread",
    "iron_condor": "Iron condor",
}

# Which sides each shortlisted shape sells. A hedged shape also buys a wing beyond each.
STRATEGY_RIGHTS: dict[str, tuple[str, ...]] = {
    "naked_ce": (cfg.CALL,),
    "naked_pe": (cfg.PUT,),
    "short_strangle": (cfg.CALL, cfg.PUT),
    "bear_call_spread": (cfg.CALL,),
    "bull_put_spread": (cfg.PUT,),
    "iron_condor": (cfg.CALL, cfg.PUT),
}
NAKED = ("naked_ce", "naked_pe", "short_strangle")
HEDGED = ("bear_call_spread", "bull_put_spread", "iron_condor")
#: The two alternatives a plan offers (docs/bot2-hedged-shapes-plan.md): the best of each group.
CHOICES = ("naked", "hedged")
#: How long a wing's buy may take to fill before nothing is sold.
WING_FILL_TIMEOUT_SECONDS = 20.0


def _side(right: str) -> str:
    return "CE" if right == cfg.CALL else "PE"


def _chain_side(
    proc: Any, user_id: str, index_code: str, exchange: str, expiry: str, right: str
) -> tuple[list[dict], Optional[str]]:
    """One chain side's rows, or why there are none -- the source's own miss text included,
    so a bhavcopy miss and a REST-fallback miss don't read the same in the run log."""
    from icici_breeze_backend.app.services.quote_source_router import (
        fetch_chain_side_icici_response,
        rows_with_source,
    )

    chain = fetch_chain_side_icici_response(proc, user_id, index_code, exchange, expiry, right)
    if not isinstance(chain, dict):
        return [], "chain fetch returned nothing"
    if chain.get("Status") != 200:
        return [], f"chain fetch status {chain.get('Status')}: {chain.get('Error') or 'no error text'}"
    rows = rows_with_source(chain)
    if not rows:
        return [], "chain fetch returned no rows"
    return rows, None


def _chain_rows(proc: Any, user_id: str, index_code: str, exchange: str, expiry: str, right: str):
    return _chain_side(proc, user_id, index_code, exchange, expiry, right)[0]


def _spot_from(rows: list[dict]) -> float:
    """The chain's spot only when a live tick set it; 0.0 for a stand-in such as the
    previous close, which would put every strike the wrong distance from the market."""
    from icici_breeze_backend.app.services.quote_source_router import row_spot

    reading = row_spot(rows)
    return reading.spot if reading is not None and reading.live else 0.0


def _no_live_spot_reason(rows_by_right: dict[str, list[dict]]) -> str:
    from icici_breeze_backend.app.services.quote_source_router import row_spot

    for rows in rows_by_right.values():
        reading = row_spot(rows)
        if reading is not None:
            return f"No live spot price; the chain only has {reading.describe()}."
    counts = ", ".join(f"{len(rows)} {_side(r)}" for r, rows in rows_by_right.items())
    return f"No spot price available (no spot_price on any of {counts} chain rows)."


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
    legs: list[tuple],
) -> Optional[float]:
    """SPAN for a set of short legs priced together in ONE margin_calculator call.

    Sending both sides of a strangle in a single call is what makes the comparison honest:
    the exchange nets the two, and pricing each side alone and adding them would overstate a
    strangle's cost by the whole netting benefit -- biasing the yield ranking against the
    very shape the netting exists to reward.

    `legs` is (right, strike, quantity[, "buy"]). Returns None on any failure; callers treat that as
    "cannot price", never as zero.
    """
    return price_margin_for_legs(
        proc,
        user_id,
        exchange_code=exchange_code,
        stock_code=stock_code,
        expiry_display=expiry_display,
        legs=legs,
    )[0]


def price_margin_for_legs(
    proc: Any,
    user_id: str,
    *,
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    legs: list[tuple[str, float, int]],
) -> tuple[Optional[float], Optional[str]]:
    """`margin_for_legs`, plus *why* it failed: (span, None) or (None, reason).

    Each failure mode gets its own wording so a skipped run says which one it was -- a
    raised call, a broker refusal, and a zero SPAN need different fixes.
    """
    from icici_breeze_backend.app.core.strike import strike_for_broker
    from icici_breeze_backend.app.services.processor import _expiry_display_to_api

    if not legs:
        return None, "no legs to price"
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
            for right, strike, quantity, *_ in legs
        ]
        # A hedged shape's wings are bought: priced together with the shorts, ICICI nets them.
        for row, leg in zip(payload, legs):
            if len(leg) > 3 and str(leg[3]).lower() == "buy":
                row["action"] = cfg.BUY
        out = breeze.margin_calculator(payload, exchange_code=exchange_code)
    except Exception as exc:  # noqa: BLE001 -- an unpriceable shape drops out of the shortlist
        _logger.warning("bot2: margin_calculator failed for %s", stock_code, exc_info=True)
        return None, f"margin_calculator raised {type(exc).__name__}: {exc}"
    shape = "+".join(f"{'CE' if l[0] == cfg.CALL else 'PE'}{l[1]:g}x{l[2]}" for l in legs)
    if not isinstance(out, dict):
        reason = f"margin_calculator returned {type(out).__name__}, not a response"
    elif out.get("Status") != 200:
        reason = (
            f"margin_calculator status {out.get('Status')}: {out.get('Error') or 'no error text'}"
        )
        # An expired/invalid session must not be reused by the next attempt.
        evict = getattr(proc, "_maybe_evict_session", None)
        if callable(evict):
            evict(user_id, out)
    else:
        raw = (out.get("Success") or {}).get("span_margin_required")
        try:
            value = float(raw or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value, None
        reason = f"margin_calculator span_margin_required was {raw!r}"
    # Every refusal is logged, not only a raised call: a broker-side refusal used to return
    # None silently, which hid a stale session behind "could not be priced" for a morning.
    _logger.warning("bot2: %s %s %s: %s", stock_code, exchange_code, shape, reason)
    return None, reason


def build_candidates(
    proc: Any,
    user_id: str,
    index_code: str,
    *,
    exchange: str,
    expiry_display: str,
    leg_cfg: IndexWriterLeg,
    lot_size: int,
    gate: Optional[PremiumGateConfig] = None,
    enforce_gate: bool = True,
    now: Optional[datetime.datetime] = None,
) -> tuple[list[Candidate], Optional[str], float, Optional[str], Optional[premium.Reading]]:
    """Price every shortlisted strategy for one lot.

    Returns (candidates, error, spot, reason_code, premium reading). When nothing prices, `error`
    names every leg and strategy that dropped out and why -- "could not be priced" on its own
    covered a missing strike, an empty book and a refused margin call alike, and each needs a
    different fix.

    The premium reading is made when the gate is on or strikes are set by implied move; with
    `enforce_gate` False (the manual run sheet) it is reported but never refuses.
    """
    from icici_breeze_backend.app.core.timezone import now_ist
    from icici_breeze_backend.app.services.premium_gate import live as premium_live
    from icici_breeze_backend.app.services.quote_source_router import row_is_live

    # CE before PE, so the same failure always reads the same way in the run log.
    rights_needed = [
        r for r in (cfg.CALL, cfg.PUT)
        if any(r in STRATEGY_RIGHTS.get(s, ()) for s in leg_cfg.strategies)
    ]
    rows_by_right: dict[str, list[dict]] = {}
    spot = 0.0
    for right in rights_needed:
        rows, chain_error = _chain_side(proc, user_id, index_code, exchange, expiry_display, right)
        if not rows:
            return (
                [],
                f"No option chain available ({_side(right)} {expiry_display}: {chain_error}).",
                0.0,
                ReasonCode.CHAIN_NOT_READY,
                None,
            )
        rows_by_right[right] = rows
        spot = spot or _spot_from(rows)
    if spot <= 0:
        return [], _no_live_spot_reason(rows_by_right), 0.0, ReasonCode.QUOTE_UNAVAILABLE, None

    # The premium reading needs the ATM call and put, whichever sides the shortlist sells.
    gate_on = gate is not None and gate.enabled
    by_implied = leg_cfg.distance_basis == "implied_move"
    # A hedged shape's wing is set by the implied move too, whatever the strikes are set by.
    wants_wings = any(s in HEDGED for s in leg_cfg.strategies)
    reading: Optional[premium.Reading] = None
    implied: Optional[float] = None
    if gate_on or by_implied or wants_wings:
        sides = {r: rows_by_right.get(r) or _chain_rows(proc, user_id, index_code, exchange, expiry_display, r)
                 for r in (cfg.CALL, cfg.PUT)}
        expiry = datetime.datetime.strptime(expiry_display, "%d-%b-%Y").date()
        reading, implied = premium_live.live_reading(
            index_code, expiry, spot, sides[cfg.CALL] or [], sides[cfg.PUT] or [], now or now_ist(),
            user_id=user_id,
        )
        refusal = gate_refusal(reading, gate) if (gate_on and enforce_gate) else None
        if refusal is not None:
            return [], refusal[1], spot, refusal[0], reading
        if by_implied and implied is None:
            # Fails closed: a strike placed by a % it was never set to is not the user's trade.
            return ([], f"Strikes are set by implied move, but there is {reading.describe()}.",
                    spot, ReasonCode.PREMIUM_UNREADABLE, reading)

    # Pick each side once and reuse it: a strangle's call leg is the same contract the
    # naked-CE candidate would sell, so pricing it twice would only invite them to drift.
    picked: dict[str, CandidateLeg] = {}
    leg_failures: list[str] = []
    for right in rights_needed:
        safety = leg_cfg.safety_pct_ce if right == cfg.CALL else leg_cfg.safety_pct_pe
        rows = rows_by_right[right]
        target = strike_target(leg_cfg, spot, right, implied)
        row = _pick_strike_at(rows, target, right)
        if row is None:
            k = leg_cfg.implied_multiple_ce if right == cfg.CALL else leg_cfg.implied_multiple_pe
            leg_failures.append(_no_strike_reason(
                rows, spot, right, safety, target=target,
                distance=f"{k:g}x the implied move" if by_implied else None))
            continue
        strike = float(parse_strike(row.get("strike_price")) or 0)
        if strike <= 0:
            leg_failures.append(
                f"{_side(right)}: picked row has unreadable strike {row.get('strike_price')!r}"
            )
            continue
        bid = _bid(row)
        # Unlike Bot 1 there is no indicative fallback: this bot only runs during market
        # hours, so a missing bid means the book really is empty.
        if bid <= 0:
            leg_failures.append(
                f"{_side(right)} {strike:g}: no bid (best_bid_price "
                f"{row.get('best_bid_price')!r}, ltp {row.get('ltp')!r})"
            )
            continue
        if not row_is_live(row):
            # The feed for this strike has stopped and the router filled in a stand-in: the
            # bot pauses (a transient skip, retried) rather than sell into a stale price.
            leg_failures.append(
                f"{_side(right)} {strike:g}: no live quote (priced from "
                f"{row.get('quote_source') or 'an unknown source'})"
            )
            continue
        picked[right] = CandidateLeg(right=right, strike_price=strike, bid=bid)

    wings: dict[str, CandidateLeg] = {}
    if wants_wings:
        if implied is None:
            leg_failures.append(f"hedged shapes: no wing without the implied move ({reading.describe() if reading else 'no reading'})")
        else:
            for right, short in picked.items():
                wing, why = _pick_wing(rows_by_right[right], short, right, implied, leg_cfg.wing_multiple)
                if wing is None:
                    leg_failures.append(f"{_side(right)} wing beyond {short.strike_price:g}: {why}")
                else:
                    wings[right] = wing

    candidates: list[Candidate] = []
    margin_failures: list[str] = []
    for strategy in leg_cfg.strategies:
        rights = STRATEGY_RIGHTS.get(strategy, ())
        legs = [picked[r] for r in rights if r in picked]
        if len(legs) != len(rights):
            # The missing leg is already explained once in `leg_failures`.
            continue
        if strategy in HEDGED:
            if any(r not in wings for r in rights):
                continue  # explained once in `leg_failures`
            # Wings first: the order they are placed in, and no naked short at any step.
            legs = [wings[r] for r in rights] + legs
        margin, margin_error = price_margin_for_legs(
            proc,
            user_id,
            exchange_code=exchange,
            stock_code=index_code,
            expiry_display=expiry_display,
            legs=[(leg.right, leg.strike_price, lot_size, leg.action) for leg in legs],
        )
        if margin is None:
            strikes = " + ".join(f"{_side(l.right)} {l.strike_price:g}" for l in legs)
            margin_failures.append(
                f"{STRATEGY_LABEL.get(strategy, strategy)} ({strikes}): {margin_error}"
            )
            continue
        candidates.append(
            Candidate(
                strategy=strategy,
                legs=legs,
                premium_per_lot=round(sum(leg.price if leg.action == "sell" else -leg.price
                                              for leg in legs) * lot_size, 2),
                margin_per_lot=round(margin, 2),
            )
        )
    if not candidates:
        detail = "; ".join(leg_failures + margin_failures) or "no strategies shortlisted"
        # Only a pure margin-call failure is a margin lookup problem; any quote gap means
        # the book itself was not tradeable.
        code = (
            ReasonCode.MARGIN_LOOKUP_FAILED
            if margin_failures and not leg_failures
            else ReasonCode.QUOTE_UNAVAILABLE
        )
        return [], f"None of the shortlisted strategies could be priced ({detail}).", spot, code, reading
    return candidates, None, spot, None, reading


def _no_strike_reason(rows: list[dict], spot: float, right: str, safety_pct: float,
                      *, target: Optional[float] = None, distance: Optional[str] = None) -> str:
    """Why `_pick_strike` found nothing: the target and how far the listed strikes reach."""
    if target is None:
        target = spot * (1 + safety_pct / 100) if right == cfg.CALL else spot * (1 - safety_pct / 100)
    strikes = sorted(
        float(s) for s in (parse_strike(r.get("strike_price")) for r in rows) if s is not None
    )
    direction = "at or above" if right == cfg.CALL else "at or below"
    reach = f"listed {strikes[0]:g}-{strikes[-1]:g}" if strikes else "no readable strikes"
    return (
        f"{_side(right)}: no strike {direction} {target:,.2f} "
        f"(spot {spot:,.2f}, {distance or f'safety {safety_pct:g}%'}; {reach})"
    )


def strike_target(leg_cfg: IndexWriterLeg, spot: float, right: str, implied: Optional[float]) -> float:
    """The index level a written strike must sit at or beyond, before rounding away from spot:
    a share of spot, or a multiple of the implied move to expiry (docs/premium-gate-plan.md 5)."""
    up = right == cfg.CALL
    if leg_cfg.distance_basis == "implied_move" and implied is not None:
        k = leg_cfg.implied_multiple_ce if up else leg_cfg.implied_multiple_pe
        return premium.strike_target(spot, implied, k, up=up)
    pct = leg_cfg.safety_pct_ce if up else leg_cfg.safety_pct_pe
    return spot * (1 + pct / 100) if up else spot * (1 - pct / 100)


def gate_refusal(reading: premium.Reading, gate: PremiumGateConfig) -> Optional[tuple[str, str]]:
    """(reason code, text) when the premium gate says no, else None. No reading is a no."""
    from icici_breeze_backend.app.services.premium_gate.gate import refusal

    return refusal(reading, gate, "sell")


def _pick_wing(
    rows: list[dict], short: CandidateLeg, right: str, implied: float, multiple: float
) -> tuple[Optional[CandidateLeg], Optional[str]]:
    """The wing for one short: `multiple` implied moves beyond it, rounded further out, and at
    least one listed strike beyond it. Bought at a live ask."""
    from icici_breeze_backend.app.services.quote_source_router import row_is_live

    up = right == cfg.CALL
    target = premium.strike_target(short.strike_price, implied, multiple, up=up)
    beyond = [r for r in rows
              if (s := parse_strike(r.get("strike_price"))) is not None
              and (float(s) > short.strike_price if up else float(s) < short.strike_price)]
    row = _pick_strike_at(beyond, target, right)
    if row is None and beyond:
        # The target lies past the listed strikes: the furthest listed one, as the condor does.
        row = max(beyond, key=lambda r: float(parse_strike(r.get("strike_price")) or 0) * (1 if up else -1))
    if row is None:
        return None, "no listed strike beyond it"
    strike = float(parse_strike(row.get("strike_price")) or 0)
    try:
        ask = float(row.get("best_offer_price") or 0)
    except (TypeError, ValueError):
        ask = 0.0
    if ask <= 0:
        return None, f"{strike:g} has no ask"
    if not row_is_live(row):
        return None, f"{strike:g} has no live quote"
    return CandidateLeg(right=right, strike_price=strike, bid=_bid(row), action="buy", ask=ask), None


def choose(candidates: list[Candidate], budget: Optional[float] = None) -> Optional[Candidate]:
    """The most premium receivable at the size `budget` buys (the user's ranking, 2026-10-10):
    premium per lot x the lots that fit. A strangle collects both premiums but ties up more
    margin, so it wins only when the extra premium pays for it -- the comparison the earlier
    premium-per-margin ranking made, now in rupees received. Ties break towards the cheaper
    shape. Without a budget, premium per rupee of margin."""
    if not candidates:
        return None
    if budget is None:
        return max(candidates, key=lambda c: (c.margin_yield, -c.margin_per_lot))
    return max(candidates, key=lambda c: (c.premium_for(budget), c.margin_yield, -c.margin_per_lot))


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
    spot: Optional[float] = None
    budget: Optional[float] = None
    order_ids: list[str] = field(default_factory=list)
    rule_id: Optional[str] = None
    reason_code: Optional[str] = None
    error: Optional[str] = None
    # The run this fire belongs to, so a stop armed later can rewrite that run's verdict.
    run_id: Optional[str] = None
    # Orders are out, the stop is waiting for them to finish (`bots/exit_arming`). Not an
    # error: it arms on the last fill without anyone doing anything.
    arm_pending: bool = False
    pending_exit_id: Optional[str] = None
    # Why the stop failed to arm, kept apart from `error` (which also carries placement
    # rejections) so a report can say "placed" and "stop failed" as two separate facts.
    arm_error: Optional[str] = None
    # Set when the order book made the bot trade fewer lots than it planned.
    liquidity_note: Optional[str] = None
    # The premium gate's reading for this index, when one was made (docs/premium-gate-plan.md).
    premium: Optional[dict] = None
    premium_text: Optional[str] = None
    # The gate is on and this reading would stop the bot selling on its own schedule.
    premium_would_refuse: bool = False
    # "naked" or "hedged": which alternative this plan is (docs/bot2-hedged-shapes-plan.md).
    choice: Optional[str] = None
    # Both alternatives as sized, keyed by choice: what a proposal offers side by side.
    alternatives: dict = field(default_factory=dict, repr=False)

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
    return _pick_strike_at(rows, target, right)


def _pick_strike_at(rows: list[dict], target: float, right: str) -> Optional[dict]:
    """The listed strike at or beyond `target`, away from the money, nearest to it."""
    if not target > 0:
        return None
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
    enforce_gate: bool = True,
    choice: str = "best",
) -> FireResult:
    """Decide *what* to trade and *how big*, without placing anything.

    `choice` picks the alternative (docs/bot2-hedged-shapes-plan.md): "naked" or "hedged" sizes
    the best of that group; "best" sizes both and returns whichever has the more premium
    receivable. The result carries every alternative it sized in `alternatives`.

    Split out from `fire_index` so the manual run and the unattended run size identically --
    a manual review that showed different numbers from what the bot would have done on its
    own would be worse than no review at all.

    `enforce_gate` False is the manual run sheet: the premium reading is still made and
    reported (`result.premium`), but a "not rich" reading does not stop a trade the user is
    choosing by hand.
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
        _logger.warning("bot2: %s %s not planned: no lot size in the scrip master", index_code, expiry_display)
        return result

    candidates, error, spot, error_code, reading = build_candidates(
        proc,
        user_id,
        index_code,
        exchange=exchange,
        expiry_display=expiry_display,
        leg_cfg=leg_cfg,
        lot_size=lot_size,
        gate=config.premium_gate,
        enforce_gate=enforce_gate,
    )
    result.spot = round(spot, 2) if spot > 0 else None
    if reading is not None:
        result.premium = reading.to_dict()
        result.premium_text = reading.describe()
        result.premium_would_refuse = gate_refusal(reading, config.premium_gate) is not None
    if error:
        result.reason_code = error_code
        result.error = error
        _logger.warning("bot2: %s %s not planned (%s): %s", index_code, expiry_display, error_code, error)
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

    # The best of each group (docs/bot2-hedged-shapes-plan.md): a proposal offers both, Auto
    # places whichever pays more, and a choice made on Telegram or by hand re-plans just that one.
    wanted = CHOICES if choice == "best" else (choice,)
    sized: dict[str, FireResult] = {}
    for group in wanted:
        pool = [c for c in candidates if c.hedged == (group == "hedged")]
        best = choose(pool, budget)
        if best is not None:
            sized[group] = _size(proc, user_id, result, best, budget=budget, lot_size=lot_size,
                                 exchange=exchange, index_code=index_code, expiry_display=expiry_display)
            sized[group].choice = group
    if not sized:
        result.reason_code = ReasonCode.QUOTE_UNAVAILABLE
        result.error = ("No strategy could be priced." if choice == "best"
                        else f"No {choice} strategy is shortlisted, or none could be priced.")
        return result
    placeable = [r for r in sized.values() if not r.error]
    if choice != "best":
        pick = sized.get(choice) or result
    elif placeable:
        pick = max(placeable, key=lambda r: float(r.premium_total or 0))
    else:
        pick = next(iter(sized.values()))
    pick.alternatives = sized
    return pick


def _size(
    proc: Any,
    user_id: str,
    base: FireResult,
    best: Candidate,
    *,
    budget: float,
    lot_size: int,
    exchange: str,
    index_code: str,
    expiry_display: str,
) -> FireResult:
    """Size one candidate to the margin cap, confirmed with ICICI."""
    import dataclasses

    result = dataclasses.replace(base, legs=[], alternatives={})
    result.strategy = best.strategy
    result.span_per_lot = best.margin_per_lot
    result.margin_yield = round(best.margin_yield, 6)
    shorts = [leg for leg in best.legs if leg.action == "sell"]
    # `right` and `strike_price` describe the single-short case and stay populated for it;
    # `legs` is the full picture and is what placement and the exit arming read.
    result.right = "call" if shorts[0].right == cfg.CALL else "put"
    result.strike_price = shorts[0].strike_price if len(shorts) == 1 else None

    lots = best.lots_for(budget)
    if lots < 1:
        result.reason_code = ReasonCode.MARGIN_CAP_TOO_SMALL
        result.error = (
            f"One lot of {best.label} needs about Rs {best.margin_per_lot:,.0f}, above the "
            f"Rs {budget:,.0f} this index is allowed."
        )
        return result

    def margin_at(n: int) -> Optional[float]:
        return margin_for_legs(
            proc, user_id, exchange_code=exchange, stock_code=index_code, expiry_display=expiry_display,
            legs=[(leg.right, leg.strike_price, n * lot_size, leg.action) for leg in best.legs],
        )

    # Verify against a real margin call at the full size before committing capital. The
    # per-lot figure does not always scale linearly, and over-committing an unattended trade
    # is exactly what the cap exists to prevent.
    verified = margin_at(lots)
    if verified is not None:
        # Each smaller size is asked of ICICI too (B-26). Its margin is not linear in
        # quantity (the basket 28L -> 73L incident), so scaling the per-lot figure down could
        # still leave the real figure over the cap -- and record the estimate as verified.
        # Each step jumps to the size the last answer says would fit, so a few calls settle it.
        checks = 0
        while verified is not None and verified > budget and lots > 1 and checks < _MARGIN_RECHECKS:
            lots = max(1, min(lots - 1, int(lots * budget / verified)))
            verified = margin_at(lots)
            checks += 1
        if verified is None:
            result.reason_code = ReasonCode.MARGIN_CAP_TOO_SMALL
            result.error = (
                f"ICICI could not confirm the margin for {lots} lot(s) of {best.label}, so "
                "nothing was placed."
            )
            return result
        if verified > budget:
            result.reason_code = ReasonCode.MARGIN_CAP_TOO_SMALL
            result.error = (
                f"Verified margin Rs {verified:,.0f} for {lots} lot(s) of {best.label} "
                f"exceeds the Rs {budget:,.0f} cap."
            )
            return result
        result.margin_total = round(verified, 2)
    else:
        result.margin_total = round(best.margin_per_lot * lots, 2)

    result.lots = lots
    result.quantity = lots * lot_size
    result.premium_total = round(best.premium_per_lot * lots, 2)
    result.entry_price = shorts[0].bid if len(best.legs) == 1 else None
    result.legs = [
        {
            "right": "call" if leg.right == cfg.CALL else "put",
            "strike_price": leg.strike_price,
            # The price the leg trades at: the bid for a short, the ask for a bought wing.
            "bid": leg.price,
            "action": leg.action,
            "quantity": result.quantity,
            "premium_total": round(leg.price * result.quantity, 2),
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
    run_id: Optional[str] = None,
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
    return execute_plan(proc, user_id, result, config=config, run_id=run_id)


def execute_plan(
    proc: Any,
    user_id: str,
    result: FireResult,
    *,
    config: ExpiryIndexWriterConfig,
    run_id: Optional[str] = None,
) -> FireResult:
    """Place a plan's legs and arm the exit. Shared by the scheduler and the manual run.

    Each leg dict comes back annotated with its own `order_ids`, `error` and the
    `limit_price` actually sent, so a report can say exactly which leg went out at what --
    rather than smearing one index-level error across every leg.
    """
    from icici_breeze_backend.app.services import portfolio_pnl_engine
    from icici_breeze_backend.app.services.bots import exit_arming, placement

    if run_id:
        result.run_id = run_id
    exchange = result.exchange_code
    if not _fit_to_book(user_id, result):
        return result
    # Captured before this bot's own legs go on, and used after placement: an SG rule pools
    # P&L across every leg sharing (stock_code, expiry_display), so arming one after adding
    # to a group that already had something else in it would silently fold an unrelated
    # position into this bot's stop. Same limitation as the manual arm route's own check
    # (`route_squareoff_rules.arm_rule`): an un-warmed registry reads as "nothing open" and
    # this fails open, not closed.
    try:
        other_legs_before_entry = portfolio_pnl_engine.group_legs_for_user(
            user_id, result.index_code, result.expiry_display
        )
    except Exception:  # noqa: BLE001
        other_legs_before_entry = []
    # Listening has to start before the first order goes out: a fast fill's events arrive
    # while the remaining freeze slices are still being placed.
    exit_arming.prepare(proc, user_id)
    # A hedged shape's wings go first and must fill completely before anything is sold, so no
    # short is ever naked, even for a moment (docs/bot2-hedged-shapes-plan.md).
    if not _buy_wings(proc, user_id, result):
        return result
    shorts = [leg for leg in result.legs if leg.get("action", "sell") == "sell"]
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
            for leg in shorts
        ],
        tolerance_pct=float(cfg.AGGRESSIVE_LIMIT_DEFAULT_TOLERANCE_PCT),
    )
    errors = []
    # `place_short_legs` returns one result per input leg, in order.
    for leg, leg_result in zip(shorts, placed):
        leg["order_ids"] = list(leg_result.order_ids)
        leg["error"] = leg_result.error
        leg["limit_price"] = leg_result.limit_price
        result.order_ids.extend(leg_result.order_ids)
        if leg_result.error:
            errors.append(leg_result.error)
    if errors:
        result.reason_code = ReasonCode.ORDER_REJECTED
        result.error = "; ".join(errors)
        if not result.order_ids:
            return result

    if other_legs_before_entry:
        _skip_arm_existing_position(result, other_legs=len(other_legs_before_entry))
    else:
        result.rule_id = _arm_exit(
            proc,
            user_id,
            result,
            config=config,
            exchange=exchange,
            expiry_display=result.expiry_display,
        )
    return result


def _buy_wings(proc: Any, user_id: str, result: FireResult) -> bool:
    """Buy a hedged plan's wings, each confirmed filled. False (with the reason on the result)
    when one did not fill completely: then nothing is sold, and any wing already bought is a
    harmless long the report names."""
    from icici_breeze_backend.app.services.aggressive_limit import round_to_tick
    from icici_breeze_backend.app.services.bots.scalping import live as scalp

    tolerance = float(cfg.AGGRESSIVE_LIMIT_DEFAULT_TOLERANCE_PCT) / 100.0
    for leg in result.legs:
        if leg.get("action") != "buy":
            continue
        ask = float(leg["bid"])  # a wing's `bid` field holds the ask it is bought at
        order = scalp.LegOrder(result.index_code, result.exchange_code, leg["right"],
                               float(leg["strike_price"]), result.expiry_display, cfg.BUY, int(leg["quantity"]))
        fill = scalp.place_and_confirm(
            proc, user_id, order,
            price_for_attempt=lambda n, ask=ask: round_to_tick(ask * (1 + tolerance * (n + 1))),
            timeout_seconds=WING_FILL_TIMEOUT_SECONDS,
        )
        leg["order_ids"] = [fill.order_id] if fill.order_id else []
        leg["limit_price"] = fill.average_price or ask
        result.order_ids.extend(leg["order_ids"])
        if fill.unaccounted or int(fill.filled_quantity or 0) < int(leg["quantity"]):
            leg["error"] = fill.error or f"filled {fill.filled_quantity} of {leg['quantity']}"
            result.reason_code = ReasonCode.ORDER_REJECTED
            result.error = (
                f"The {_side(cfg.CALL if leg['right'] == 'call' else cfg.PUT)} {leg['strike_price']:g} wing "
                f"did not fill completely ({leg['error']}), so nothing was sold. Any wing bought is a "
                "long option with no short against it: close it or keep it."
            )
            return False
        leg["error"] = None
    return True


def _fit_to_book(user_id: str, result: FireResult) -> bool:
    """Shrink the plan to what every leg's live book absorbs, or refuse it
    (docs/liquidity-checks-plan.md, decision 8). False when nothing may be placed.

    The legs keep one size, so a strangle shrinks as a strangle. Margin and premium scale with
    the lots: ICICI's margin is not linear in size (B-55), but a smaller position needs no more
    than its share, so the planned figure stays a ceiling.
    """
    from icici_breeze_backend.app.db.bots_migrate import BOT_EXPIRY_INDEX_WRITER
    from icici_breeze_backend.app.services.liquidity import check as liquidity

    if result.lots <= 0 or not result.legs:
        return True
    lot_size = int(result.quantity // result.lots)
    fit = liquidity.fit_lots(
        [
            liquidity.SizedLeg(
                result.exchange_code, result.index_code, result.expiry_display,
                float(leg["strike_price"]), leg["right"],
                liquidity.BUY if leg.get("action") == "buy" else liquidity.SELL,
            )
            for leg in result.legs
        ],
        result.lots,
        lot_size,
    )
    label = f"{INDEX_LABEL.get(result.index_code, result.index_code)} {result.expiry_display}"
    note = liquidity.note_bot_fit(user_id, BOT_EXPIRY_INDEX_WRITER, label, fit)
    if fit.refused:
        result.reason_code = ReasonCode.LIQUIDITY_THIN
        result.error = note
        return False
    if fit.shrunk:
        scale = fit.lots / result.lots
        result.lots = fit.lots
        result.quantity = fit.lots * lot_size
        if result.premium_total is not None:
            result.premium_total = round(result.premium_total * scale, 2)
        if result.margin_total is not None:
            result.margin_total = round(result.margin_total * scale, 2)
        for leg in result.legs:
            leg["quantity"] = result.quantity
            leg["premium_total"] = round(float(leg.get("bid") or 0) * result.quantity, 2)
        result.liquidity_note = note
    return True


def exit_terms(result: FireResult, config: ExpiryIndexWriterConfig) -> Optional[dict]:
    """The stop's numbers, fixed at placement. None when there is no stop to arm.

    The loss limit genuinely is a rupee P&L (N x the premium collected) so it maps onto
    `loss_limit_pnl` on the group -- the right shape for a strangle, whose risk is net
    across both legs.

    Profit booking is a share of the premium, applied as a per-leg PRICE target of
    `entry x (1 - pct/100)` and never converted into rupees: the engine's P&L uses the
    broker's `average_price`, which need not equal the price the bot sold at. At 100% the
    target price is zero, which no limit order can reach, so no profit target is armed at
    all and only the stop-loss stands -- the honest reading of "let it expire worthless".

    Frozen here, not recomputed when the stop finally arms, because a stop that arms minutes
    later must still protect the trade that was placed, under the settings it was placed
    with -- not whatever the config says by then.
    """
    premium_collected = float(result.premium_total or 0)
    loss_limit = config.loss_limit_premium_multiple * premium_collected
    if loss_limit <= 0:
        return None

    # One target price for the group, so the rule fires only when EVERY short leg is at or
    # below it. On a strangle the cheapest leg would otherwise book the whole group and
    # leave the other side naked, which is strictly worse than holding both.
    # Short legs only: a hedged shape's wing is bought, and "buy it back cheaply" means nothing
    # for it (the engine's price target reads short legs only too).
    leg_targets = [
        config.profit_target_price_for(float(leg.get("bid") or 0))
        for leg in result.legs if leg.get("action", "sell") == "sell"
    ]
    target_option_price = (
        min(t for t in leg_targets if t is not None)
        if leg_targets and all(t is not None for t in leg_targets)
        else None
    )
    return {
        "premium_collected": premium_collected,
        "loss_limit": loss_limit,
        "loss_multiple": float(config.loss_limit_premium_multiple),
        "target_option_price": target_option_price,
    }


def arm_exit_rule(
    proc: Any,
    user_id: str,
    *,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    terms: dict,
) -> str:
    """Arm the SG that will close this position. Returns the rule id; raises if it cannot.

    `ArmPreconditionError` means "not yet" -- an order for this expiry is still working, or
    the order book could not be read -- and is what `exit_arming` waits out. Anything else
    is a real failure.
    """
    from icici_breeze_backend.app.repositories import squareoff_rules as sq_repo
    from icici_breeze_backend.app.services import portfolio_pnl_engine
    from icici_breeze_backend.app.services import strategy_group_lifecycle
    from icici_breeze_backend.app.services.strategy_group_arm_guard import assert_can_arm

    premium_collected = float(terms.get("premium_collected") or 0)
    # The Processor, not `proc.get_session_breeze(...)`: the guard reads the order book
    # through `get_orders`, which chunks ICICI's 10-day window and merges exchanges.
    # `BreezeConnect` itself only has the raw `get_order_list`.
    # One exchange's book, not both: an index's contracts live on exactly one of them.
    assert_can_arm(proc, user_id, stock_code, expiry_display, exchange_code)
    legs = _arm_baseline_legs(user_id, stock_code, expiry_display)
    rule = sq_repo.arm_rule(
        user_id,
        stock_code=stock_code,
        expiry_display=expiry_display,
        exchange_code=exchange_code,
        # `profit_target_pnl` is NOT NULL and must stay positive. Where a price target
        # exists the two are alternatives, not a pair, so this is pushed out of reach so
        # it cannot front-run it. Where the user asked to let the position expire, it is
        # pushed out of reach for the same reason -- the stop-loss is the only live exit.
        profit_target_pnl=max(premium_collected * 100.0, 1.0),
        loss_limit_pnl=float(terms["loss_limit"]),
        target_premium_pct=5,
        stop_loss_premium_pct=5,
        target_option_price=terms.get("target_option_price"),
        legs_snapshot=strategy_group_lifecycle.snapshot_from_legs(legs),
    )
    # Hold the chain's feed for as long as the stop is armed, as the manual route does.
    strategy_group_lifecycle.pin_subscription(user_id, rule)
    portfolio_pnl_engine.set_group_rule(
        user_id,
        rule.id,
        stock_code=stock_code,
        expiry_display=expiry_display,
        exchange_code=exchange_code,
        target_pnl=rule.profit_target_pnl,
        stop_loss_pnl=rule.loss_limit_pnl,
        target_premium_pct=rule.target_premium_pct,
        stop_loss_premium_pct=rule.stop_loss_premium_pct,
        target_option_price=rule.target_option_price,
    )
    return rule.id


def _arm_baseline_legs(user_id: str, stock_code: str, expiry_display: str) -> list:
    """The legs the stop covers, read fresh, as the SG's drift baseline (B-12).

    The manual arm route records this; the bot's arm did not, so drift detection had
    nothing to compare. Legs the user added later were pooled into the bot's stop, and a
    group closed elsewhere was never reset. Read from the broker, not the registry: the
    registry may not hold the fills yet, and an empty baseline would read as drift the
    moment they land. Either failure is "not yet", which `exit_arming` waits out.
    """
    from icici_breeze_backend.app.services import portfolio_pnl_engine
    from icici_breeze_backend.app.services.squareoff_protection_guard import (
        warm_positions_for_user,
    )
    from icici_breeze_backend.app.services.strategy_group_arm_guard import ArmPreconditionError

    if not warm_positions_for_user(user_id):
        raise ArmPreconditionError("Your positions could not be read to record what the stop covers.")
    legs = portfolio_pnl_engine.group_legs_for_user(user_id, stock_code, expiry_display)
    if not legs:
        raise ArmPreconditionError("The filled position is not showing in your positions yet.")
    return legs


def _arm_exit(
    proc: Any,
    user_id: str,
    result: FireResult,
    *,
    config: ExpiryIndexWriterConfig,
    exchange: str,
    expiry_display: str,
) -> Optional[str]:
    """Arm now if the orders are already done; otherwise leave it to the order feed.

    Arming straight after placement used to be the only attempt, and it failed on almost
    every real-sized trade: the guard refuses while any order for the expiry is working, and
    freshly-placed limit orders nearly always are. So "not yet" is no longer a failure --
    `exit_arming` registers the position and arms it on the fill that completes it. Only a
    genuine error (not a still-working order) is reported as a failed arm, and even that is
    retried.
    """
    from icici_breeze_backend.app.db.bots_migrate import BOT_EXPIRY_INDEX_WRITER
    from icici_breeze_backend.app.services.bots import exit_arming
    from icici_breeze_backend.app.services.strategy_group_arm_guard import (
        ArmPreconditionError,
    )

    terms = exit_terms(result, config)
    if terms is None:
        return None

    alerted = False
    if exit_arming.orders_settled(result.order_ids):
        try:
            return arm_exit_rule(
                proc,
                user_id,
                stock_code=result.index_code,
                exchange_code=exchange,
                expiry_display=expiry_display,
                terms=terms,
            )
        except ArmPreconditionError:
            pass  # something else on this expiry is still working -- wait for it
        except Exception as e:  # noqa: BLE001
            _logger.exception("bot2: could not arm exit for %s", result.index_code)
            # The report carries this failure, so the retry below must not alert again.
            _record_arm_failure(result, e)
            alerted = True

    result.pending_exit_id = exit_arming.wait_then_arm(
        user_id=user_id,
        bot_type=BOT_EXPIRY_INDEX_WRITER,
        run_id=result.run_id,
        stock_code=result.index_code,
        exchange_code=exchange,
        expiry_display=expiry_display,
        order_ids=list(result.order_ids),
        terms=terms,
        alerted=alerted,
    )
    if not alerted:
        result.arm_pending = True
        if result.reason_code is None:
            result.reason_code = ReasonCode.EXIT_ARM_PENDING
    return None


def _record_arm_failure(result: FireResult, e: Exception) -> None:
    """Lead with the missing stop, but keep whatever came before it.

    On a partial fill `execute_plan` has already recorded why the other leg was rejected.
    Overwriting that would leave the user knowing a stop is missing but not which leg never
    went on -- and the open position is a different shape from the one the bot planned.
    """
    message = f"Position is OPEN but its stop could not be armed: {e}"
    if result.error:
        message = f"{message}. Also: {result.error}"
    result.error = message
    result.arm_error = str(e)
    result.reason_code = ReasonCode.EXIT_ARM_FAILED


def _skip_arm_existing_position(result: FireResult, *, other_legs: int) -> None:
    """Position filled; the group already carried other open legs, so no stop is armed.

    A Strategy Group rule pools P&L across every leg sharing (stock_code, expiry_display) --
    arming one here would silently fold the user's other position into this bot's stop and
    could close it at a moment and price they never chose (the same hazard
    `bots/scalping/guards.find_sg_conflict` refuses to trade into, from the other direction).
    Bot 2 never disarms or touches what was already there; it leaves its own new legs
    unprotected and says so. Unlike a genuine arm failure this is final -- there is nothing
    for `bots/exit_arming` to retry, since the other position will not close itself.
    """
    detail = (
        f"{other_legs} other open leg(s) already exist for {result.index_code} "
        f"{result.expiry_display}; arming a stop here would pool profit/loss across both "
        "positions"
    )
    message = f"Position is OPEN but no stop was armed: {detail}"
    if result.error:
        message = f"{message}. Also: {result.error}"
    result.error = message
    result.arm_error = detail
    result.reason_code = ReasonCode.EXIT_ARM_SKIPPED_EXISTING_POSITION


def notify_arm_skipped(user_id: str, result: FireResult) -> None:
    """Tell the user directly why this position has no automatic exit.

    Only the autonomous scheduler calls this: it has no other message that reaches the user
    for this run, unlike the Telegram-approval path, where `hitl.format_outcome` already
    folds this reason into the single reply it sends after the tap. Calling it from both
    would double the message.
    """
    if result.reason_code != ReasonCode.EXIT_ARM_SKIPPED_EXISTING_POSITION:
        return
    try:
        from icici_breeze_backend.app.services.telegram_alerts import notify_bot_exit_update
    except Exception:  # noqa: BLE001
        _logger.exception("bot2: could not import telegram alerts")
        return
    label = INDEX_LABEL.get(result.index_code, result.index_code)
    text = (
        f"⚠️ *Stop NOT armed* — {label} {result.expiry_display}: {result.arm_error}.\n\n"
        "This position has no automatic exit, and it will not be armed automatically later "
        "-- set PB/SL yourself in Portfolio if you want it protected."
    )
    try:
        notify_bot_exit_update(user_id, text)
    except Exception:  # noqa: BLE001
        _logger.exception("bot2: telegram update failed")
