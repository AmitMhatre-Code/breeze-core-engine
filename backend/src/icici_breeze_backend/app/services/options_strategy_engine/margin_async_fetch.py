"""Async parallel margin_calculator fetches (transport limiter handles pacing)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from icici_breeze_backend.app.services.options_strategy_engine.build_progress import BuildProgress
from icici_breeze_backend.app.services.options_strategy_engine.helpers import parse_float


@dataclass(frozen=True)
class MarginFetchRequest:
    cache_key: tuple
    margin_input: list
    strategy_id: str
    phase: str | None = None
    audit_rationale: str | None = None


def _sync_fetch_margin(
    proc: Any,
    user_id: str,
    exchange_code: str,
    request: MarginFetchRequest,
    *,
    audit: Any | None,
) -> dict[str, Any]:
    audit_context: dict[str, Any] = {
        "strategy_id": request.strategy_id,
        "legs": request.margin_input,
    }
    if request.phase:
        audit_context["phase"] = request.phase
    kwargs: dict[str, Any] = {
        "audit": audit,
        "audit_context": audit_context,
    }
    if request.audit_rationale:
        kwargs["audit_rationale"] = request.audit_rationale
    return proc.strategy_builder_margin(
        user_id,
        exchange_code,
        request.margin_input,
        **kwargs,
    )


def _span_from_response(res: dict[str, Any]) -> float:
    status = res.get("Status")
    if status is not None:
        try:
            if int(status) != 200:
                return 0.0
        except (TypeError, ValueError):
            return 0.0
    return parse_float((res.get("Success") or {}).get("span_margin_required"))


async def _fetch_one_margin(
    proc: Any,
    user_id: str,
    exchange_code: str,
    request: MarginFetchRequest,
    *,
    audit: Any | None,
) -> tuple[tuple, float]:
    try:
        res = await asyncio.to_thread(
            _sync_fetch_margin,
            proc,
            user_id,
            exchange_code,
            request,
            audit=audit,
        )
        span = _span_from_response(res)
        if span <= 0 and audit:
            err = str(res.get("Error") or "non-200 margin response")
            audit.record(
                "margin_fetch_error",
                f"Margin fetch failed ({request.strategy_id})",
                {
                    "strategy_id": request.strategy_id,
                    "phase": request.phase,
                    "status": res.get("Status"),
                    "error": err,
                },
                rationale="Isolated margin fetch failure; other batch tasks continue.",
            )
        return request.cache_key, span
    except Exception as exc:
        if audit:
            audit.record(
                "margin_fetch_error",
                f"Margin fetch exception ({request.strategy_id})",
                {
                    "strategy_id": request.strategy_id,
                    "phase": request.phase,
                    "error": str(exc),
                },
                rationale="Isolated margin fetch failure; other batch tasks continue.",
            )
        return request.cache_key, 0.0


async def fetch_margins_concurrent(
    proc: Any,
    user_id: str,
    exchange_code: str,
    requests: list[MarginFetchRequest],
    *,
    audit: Any | None = None,
    existing_cache: dict[tuple, float] | None = None,
    progress: BuildProgress | None = None,
) -> dict[tuple, float]:
    """Fetch SPAN margins concurrently; return cache_key -> span for all requests."""
    if not requests:
        return {}

    cache = existing_cache or {}
    pending = [req for req in requests if req.cache_key not in cache]
    cached_count = len(requests) - len(pending)
    if audit and hasattr(audit, "telemetry"):
        audit.telemetry.record_span_cache(hit=True, count=cached_count)
        audit.telemetry.record_span_cache(hit=False, count=len(pending))

    if not pending:
        return {req.cache_key: cache[req.cache_key] for req in requests}

    if audit:
        audit.record(
            "margin_fetch_batch",
            f"Async fetch of {len(pending)} margin_calculator call(s)",
            {
                "count": len(pending),
                "strategy_ids": sorted({r.strategy_id for r in pending}),
                "phases": sorted({r.phase for r in pending if r.phase}),
            },
            rationale="Concurrent SPAN margin batch.",
        )

    total = len(pending)
    tasks = [
        asyncio.create_task(
            _fetch_one_margin(proc, user_id, exchange_code, req, audit=audit)
        )
        for req in pending
    ]

    spans: dict[tuple, float] = {}
    done = 0
    for coro in asyncio.as_completed(tasks):
        key, span = await coro
        spans[key] = span
        done += 1
        if progress is not None:
            progress.tick(
                phase="margins",
                message=f"Calculating margins ({done}/{total})…",
            )

    out: dict[tuple, float] = {}
    for req in requests:
        if req.cache_key in cache:
            out[req.cache_key] = cache[req.cache_key]
        else:
            out[req.cache_key] = spans.get(req.cache_key, 0.0)
    return out
