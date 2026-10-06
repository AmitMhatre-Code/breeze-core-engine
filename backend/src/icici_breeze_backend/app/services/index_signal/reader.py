"""The one way to read a signal -- for the navbar, the Signals page and the bots.

Reads the payload `publisher` put in Redis, so every process gets the same answer. The payload's
own `valid_until` is judged here, not the key's TTL: a publisher that stopped must read as
`unavailable` within a few intervals, never as the last verdict it happened to write. Consumers
treat anything but "bullish"/"bearish" as no directional trade -- `unavailable` is never
`neutral` (#30).
"""
from __future__ import annotations

import datetime
import time
from typing import Any, Optional, Union

from icici_breeze_backend.app.db.redis_client import cache_get_json
from icici_breeze_backend.app.services.index_signal.mechanisms import (
    INDICES,
    NAVBAR_DURATION,
    SeriesKey,
    all_keys,
)
from icici_breeze_backend.app.services.index_signal.series import apply_direction
from icici_breeze_backend.app.services.index_signal.states import (
    REASON_NOT_PUBLISHED,
    REASON_STALE,
    REASON_WITHDRAWN,
)
from icici_breeze_backend.app.services.reference_data.keys import signal_series_key

LABELS: tuple[str, ...] = INDICES


def _missing(key: SeriesKey, reason: str) -> dict[str, Any]:
    return {
        "key": key.id,
        "index": key.index,
        "label": key.index,
        "mechanism": key.mechanism,
        "duration_minutes": key.duration,
        "version": key.version,
        "state": "unavailable",
        "reason": reason,
        "signal": None,
    }


def get_signal(
    key: Union[SeriesKey, str], *, direction: str = "follow", now: Optional[float] = None
) -> dict[str, Any]:
    """One series' current reading, turned the bot's way (`follow` or `fade`)."""
    series = key if isinstance(key, SeriesKey) else SeriesKey.parse(key)
    if not series.published:
        return apply_direction(_missing(series, REASON_WITHDRAWN), direction)
    try:
        payload = cache_get_json(signal_series_key(series.id))
    except Exception:  # noqa: BLE001 -- Redis unreachable is no reading, never a crash
        payload = None
    if not isinstance(payload, dict):
        return apply_direction(_missing(series, REASON_NOT_PUBLISHED), direction)
    ts = time.time() if now is None else now
    try:
        valid_until = float(payload.get("valid_until") or 0.0)
    except (TypeError, ValueError):
        valid_until = 0.0
    if ts > valid_until:
        payload = {**payload, "state": "unavailable", "reason": REASON_STALE,
                   "call_started_at": None, "held_until": None}
    return apply_direction(payload, direction)


def rollover_standdown(key: Union[SeriesKey, str], day: datetime.date) -> Optional[datetime.date]:
    """The futures expiry a series stands down for on `day`, or None when it reads normally.

    Only a reading that uses open interest stands down (#34), and it does so for the whole day,
    so this is known before the first bar -- unlike the reading's own `excluded_session` reason,
    which appears only inside 09:15-15:15. Never raises: a calendar problem is "not known",
    and the reading itself still stands down on its own."""
    try:
        series = key if isinstance(key, SeriesKey) else SeriesKey.parse(key)
        if not series.uses_oi:
            return None
        from icici_breeze_backend.app.services.index_signal.series import rollover_expiry

        return rollover_expiry(day)
    except Exception:  # noqa: BLE001
        return None


def get_signals(*, now: Optional[float] = None) -> dict[str, dict[str, Any]]:
    """Every series, keyed by id."""
    return {key.id: get_signal(key, now=now) for key in all_keys()}


# The slim view the navbar chip needs, so the ticker poll stays small.
_NAVBAR_FIELDS = (
    "state", "reason", "signal", "mechanism", "version", "duration_minutes", "computed_at",
    "thin_data", "uses_oi", "call_started_at", "held_until",
)


def navbar_view(*, now: Optional[float] = None) -> dict[str, dict[str, Any]]:
    """The chosen mechanism's 15-minute reading per index (decision 11), at the newest version
    bots may trade (`gate.navbar_version`). Never raises: a signal problem must not take the
    price ticker down with it. A version withdrawn for an index (Momentum v3 on SENSEX) shows
    that index's legacy version rather than a blank chip."""
    from icici_breeze_backend.app.services.index_signal.gate import navbar_version
    from icici_breeze_backend.app.services.index_signal.mechanisms import LEGACY_VERSIONS
    from icici_breeze_backend.app.services.index_signal.settings import navbar_mechanism

    try:
        mechanism = navbar_mechanism()
    except Exception:  # noqa: BLE001
        mechanism = "expansion"
    try:
        version = navbar_version(mechanism)
    except Exception:  # noqa: BLE001
        version = LEGACY_VERSIONS[mechanism]
    out: dict[str, dict[str, Any]] = {}
    for index in INDICES:
        key = SeriesKey(mechanism, NAVBAR_DURATION, index, version)
        if not key.published:
            key = SeriesKey(mechanism, NAVBAR_DURATION, index)
        try:
            payload = get_signal(key, now=now)
        except Exception:  # noqa: BLE001
            payload = _missing(key, REASON_NOT_PUBLISHED)
        out[index] = {field: payload.get(field) for field in _NAVBAR_FIELDS}
    return out
