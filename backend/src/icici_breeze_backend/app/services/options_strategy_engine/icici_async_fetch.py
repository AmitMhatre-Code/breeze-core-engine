"""Async parallel ICICI option-chain fetches (transport limiter handles pacing)."""
from __future__ import annotations

import asyncio
from typing import Any

from icici_breeze_backend.app.services.processor import _expiry_display_to_api
from icici_breeze_backend.app.services.options_strategy_engine.helpers import quote_from_api
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, QuoteRow, Right
from icici_breeze_backend.app.services.options_strategy_engine.universe import record_ingested_strikes


def _sync_fetch_one(
    ctx: EngineContext,
    strike: int,
    right: Right,
    *,
    fetch_reason: str,
) -> tuple[tuple[int, Right], QuoteRow] | None:
    if ctx.chain_backoff is None:
        return None
    expiry_api = _expiry_display_to_api(ctx.expiry_display)
    quote = ctx.processor.fetch_option_chain_quotes_sb(
        ctx.user_id,
        ctx.stock_code,
        ctx.exchange_code,
        expiry_api,
        right,
        strike_price=str(strike),
        audit=ctx.audit,
        audit_rationale=fetch_reason,
        backoff=ctx.chain_backoff,
    )
    if quote.get("Status") != 200:
        return None
    rows = quote.get("Success") or []
    if not rows:
        return None
    return (strike, right), quote_from_api(strike, right, rows[0])


async def _fetch_one_pair(
    ctx: EngineContext,
    strike: int,
    right: Right,
    *,
    fetch_reason: str,
) -> tuple[tuple[int, Right], QuoteRow] | None:
    return await asyncio.to_thread(
        _sync_fetch_one,
        ctx,
        strike,
        right,
        fetch_reason=fetch_reason,
    )


async def fetch_strike_pairs_async(
    ctx: EngineContext,
    pairs: set[tuple[int, Right]],
    *,
    fetch_reason: str = "Targeted wing/short strike outside bulk chain cache",
    max_per_minute: int = 100,
    max_concurrent: int = 10,
) -> dict[tuple[int, Right], QuoteRow]:
    del max_per_minute, max_concurrent  # pacing handled globally at transport layer
    if not pairs:
        return {}

    if ctx.audit:
        ctx.audit.record(
            "quote_fetch_batch",
            f"Async fetch of {len(pairs)} targeted option quote(s)",
            {
                "reason": fetch_reason,
                "pairs": [{"strike": s, "right": r} for s, r in sorted(pairs)],
            },
            rationale=fetch_reason,
        )

    results = await asyncio.gather(
        *[
            _fetch_one_pair(ctx, strike, right, fetch_reason=fetch_reason)
            for strike, right in sorted(pairs)
        ]
    )

    ingested: dict[tuple[int, Right], QuoteRow] = {}
    for item in results:
        if item is not None:
            key, row = item
            ingested[key] = row

    record_ingested_strikes(ctx.audit, ingested, context=fetch_reason)
    return ingested
