"""India VIX one-minute series for Bot 4's `vix_not_rising` entry filter (#38).

One pure rule -- how much VIX rose over the last N minutes -- judged on the same kind of series
live and in a backtest: ICICI's 1-minute INDVIX bars. Live reads today's bars from
`get_historical_data_v2` (restart-proof, and exactly the series a replay reads from the cache);
the backtest reads them from `spot_candles` under the `INDVIX` code.

Cost: one ICICI call a minute at most, and only on a pass where the fly is flat and would
otherwise enter -- the caller asks here last, after every cheaper gate has already said go.

Unverified against the live broker when written (2026-09-19): that ICICI serves INDVIX at
1-minute granularity. If it does not, the filter reads "no VIX series" and holds the entry,
which is the fail-closed reading, and the backtest reports the missing bars.
"""
from __future__ import annotations

import datetime
import logging
import threading
import time
from bisect import bisect_right
from typing import Any, Optional, Sequence

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.domain.bots import IronFlyEntryFilterConfig, ReasonCode

_logger = logging.getLogger(__name__)

STOCK_CODE = "INDVIX"
EXCHANGE = "NSE"
# The reference reading may be this much older than `lookback` before it is too old to use:
# ICICI skips a minute now and then, and a one-bar gap should not blind the filter.
_REFERENCE_SLACK_SECONDS = 180.0
# Newest bar older than this is no current reading at all.
_STALE_SECONDS = 300.0
_REFRESH_SECONDS = 60.0

Series = Sequence[tuple[float, float]]  # (bar start epoch, close), ascending

_lock = threading.Lock()
_cache: dict[str, tuple[float, datetime.date, list[tuple[float, float]]]] = {}


def change_pct(series: Series, now: float, lookback_minutes: int) -> tuple[Optional[float], dict[str, Any]]:
    """(% change of VIX over the last `lookback_minutes`, detail). None when it cannot be told.

    Bars are keyed by their START, and a bar is only known once it has closed, so the latest
    usable bar is the one that started at least a minute before `now`."""
    times = [t for t, _ in series]
    i = bisect_right(times, now - 60.0) - 1
    if i < 0:
        return None, {"reason": "no_vix_bars"}
    t_now, v_now = series[i]
    if now - (t_now + 60.0) > _STALE_SECONDS:
        return None, {"reason": "vix_stale", "last_bar_at": t_now}
    target = t_now - lookback_minutes * 60.0
    j = bisect_right(times, target) - 1
    if j < 0 or target - series[j][0] > _REFERENCE_SLACK_SECONDS:
        return None, {"reason": "vix_history_short", "last_bar_at": t_now}
    t_ref, v_ref = series[j]
    if v_ref <= 0:
        return None, {"reason": "vix_unusable"}
    pct = (v_now - v_ref) / v_ref * 100.0
    return pct, {"vix": v_now, "vix_ref": v_ref, "ref_at": t_ref, "change_pct": round(pct, 2)}


def filter_hold(
    config: IronFlyEntryFilterConfig, series: Series, now: float
) -> Optional[tuple[str, str]]:
    """The `vix_not_rising` verdict: None means go. Fails closed on an unreadable series."""
    pct, detail = change_pct(series, now, config.vix_lookback_minutes)
    if pct is None:
        return (
            ReasonCode.ENTRY_FILTER_CLOSED,
            f"VIX filter: no usable India VIX series ({detail.get('reason')}); holding off.",
        )
    if pct > config.vix_max_rise_pct:
        return (
            ReasonCode.ENTRY_FILTER_CLOSED,
            f"VIX filter: India VIX up {pct:.2f}% over {config.vix_lookback_minutes} min "
            f"({detail['vix_ref']:.2f} → {detail['vix']:.2f}), above the "
            f"{config.vix_max_rise_pct:.2f}% limit.",
        )
    return None


def series_from_candles(candles: Sequence[Any]) -> list[tuple[float, float]]:
    """Cached `HistCandle`s (naive IST starts) as a series."""
    return [(c.ts.replace(tzinfo=IST).timestamp(), float(c.close)) for c in candles if c.close > 0]


def live_series(proc: Any, user_id: str, *, now: Optional[float] = None) -> list[tuple[float, float]]:
    """Today's INDVIX minute bars, refreshed at most once a minute. [] when unavailable."""
    ts = time.time() if now is None else now
    today = datetime.datetime.fromtimestamp(ts, IST).date()
    with _lock:
        hit = _cache.get(user_id)
    if hit is not None and hit[1] == today and ts - hit[0] < _REFRESH_SECONDS:
        return list(hit[2])
    series = _fetch_today(proc, user_id, today)
    if series or hit is None or hit[1] != today:
        with _lock:
            _cache[user_id] = (ts, today, series)
        return list(series)
    # A failed refresh keeps the last good series; `change_pct` judges its staleness.
    return list(hit[2])


def _fetch_today(proc: Any, user_id: str, today: datetime.date) -> list[tuple[float, float]]:
    from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
    from icici_breeze_backend.app.services.bots.scalping.backtest_fetch import parse_response

    try:
        breeze = proc.get_session_breeze(user_id)
        if breeze is None:
            return []
        response = breeze.get_historical_data_v2(
            interval="1minute",
            from_date=f"{today.isoformat()}T00:00:00.000Z",
            to_date=f"{today.isoformat()}T23:59:59.000Z",
            stock_code=STOCK_CODE,
            exchange_code=EXCHANGE,
            product_type="cash",
        )
    except Exception:  # noqa: BLE001 -- an unreadable series holds the entry; never raise
        _logger.warning("vix filter: INDVIX minute bars fetch failed", exc_info=True)
        return []
    rows, error = parse_response(response)
    if error:
        _logger.warning("vix filter: INDVIX minute bars refused: %s", error)
        return []
    out: list[tuple[float, float]] = []
    for row in rows:
        at = store._parse_ts(row.get("datetime") or row.get("date"))
        try:
            close = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        if at is not None and close > 0:
            out.append((at.replace(tzinfo=IST).timestamp(), close))
    out.sort()
    return out


def reset_state_for_tests() -> None:
    with _lock:
        _cache.clear()
