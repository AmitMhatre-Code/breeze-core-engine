"""CAS Bingo's manual run sheet (docs/bots-cas-bingo-plan.md section 6).

All five structures priced side by side for every index expiring today, each sized from the
user's settings and carrying its liquidation plan when free margin falls short. The signal is
shown for context and never consulted. Execute re-plans that one structure at fresh prices
before placing -- a figure nobody quoted is not a figure to trade on.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from icici_breeze_backend.app.db.bots_migrate import BOT_CAS_BINGO
from icici_breeze_backend.app.domain.bots import CAS_BINGO_INDICES, CasBingoConfig, ReasonCode
from icici_breeze_backend.app.services.bots.cas_bingo import execution, market, runtime
from icici_breeze_backend.app.services.bots.cas_bingo import liquidation as liq
from icici_breeze_backend.app.services.bots.cas_bingo.plan import MANUAL_ORDER, STRUCTURE_LABEL, STRUCTURES, build_plan

_logger = logging.getLogger(__name__)

CREDIT_WARNING = (
    "ICICI may square off your positions at an extreme loss if MTM or margin requirements "
    "spike during CAS."
)
CAS_REGIME_NOTE = (
    "During CAS the index is an indicative auction value, and SEBI's consultation (comments "
    "due 3 Oct 2026) may move expiry settlement off the auction."
)


class ManualRefused(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _indices(proc: Any, config: CasBingoConfig) -> dict[str, str]:
    expiring = market.expiring_today(proc)
    chosen = [c for c in config.enabled_indices() if c in expiring]
    # Nothing enabled expires today but something does: show that rather than an empty sheet.
    if not chosen:
        chosen = [c for c in CAS_BINGO_INDICES if c in expiring]
    return {c: expiring[c] for c in chosen}


def _signal_blocked(config: CasBingoConfig) -> Optional[str]:
    """Why the bot's signal is not yet available to it, or None (the 30-day backtest gate)."""
    from icici_breeze_backend.app.db.bots_migrate import BOT_CAS_BINGO
    from icici_breeze_backend.app.services.bots.signal_gate import refusal

    return refusal(BOT_CAS_BINGO, config)


def sheet(proc: Any, user_id: str, config: CasBingoConfig) -> dict[str, Any]:
    indices = _indices(proc, config)
    if not indices:
        return {"indices": [], "message": "Neither NIFTY nor SENSEX expires today.", "warnings": {"credit": CREDIT_WARNING, "cas": CAS_REGIME_NOTE}}

    available = execution._available(proc, user_id)
    out: list[dict[str, Any]] = []
    for code, expiry in indices.items():
        calls = market.chain_rows(proc, user_id, code, expiry, "call")
        puts = market.chain_rows(proc, user_id, code, expiry, "put")
        opening = runtime.day_open(code)
        spot = market.index_spot(code) or market.spot_from(calls) or market.spot_from(puts)
        state, value, _reason = runtime._signal(config, code)

        book: Optional[list[liq.BookLeg]] = None
        quotes: dict = {}
        book_error: Optional[str] = None
        candidates: list[dict[str, Any]] = []
        for structure in MANUAL_ORDER:
            plan, problem = build_plan(
                proc, user_id, config, index_code=code, expiry_display=expiry,
                structure=structure, day_open=opening, spot=spot, calls=calls, puts=puts,
            )
            row: dict[str, Any] = {
                "structure": structure,
                "label": STRUCTURE_LABEL[structure],
                "family": STRUCTURES[structure][0],
            }
            if plan is None:
                row["problem"] = {"reason_code": problem[0], "reason": problem[1]} if problem else None
                candidates.append(row)
                continue
            row["plan"] = plan.summary()
            if available is not None and plan.margin_required > available:
                if book is None and book_error is None:
                    book, quotes, book_error = liq.fetch_book(proc, user_id, code, expiry)
                if book is not None:
                    lplan = liq.plan_liquidation(
                        book, liq.shorts_in(book, quotes),
                        shortfall=plan.margin_required - available,
                        min_captured_pct=config.liquidation.min_captured_pct,
                        safety_buffer_pct=config.liquidation.safety_buffer_pct,
                        lot_size=plan.lot_size, spot=plan.spot,
                        span_fn=liq.local_span_fn(code, expiry, plan.spot),
                    )
                    row["liquidation"] = lplan.summary()
                else:
                    row["liquidation"] = {"covered": False, "note": book_error, "buybacks": []}
            candidates.append(row)

        out.append(
            {
                "index_code": code,
                "index_label": market.INDEX_LABEL[code],
                "expiry_display": expiry,
                "day_open": opening,
                "spot": spot,
                "signal": {"state": state, "value": value, "name": config.signal.label()},
                "signal_blocked": _signal_blocked(config),
                "sg_conflict": runtime.sg_conflict(user_id, code, expiry),
                "candidates": candidates,
            }
        )
    return {
        "indices": out,
        "available_margin": available,
        "liquidation_enabled": config.liquidation.enabled,
        "warnings": {"credit": CREDIT_WARNING, "cas": CAS_REGIME_NOTE},
    }


def execute(proc: Any, user_id: str, config: CasBingoConfig, *, index_code: str, structure: str) -> dict[str, Any]:
    """Re-plan one structure at fresh prices, then run the same margin/liquidation/entry
    pipeline the loop uses, placing real orders."""
    from icici_breeze_backend.app.repositories import bots as repo
    from icici_breeze_backend.app.services.bots.charges import load_charges

    if structure not in STRUCTURES:
        raise ManualRefused(400, f"Unknown structure: {structure}")
    expiry = market.expiring_today(proc).get(index_code)
    if not expiry:
        raise ManualRefused(409, f"{market.INDEX_LABEL.get(index_code, index_code)} does not expire today.")
    if runtime.sg_conflict(user_id, index_code, expiry):
        raise ManualRefused(
            409,
            f"A PB/SL rule is armed on {index_code} {expiry}. It would absorb these legs and "
            f"square them off with its own; disarm it first.",
        )

    plan, problem = build_plan(
        proc, user_id, config, index_code=index_code, expiry_display=expiry,
        structure=structure, day_open=runtime.day_open(index_code),
    )
    if plan is None:
        raise ManualRefused(409, problem[1] if problem else "The structure could not be priced.")

    run_id = repo.start_run(user_id, BOT_CAS_BINGO, "manual")
    outcome = execution.enter(
        proc, user_id, config, run_id, plan, live=True, charges=load_charges(),
        extra={"trigger": "manual", "mode": "manual"},
    )
    repo.finish_run(
        run_id,
        status="completed" if outcome.opened else ("failed" if outcome.reason_code == ReasonCode.ORDER_REJECTED else "skipped"),
        reason_code=outcome.reason_code,
        reason_text=outcome.reason_text,
        detail={"plan": plan.summary(), "liquidation": outcome.liquidation, "cycle_id": outcome.cycle_id},
    )
    return {
        "opened": outcome.opened,
        "reason_code": outcome.reason_code,
        "reason_text": outcome.reason_text,
        "cycle_id": outcome.cycle_id,
        "plan": plan.summary(),
        "liquidation": outcome.liquidation,
    }
