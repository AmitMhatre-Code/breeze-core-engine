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

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import parse_strike
from icici_breeze_backend.app.core.timezone import today_ist_date
from icici_breeze_backend.app.domain.bots import HoldingsWriterConfig, ProposalLeg, ScripPref
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
        for right, enabled, safety_pct in (
            (cfg.CALL, pref.ce_enabled, pref.safety_pct_ce or config.default_safety_pct_ce),
            (cfg.PUT, pref.pe_enabled, pref.safety_pct_pe or config.default_safety_pct_pe),
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
                margin_source=margin_source,
                result=result,
            )
            if leg is not None:
                result.legs.append(leg)

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
    margin_source: str,
    result: ScanResult,
) -> Optional[ProposalLeg]:
    code = cand["stock_code"]
    lot_size = cand["lot_size"]
    expiry_display = cand["expiry_display"]
    label = f"{code} {'CE' if right == cfg.CALL else 'PE'}"

    if right == cfg.CALL:
        # Hard cap: you can only deliver stock you actually hold.
        lots = cand["quantity"] // lot_size - existing_lots
        if lots <= 0:
            result.skipped.append(
                SkippedScrip(
                    label,
                    "coverage_exhausted",
                    f"All {cand['quantity'] // lot_size} covered lot(s) are already written.",
                )
            )
            return None
    else:
        # Holdings do not cover a short put. Sized at one lot; the delivery-cash budget is
        # allocated by the user across scrips at approval time.
        lots = PE_LOTS_PER_SCRIP

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
        note=_pledge_note(cand, lot_size) if right == cfg.CALL else None,
    )


def _pledge_note(cand: dict, lot_size: int) -> Optional[str]:
    """Pledged stock is still coverage, but it cannot be delivered without unpledging
    first — a settlement-timing obligation the user should see before approving, not after
    assignment."""
    pledged = cand.get("pledged_quantity")
    if not pledged:
        return None
    pledged_lots = int(pledged) // lot_size
    if pledged_lots <= 0:
        return None
    return f"{pledged_lots} of {cand['quantity'] // lot_size} lots are pledged and would need unpledging to deliver."


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
