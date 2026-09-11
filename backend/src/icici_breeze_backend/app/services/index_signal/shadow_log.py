"""Shadow log for the index signal: evidence before any bot is allowed to act on it.

Records a one-minute sample while the market is open, plus every state transition, each with the
index spot at that moment, into `index_signal_log` (users.sqlite3). `score` pairs each reading with
the index level 1/5/15 minutes later and judges the signal against what the index actually did.
That report is the gate for letting bots consume the signal (docs/design-decisions.md #30) -- the
+/-0.30 threshold is an untested prior, not a finding.

A naive hit rate flatters a signal in four ways, and the report is built against each:
- minute readings inside one run are nearly the same bet, so every cell also counts the readings a
  whole horizon apart and takes its 95% range from that count, not from every reading;
- a rising week makes "bullish" look good on its own, so each directional cell carries its edge
  over how often the index rose (or fell) after *any* reading at the same horizon;
- a 1 bp wiggle is not a call a trade could have used, so moves under `min_move_bps` are flat --
  neither hit nor miss;
- the useful question is "what happened after it turned bullish", so flips are scored on their
  own, from the index level at the flip, one flip one bet.
"""
from __future__ import annotations

import csv
import io
import logging
import math
import sqlite3
import threading
import time
from bisect import bisect_left
from datetime import datetime
from typing import Any, Iterable

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import IST

_logger = logging.getLogger(__name__)

DEFAULT_HORIZONS_SECONDS: tuple[int, ...] = (60, 300, 900)
DEFAULT_MIN_MOVE_BPS = 5.0
_FORWARD_TOLERANCE_SECONDS = 90.0
_Z95 = 1.959964
DIRECTIONAL_STATES = ("bullish", "bearish")
# A flip out of `unavailable` is the signal waking up (warm-up, coverage or feed back), not the
# order books changing their mind, so only flips from a live reading are scored.
_FLIP_FROM_STATES = frozenset({"neutral", "bullish", "bearish"})
_CSV_HORIZONS: tuple[tuple[int, str], ...] = ((60, "1m"), (300, "5m"), (900, "15m"))

_lock = threading.Lock()
_last_state: dict[str, str] = {}
_last_minute: dict[str, int] = {}
_ready_paths: set[str] = set()
_last_purge_date: str | None = None


def _db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


def ensure_log_table(db_path: str | None = None) -> None:
    path = db_path or _db_path()
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS index_signal_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                ts REAL NOT NULL,
                kind TEXT NOT NULL,
                state TEXT NOT NULL,
                reason TEXT,
                signal REAL,
                raw_wobi REAL,
                coverage REAL,
                spot REAL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_index_signal_log_label_ts "
            "ON index_signal_log (label, ts)"
        )
        conn.commit()
    with _lock:
        _ready_paths.add(path)


_retention_days_setting = 90


def set_retention_days(days: int) -> None:
    """Pushed in by the publisher from Settings -> Index Signal."""
    global _retention_days_setting
    _retention_days_setting = max(1, int(days))


def _retention_days() -> int:
    return _retention_days_setting


