"""Async parallel ICICI option-chain fetches with broker rate-limit defense."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

from icici_breeze_backend.app.services.processor import _expiry_display_to_api
from icici_breeze_backend.app.services.options_strategy_engine.helpers import quote_from_api
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, QuoteRow, Right
from icici_breeze_backend.app.services.options_strategy_engine.universe import record_ingested_strikes

DEFAULT_MAX_CALLS_PER_MINUTE = 100
DEFAULT_MAX_CONCURRENT = 10


class IciciCallBudget:
    """Rolling 60s window cap plus concurrency semaphore for ICICI API calls."""

    def __init__(
        self,
        *,
        max_per_minute: int = DEFAULT_MAX_CALLS_PER_MINUTE,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    ) -> None:
        self._max_per_minute = max(1, max_per_minute)
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent))
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def _wait_for_minute_slot(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] >= 60.0:
                    self._timestamps.popleft()
                if len(self._timestamps) < self._max_per_minute:
                    self._timestamps.append(now)
                    return
                wait_sec = 60.0 - (now - self._timestamps[0]) + 0.001
            await asyncio.sleep(max(wait_sec, 0.001))

    async def acquire(self) -> None:
        await self._semaphore.acquire()
        try:
            await self._wait_for_minute_slot()
        except Exception:
            self._semaphore.release()
            raise

    def release(self) -> None:
        self._semaphore.release()


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
    budget: IciciCallBudget,
    strike: int,
    right: Right,
    *,
    fetch_reason: str,
) -> tuple[tuple[int, Right], QuoteRow] | None:
    await budget.acquire()
    try:
        return await asyncio.to_thread(
            _sync_fetch_one,
            ctx,
            strike,
            right,
            fetch_reason=fetch_reason,
        )
    finally:
        budget.release()


async def fetch_strike_pairs_async(
    ctx: EngineContext,
    pairs: set[tuple[int, Right]],
    *,
    fetch_reason: str = "Targeted wing/short strike outside bulk chain cache",
    max_per_minute: int = DEFAULT_MAX_CALLS_PER_MINUTE,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> dict[tuple[int, Right], QuoteRow]:
    if not pairs:
        return {}

    budget = IciciCallBudget(max_per_minute=max_per_minute, max_concurrent=max_concurrent)
    if ctx.audit:
        ctx.audit.record(
            "quote_fetch_batch",
            f"Async fetch of {len(pairs)} targeted option quote(s)",
            {
                "reason": fetch_reason,
                "pairs": [{"strike": s, "right": r} for s, r in sorted(pairs)],
                "max_per_minute": max_per_minute,
                "max_concurrent": max_concurrent,
            },
            rationale=fetch_reason,
        )

    results = await asyncio.gather(
        *[
            _fetch_one_pair(ctx, budget, strike, right, fetch_reason=fetch_reason)
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
