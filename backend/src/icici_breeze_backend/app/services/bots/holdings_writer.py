"""Bot 1 — Holdings Option Writer: the scan that produces a proposal.

The two sides of this bot are capped by different things, and conflating them is the
mistake the whole design exists to avoid (docs/bots-mvp-plan.md section 0):

  * A short **CE** is genuinely covered by stock. Assignment means delivering shares you
    already hold, so the cap is hard: `floor(held / lot) - existing short CE lots`.
  * A short **PE** is not covered by stock at all. Indian stock options settle physically,
    so assignment means *buying* shares — that needs cash. The cap is the user's
    delivery-cash budget, and which scrips consume it is the user's call, not ours.

Everything here is sourced ICICI-native (ShortName namespace throughout): holdings, lot
sizes, expiries and the chain all speak the same codes, so no symbol bridge is involved.
"""
from __future__ import annotations

import datetime
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import parse_strike
from icici_breeze_backend.app.core.timezone import today_ist_date
from icici_breeze_backend.app.domain.bots import (
    HoldingsWriterConfig,
    ProposalLeg,
    ReasonCode,
    ScripPref,
)
from icici_breeze_backend.app.services.options_strategy_engine.helpers import _otm_elm_rate
from icici_breeze_backend.app.services.quote_source_router import (
    fetch_chain_side_icici_response,
)
from icici_breeze_backend.app.services.reference_data.scrip_master_sql import (
    _expiry_api_to_display,
)

_logger = logging.getLogger(__name__)

# One PE lot is proposed per eligible scrip. The delivery-cash budget is allocated by the
# user across scrips (design decision), so the bot's job is to surface each candidate with
# its assignment cost, not to decide how deep to go on any one name.
PE_LOTS_PER_SCRIP = 1


@dataclass
class SkippedScrip:
    stock_code: str
    reason_code: str
    reason: str


@dataclass
class ScanResult:
    legs: list[ProposalLeg] = field(default_factory=list)
    skipped: list[SkippedScrip] = field(default_factory=list)
    totals: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict] = field(default_factory=list)


def _parse_expiry(value: str):
    import datetime

    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(str(value).strip(), fmt).date()
        except (TypeError, ValueError):
            continue
    return None


def _monthly_expiries(raw_expiries: list[str]) -> list[str]:
    """Future expiries in display form, soonest first, de-duplicated.

    Stock options in India are monthly-only, so no weekly filtering is needed — but the
    scrip master carries past expiries too, and a proposal against one of those would be
    silently unfillable.
    """
    today = today_ist_date()
    seen: dict[Any, str] = {}
    for raw in raw_expiries or []:
        if not raw:
            continue
        try:
            display = _expiry_api_to_display(str(raw))
        except (ValueError, TypeError):
            # The scrip master is third-party data; one unparseable expiry must drop that
            # row, not abort the scan for every other holding.
            display = ""
        d = _parse_expiry(display) or _parse_expiry(str(raw))
        if d is None or d < today:
            continue
        seen.setdefault(d, display or str(raw))
    return [seen[d] for d in sorted(seen)]


def _existing_short_lots(positions: Any, lot_sizes: dict[str, int]) -> dict[tuple[str, str], int]:
    """Open short option lots keyed by (stock_code, right).

    Netted **across every expiry**, not per expiry: a short PE in September and another in
    October both consume the same coverage today, so summing per expiry would let the bot
    re-sell coverage it has already committed.
    """
    out: dict[tuple[str, str], int] = {}
    rows = (positions or {}).get("Success") or []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        if str(row.get("action") or "").strip().lower() != cfg.SELL.lower():
            continue
        right = str(row.get("right") or "").strip().capitalize()
        if right not in (cfg.CALL, cfg.PUT):
            continue
        code = str(row.get("stock_code") or "").strip().upper()
        if not code:
            continue
        lot = lot_sizes.get(code) or 0
        try:
            qty = abs(int(float(row.get("quantity") or 0)))
        except (TypeError, ValueError):
            continue
        if lot <= 0 or qty <= 0:
            continue
        out[(code, right)] = out.get((code, right), 0) + qty // lot
    return out


