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
from bisect import bisect_left, bisect_right
from datetime import datetime
from typing import Any, Iterable

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.index_signal import breakeven

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
        # Added 2026-09-16 (#33). The futures challenger publishes 0.5 * (ofi + aggressor);
        # storing only the blend made its verdict unattributable to either half. Additive and
        # idempotent because `shadow_log` owns this table outright and no migration touches it.
        for ddl in (
            "ALTER TABLE index_signal_log ADD COLUMN ofi REAL",
            "ALTER TABLE index_signal_log ADD COLUMN aggressor REAL",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # already present
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


def _flow_components(payload: dict[str, Any]) -> tuple[float | None, float | None]:
    """The futures challenger's two halves, or (None, None) for anything else (#33).

    `flow.snapshot` reports `components` as `{"order_flow": .., "aggressor": ..}` only for
    `KIND_FUTURES`. The constituent challenger's map is keyed by ShortName and has no aggressor
    half at all -- depth rooms carry no trade prints -- and `IndexSignalEngine` has no
    `components` at all. Both keep NULL halves, which is the truth about them.
    """
    parts = payload.get("components")
    if not isinstance(parts, dict):
        return None, None
    out: list[float | None] = []
    for key in ("order_flow", "aggressor"):
        try:
            value = float(parts[key])  # type: ignore[arg-type]
        except (KeyError, TypeError, ValueError):
            out.append(None)
        else:
            out.append(value if math.isfinite(value) else None)
    return out[0], out[1]


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
    ofi, aggressor = _flow_components(payload)
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
            ofi,
            aggressor,
        )
        for kind in kinds
    ]
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO index_signal_log "
            "(label, ts, kind, state, reason, signal, raw_wobi, coverage, spot, ofi, aggressor) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        today = datetime.fromtimestamp(ts, IST).date().isoformat()
        if _last_purge_date != today:
            # Replay rows carry historical timestamps, so retention would delete a fresh
            # backtest of an older range the moment it was written. They are cleared only by
            # `purge_label`, when the next replay replaces them.
            conn.execute(
                "DELETE FROM index_signal_log WHERE ts < ? AND label NOT LIKE '%:backtest'",
                (ts - _retention_days() * 86400.0,),
            )
            _last_purge_date = today
        conn.commit()
    return kinds


def purge_label(label: str, db_path: str | None = None) -> int:
    """Delete every row for one label. Returns how many went.

    Only a *replay* label (`<index>:expansion:backtest`) should ever be passed here: a backtest
    re-run must start from an empty series or its second run would score the first run's rows
    as well. Live evidence is never purged this way -- it ages out on the retention setting,
    which is what makes the readiness gate's "10 sessions" mean ten real ones.
    """
    if not label.endswith(":backtest"):
        raise ValueError(
            f"refusing to purge {label!r}: only replay labels may be cleared wholesale"
        )
    path = db_path or _db_path()
    ensure_log_table(path)
    with _lock:
        # The transition and once-a-minute guards are in-process state keyed by label. Leaving
        # them behind would make a re-run drop its first sample and miss its first transition.
        _last_state.pop(label, None)
        _last_minute.pop(label, None)
    with sqlite3.connect(path) as conn:
        cursor = conn.execute("DELETE FROM index_signal_log WHERE label = ?", (label,))
        conn.commit()
        return int(cursor.rowcount or 0)


