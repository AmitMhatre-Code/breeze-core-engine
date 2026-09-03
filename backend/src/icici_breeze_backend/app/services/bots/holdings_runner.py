"""Bot 1 orchestration: the steps around a scan that both entry points share.

`holdings_writer` decides *what* is writable; this module owns the run-log bookkeeping and
the unattended path. It exists so the manual route and the scheduler cannot drift apart --
a manual review that showed different numbers from what the bot would have done on its own
would be worse than no review at all.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.bots_migrate import BOT_HOLDINGS_WRITER
from icici_breeze_backend.app.domain.bots import HoldingsWriterConfig, ReasonCode
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots import holdings_writer

_logger = logging.getLogger(__name__)


def run_scan(user_id: str, trigger: str):
    """Scan, and record the outcome in the run log whatever happens.

    Returns `(run_id, proposal | None, skipped, warnings)`. Raises `BotScanError` when the
    scan could not run at all, which the caller turns into a 502 -- distinct from a scan
    that ran fine and found nothing.
    """
    from icici_breeze_backend.app.services.processor import processor

    proc = processor()
    bot = repo.get_or_create_bot(user_id, BOT_HOLDINGS_WRITER)
    config = HoldingsWriterConfig(**bot.config)
    prefs = {p.stock_code: p for p in repo.list_scrip_prefs(user_id)}
    run_id = repo.start_run(user_id, BOT_HOLDINGS_WRITER, trigger)

    try:
        result = holdings_writer.scan(
            proc,
            user_id,
            config=config,
            prefs=prefs,
            margin_source=proc.get_strategy_builder_margin_source(user_id),
        )
    except holdings_writer.BotScanError as e:
        repo.finish_run(
            run_id, status="failed", reason_code=ReasonCode.BROKER_ERROR, reason_text=str(e)
        )
        raise
    except Exception:
        _logger.exception("holdings-writer scan failed for user=%s", user_id)
        repo.finish_run(
            run_id,
            status="failed",
            reason_code=ReasonCode.INTERNAL_ERROR,
            reason_text="The scan failed unexpectedly.",
        )
        raise

    skipped = [
        {"stock_code": s.stock_code, "reason_code": s.reason_code, "reason": s.reason}
        for s in result.skipped
    ]
    if not result.legs:
        repo.finish_run(
            run_id,
            status="skipped",
            reason_code=ReasonCode.NOTHING_ELIGIBLE,
            reason_text=(
                f"No writable contracts found across {len(skipped)} holding(s)."
                if skipped
                else "No F&O-eligible holdings."
            ),
            detail={"skipped": skipped},
        )
        return run_id, None, skipped, result.warnings

    proposal = repo.create_proposal(
        run_id=run_id,
        user_id=user_id,
        bot_type=BOT_HOLDINGS_WRITER,
        legs=result.legs,
        totals=result.totals,
        ttl_minutes=config.proposal_ttl_minutes,
    )
    repo.finish_run(
        run_id,
        status="proposed",
        reason_code=ReasonCode.PROPOSAL_READY,
        reason_text=(
            f"{len(result.legs)} contract(s) proposed; "
            f"{len(skipped)} holding(s) produced nothing."
        ),
        detail={"skipped": skipped, "proposal_id": proposal.id},
    )
    return run_id, proposal, skipped, result.warnings


def fire_autonomous(
    proc: Any,
    user_id: str,
    config: HoldingsWriterConfig,
    *,
    margin_committed: float = 0.0,
    trigger: str = "schedule",
    propose_only: bool = False,
) -> float:
    """Scan, allocate under the caps, place. Returns the margin this run committed.

    The return value is what makes cross-bot priority mean something: the scheduler feeds it
    forward, so a lower-priority bot sizes against what is genuinely left rather than
    against a free-margin figure this run has already spent.

    `propose_only` is the semi-autonomous path: everything up to and including the
    allocation runs identically, and then the chosen legs are persisted as a proposal and
    sent to Telegram instead of being placed. It shares this function rather than getting
    its own precisely because the allocation is the interesting part -- a proposal built by
    a second code path would eventually offer a trade the autonomous bot would not make.

    In `propose_only` the return is 0.0: nothing has been committed yet, so a lower-priority
    bot must be free to size against the whole remaining margin. The capital is only
    genuinely spoken for once the user approves.
    """
    from icici_breeze_backend.app.services.bots import placement

    run_id = repo.start_run(user_id, BOT_HOLDINGS_WRITER, trigger)
    try:
        prefs = {p.stock_code: p for p in repo.list_scrip_prefs(user_id)}
        margin_source = proc.get_strategy_builder_margin_source(user_id)
        result = holdings_writer.scan(
            proc, user_id, config=config, prefs=prefs, margin_source=margin_source
        )
        skipped = [
            {"stock_code": s.stock_code, "reason_code": s.reason_code, "reason": s.reason}
            for s in result.skipped
        ]
        if not result.legs:
            repo.finish_run(
                run_id,
                status="skipped",
                reason_code=ReasonCode.NOTHING_ELIGIBLE,
                reason_text=f"Nothing writable across {len(skipped)} holding(s).",
                detail={"skipped": skipped},
            )
            return 0.0

        # An indicative premium is priced off the last trade because there is no order book.
        # Fine for planning on a Sunday; never something to sell into. The unattended path
        # fails closed on it exactly as the manual approval does.
        priceable = [leg for leg in result.legs if leg.premium_basis == "bid"]
        if not priceable:
            repo.finish_run(
                run_id,
                status="skipped",
                reason_code=ReasonCode.QUOTE_UNAVAILABLE,
                reason_text="No live bids — nothing was written.",
                detail={"skipped": skipped},
            )
            return 0.0

        available = _free_margin(proc, user_id)
        if available is None:
            repo.finish_run(
                run_id,
                status="failed",
                reason_code=ReasonCode.BROKER_ERROR,
                reason_text="Could not read available margin from the broker.",
            )
            return 0.0
        available = max(0.0, available - float(margin_committed))

        alloc = holdings_writer.allocate(
            priceable,
            free_margin=available,
            delivery_budget=config.delivery_cash_budget,
        )
        dropped = [
            {"stock_code": d.stock_code, "reason_code": d.reason_code, "reason": d.reason}
            for d in alloc.dropped
        ]
        chosen = [priceable[i] for i in alloc.selected]
        if not chosen:
            repo.finish_run(
                run_id,
                status="skipped",
                reason_code=ReasonCode.MARGIN_EXHAUSTED,
                reason_text=(
                    f"Nothing fitted the Rs {available:,.0f} of margin available"
                    + (f" after higher-priority bots" if margin_committed else "")
                    + "."
                ),
                detail={"skipped": skipped, "dropped": dropped},
            )
            return 0.0

        if propose_only:
            from icici_breeze_backend.app.services.bots import hitl

            totals = {
                "premium_total": alloc.premium_total,
                "span_total": round(sum(float(l.span_margin or 0) for l in chosen), 2),
                "elm_total": round(sum(float(l.elm_margin or 0) for l in chosen), 2),
                "delivery_exposure_total": alloc.delivery_used,
                "delivery_cash_budget": config.delivery_cash_budget,
                "leg_count": len(chosen),
                "selected_count": len(chosen),
            }
            hitl.propose(
                user_id,
                BOT_HOLDINGS_WRITER,
                run_id=run_id,
                legs=chosen,
                totals=totals,
                ttl_minutes=config.proposal_ttl_minutes,
                detail={"skipped": skipped, "dropped": dropped, **totals},
            )
            return 0.0

        results = placement.place_short_legs(
            proc,
            user_id,
            [leg.model_dump() for leg in chosen],
            tolerance_pct=float(cfg.AGGRESSIVE_LIMIT_DEFAULT_TOLERANCE_PCT),
        )
        ok = [r for r in results if r.ok]
        detail = {
            "skipped": skipped,
            "dropped": dropped,
            "margin_used": alloc.margin_used,
            "delivery_used": alloc.delivery_used,
            "premium_total": alloc.premium_total,
            "legs": [
                {
                    "stock_code": r.stock_code,
                    "right": r.right,
                    "strike_price": r.strike_price,
                    "quantity": r.quantity,
                    "limit_price": r.limit_price,
                    "order_ids": r.order_ids,
                    "error": r.error,
                }
                for r in results
            ],
        }
        repo.finish_run(
            run_id,
            status="completed" if len(ok) == len(results) else "failed",
            reason_code=ReasonCode.ORDERS_PLACED if ok else ReasonCode.ORDER_REJECTED,
            reason_text=(
                f"{len(ok)} of {len(results)} leg(s) placed for "
                f"Rs {alloc.premium_total:,.0f} of premium."
            ),
            detail=detail,
        )
        # Only what actually placed has taken margin. Reporting the full allocation would
        # make the next bot size against capital that was never committed.
        placed_codes = {(r.stock_code, r.right) for r in ok}
        return round(
            sum(
                float(leg.span_margin or 0) + float(leg.elm_margin or 0)
                for leg in chosen
                if (leg.stock_code, leg.right) in placed_codes
            ),
            2,
        )
    except holdings_writer.BotScanError as e:
        repo.finish_run(
            run_id, status="failed", reason_code=ReasonCode.BROKER_ERROR, reason_text=str(e)
        )
        return 0.0
    except Exception:  # noqa: BLE001
        _logger.exception("bot1: autonomous run failed for user=%s", user_id)
        repo.finish_run(
            run_id,
            status="failed",
            reason_code=ReasonCode.INTERNAL_ERROR,
            reason_text="The bot failed unexpectedly while writing. Check the Order Book.",
        )
        return 0.0


def _free_margin(proc: Any, user_id: str) -> Optional[float]:
    from icici_breeze_backend.app.services.bots import expiry_index_writer as bot2

    return bot2._available_margin(proc, user_id)
