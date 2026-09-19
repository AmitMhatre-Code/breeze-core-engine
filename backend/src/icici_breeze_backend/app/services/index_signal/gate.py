"""The 30-day gate: a signal is available to bots once a backtest has covered 30 days (decision 7).

Because every signal is replayable from ICICI history, live sessions are never counted. What a bot
needs before trading on a series is one completed signal backtest whose date range spans at least
30 calendar days, run on the series' current mechanism version. Coverage only: data gaps inside the
range are allowed, and the result need not show an edge -- the verdict sits beside the gate on the
Signals page and in each bot's comparison, for the user to judge.

One backtest run replays every series, so in practice one 30-day run opens the whole grid, and a
change to a mechanism's formula (a version bump) closes that mechanism until it is re-run.

Enforced when a bot config is saved, when a bot is armed, and on every pass of a running bot.
Backtests are never gated.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional, Union

from icici_breeze_backend.app.services.index_signal.mechanisms import MECHANISMS, VERSIONS, SeriesKey

GATE_DAYS = 30
_TTL_SECONDS = 30.0

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _qualifying(mechanism: str, runs: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    for run in runs:  # newest first
        versions = run.get("versions") or {}
        if versions.get(mechanism) != VERSIONS[mechanism]:
            continue
        if (run.get("range_days") or 0) >= GATE_DAYS:
            return run
    return None


def _longest(mechanism: str, runs: list[dict[str, Any]]) -> int:
    return max(
        ((r.get("range_days") or 0) for r in runs if (r.get("versions") or {}).get(mechanism) == VERSIONS[mechanism]),
        default=0,
    )


def mechanism_availability(mechanism: str, *, db_path: Optional[str] = None, fresh: bool = False) -> dict[str, Any]:
    """{available, reason, run_id, from, to, range_days, longest_days, version}."""
    if mechanism not in MECHANISMS:
        raise ValueError(f"unknown signal mechanism {mechanism!r}")
    now = time.monotonic()
    cache_key = f"{db_path}:{mechanism}"
    with _lock:
        held = _cache.get(cache_key)
        if not fresh and held is not None and now - held[0] < _TTL_SECONDS:
            return held[1]
    from icici_breeze_backend.app.services.index_signal.backtest import completed_runs

    runs = completed_runs(db_path=db_path)
    run = _qualifying(mechanism, runs)
    if run is not None:
        out = {
            "available": True,
            "reason": None,
            "run_id": run["id"],
            "from": run["from_date"],
            "to": run["to_date"],
            "range_days": run["range_days"],
            "longest_days": run["range_days"],
            "version": VERSIONS[mechanism],
        }
    else:
        longest = _longest(mechanism, runs)
        out = {
            "available": False,
            "reason": (
                f"Needs a signal backtest covering at least {GATE_DAYS} days"
                + (f" (the longest so far covers {longest})." if longest else ".")
                + " Run one on the Signals page."
            ),
            "run_id": None,
            "from": None,
            "to": None,
            "range_days": None,
            "longest_days": longest,
            "version": VERSIONS[mechanism],
        }
    with _lock:
        _cache[cache_key] = (now, out)
    return out


def is_available(key: Union[SeriesKey, str], *, db_path: Optional[str] = None) -> bool:
    mechanism = key.mechanism if isinstance(key, SeriesKey) else str(key)
    try:
        return bool(mechanism_availability(mechanism, db_path=db_path)["available"])
    except Exception:  # noqa: BLE001 -- fail closed: an unreadable gate is a closed gate
        return False


def refusal(mechanism: str, *, db_path: Optional[str] = None) -> Optional[str]:
    """None when the mechanism is available to bots, else the reason to show the user."""
    try:
        info = mechanism_availability(mechanism, db_path=db_path)
    except Exception:  # noqa: BLE001
        return "The signal backtest record could not be read, so no bot may trade on a signal."
    return None if info["available"] else info["reason"]


def invalidate() -> None:
    with _lock:
        _cache.clear()