def _pick_strike(rows: list[dict], spot: float, right: str, safety_pct: float) -> Optional[dict]:
    """The chain row at or beyond the safety distance, closest to it.

    Rounding is always *away* from spot — up for calls, down for puts — so a strike grid
    that has no exact match yields a safer strike than asked for, never a riskier one.
    """
    if spot <= 0:
        return None
    target = (
        spot * (1 + safety_pct / 100) if right == cfg.CALL else spot * (1 - safety_pct / 100)
    )
    best: Optional[dict] = None
    best_distance = float("inf")
    for row in rows:
        strike = parse_strike(row.get("strike_price"))
        if strike is None:
            continue
        strike_f = float(strike)
        if right == cfg.CALL and strike_f < target:
            continue
        if right == cfg.PUT and strike_f > target:
            continue
        distance = abs(strike_f - target)
        if distance < best_distance:
            best, best_distance = row, distance
    return best


def _premium(row: dict) -> tuple[float, str]:
    """(price, basis) for one chain row.

    Premium is quoted at the **bid** — we are the seller, and on the wide spreads stock
    options actually trade at, pricing off LTP flatters every proposal.

    But a bid only exists while the market is open: the bhavcopy is an end-of-day file with
    no order book at all (0 of ~30k NFO rows carry one). Refusing to price off-market would
    make the bot unusable precisely when a monthly write is normally considered. So LTP is
    used as a clearly-labelled **indicative** price for planning, and `approve` refuses to
    place any leg that is still indicative when it re-prices. The user can plan on a Sunday;
    they cannot sell into a book that does not exist.
    """
    def _f(key: str) -> float:
        try:
            return float(row.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    bid = _f("best_bid_price")
    if bid > 0:
        return bid, "bid"
    ltp = _f("ltp")
    if ltp > 0:
        return ltp, "ltp_indicative"
    return 0.0, "bid"


def scan(
    proc: Any,
    user_id: str,
    *,
    config: HoldingsWriterConfig,
    prefs: dict[str, ScripPref],
    margin_source: str,
) -> ScanResult:
    result = ScanResult()

    holdings = proc.get_holdings(user_id)
    if (holdings or {}).get("Status") != 200:
        raise BotScanError(
            (holdings or {}).get("Error") or "Could not fetch holdings from the broker."
        )
    rows = holdings.get("Success") or []
    if not rows:
        return result

    # One scrip-master sweep for the whole scan rather than a lookup per holding.
    universe = {
        str(u.get("stock_code") or "").strip().upper(): (u.get("expiry_dates") or [])
        for u in (proc.fetch_stock_codes(cfg.NFO) or [])
    }

    lot_sizes: dict[str, int] = {}
    candidates: list[dict] = []
    for holding in rows:
        code = holding["stock_code"]
        expiries = _monthly_expiries(universe.get(code) or [])
        if not expiries:
            result.skipped.append(
                SkippedScrip(code, "not_fno_eligible", "No NSE F&O contracts for this scrip.")
            )
            continue
        idx = 1 if config.expiry_preference == "next" and len(expiries) > 1 else 0
        expiry_display = expiries[idx]
        lot_size = proc.fetch_lot_size(code, expiry_display, exchange_code=cfg.NFO)
        try:
            lot_size = int(lot_size or 0)
        except (TypeError, ValueError):
            lot_size = 0
        if lot_size <= 0:
            result.skipped.append(
                SkippedScrip(code, "no_lot_size", "No lot size in the scrip master.")
            )
            continue
        lot_sizes[code] = lot_size
        if holding["quantity"] < lot_size:
            result.skipped.append(
                SkippedScrip(
                    code,
                    "below_one_lot",
                    f"Holding {holding['quantity']} is under one lot of {lot_size}.",
                )
            )
            continue
        candidates.append({**holding, "expiry_display": expiry_display, "lot_size": lot_size})

    existing = _existing_short_lots(proc.get_positions(user_id), lot_sizes)

    for cand in candidates:
        code = cand["stock_code"]
        pref = prefs.get(code) or ScripPref(stock_code=code)
        for right, enabled, safety_pct, wanted_lots in (
            (
                cfg.CALL,
                pref.writes_ce,
                pref.safety_pct_ce or config.default_safety_pct_ce,
                pref.ce_lots,
            ),
            (
                cfg.PUT,
                pref.writes_pe,
                pref.safety_pct_pe or config.default_safety_pct_pe,
                pref.pe_lots,
            ),
        ):
            if not enabled:
                continue
            leg = _build_leg(
                proc,
                user_id,
                cand,
                right=right,
                safety_pct=float(safety_pct),
                existing_lots=existing.get((code, right), 0),
                wanted_lots=wanted_lots,
                priority=pref.priority,
                margin_source=margin_source,
                result=result,
            )
            if leg is not None:
                result.legs.append(leg)

    # Funding order, not alphabetical: the proposal is read top-down while allocating a
    # budget, and in autonomous mode this is literally the order money is committed in.
    # Calls lead within a scrip -- they are the covered trade and cost no delivery cash.
    result.legs.sort(key=lambda leg: (leg.scrip_priority, leg.stock_code, leg.right != "call"))
    result.totals = _totals(result.legs, config)
    return result


class BotScanError(RuntimeError):
    """Scan could not run at all (as opposed to finding nothing)."""


def _build_leg(
    proc: Any,
    user_id: str,
    cand: dict,
    *,
    right: str,
    safety_pct: float,
    existing_lots: int,
    wanted_lots: Optional[int] = None,
    priority: int = 1,
    margin_source: str,
    result: ScanResult,
) -> Optional[ProposalLeg]:
    code = cand["stock_code"]
    lot_size = cand["lot_size"]
    expiry_display = cand["expiry_display"]
    label = f"{code} {'CE' if right == cfg.CALL else 'PE'}"
    clipped_note: Optional[str] = None

    if right == cfg.CALL:
        # Hard cap: you can only deliver stock you can actually deliver. A configured lot
        # count is a target within that cap, never a way past it -- so asking for more lots
        # than the holding covers writes what is covered and says so on the row, rather than
        # either refusing the scrip outright or quietly selling naked calls.
        covered = deliverable_quantity(cand) // lot_size - existing_lots
        if covered <= 0:
            result.skipped.append(
                SkippedScrip(
                    label,
                    "coverage_exhausted",
                    f"All {cand['quantity'] // lot_size} covered lot(s) are already written.",
                )
            )
            return None
        lots = covered if wanted_lots is None else min(int(wanted_lots), covered)
        if lots <= 0:
            return None
        if wanted_lots is not None and int(wanted_lots) > covered:
            clipped_note = (
                f"You asked for {int(wanted_lots)} lot(s); only {covered} are covered by "
                "stock you hold, so that is what will be written."
            )
    else:
        # Holdings do not cover a short put -- assignment means buying shares. The cap is
        # the delivery-cash budget, spent in scrip-priority order, so the count here is
        # whatever the user asked for and the budget is enforced at selection time.
        lots = PE_LOTS_PER_SCRIP if wanted_lots is None else int(wanted_lots)
        if lots <= 0:
            return None

    chain = fetch_chain_side_icici_response(proc, user_id, code, cfg.NFO, expiry_display, right)
    if (chain or {}).get("Status") != 200 or not chain.get("Success"):
        result.skipped.append(
            SkippedScrip(label, "chain_unavailable", "No option chain available.")
        )
        return None
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
        # ICICI populates current_market_price on the holdings row, so a chain without a
        # usable spot is recoverable rather than fatal.
        spot = float(cand.get("current_market_price") or 0)
    if spot <= 0:
        result.skipped.append(SkippedScrip(label, "no_spot", "No spot price available."))
        return None

    row = _pick_strike(rows, spot, right, safety_pct)
    if row is None:
        result.skipped.append(
            SkippedScrip(label, "no_strike", f"No strike at least {safety_pct}% from spot.")
        )
        return None

    strike = float(parse_strike(row.get("strike_price")) or 0)
    bid, basis = _premium(row)
    if bid <= 0:
        result.skipped.append(
            SkippedScrip(label, "no_price", f"No bid or last trade on the {strike:g} strike.")
        )
        return None

    quantity = lots * lot_size
    span_margin: Optional[float] = None
    try:
        margin, warnings = proc._resolve_leg_margin_with_source(
            user_id=user_id,
            exchange_code=cfg.NFO,
            stock_code=code,
            expiry_display=expiry_display,
            strike_price=strike,
            right=right,
            quantity=quantity,
            margin_source=margin_source,
            action=cfg.SELL,
            product=cfg.OPTIONS,
        )
        if warnings:
            result.warnings.extend(warnings)
        if (margin or {}).get("Status") == 200:
            span_margin = float(margin["Success"]["span_margin_required"])
    except Exception as e:  # noqa: BLE001 -- a margin gap must not lose the whole row
        _logger.warning("holdings-writer margin lookup failed for %s: %s", label, e, exc_info=True)

    # ELM uses the same tiered rate the strategy engine applies, so the two surfaces cannot
    # disagree about what a short leg costs.
    elm_rate = _otm_elm_rate(right, strike, spot, is_index=False)
    elm_margin = spot * lot_size * lots * elm_rate

    return ProposalLeg(
        stock_code=code,
        exchange_code=cfg.NFO,
        right="call" if right == cfg.CALL else "put",
        expiry_display=expiry_display,
        strike_price=strike,
        lots=lots,
        lot_size=lot_size,
        quantity=quantity,
        premium_per_share=bid,
        premium_total=round(bid * quantity, 2),
        premium_basis=basis,
        span_margin=round(span_margin, 2) if span_margin is not None else None,
        elm_margin=round(elm_margin, 2),
        delivery_exposure=round(strike * quantity, 2) if right == cfg.PUT else None,
        held_quantity=cand["quantity"],
        pledged_quantity=cand.get("pledged_quantity"),
        existing_short_lots=existing_lots,
        selected=right == cfg.CALL,
        note=_note(clipped_note, _pledge_note(cand, lot_size) if right == cfg.CALL else None),
        scrip_priority=int(priority or 1),
    )


def _note(*parts: Optional[str]) -> Optional[str]:
    kept = [p for p in parts if p]
    return " ".join(kept) if kept else None


def deliverable_quantity(cand: dict) -> int:
    """How much of a holding could actually be delivered against a short call.

    Pledged stock counts: it is genuinely owned, and unpledging it before expiry is a step
    the user takes, not a reason to leave the coverage unwritten. Blocked-for-trade stock
    does not: it is already earmarked (a pending sale, a settlement hold) and is not the
    user's to deliver.

    When demat could not be read, `blocked_quantity` is None -- unknown, not zero -- and the
    full holding is used. That is the pre-existing behaviour and the conservative direction
    is arguable either way; erring the other way would silently stop writing covered calls
    across the whole portfolio the moment one broker call failed.
    """
    total = int(cand.get("quantity") or 0)
    blocked = cand.get("blocked_quantity")
    if blocked is None:
        return total
    return max(0, total - int(blocked))


def _pledge_note(cand: dict, lot_size: int) -> Optional[str]:
    """Surface the obligations behind the coverage, before approval rather than after
    assignment: pledged stock has to be unpledged before it can be delivered, and blocked
    stock is not coverage at all."""
    if lot_size <= 0:
        return None
    parts: list[str] = []
    total_lots = int(cand.get("quantity") or 0) // lot_size

    pledged_lots = int(cand.get("pledged_quantity") or 0) // lot_size
    if pledged_lots > 0:
        parts.append(
            f"{pledged_lots} of {total_lots} lots are pledged — unpledge them before expiry "
            "to deliver."
        )

    blocked_lots = int(cand.get("blocked_quantity") or 0) // lot_size
    if blocked_lots > 0:
        parts.append(
            f"{blocked_lots} lot(s) are blocked for trade and are not counted as coverage."
        )
    return " ".join(parts) if parts else None


def _totals(legs: list[ProposalLeg], config: HoldingsWriterConfig) -> dict[str, Any]:
    selected = [leg for leg in legs if leg.selected]
    delivery = sum(leg.delivery_exposure or 0 for leg in selected)
    return {
        "premium_total": round(sum(leg.premium_total for leg in selected), 2),
        "span_total": round(sum(leg.span_margin or 0 for leg in selected), 2),
        "elm_total": round(sum(leg.elm_margin or 0 for leg in selected), 2),
        "delivery_exposure_total": round(delivery, 2),
        "delivery_cash_budget": config.delivery_cash_budget,
        "delivery_headroom": round(config.delivery_cash_budget - delivery, 2),
        "leg_count": len(legs),
        "selected_count": len(selected),
    }


# --------------------------------------------------------------------------------------
# Autonomous allocation
# --------------------------------------------------------------------------------------


@dataclass
class Allocation:
    """Which legs an unattended run would actually place, and why the rest were dropped."""

    selected: list[int] = field(default_factory=list)
    dropped: list[SkippedScrip] = field(default_factory=list)
    margin_used: float = 0.0
    delivery_used: float = 0.0
    premium_total: float = 0.0


def allocate(
    legs: list[ProposalLeg],
    *,
    free_margin: float,
    delivery_budget: float,
) -> Allocation:
    """Spend free margin and delivery cash across the proposal, in scrip-priority order.

    This is the step a human does by hand in manual mode, and it is the reason Bot 1 can
    run unattended at all: without an explicit order, "which puts do I fund?" has no answer
    the bot is entitled to make up.

    Two independent budgets, because the two legs cost different things (see section 0 of
    the design doc). Every leg consumes SPAN + ELM against free margin. A put *additionally*
    commits delivery cash it would need if assigned -- checked against the user's own budget
    figure, deliberately not against broker funds, which drift with unrelated activity.

    A leg that does not fit is skipped and the walk **continues**: a cheaper lower-priority
    scrip that still fits should be written, rather than being punished for sitting behind
    an expensive one. Priority orders funding; it does not gate it.
    """
    alloc = Allocation()
    margin_left = float(free_margin)
    delivery_left = float(delivery_budget)

    for index, leg in enumerate(legs):
        label = f"{leg.stock_code} {leg.strike_price:g} {'CE' if leg.right == 'call' else 'PE'}"
        needed = float(leg.span_margin or 0) + float(leg.elm_margin or 0)
        if needed <= 0:
            # No margin number means the lookup failed earlier. Placing on an unknown margin
            # is exactly the unattended over-commitment the caps exist to prevent.
            alloc.dropped.append(
                SkippedScrip(label, ReasonCode.MARGIN_LOOKUP_FAILED, "Margin could not be priced.")
            )
            continue
        if needed > margin_left:
            alloc.dropped.append(
                SkippedScrip(
                    label,
                    ReasonCode.MARGIN_EXHAUSTED,
                    f"Needs Rs {needed:,.0f} of margin; Rs {margin_left:,.0f} left.",
                )
            )
            continue
        exposure = float(leg.delivery_exposure or 0)
        if exposure > 0 and exposure > delivery_left:
            alloc.dropped.append(
                SkippedScrip(
                    label,
                    ReasonCode.BUDGET_EXHAUSTED,
                    f"Assignment would cost Rs {exposure:,.0f}; Rs {delivery_left:,.0f} of "
                    "the delivery-cash budget is left.",
                )
            )
            continue

        margin_left -= needed
        delivery_left -= exposure
        alloc.selected.append(index)
        alloc.margin_used += needed
        alloc.delivery_used += exposure
        alloc.premium_total += float(leg.premium_total)

    alloc.margin_used = round(alloc.margin_used, 2)
    alloc.delivery_used = round(alloc.delivery_used, 2)
    alloc.premium_total = round(alloc.premium_total, 2)
    return alloc


# --------------------------------------------------------------------------------------
# Autonomous firing -- when, and whether
# --------------------------------------------------------------------------------------

TickAction = Literal["idle", "nag", "fire", "skip"]


def firing_date(expiry: datetime.date, days_before: int) -> datetime.date:
    """The date `days_before` TRADING days ahead of an expiry.

    Trading days, not calendar days, because calendar arithmetic lands on weekends and
    exchange holidays -- a bot configured to fire "3 days before" would then spend those
    days skipping with `market_closed` and write nothing at all in a month with a long
    weekend before expiry. 0 means the expiry day itself.
    """
    from icici_breeze_backend.app.services.market_calendar import IST, is_trading_day

    def _is_trading(d: datetime.date) -> bool:
        return is_trading_day(datetime.datetime(d.year, d.month, d.day, 12, 0, tzinfo=IST))

    day = expiry
    remaining = max(0, int(days_before))
    while remaining > 0:
        day -= datetime.timedelta(days=1)
        if _is_trading(day):
            remaining -= 1
    # If the target itself is a holiday (an expiry moved, say), step back to the last day
    # the market was actually open rather than silently skipping the month.
    guard = 0
    while not _is_trading(day) and guard < 14:
        day -= datetime.timedelta(days=1)
        guard += 1
    return day


@dataclass(frozen=True)
class TickContext:
    now: datetime.datetime
    app_started_at: datetime.datetime
    config: HoldingsWriterConfig
    # True when today is the configured firing day for the target expiry. Computed by the
    # caller from the scrip master and the exchange calendar, so this function stays pure.
    is_firing_day: bool
    has_session: bool
    ran_today: bool
    last_nag_at: Optional[datetime.datetime] = None


@dataclass(frozen=True)
class TickDecision:
    action: TickAction
    reason_code: Optional[str] = None
    reason_text: Optional[str] = None


def _at(now: datetime.datetime, hhmm: str) -> datetime.datetime:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def decide(ctx: TickContext) -> TickDecision:
    """What an unattended Bot 1 should do on this tick. Pure -- no IO, no wall clock.

    Deliberately the same shape as Bot 2's `decide`, because on its firing day this bot has
    the same problem: the ICICI session lapses overnight, so an unattended entry depends on
    someone having logged in. `nag_start_ist` doubles as the entry time -- with a session in
    hand it fires then, and without one it nags until a session appears or the cutoff ends
    the day.
    """
    config = ctx.config

    if ctx.ran_today:
        # Terminal for the day either way: a fired bot must not fire twice, and a day
        # already logged as skipped must not re-log on every subsequent tick.
        return TickDecision("idle")

    if not ctx.is_firing_day:
        # Silent, not a logged skip. Bot 2 skips because an expiry day either happens or it
        # does not; this bot has ~20 non-firing days a month and logging each one would bury
        # the days that actually mattered.
        return TickDecision("idle")

    cutoff = _at(ctx.now, config.cutoff_ist)
    entry = max(_at(ctx.now, config.nag_start_ist), ctx.app_started_at)

    if ctx.now >= cutoff:
        if not ctx.has_session:
            return TickDecision(
                "skip",
                ReasonCode.NO_BROKER_SESSION,
                f"No ICICI session by the {config.cutoff_ist} cut-off, so nothing was written.",
            )
        return TickDecision(
            "skip",
            ReasonCode.CUTOFF_PASSED,
            f"The {config.cutoff_ist} cut-off passed before this could write.",
        )

    if not ctx.has_session:
        if ctx.now < _at(ctx.now, config.nag_start_ist) or ctx.now < ctx.app_started_at:
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
                "Your Holdings Option Writer is due to write today, but your ICICI session "
                f"has lapsed. Log in before {config.cutoff_ist} or it will skip this month."
            ),
        )

    if ctx.now < entry:
        return TickDecision("idle")

    return TickDecision("fire")