def load_rows(label: str, since_ts: float, db_path: str | None = None) -> list[dict[str, Any]]:
    """Oldest first; rows written by one publish keep their write order (transition, then
    sample), which is what `_flips` relies on to see the state a transition left."""
    path = db_path or _db_path()
    ensure_log_table(path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT label, ts, kind, state, reason, signal, raw_wobi, coverage, spot, "
            "ofi, aggressor "
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
        "n_independent_decisive": t.independent_decisive,
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


def flip_list(
    label: str,
    *,
    days: int,
    min_move_bps: float | None = None,
    db_path: str | None = None,
    now: float | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Each time the signal turned bullish or bearish, newest first, with what the index did
    5 and 15 minutes later -- the plain list behind the "when it turned" view on Settings.

    A flip is `_flips`' definition, so this list and the readiness verdict count the same
    events. `right` is judged against `min_move_bps` (the breakeven when omitted), the same bar
    the verdict uses: a move the called way that would not pay for a trade is not a right call.
    """
    since = (time.time() if now is None else now) - days * 86400.0
    rows = load_rows(label, since, db_path)
    prices = _PriceSeries(rows, _FORWARD_TOLERANCE_SECONDS)
    bar = min_move_bps if min_move_bps is not None else _breakeven_move(label, rows)[1]
    out: list[dict[str, Any]] = []
    for ts, state, level in _flips(rows):
        entry: dict[str, Any] = {
            "ts": ts,
            "time_ist": datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d %H:%M:%S"),
            "state": state,
            "level": level,
        }
        want = 1.0 if state == "bullish" else -1.0
        for horizon, tag in ((300, "5m"), (900, "15m")):
            later, move, why = prices.forward(ts, level, horizon)
            entry[f"move_{tag}_bps"] = None if move is None else round(move, 2)
            entry[f"right_{tag}"] = None if move is None else (move * want >= bar)
            if why:
                entry[f"missing_{tag}"] = why
        out.append(entry)
    out.reverse()
    return {"label": label, "days": days, "min_move_bps": round(bar, 2), "flips": out[:limit]}


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


def _latest_level(rows: list[dict[str, Any]]) -> float | None:
    for r in reversed(rows):
        level = _level(r)
        if level is not None:
            return level
    return None


def _breakeven_move(label: str, rows: list[dict[str, Any]]) -> tuple[dict[str, Any], float]:
    """The breakeven block and the minimum move to score with: the breakeven in bps, or the
    default when it cannot be worked out (no lot size or index level yet)."""
    be = breakeven.breakeven(base_label(label), _latest_level(rows))
    return be, be["bps"] if be["bps"] is not None else DEFAULT_MIN_MOVE_BPS


def base_label(label: str) -> str:
    """The index a log label belongs to: a challenger (`nifty:flow`) trades the same options."""
    return label.split(":", 1)[0]


def shadow_report(
    label: str,
    *,
    days: int = 5,
    min_move_bps: float | None = DEFAULT_MIN_MOVE_BPS,
    db_path: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """`min_move_bps=None` scores against the breakeven move (`breakeven.py`)."""
    since = (time.time() if now is None else now) - days * 86400.0
    rows = load_rows(label, since, db_path)
    be, breakeven_bps = _breakeven_move(label, rows)
    used = breakeven_bps if min_move_bps is None else min_move_bps
    return {
        "label": label,
        "days": days,
        "min_move_bps": used,
        "breakeven": be,
        "samples": sum(1 for r in rows if r["kind"] == "sample"),
        "transitions": sum(1 for r in rows if r["kind"] == "transition"),
        **score(rows, min_move_bps=used),
    }


# The readiness verdict: the one answer to "may a scalping bot act on this signal yet?". The
# test is fixed in advance -- flips, at +5 minutes, against the breakeven move -- because picking
# whichever of the report's cells looks best would find a winner in pure noise. +15 minutes rides
# along as information (does the move hold?), never as a way to pass.
SCALP_HORIZON_SECONDS = 300
HOLD_HORIZON_SECONDS = 900
READINESS_LOOKBACK_DAYS = 60
# A floor against early luck, not a target: the 95% range must also clear the trend, so a strong
# signal passes soon after the floor and a marginal one needs far more calls than this.
MIN_SEPARATE_CALLS = 30
# A one-way week makes one side look clever, so the evidence must span both kinds of day.
MIN_SESSIONS = 10
MIN_UP_DAYS = 3
MIN_DOWN_DAYS = 3


def _sessions(ordered: list[dict[str, Any]]) -> tuple[int, int, int]:
    """(trading days logged, up days, down days), a day's direction being its last logged level
    against its first."""
    first: dict[str, float] = {}
    last: dict[str, float] = {}
    for r in ordered:
        level = _level(r)
        if r.get("kind") != "sample" or level is None:
            continue
        day = _ist_day(float(r["ts"]))
        first.setdefault(day, level)
        last[day] = level
    up = sum(1 for d in first if last[d] > first[d])
    down = sum(1 for d in first if last[d] < first[d])
    return len(first), up, down


def _dropped_within(ordered: list[dict[str, Any]], seconds: float) -> tuple[int, int]:
    """(flips, flips the signal let go of within `seconds`). Every flip is a trade for a bot that
    acts on it, so one given up minutes later is a round trip paid for a call withdrawn."""
    flips = _flips(ordered)
    changes = [float(r["ts"]) for r in ordered if r.get("kind") == "transition"]
    dropped = 0
    for t0, _state, _level_at_flip in flips:
        j = bisect_right(changes, t0)
        if j < len(changes) and changes[j] - t0 < seconds:
            dropped += 1
    return len(flips), dropped


def _call_status(cell: dict[str, Any] | None) -> str:
    if cell is None or cell["ups"] + cell["downs"] == 0:
        return "no_calls"
    if cell["n_independent_decisive"] < MIN_SEPARATE_CALLS:
        return "too_early"
    return {"better": "better", "worse": "worse"}.get(str(cell["verdict"]), "no_edge")


def _call_view(state: str, cell: dict[str, Any] | None, baseline: dict[str, Any]) -> dict[str, Any]:
    bullish = state == "bullish"
    wins = (cell["ups"] if bullish else cell["downs"]) if cell else 0
    return {
        "status": _call_status(cell),
        "right": wins,
        "calls": (cell["ups"] + cell["downs"]) if cell else 0,
        "separate_calls": cell["n_independent_decisive"] if cell else 0,
        "hit_rate": cell["hit_rate"] if cell else None,
        "hit_rate_low": cell["hit_rate_low"] if cell else None,
        "hit_rate_high": cell["hit_rate_high"] if cell else None,
        # How often the index went the called way after any reading: what the flip must beat.
        "trend_share": baseline["up_share" if bullish else "down_share"],
    }


def readiness(
    label: str,
    *,
    db_path: str | None = None,
    now: float | None = None,
    lookback_days: int | None = None,
) -> dict[str, Any]:
    """Whether the signal has earned a scalping bot's trust, in the terms the Settings screen's
    summary shows. `status`: `ready` (both sides beat the trend at +5 min), `worse` (a side is
    reliably worse than the trend), `no_edge` (enough evidence, no better than the trend), or
    `too_early` (below a floor).

    `now` and `lookback_days` exist for a replay, whose evidence is the range it replayed rather
    than the 60 days before today. The test itself is the same either way."""
    lookback = READINESS_LOOKBACK_DAYS if lookback_days is None else lookback_days
    since = (time.time() if now is None else now) - lookback * 86400.0
    ordered = load_rows(label, since, db_path)
    be, min_move = _breakeven_move(label, ordered)
    scored = score(ordered, horizons=(SCALP_HORIZON_SECONDS, HOLD_HORIZON_SECONDS), min_move_bps=min_move)
    sessions, up_days, down_days = _sessions(ordered)
    flips, dropped = _dropped_within(ordered, SCALP_HORIZON_SECONDS)

    directions: dict[str, dict[str, Any]] = {}
    for state in DIRECTIONAL_STATES:
        directions[state] = {
            key: _call_view(
                state,
                scored["flip_returns"][h].get(state),
                scored["baseline"][h],
            )
            for key, h in (("scalp", SCALP_HORIZON_SECONDS), ("hold", HOLD_HORIZON_SECONDS))
        }
    scalp = [directions[s]["scalp"]["status"] for s in DIRECTIONAL_STATES]
    enough_days = sessions >= MIN_SESSIONS and up_days >= MIN_UP_DAYS and down_days >= MIN_DOWN_DAYS
    if "worse" in scalp:
        status = "worse"
    elif not enough_days or any(s in ("no_calls", "too_early") for s in scalp):
        status = "too_early"
    elif all(s == "better" for s in scalp):
        status = "ready"
    else:
        status = "no_edge"

    return {
        "label": label,
        "status": status,
        "lookback_days": lookback,
        "scalp_horizon_seconds": SCALP_HORIZON_SECONDS,
        "hold_horizon_seconds": HOLD_HORIZON_SECONDS,
        "min_move_bps": min_move,
        "breakeven": be,
        "directions": directions,
        "sessions": sessions,
        "up_days": up_days,
        "down_days": down_days,
        "flips": flips,
        "dropped_quickly": dropped,
        "requirements": {
            "separate_calls": MIN_SEPARATE_CALLS,
            "sessions": MIN_SESSIONS,
            "up_days": MIN_UP_DAYS,
            "down_days": MIN_DOWN_DAYS,
        },
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

    `ofi` and `aggressor` are the futures challenger's two halves (#33) and are blank for every
    other label -- the blended `signal` alone could not say which half carried the reading.

    An outcome is blank where `score` has none -- the last 5 minutes of a session have no +5,
    the last 15 no +15, and a reading with no index level has none at all. IST times are written
    as `YYYY-MM-DD HH:MM:SS`, which Excel reads as a date-time."""
    since = (time.time() if now is None else now) - days * 86400.0
    rows = load_rows(label, since, db_path)
    prices = _PriceSeries(rows, _FORWARD_TOLERANCE_SECONDS)
    buf = io.StringIO()
    writer = csv.writer(buf)
    header = [
        "time_ist",
        "state",
        "reason",
        "signal",
        "raw_wobi",
        # The challenger's halves (#33); blank for W-OBI and for the constituent challenger.
        "ofi",
        "aggressor",
        "coverage",
        f"{label}_level",
    ]
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
            _csv_number(r.get("ofi"), 4),
            _csv_number(r.get("aggressor"), 4),
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
    return f"{label.replace(':', '-')}-signal-readings-{stamp}-{days}d.csv"


def reset_state_for_tests() -> None:
    global _last_purge_date
    with _lock:
        _last_state.clear()
        _last_minute.clear()
        _ready_paths.clear()
        _last_purge_date = None
    breakeven.reset_state_for_tests()
