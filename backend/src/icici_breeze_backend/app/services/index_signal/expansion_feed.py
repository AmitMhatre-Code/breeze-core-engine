"""One-minute bars for the expansion mechanism, built from F&O quote ticks (#34).

Pure: no I/O, no clock of its own, no broker. `publisher` owns the tick subscription and hands
ticks here; this only turns them into completed bars.

Why not reuse `scalping/candles.CandleBuilder`
----------------------------------------------
That builder feeds the live scalpers and carries VWAP, turnover and a cross-check the bots
depend on -- but it does not track open interest, and adding a field to it would put the
expansion mechanism inside the code path that decides real orders. This accumulator is the
cheaper half of it, plus OI, and nothing else reads it.

Volume: difference the cumulative counter, never sum `ltq`
----------------------------------------------------------
An NSE F&O tick carries `ttq` (day-cumulative traded quantity). Differencing it self-corrects
after a dropped tick -- the next tick carries the whole day's total -- while summing per-tick
quantities understates the bar permanently, and the pipeline drops on a full queue by design.
A counter that runs *backwards* is the day rolling over (or a stale packet), so the baseline
restarts and that bar's volume is unknown rather than negative.

Open interest: last reading in the bucket, and zero means absent
----------------------------------------------------------------
ICICI serves `OI: 0` on pre-open bars and on every BSE contract (#34), so a non-positive
reading is stored as None. `CHNGOI` is deliberately ignored: it arrives as an empty string on
some contracts, so the absolute `OI` is differenced instead -- the same lesson as `ttq`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from icici_breeze_backend.app.services.index_signal.expansion import Bar

BUCKET_SECONDS = 60


def _num(raw: Any) -> Optional[float]:
    """A finite number, or None. Empty strings are real in these payloads (`"CHNGOI": ""`)."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def parse_quote(payload: Any) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """(last price, cumulative traded quantity, open interest) from an F&O quote tick."""
    if not isinstance(payload, dict):
        return None, None, None
    oi = _num(payload.get("OI"))
    return _num(payload.get("last")), _num(payload.get("ttq")), (oi if oi and oi > 0 else None)


@dataclass
class _Bucket:
    start: int
    close: float
    ttq_open: Optional[float]
    ttq_last: Optional[float]
    oi_last: Optional[float]


class BarAccumulator:
    """Turns ticks into completed one-minute bars. Not thread-safe; the caller holds the lock.

    Bucketing uses **arrival time**, not the tick's own `ltt`: that field arrives as a
    locale-formatted local-time string from the SDK and as a bare int from the mock, two types
    for one field and neither reliable (`scalping/candles`). WebSocket latency is far below a
    one-minute bar.
    """

    def __init__(self, bucket_seconds: int = BUCKET_SECONDS) -> None:
        self.bucket_seconds = bucket_seconds
        self._cur: Optional[_Bucket] = None
        #: Day-cumulative quantity as the previous bar closed, the baseline this bar differences
        #: against. None after a reset, which makes the next bar's volume unknown, not zero.
        self._prev_ttq: Optional[float] = None

    def _bucket_start(self, ts: float) -> int:
        return int(ts // self.bucket_seconds) * self.bucket_seconds

    def ingest(self, ts: float, payload: Any) -> Optional[Bar]:
        """Feed one tick. Returns the bar that just *completed*, if this tick closed one."""
        last, ttq, oi = parse_quote(payload)
        if last is None or not last > 0:
            return None
        start = self._bucket_start(ts)
        finished: Optional[Bar] = None

        if self._cur is not None and start > self._cur.start:
            finished = self._close(self._cur)
            self._cur = None
        elif self._cur is not None and start < self._cur.start:
            return None  # an out-of-order tick from a bucket already published

        if self._cur is None:
            self._cur = _Bucket(start, last, ttq, ttq, oi)
        else:
            self._cur.close = last
            if ttq is not None:
                if self._cur.ttq_open is None:
                    self._cur.ttq_open = ttq
                self._cur.ttq_last = ttq
            if oi is not None:
                self._cur.oi_last = oi
        return finished

    def flush(self, ts: float) -> Optional[Bar]:
        """Close a bar the clock has left even though no further tick printed."""
        if self._cur is None or self._bucket_start(ts) <= self._cur.start:
            return None
        finished = self._close(self._cur)
        self._cur = None
        return finished

    def _close(self, bucket: _Bucket) -> Bar:
        volume: Optional[float] = None
        end = bucket.ttq_last
        if end is not None:
            if self._prev_ttq is not None and end >= self._prev_ttq:
                volume = end - self._prev_ttq
            # A counter running backwards is the day rolling over or a stale packet: restart
            # the baseline rather than emit a negative bar.
            self._prev_ttq = end
        return Bar(ts=float(bucket.start), close=bucket.close, volume=volume, oi=bucket.oi_last)