def list_holdings(proc: Any, user_id: str, config: HoldingsWriterConfig) -> list[dict]:
    """The scrip list the settings drawer configures, read live from the broker.

    Deliberately not a stored list. What the user holds changes without the bot being told,
    so a cached list would offer to write calls against stock that has since been sold --
    and, worse, would hide a newly-bought scrip the user expects to see. `proc.get_holdings`
    resolves pledging across both broker endpoints (portfolio for the true quantity, demat
    for what is pledged), which is the number the coverage cap has to be built on.

    Rows the bot cannot write are returned too, marked ineligible with the reason. A scrip
    that silently vanishes from this list is indistinguishable from a bug.
    """
    holdings = proc.get_holdings(user_id)
    if (holdings or {}).get("Status") != 200:
        raise BotScanError(
            (holdings or {}).get("Error") or "Could not fetch holdings from the broker."
        )
    rows = holdings.get("Success") or []
    universe = {
        str(u.get("stock_code") or "").strip().upper(): (u.get("expiry_dates") or [])
        for u in (proc.fetch_stock_codes(cfg.NFO) or [])
    }

    lot_sizes: dict[str, int] = {}
    prepared: list[dict] = []
    for holding in rows:
        code = str(holding.get("stock_code") or "").strip().upper()
        if not code:
            continue
        quantity = int(holding.get("quantity") or 0)
        pledged = int(holding.get("pledged_quantity") or 0)
        blocked = int(holding.get("blocked_quantity") or 0)
        row = {
            "stock_code": code,
            "quantity": quantity,
            "pledged_quantity": pledged,
            "blocked_quantity": blocked,
            "available_quantity": int(
                holding.get("available_quantity")
                if holding.get("available_quantity") is not None
                else max(0, quantity - pledged - blocked)
            ),
            "deliverable_quantity": deliverable_quantity(holding),
            "current_market_price": holding.get("current_market_price"),
            "lot_size": None,
            "lots_held": 0,
            "available_lots": 0,
            "blocked_lots": 0,
            "pledged_lots": 0,
            "deliverable_lots": 0,
            "fno_eligible": True,
            "ineligible_reason": None,
        }
        expiries = _monthly_expiries(universe.get(code) or [])
        if not expiries:
            row["fno_eligible"] = False
            row["ineligible_reason"] = "No NSE F&O contracts for this scrip."
            prepared.append(row)
            continue
        idx = 1 if config.expiry_preference == "next" and len(expiries) > 1 else 0
        lot_size = proc.fetch_lot_size(code, expiries[idx], exchange_code=cfg.NFO)
        try:
            lot_size = int(lot_size or 0)
        except (TypeError, ValueError):
            lot_size = 0
        if lot_size <= 0:
            row["fno_eligible"] = False
            row["ineligible_reason"] = "No lot size in the scrip master."
            prepared.append(row)
            continue
        lot_sizes[code] = lot_size
        row["lot_size"] = lot_size
        row["lots_held"] = quantity // lot_size
        # Each category floored independently, so the parts can sum to one less than the
        # total when a category carries a sub-lot remainder. That matches how this bot
        # already thinks -- a remainder below one lot is not writable coverage -- and the UI
        # carries the exact share counts alongside.
        row["available_lots"] = row["available_quantity"] // lot_size
        row["blocked_lots"] = row["blocked_quantity"] // lot_size
        row["pledged_lots"] = row["pledged_quantity"] // lot_size
        row["deliverable_lots"] = row["deliverable_quantity"] // lot_size
        if row["lots_held"] < 1:
            row["fno_eligible"] = False
            row["ineligible_reason"] = (
                f"Holding {quantity} is under one lot of {lot_size}."
            )
        elif row["deliverable_lots"] < 1:
            # Held, F&O-eligible, and still unwritable — worth saying out loud rather than
            # leaving a row that looks configurable but can never produce a call.
            row["fno_eligible"] = False
            row["ineligible_reason"] = (
                f"All {row['lots_held']} lot(s) are blocked for trade, so none can be "
                "delivered against a call."
            )
        prepared.append(row)

    # Existing shorts net across every expiry, matching the cap the scan applies -- so the
    # drawer shows the same coverage the bot will actually have.
    existing = _existing_short_lots(proc.get_positions(user_id), lot_sizes)
    for row in prepared:
        code = row["stock_code"]
        row["existing_short_ce_lots"] = existing.get((code, cfg.CALL), 0)
        row["existing_short_pe_lots"] = existing.get((code, cfg.PUT), 0)
    prepared.sort(key=lambda r: (not r["fno_eligible"], r["stock_code"]))
    return prepared


