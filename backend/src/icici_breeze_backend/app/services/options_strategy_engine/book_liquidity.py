"""The Strategy Builder's side of the order-book liquidity check (docs/liquidity-checks-plan.md,
decision 7).

Two steps. Before any strategy is evaluated, every quote is checked at one lot on both sides, and a
strike that cannot take one lot on either side is not `liquid`, so no strategy proposes it. After
proposals are sized to the margin budget, each is capped at the lots its thinnest leg's book
absorbs within the threshold, and badged so the cap is visible.

A stale LTP neither excludes nor caps here: it is a fact about the benchmark, not about how much
the book holds, and the order ticket warns about it. Outside market hours nothing is judged.
"""
from __future__ import annotations

from typing import Iterable

from icici_breeze_backend.app.services.liquidity import check as liquidity
from icici_breeze_backend.app.services.liquidity.settings import load_liquidity_settings
from icici_breeze_backend.app.services.options_strategy_engine.sizing import rescale_result_to_lots
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    QuoteRow,
    StrategyResult,
)

BOOK_CAPPED_BADGE = "Capped by order book"


def apply_book_check(ctx: EngineContext, quotes: Iterable[QuoteRow]) -> None:
    if not liquidity._market_open():
        return
    settings = load_liquidity_settings()
    lot = max(1, int(ctx.lot_size or 1))
    for q in quotes:
        for side in (liquidity.SELL, liquidity.BUY):
            v = liquidity.check_contract(
                ctx.exchange_code, ctx.stock_code, ctx.expiry_display, float(q.strike), q.right,
                side, lot, lot_size=lot, settings=settings, market_open=True,
            )
            ok = v.size_ok
            if side == liquidity.SELL:
                q.book_sell_ok = ok
            else:
                q.book_buy_ok = ok


def cap_results_to_book(ctx: EngineContext, results: Iterable[StrategyResult]) -> None:
    L = int(ctx.lot_size or 0)
    if L <= 0 or not liquidity._market_open():
        return
    for r in results:
        if r.status != "ok" or not r.legs or r.legs[0].quantity <= 0:
            continue
        lots = max(1, r.legs[0].quantity // L)
        base = lots * L
        legs = [
            liquidity.SizedLeg(
                ctx.exchange_code, ctx.stock_code, ctx.expiry_display, float(leg.strike),
                leg.right, leg.side, lots_per_unit=max(1, round(leg.quantity / base)),
            )
            for leg in r.legs
            if leg.quantity > 0
        ]
        fit = liquidity.fit_lots(legs, lots, L, treat_stale_as_fail=False)
        if not fit.judged or fit.lots >= lots:
            continue
        if fit.lots <= 0:
            r.status = "skipped"
            r.skip_reason = "The order book is too thin for one lot of this structure."
            continue
        rescale_result_to_lots(r, lot_size=L, lots=fit.lots)
        if BOOK_CAPPED_BADGE not in r.badges:
            r.badges.append(BOOK_CAPPED_BADGE)
