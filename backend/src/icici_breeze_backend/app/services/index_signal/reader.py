"""The one way to read the index signal -- for the navbar, any other screen, and the bots.

Reads the payload `publisher` put in Redis, so it gives the same answer in every process. The
payload's own `valid_until` is judged here, not the key's TTL: a publisher that stopped (crashed
loop, wedged thread) must read as `unavailable` within a few intervals, never as the last verdict
it happened to write. Consumers must treat anything but "bullish"/"bearish" as *no directional
trade* -- in particular `unavailable` is not `neutral` (docs/design-decisions.md #30).
"""
from __future__ import annotations

import time
from typing import Any

from icici_breeze_backend.app.db.redis_client import cache_get_json
from icici_breeze_backend.app.services.reference_data.keys import index_signal_key

LABELS: tuple[str, ...] = ("nifty", "sensex")


def _unavailable(label: str, reason: str) -> dict[str, Any]:
    return {"label": label, "state": "unavailable", "reason": reason, "signal": None}


def get_index_signal(label: str, *, now: float | None = None) -> dict[str, Any]:
    key = str(label or "").strip().lower()
    if key not in LABELS:
        raise ValueError(f"unknown index label: {label!r}")
    payload = cache_get_json(index_signal_key(key))
    if not isinstance(payload, dict):
        return _unavailable(key, "not_published")
    ts = time.time() if now is None else now
    try:
        valid_until = float(payload.get("valid_until") or 0.0)
    except (TypeError, ValueError):
        valid_until = 0.0
    if ts > valid_until:
        stale = dict(payload)
        stale.update(state="unavailable", reason="stale")
        return stale
    return payload


def get_index_signals(*, now: float | None = None) -> dict[str, dict[str, Any]]:
    return {label: get_index_signal(label, now=now) for label in LABELS}


def index_signal_state(label: str, *, now: float | None = None) -> str:
    """Just the state: "bullish", "bearish", "neutral" or "unavailable"."""
    return str(get_index_signal(label, now=now).get("state") or "unavailable")


# `mechanism` says which reading this is (#34): the chip must not describe a volume-expansion
# strength as an order-book imbalance, and the two carry differently shaped `thresholds`.
_NAVBAR_FIELDS = ("state", "reason", "signal", "coverage", "thresholds", "computed_at", "mechanism")


def navbar_view(*, now: float | None = None) -> dict[str, dict[str, Any]]:
    """The slim per-index view the navbar chip needs -- no constituent rows, so the ticker poll
    stays small. Never raises: a signal problem must not take the price ticker down with it."""
    out: dict[str, dict[str, Any]] = {}
    for label in LABELS:
        try:
            payload = get_index_signal(label, now=now)
        except Exception:  # noqa: BLE001 -- e.g. Redis unreachable
            payload = _unavailable(label, "not_published")
        view = {field: payload.get(field) for field in _NAVBAR_FIELDS}
        weights = payload.get("weights") if isinstance(payload.get("weights"), dict) else {}
        view["weights_source"] = weights.get("source")
        view["weights_as_of"] = weights.get("as_of")
        out[label] = view
    return out