def price_contract(
    proc: Any,
    user_id: str,
    *,
    stock_code: str,
    right: str,
    expiry_display: str,
    strike_price: float,
    lots: int,
    lot_size: int,
    margin_source: str,
    held_quantity: Optional[int] = None,
    pledged_quantity: Optional[int] = None,
    existing_short_lots: int = 0,
    scrip_priority: int = 1,
) -> Optional[ProposalLeg]:
    """Price one EXPLICIT contract — the strike and size the user edited to.

    The scan proposes exactly one strike per scrip and side, so a user who moves the strike
    in the manual review has asked for a contract the scan never priced. Re-deriving it from
    the scan is impossible; pricing it directly is the only honest answer, and it goes
    through the same bid-not-LTP and margin rules the scan uses so the two cannot disagree.
    """
    rights = cfg.CALL if str(right).lower().startswith("c") else cfg.PUT
    chain = fetch_chain_side_icici_response(
        proc, user_id, stock_code, cfg.NFO, expiry_display, rights
    )
    if (chain or {}).get("Status") != 200 or not chain.get("Success"):
        return None
    rows = [r for r in chain["Success"] if isinstance(r, dict)]

    match = None
    for row in rows:
        value = parse_strike(row.get("strike_price"))
        if value is not None and abs(float(value) - float(strike_price)) < 1e-6:
            match = row
            break
    if match is None:
        return None

    spot = 0.0
    for r in rows:
        try:
            spot = float(r.get("spot_price") or 0)
        except (TypeError, ValueError):
            spot = 0.0
        if spot > 0:
            break

    bid, basis = _premium(match)
    if bid <= 0:
        return None

    quantity = max(1, int(lots)) * int(lot_size)
    span_margin: Optional[float] = None
    try:
        margin, _warnings = proc._resolve_leg_margin_with_source(
            user_id=user_id,
            exchange_code=cfg.NFO,
            stock_code=stock_code,
            expiry_display=expiry_display,
            strike_price=float(strike_price),
            right=rights,
            quantity=quantity,
            margin_source=margin_source,
            action=cfg.SELL,
            product=cfg.OPTIONS,
        )
        if (margin or {}).get("Status") == 200:
            span_margin = float(margin["Success"]["span_margin_required"])
    except Exception:  # noqa: BLE001 -- an unpriceable margin drops the leg, never places it
        _logger.warning(
            "holdings-writer could not price margin for %s %s", stock_code, strike_price,
            exc_info=True,
        )

    elm_rate = _otm_elm_rate(rights, float(strike_price), spot, is_index=False)
    return ProposalLeg(
        stock_code=stock_code,
        exchange_code=cfg.NFO,
        right="call" if rights == cfg.CALL else "put",
        expiry_display=expiry_display,
        strike_price=float(strike_price),
        lots=max(1, int(lots)),
        lot_size=int(lot_size),
        quantity=quantity,
        premium_per_share=bid,
        premium_total=round(bid * quantity, 2),
        premium_basis=basis,
        span_margin=round(span_margin, 2) if span_margin is not None else None,
        elm_margin=round(spot * quantity * elm_rate, 2) if spot > 0 else 0.0,
        delivery_exposure=(
            round(float(strike_price) * quantity, 2) if rights == cfg.PUT else None
        ),
        held_quantity=held_quantity,
        pledged_quantity=pledged_quantity,
        existing_short_lots=existing_short_lots,
        scrip_priority=scrip_priority,
        selected=True,
    )
