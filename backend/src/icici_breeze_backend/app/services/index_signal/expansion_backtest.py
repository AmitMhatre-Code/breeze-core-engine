"""Replay the expansion mechanism over stored history and score it exactly as live (#34).

This is the whole point of the mechanism: it reads price, volume and open interest, none of
which needs an order book or a quote, so months of `get_historical_data_v2` bars can judge it in
an afternoon instead of accumulating live shadow sessions two days at a time (#33).

How it reuses the live scoring rather than growing a second one
---------------------------------------------------------------
The replay writes its readings into the **same** `index_signal_log` the live publisher writes
to, under a `<index>:expansion:backtest` label. `shadow_log.shadow_report` and
`shadow_log.readiness` then judge it with the identical fixed test -- same forward horizons,
same breakeven from the Trading Costs model, same minimum-sample gates. A backtest that scored
itself would be free to be kinder than the live report, which is exactly the failure #33's
"one fixed readiness test, decided in advance" rule exists to prevent.

What the level is
-----------------
Forward moves are measured on the **futures close**, the same series the bars come from, rather
than the cash index. Basis drifts slowly against spot intraday, so a move measured within one
series is right to within that drift, and mixing two series would introduce a difference that
is not the signal's doing. Live readings use the index level, so the two are not interchangeable
to the last basis point -- the verdict, which is about direction over 5 and 15 minutes, is.
"""
from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Optional

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.index_signal import expansion, shadow_log

_logger = logging.getLogger(__name__)

BACKTEST_SUFFIX = ":backtest"
# The continuous session. Pre-open carries OI 0 and the closing auction (from 2026-08-03, F&O
# stocks auction 15:15-15:35) is not continuous trading, so neither is a reading.
SESSION_START = datetime.time(9, 15)
SESSION_END = datetime.time(15, 15)


def backtest_label(label: str) -> str:
    """`nifty` -> `nifty:expansion:backtest`.

    Its own label, so a replay never pollutes the live evidence the readiness gate counts."""
    from icici_breeze_backend.app.services.index_signal.publisher import MECHANISM_SUFFIX

    return f"{label}{MECHANISM_SUFFIX}{BACKTEST_SUFFIX}"


LABELS = ("nifty", "sensex")
STOCK_CODES = {"nifty": "NIFTY", "sensex": "BSESEN"}
# The last backtest's range and replay summaries, so its results outlive the page and a restart.
LAST_RUN_META = "index_signal_expansion_last_backtest"


def _stock_code(label: str) -> str:
    return STOCK_CODES[label]


def score(label: str, start: datetime.date, end: datetime.date, *, db_path: Optional[str] = None) -> dict[str, Any]:
    """The live readiness test over exactly the replayed range -- not the 60 days before today,
    which would score an older range as empty."""
    now = datetime.datetime.combine(end + datetime.timedelta(days=1), datetime.time()).replace(tzinfo=IST)
    return shadow_log.readiness(
        backtest_label(label),
        now=now.timestamp(),
        lookback_days=(end - start).days + 2,
        db_path=db_path,
    )


def save_last_run(run: dict[str, Any], *, cache_path: Optional[str] = None) -> None:
    from icici_breeze_backend.app.services.bots.scalping import backtest_store as store

    store.ensure_tables(cache_path)
    store.set_meta(LAST_RUN_META, json.dumps(run), path=cache_path)


def last_run(*, cache_path: Optional[str] = None, db_path: Optional[str] = None) -> Optional[dict[str, Any]]:
    """The last backtest with each index's verdict, or None when none has run."""
    from icici_breeze_backend.app.services.bots.scalping import backtest_store as store

    store.ensure_tables(cache_path)
    raw = store.get_meta(LAST_RUN_META, path=cache_path)
    if not raw:
        return None
    try:
        run = json.loads(raw)
        start = datetime.date.fromisoformat(run["from"])
        end = datetime.date.fromisoformat(run["to"])
    except (ValueError, KeyError, TypeError):
        return None
    # The flip list counts days back from today, so it must reach the start of the range.
    run["flip_days"] = max(1, (datetime.datetime.now(IST).date() - start).days + 1)
    for label in LABELS:
        entry = (run.get("indices") or {}).get(label)
        if entry and entry.get("summary", {}).get("readings"):
            entry["readiness"] = score(label, start, end, db_path=db_path)
    return run


def replay(
    label: str,
    *,
    from_date: Optional[datetime.date] = None,
    to_date: Optional[datetime.date] = None,
    params: Optional[expansion.ExpansionParams] = None,
    cache_path: Optional[str] = None,
    db_path: Optional[str] = None,
    rollover_expiries: Optional[set[datetime.date]] = None,
) -> dict[str, Any]:
    """Replay stored bars through a fresh engine and log every reading. Returns a summary.

    Bars are fed in order, so the percentile baseline builds exactly as it does live, including
    carrying across sessions -- windows straddling the overnight break are dropped by the engine
    itself, not by this caller.
    """
    from icici_breeze_backend.app.services.bots.scalping import backtest_store as store

    from_label = backtest_label(label)
    # A cache that has never been fetched is the ordinary first-run state, not an error.
    store.ensure_tables(cache_path)
    bars = store.load_candles(
        stock_code=_stock_code(label), from_date=from_date, to_date=to_date, path=cache_path
    )
    if not bars:
        return {
            "label": from_label,
            "bars": 0,
            "readings": 0,
            "days": 0,
            "verdict": "no_data",
            "message": "No ICICI history is stored for this range.",
        }

    engine = expansion.ExpansionEngine(
        label, params or expansion.ExpansionParams(require_oi=_requires_oi(label))
    )
    shadow_log.purge_label(from_label, db_path=db_path)

    rollover = rollover_expiries or set()
    states: dict[str, int] = {}
    readings = 0
    days: set[datetime.date] = set()
    for candle in bars:
        moment = candle.ts
        if not SESSION_START <= moment.time() <= SESSION_END:
            continue
        ts = moment.replace(tzinfo=IST).timestamp()
        engine.on_bar(
            expansion.Bar(ts=ts, close=candle.close, volume=candle.volume, oi=candle.oi)
        )
        excluded = any(
            expansion.in_rollover_window(moment.date(), expiry) for expiry in rollover
        )
        snap = engine.snapshot(ts, session_open=True, excluded=excluded)
        states[snap["state"]] = states.get(snap["state"], 0) + 1
        readings += 1
        days.add(moment.date())
        # The futures close is the level (see the module docstring).
        shadow_log.record(from_label, snap, spot=candle.close, now=ts, db_path=db_path)

    return {
        "label": from_label,
        "bars": len(bars),
        "readings": readings,
        "days": len(days),
        "from": min(days).isoformat() if days else None,
        "to": max(days).isoformat() if days else None,
        "states": states,
        "directional_pct": round(
            100.0 * (states.get("bullish", 0) + states.get("bearish", 0)) / readings, 1
        )
        if readings
        else 0.0,
    }


def _requires_oi(label: str) -> bool:
    # SENSEX cannot read OI at all: ICICI serves none for BSE (#34).
    return label != "sensex"
