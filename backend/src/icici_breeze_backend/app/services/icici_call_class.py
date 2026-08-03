"""How important is this outbound ICICI call?

The budget problem is not that the app makes too many broker calls in the abstract — it
is that when the budget runs short, the calls that lose are chosen at random. On
2026-07-31 an advisory hazard banner spent 4236 of 4730 daily calls; `place_order` used
51. Without a way to tell those apart, every future polling regression can starve order
placement again, and each one has to be found and fixed individually forever.

`critical`  — placing or retrying an exit order, foreign-order reconcile for an armed SG,
              position composition refresh for a live SG. Losing one of these can leave a
              position unhedged or a rule silently inert.
`advisory`  — hazard/orphan banner detail, badge decoration, history views. Losing one
              degrades a display until the next poll.

**Unmarked calls are `critical`.** That default is deliberate and is the fail-safe
direction: shedding is opt-in, so a call nobody has classified keeps working exactly as it
does today. The opposite default would silently start dropping unaudited call sites the
first time a user approached their cap — a far worse failure than spending a few calls we
could have saved.

The class travels in a ContextVar rather than a parameter because enforcement lives in the
`requests` transport monkeypatch (`app/core/requests_patch.py`), which cannot see its
caller's arguments. `contextvars` propagate across `asyncio.to_thread`, so the P&L engine's
worker threads inherit whatever the dispatching code set.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

CRITICAL = "critical"
ADVISORY = "advisory"

_call_class_ctx: ContextVar[str] = ContextVar("icici_call_class", default=CRITICAL)


def current_call_class() -> str:
    return _call_class_ctx.get() or CRITICAL


def is_advisory() -> bool:
    return current_call_class() == ADVISORY


@contextmanager
def advisory_calls() -> Iterator[None]:
    """Mark every ICICI call made in this block as sheddable under budget pressure."""
    token = _call_class_ctx.set(ADVISORY)
    try:
        yield
    finally:
        _call_class_ctx.reset(token)


@contextmanager
def critical_calls() -> Iterator[None]:
    """Force critical classification inside an otherwise-advisory block.

    Needed because advisory scopes nest: an advisory request handler may still have to
    make one call that genuinely must not be shed.
    """
    token = _call_class_ctx.set(CRITICAL)
    try:
        yield
    finally:
        _call_class_ctx.reset(token)