def record(
    label: str,
    payload: dict[str, Any],
    *,
    spot: float | None,
    now: float | None = None,
    db_path: str | None = None,
) -> list[str]:
    """Write whatever this publish owes the log; returns the row kinds written.

    A transition is logged whenever the state differs from the last publish (the first publish
    after a restart has nothing to differ from, so it is not one). A sample is logged once per
    wall-clock minute unless the market is closed -- off-hours samples would only pad the
    forward-return join with pairs that span a closed market."""
    global _last_purge_date
    ts = time.time() if now is None else now
    state = str(payload.get("state") or "unavailable")
    reason = payload.get("reason")
    minute = int(ts // 60)
    kinds: list[str] = []
    with _lock:
        prev = _last_state.get(label)
        if prev is not None and prev != state:
            kinds.append("transition")
        _last_state[label] = state
        if reason != "market_closed" and _last_minute.get(label) != minute:
            kinds.append("sample")
            _last_minute[label] = minute
    if not kinds:
        return []

    path = db_path or _db_path()
    with _lock:
        ready = path in _ready_paths
    if not ready:
        ensure_log_table(path)
    rows = [
        (
            label,
            ts,
            kind,
            state,
            reason,
            payload.get("signal"),
            payload.get("raw_wobi"),
            payload.get("coverage"),
            spot,
        )
        for kind in kinds
    ]
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO index_signal_log "
            "(label, ts, kind, state, reason, signal, raw_wobi, coverage, spot) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        today = datetime.fromtimestamp(ts, IST).date().isoformat()
        if _last_purge_date != today:
            conn.execute(
                "DELETE FROM index_signal_log WHERE ts < ?",
                (ts - _retention_days() * 86400.0,),
            )
            _last_purge_date = today
        conn.commit()
    return kinds


def load_rows(label: str, since_ts: float, db_path: str | None = None) -> list[dict[str, Any]]:
    """Oldest first; rows written by one publish keep their write order (transition, then
    sample), which is what `_flips` relies on to see the state a transition left."""
    path = db_path or _db_path()
    ensure_log_table(path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT label, ts, kind, state, reason, signal, raw_wobi, coverage, spot "
            "FROM index_signal_log WHERE label = ? AND ts >= ? ORDER BY ts, id",
            (label, since_ts),
        ).fetchall()
    return [dict(r) for r in rows]


def _level(row: dict[str, Any]) -> float | None:
    try:
        spot = float(row.get("spot"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return spot if spot > 0 else None


def _ist_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, IST).date().isoformat()


class _PriceSeries:
    """Every logged index level, samples and transitions alike, for looking up where the index
    was h seconds after a reading."""

    def __init__(self, rows: Iterable[dict[str, Any]], tolerance_seconds: float) -> None:
        points = sorted(
            (float(r["ts"]), level) for r in rows if (level := _level(r)) is not None
        )
        self.times = [t for t, _ in points]
        self.levels = [level for _, level in points]
        self.tolerance = tolerance_seconds
        self._day_last: dict[str, float] = {}
        for t in self.times:
            self._day_last[_ist_day(t)] = t  # ascending, so the day's last write wins

    def forward(
        self, t0: float, level0: float | None, horizon: float
    ) -> tuple[float | None, float | None, str | None]:
        """(level `horizon` seconds on, move in bps, None), or (None, None, why there is none).

        The pair is the first level at least `horizon` on, and is refused when that one is more
        than the tolerance late -- a pair spanning a gap in the log (restart, feed outage) would
        otherwise pass for a `horizon` outcome. Why: `no_level` (the reading has no index level),
        `day_end` (the day's log ends before the horizon -- the close), `gap` (it does not)."""
        if level0 is None:
            return None, None, "no_level"
        target = t0 + horizon
        j = bisect_left(self.times, target)
        if j < len(self.times) and self.times[j] <= target + self.tolerance:
            later = self.levels[j]
            return later, (later / level0 - 1.0) * 1e4, None
        if target > self._day_last.get(_ist_day(t0), t0):
            return None, None, "day_end"
        return None, None, "gap"


def _direction(move_bps: float, min_move_bps: float) -> str:
    if move_bps == 0 or abs(move_bps) < min_move_bps:
        return "flat"
    return "up" if move_bps > 0 else "down"


def _wilson(p: float, n: int) -> tuple[float | None, float | None]:
    """95% Wilson score range for a proportion `p` observed over `n` independent trials."""
    if n <= 0:
        return None, None
    z2 = _Z95 * _Z95
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = _Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


class _Tally:
    """One report cell. `add` must be fed in time order: a reading counts as independent only
    when it is at least a horizon after the last one that did, so no two independent readings
    share any of the index path they are scored on."""

    def __init__(self, horizon: float) -> None:
        self.horizon = horizon
        self.n = 0
        self.sum_bps = 0.0
        self.counts = {"up": 0, "down": 0, "flat": 0}
        self.independent = 0
        self.independent_decisive = 0
        self._last_independent: float | None = None

    def add(self, ts: float, move_bps: float, direction: str) -> None:
        self.n += 1
        self.sum_bps += move_bps
        self.counts[direction] += 1
        if self._last_independent is None or ts >= self._last_independent + self.horizon:
            self._last_independent = ts
            self.independent += 1
            if direction != "flat":
                self.independent_decisive += 1

    @property
    def mean_bps(self) -> float:
        return self.sum_bps / self.n if self.n else 0.0


def _baseline_summary(t: _Tally) -> dict[str, Any]:
    decisive = t.counts["up"] + t.counts["down"]
    up_share = t.counts["up"] / decisive if decisive else None
    return {
        "n": t.n,
        "mean_return_bps": round(t.mean_bps, 2) if t.n else None,
        "up_share": round(up_share, 4) if up_share is not None else None,
        "down_share": round(1.0 - up_share, 4) if up_share is not None else None,
    }


def _cell_summary(state: str, t: _Tally, baseline: dict[str, Any]) -> dict[str, Any]:
    """Counts and mean move for any state; for bullish/bearish also the hit rate (wins over wins
    plus losses -- flats are neither), its 95% range from the independent readings, and the edge
    over the baseline in the called direction (positive = better than reading nothing at all)."""
    out: dict[str, Any] = {
        "n": t.n,
        "n_independent": t.independent,
        "ups": t.counts["up"],
        "downs": t.counts["down"],
        "flats": t.counts["flat"],
        "mean_return_bps": round(t.mean_bps, 2),
        "hit_rate": None,
        "hit_rate_low": None,
        "hit_rate_high": None,
        "edge_hit": None,
        "edge_bps": None,
        "verdict": None,
    }
    if state not in DIRECTIONAL_STATES:
        return out
    bullish = state == "bullish"
    wins = t.counts["up"] if bullish else t.counts["down"]
    decisive = t.counts["up"] + t.counts["down"]
    base_share = baseline["up_share"] if bullish else baseline["down_share"]
    if baseline["mean_return_bps"] is not None:
        sign = 1.0 if bullish else -1.0
        out["edge_bps"] = round(sign * (t.mean_bps - baseline["mean_return_bps"]), 2)
    if not decisive:
        return out
    hit = wins / decisive
    low, high = _wilson(hit, t.independent_decisive)
    out["hit_rate"] = round(hit, 4)
    out["hit_rate_low"] = round(low, 4) if low is not None else None
    out["hit_rate_high"] = round(high, 4) if high is not None else None
    if base_share is not None:
        out["edge_hit"] = round(hit - base_share, 4)
        if low is not None and low > base_share:
            out["verdict"] = "better"
        elif high is not None and high < base_share:
            out["verdict"] = "worse"
        else:
            out["verdict"] = "unclear"
    return out


def _flips(ordered: list[dict[str, Any]]) -> list[tuple[float, str, float | None]]:
    """(ts, new state, index level) for each transition into bullish/bearish from a live state."""
    out: list[tuple[float, str, float | None]] = []
    prev: str | None = None
    for r in ordered:
        state = str(r.get("state") or "")
        if (
            r.get("kind") == "transition"
            and prev in _FLIP_FROM_STATES
            and state in DIRECTIONAL_STATES
            and state != prev
        ):
            out.append((float(r["ts"]), state, _level(r)))
        prev = state
    return out


def score(
    rows: Iterable[dict[str, Any]],
    *,
    horizons: Iterable[int] = DEFAULT_HORIZONS_SECONDS,
    min_move_bps: float = DEFAULT_MIN_MOVE_BPS,
    tolerance_seconds: float = _FORWARD_TOLERANCE_SECONDS,
) -> dict[str, Any]:
    """Judge the logged readings against what the index did next, per horizon (seconds):

    - `forward_returns`: per state, every minute reading;
    - `flip_returns`: per state flipped into, from the index level at the flip;
    - `baseline`: every reading regardless of state -- the yardstick for the edges;
    - `excluded`: readings with no outcome at that horizon, by reason. Each horizon is paired on
      its own, so a reading ten minutes before the close still has its +1 and +5 minute outcomes.
    """
    ordered = sorted(rows, key=lambda r: float(r["ts"]))  # stable: keeps a publish's write order
    prices = _PriceSeries(ordered, tolerance_seconds)
    samples = [r for r in ordered if r.get("kind") == "sample"]
    flips = _flips(ordered)
    forward: dict[int, dict[str, dict[str, Any]]] = {}
    flip_returns: dict[int, dict[str, dict[str, Any]]] = {}
    baselines: dict[int, dict[str, Any]] = {}
    excluded: dict[int, dict[str, int]] = {}
    for h in horizons:
        h = int(h)
        by_state: dict[str, _Tally] = {}
        everything = _Tally(h)
        missing = {"no_level": 0, "day_end": 0, "gap": 0}
        for r in samples:
            t0 = float(r["ts"])
            _later, move, why = prices.forward(t0, _level(r), h)
            if move is None:
                missing[str(why)] += 1
                continue
            direction = _direction(move, min_move_bps)
            by_state.setdefault(str(r["state"]), _Tally(h)).add(t0, move, direction)
            everything.add(t0, move, direction)
        by_flip: dict[str, _Tally] = {}
        for t0, state, level in flips:
            _later, move, _why = prices.forward(t0, level, h)
            if move is not None:
                by_flip.setdefault(state, _Tally(h)).add(t0, move, _direction(move, min_move_bps))
        base = _baseline_summary(everything)
        baselines[h] = base
        forward[h] = {s: _cell_summary(s, t, base) for s, t in sorted(by_state.items())}
        flip_returns[h] = {s: _cell_summary(s, t, base) for s, t in sorted(by_flip.items())}
        excluded[h] = missing
    return {
        "flips": len(flips),
        "forward_returns": forward,
        "flip_returns": flip_returns,
        "baseline": baselines,
        "excluded": excluded,
    }


def shadow_report(
    label: str,
    *,
    days: int = 5,
    min_move_bps: float = DEFAULT_MIN_MOVE_BPS,
    db_path: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    since = (time.time() if now is None else now) - days * 86400.0
    rows = load_rows(label, since, db_path)
    return {
        "label": label,
        "days": days,
        "min_move_bps": min_move_bps,
        "samples": sum(1 for r in rows if r["kind"] == "sample"),
        "transitions": sum(1 for r in rows if r["kind"] == "transition"),
        **score(rows, min_move_bps=min_move_bps),
    }


def _csv_number(value: Any, places: int) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.{places}f}"
    except (TypeError, ValueError):
        return ""


def readings_csv(
    label: str,
    *,
    days: int,
    db_path: str | None = None,
    now: float | None = None,
) -> str:
    """The minute readings behind the report, for Excel or a charting tool: one row per sample
    with the signal, the index level, and the index 1/5/15 minutes later with the move in bps.

    An outcome is blank where `score` has none -- the last 5 minutes of a session have no +5,
    the last 15 no +15, and a reading with no index level has none at all. IST times are written
    as `YYYY-MM-DD HH:MM:SS`, which Excel reads as a date-time."""
    since = (time.time() if now is None else now) - days * 86400.0
    rows = load_rows(label, since, db_path)
    prices = _PriceSeries(rows, _FORWARD_TOLERANCE_SECONDS)
    buf = io.StringIO()
    writer = csv.writer(buf)
    header = ["time_ist", "state", "reason", "signal", "raw_wobi", "coverage", f"{label}_level"]
    for _h, tag in _CSV_HORIZONS:
        header += [f"{label}_after_{tag}", f"move_{tag}_bps"]
    writer.writerow(header)
    for r in rows:
        if r["kind"] != "sample":
            continue
        t0 = float(r["ts"])
        level = _level(r)
        line = [
            datetime.fromtimestamp(t0, IST).strftime("%Y-%m-%d %H:%M:%S"),
            r["state"],
            r.get("reason") or "",
            _csv_number(r.get("signal"), 4),
            _csv_number(r.get("raw_wobi"), 4),
            _csv_number(r.get("coverage"), 4),
            _csv_number(level, 2),
        ]
        for h, _tag in _CSV_HORIZONS:
            later, move, _why = prices.forward(t0, level, h)
            line += [_csv_number(later, 2), _csv_number(move, 2)]
        writer.writerow(line)
    return buf.getvalue()


def readings_filename(label: str, days: int, now: float | None = None) -> str:
    stamp = datetime.fromtimestamp(time.time() if now is None else now, IST).strftime("%Y-%m-%d")
    return f"{label}-signal-readings-{stamp}-{days}d.csv"


def reset_state_for_tests() -> None:
    global _last_purge_date
    with _lock:
        _last_state.clear()
        _last_minute.clear()
        _ready_paths.clear()
        _last_purge_date = None
