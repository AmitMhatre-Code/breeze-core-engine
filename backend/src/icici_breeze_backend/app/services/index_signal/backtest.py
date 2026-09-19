"""Signal backtest: one run replays every series over a period and writes one zip (plan section 4).

A run asks for a period and nothing else, like a bot card's backtest (#36):

1. **Fetch what is missing** -- NIFTY and BSESEN one-minute futures bars for the range plus a
   warm-up -- under the same rules as every backtest fetch: live broker only, never 09:00-15:45 on
   a trading day, within the day's call budget. A stopped fetch is a note, never a failure; the
   replay then runs on what is cached and the notes say so.
2. **Replay all twelve series** through `series.replay_series` -- the same engine the live
   publisher runs -- warmed on the sessions before the range, exactly as live warms each morning.
3. **Score each series** (`scoring`) and stream its audit files into the run's zip, one series at
   a time so a six-month run never holds every reading in memory.
4. **Record the run** in `signal_backtest_runs`: range, status, the mechanism versions replayed,
   the per-series summaries and the zip's path. The 30-day availability gate reads these rows.

Runs share the backtest job slot with the bot backtests (`bots/backtest_jobs`), so a signal run
and a bot run never fetch at once.
"""
from __future__ import annotations

import csv
import datetime
import io
import json
import logging
import os
import sqlite3
import uuid
import zipfile
from typing import Any, Iterable, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.db.signals_migrate import ensure_signal_tables
from icici_breeze_backend.app.services.index_signal import bars as bars_mod
from icici_breeze_backend.app.services.index_signal import scoring
from icici_breeze_backend.app.services.index_signal.mechanisms import (
    INDEX_NAMES,
    INDICES,
    MECHANISM_NAMES,
    STOCK_CODES,
    VERSIONS,
    SeriesKey,
    all_keys,
    params_dict,
)
from icici_breeze_backend.app.services.index_signal.series import replay_series, rollover_days

_logger = logging.getLogger(__name__)

RUNS_SUBDIR = "signals-backtest"
# Zips kept on disk. A run's row outlives its zip; an older zip is deleted, and its row says so.
KEEP_ZIPS = 30
# Calendar days of bars before the range that warm each series, as the live warm-up does.
WARMUP_DAYS = 10
# Scored against this when the breakeven cannot be priced (no lot size or level yet).
FALLBACK_MIN_MOVE_BPS = 5.0


# --------------------------------------------------------------------------------------
# Runs table
# --------------------------------------------------------------------------------------


def _db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


def _connect(path: Optional[str] = None) -> sqlite3.Connection:
    p = path or _db_path()
    ensure_signal_tables(p)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    return conn


def runs_dir() -> str:
    path = os.path.join(cfg.DATA_PATH, RUNS_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def _row(r: sqlite3.Row) -> dict[str, Any]:
    out = dict(r)
    for key in ("versions", "summary", "notes"):
        raw = out.get(key)
        try:
            out[key] = json.loads(raw) if raw else ({} if key != "notes" else [])
        except (TypeError, ValueError):
            out[key] = {} if key != "notes" else []
    try:
        out["range_days"] = (
            datetime.date.fromisoformat(out["to_date"]) - datetime.date.fromisoformat(out["from_date"])
        ).days + 1
    except (TypeError, ValueError, KeyError):
        out["range_days"] = None
    out["has_zip"] = bool(out.get("zip_path")) and os.path.exists(str(out.get("zip_path")))
    return out


def create_run(run_id: str, user_id: str, period: str, start: datetime.date, end: datetime.date,
               *, db_path: Optional[str] = None) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO signal_backtest_runs
               (id, user_id, triggered_at, period, from_date, to_date, status, versions)
               VALUES (?, ?, ?, ?, ?, ?, 'running', ?)""",
            (run_id, user_id, now_ist().isoformat(timespec="seconds"), period,
             start.isoformat(), end.isoformat(), json.dumps(VERSIONS)),
        )


def update_run(run_id: str, *, db_path: Optional[str] = None, **fields: Any) -> None:
    if not fields:
        return
    for key in ("summary", "notes", "versions"):
        if key in fields and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key], default=str)
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _connect(db_path) as conn:
        conn.execute(f"UPDATE signal_backtest_runs SET {cols} WHERE id = ?", (*fields.values(), run_id))


def list_runs(limit: int = 50, *, db_path: Optional[str] = None) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM signal_backtest_runs ORDER BY triggered_at DESC LIMIT ?", (int(limit),)
        ).fetchall()
    return [_row(r) for r in rows]


def get_run(run_id: str, *, db_path: Optional[str] = None) -> Optional[dict[str, Any]]:
    with _connect(db_path) as conn:
        r = conn.execute("SELECT * FROM signal_backtest_runs WHERE id = ?", (run_id,)).fetchone()
    return _row(r) if r else None


def completed_runs(*, db_path: Optional[str] = None) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM signal_backtest_runs WHERE status = 'completed' ORDER BY triggered_at DESC"
        ).fetchall()
    return [_row(r) for r in rows]


def reap_orphaned_runs(*, db_path: Optional[str] = None) -> int:
    """A run still `running` at startup was cut off by the restart."""
    with _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE signal_backtest_runs SET status = 'failed', error = ?, finished_at = ? "
            "WHERE status = 'running'",
            ("Interrupted by a restart.", now_ist().isoformat(timespec="seconds")),
        )
        return cur.rowcount


def _prune_zips(*, db_path: Optional[str] = None) -> None:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, zip_path FROM signal_backtest_runs WHERE zip_path IS NOT NULL "
            "ORDER BY triggered_at DESC"
        ).fetchall()
        for r in rows[KEEP_ZIPS:]:
            try:
                if r["zip_path"] and os.path.exists(r["zip_path"]):
                    os.remove(r["zip_path"])
            except OSError:
                _logger.warning("signal backtest: could not remove %s", r["zip_path"])
            conn.execute("UPDATE signal_backtest_runs SET zip_path = NULL WHERE id = ?", (r["id"],))


# --------------------------------------------------------------------------------------
# Zip writing
# --------------------------------------------------------------------------------------


def _columns(rows: list[dict[str, Any]]) -> list[str]:
    seen: dict[str, None] = {}
    for row in rows:
        for key in row:
            seen.setdefault(key, None)
    return list(seen)


def _cell(value: Any) -> Any:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        # Ten significant figures: a NIFTY level keeps its paise, an OI count its units.
        return format(value, ".10g")
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return "" if value is None else value


def write_csv(zf: zipfile.ZipFile, name: str, rows: list[dict[str, Any]],
              columns: Optional[list[str]] = None) -> None:
    cols = columns or _columns(rows)
    with zf.open(name, "w") as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
        writer = csv.DictWriter(text, fieldnames=cols, extrasaction="ignore", restval="")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _cell(row.get(k)) for k in cols})
        text.flush()
        text.detach()


def _bars_rows(bars: Iterable[bars_mod.Bar]) -> list[dict[str, Any]]:
    out = []
    for b in bars:
        t = bars_mod.ist(b.ts)
        out.append({
            "date": t.date().isoformat(),
            "time": t.strftime("%H:%M"),
            "open": b.open, "high": b.high, "low": b.low, "close": b.close,
            "volume": b.volume, "oi": b.oi,
            "part": "session" if bars_mod.in_session(b) else "pre-open" if bars_mod.is_pre_open(b) else "after",
        })
    return out


README = """Signal backtest -- what is in this zip
=========================================

Every file is a plain CSV (open in Excel). Times are IST. "bps" is basis points: 1 bps = 0.01%.

run.json
    The run: period, dates, when it ran, the app and mechanism versions, every parameter of every
    series, how many ICICI calls the fetch spent, and notes (data gaps, days without bars).

summary.csv
    One row per index x mechanism x duration -- the headline numbers:
    sessions_replayed      trading sessions that had bars to replay
    calls                  how many calls (bullish or bearish) the signal made
    right / wrong          calls where the index moved the called way / the other way by at least
                           the breakeven move within the duration; smaller moves are neither
    hit_rate               right / (right + wrong)
    <side>_trend_share     how often the index moved that way after ANY reading -- the bar a
                           side's hit rate must beat to mean anything
    <side>_verdict         better / worse / no_edge than the trend share (95% range, calls at
                           least one duration apart), or too_few_calls (under 30 separate calls)
    verdict                edge (both sides better), worse (a side reliably worse), no_edge,
                           or too_few_calls
    mean_move_*_bps        average index move the called way after the call
    breakeven_bps          the move one lot of the at-the-money option needs to pay its round-trip
                           charges (Settings -> Trading Costs), in bps of the index
    rough_pnl_one_lot_rupees  ROUGH: called-way index points x lot x delta 0.5, less charges,
                           summed over calls. No spread, no time decay -- the bot backtests price
                           real options; this is only for comparing signals with each other.
    mean_daily_correlation strength vs the next move, correlated per day then averaged (pooling
                           days manufactures correlation)

<INDEX>/bars.csv
    The exact one-minute futures bars the run replayed (NIFTY near-month on NFO, SENSEX BSESEN on
    BFO), including pre-open bars, which feed the session VWAP only. Anything in this zip can be
    recomputed from these.

<INDEX>/<mechanism>-<duration>/readings.csv
    One row per minute of the session: the bar, the reading (state, reason, strength), the call it
    belonged to, every input the mechanism computed (c_* columns), and where the index was 1, 5,
    15 and 30 minutes later (fwd_*_bps).

<INDEX>/<mechanism>-<duration>/calls.csv
    One row per call: when it fired, which way, at what level, everything the mechanism read when
    it fired (c_*), how it ended (lapsed / turned / unavailable / session_end), the move after one
    and two durations and at its end, the best and worst move while it stood, and the result.

<INDEX>/<mechanism>-<duration>/days.csv
    One row per session: the day's move, readings, calls, hit rate, the day's correlation, and
    whether it was a rollover day (NIFTY expansion reads no OI then, so it stands down).

States: bullish / bearish are calls; neutral means the market was read and nothing fired;
unavailable means there was no reading (warming up, outside the session, a data gap), and is never
a quiet market.

SENSEX runs on BSESEN futures, which trade thinly (no trade in about half the minutes), and ICICI
serves no BSE open interest, so SENSEX expansion reads price and volume only.
"""


# --------------------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------------------


def _breakeven(index: str, level: Optional[float]) -> tuple[dict[str, Any], float, Optional[str]]:
    try:
        from icici_breeze_backend.app.services.index_signal import breakeven

        be = breakeven.breakeven(index, level)
    except Exception:  # noqa: BLE001
        _logger.debug("signal backtest: breakeven failed for %s", index, exc_info=True)
        be = {}
    bps = be.get("bps") if isinstance(be, dict) else None
    if isinstance(bps, (int, float)) and bps > 0:
        return be, float(bps), None
    note = (f"{INDEX_NAMES[index]}: the breakeven move could not be priced (no lot size or "
            f"premium yet), so calls were judged against {FALLBACK_MIN_MOVE_BPS:g} bps.")
    return be or {}, FALLBACK_MIN_MOVE_BPS, note


def _summary_row(key: SeriesKey, s: dict[str, Any]) -> dict[str, Any]:
    d = key.duration
    row = {
        "index": INDEX_NAMES[key.index],
        "mechanism": MECHANISM_NAMES[key.mechanism],
        "duration_minutes": d,
        "series": key.id,
        "version": key.version,
        "thin_data": key.thin_data,
        "uses_open_interest": key.uses_oi,
    }
    for k in ("sessions_replayed", "up_days", "down_days", "readings", "directional_share", "calls",
              "bullish_calls", "bearish_calls", "right", "wrong", "hit_rate", "verdict",
              f"mean_move_{d}m_bps", f"mean_move_{2 * d}m_bps", "mean_move_at_end_bps",
              "mean_best_move_bps", "mean_worst_move_bps", "withdrawn_early", "breakeven_bps",
              "rough_pnl_one_lot_rupees", "mean_daily_correlation"):
        row[k] = s.get(k)
    for side, v in (s.get("sides") or {}).items():
        for k in ("hit_rate", "hit_rate_low", "hit_rate_high", "trend_share", "separate_calls", "verdict"):
            row[f"{side}_{k}"] = v.get(k)
    return row


def run_backtest(
    start: datetime.date,
    end: datetime.date,
    *,
    run_id: str,
    period: str,
    holidays: set[datetime.date],
    notes: list[str],
    calls: int = 0,
    cache_path: Optional[str] = None,
    out_dir: Optional[str] = None,
    cancelled=lambda: False,
    log=lambda line: None,
) -> dict[str, Any]:
    """Replay, score and zip. Returns {"summary": {series_id: summary}, "zip_path": ...}; raises
    `Cancelled` when asked to stop (the partial zip is removed)."""
    from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime
    from icici_breeze_backend.app.services.bots.scalping import backtest_store as store

    store.ensure_tables(cache_path)
    warm_from = start - datetime.timedelta(days=WARMUP_DAYS)
    bars: dict[str, list[bars_mod.Bar]] = {}
    for index in INDICES:
        candles = store.load_candles(stock_code=STOCK_CODES[index], from_date=warm_from, to_date=end,
                                     path=cache_path)
        bars[index] = [bars_mod.from_hist(c) for c in candles]
    sessions = regime.trading_days(start, end, holidays)
    rollover = rollover_days(start, end, holidays)

    directory = out_dir or runs_dir()
    final = os.path.join(directory, f"signals-backtest-{start}-to-{end}-{run_id[:8]}.zip")
    partial = final + ".partial"
    summaries: dict[str, dict[str, Any]] = {}
    summary_rows: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            zf.writestr("README.txt", README)
            for index in INDICES:
                in_range = [b for b in bars[index] if start <= bars_mod.trading_date(b.ts) <= end]
                write_csv(zf, f"{INDEX_NAMES[index]}/bars.csv", _bars_rows(in_range))
                if not in_range:
                    notes.append(f"{INDEX_NAMES[index]}: no stored bars for {start}..{end}.")
            breakevens: dict[str, tuple[dict[str, Any], float]] = {}
            for index in INDICES:
                last = next((b.close for b in reversed(bars[index])), None)
                be, bps, note = _breakeven(index, last)
                breakevens[index] = (be, bps)
                if note:
                    notes.append(note)
            for key in all_keys():
                if cancelled():
                    raise Cancelled()
                log(f"Replaying {key.name}…")
                excluded = rollover if key.uses_oi else set()
                rows = [
                    (b, snap) for b, snap in replay_series(bars[key.index], key, excluded_days=excluded)
                    if start <= bars_mod.trading_date(b.ts) <= end
                ]
                be, bps = breakevens[key.index]
                scored = scoring.score_series(
                    rows, duration=key.duration, breakeven_bps=bps,
                    all_bars=[b for b in bars[key.index] if start <= bars_mod.trading_date(b.ts) <= end],
                    breakeven=be, sessions_in_range=sessions,
                )
                folder = f"{INDEX_NAMES[key.index]}/{key.slug}"
                write_csv(zf, f"{folder}/readings.csv", scored.readings)
                write_csv(zf, f"{folder}/calls.csv", scored.calls or [{"note": "no calls"}])
                write_csv(zf, f"{folder}/days.csv", scored.days)
                summary = {**scored.summary, **key.to_dict()}
                summaries[key.id] = summary
                summary_rows.append(_summary_row(key, summary))
            write_csv(zf, "summary.csv", summary_rows)
            run_meta = {
                "run_id": run_id,
                "period": period,
                "from": start.isoformat(),
                "to": end.isoformat(),
                "range_days": (end - start).days + 1,
                "sessions_in_range": [d.isoformat() for d in sessions],
                "rollover_days_excluded_for_oi": sorted(d.isoformat() for d in rollover),
                "warmup_from": warm_from.isoformat(),
                "generated_at": now_ist().isoformat(timespec="seconds"),
                "app_version": getattr(cfg, "APP_VERSION", None),
                "versions": VERSIONS,
                "icici_calls_spent": calls,
                "notes": notes,
                "series": {k.id: params_dict(k) for k in all_keys()},
            }
            zf.writestr("run.json", json.dumps(run_meta, indent=2, default=str))
        os.replace(partial, final)
    except BaseException:
        try:
            os.remove(partial)
        except OSError:
            pass
        raise
    return {"summary": summaries, "zip_path": final}


class Cancelled(RuntimeError):
    pass


def start(user_id: str, period: str, from_date: Optional[datetime.date] = None,
          to_date: Optional[datetime.date] = None) -> dict[str, Any]:
    """Start a run on the shared backtest job slot. Returns the job state."""
    from icici_breeze_backend.app.services.bots import backtest_jobs as jobs
    from icici_breeze_backend.app.services.bots import backtest_service as service
    from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
    from icici_breeze_backend.app.services.bots.scalping.backtest_fetch import Stopped

    if period not in service.PERIODS:
        raise ValueError(f"Unknown period {period!r}.")
    if jobs.is_running():
        raise jobs.Busy("A backtest is already running. Wait for it, or stop it.")
    jobs.ensure_store()
    now = now_ist()
    hol = service.holidays()
    start_d, end_d = service.resolve_period(period, from_date, to_date, now, hol)
    run_id = str(uuid.uuid4())
    create_run(run_id, user_id, period, start_d, end_d)

    def target() -> None:
        notes: list[str] = []
        calls = 0
        try:
            remaining = store.calls_remaining(now.date())
            block = jobs.market_hours_reason()
            if not jobs.broker_live():
                notes.append(f"Nothing fetched: this instance is in '{cfg.ICICI_BROKER_MODE}' mode, "
                             "so only history already stored was replayed.")
            elif block:
                notes.append("Nothing fetched: ICICI history isn't fetched between 09:00 and 15:45 IST "
                             "on a trading day. Only history already stored was replayed.")
            elif remaining <= 0:
                notes.append(f"Nothing fetched: today's budget of {store.DAILY_CALL_BUDGET} ICICI calls "
                             "is spent. Only history already stored was replayed.")
            else:
                jobs._log(f"Fetching missing futures bars, {start_d} to {end_d}…")  # noqa: SLF001
                with jobs._broker_scope(user_id):  # noqa: SLF001
                    fetcher = jobs._fetcher(user_id)  # noqa: SLF001
                    fetcher.max_calls = remaining
                    try:
                        for index in INDICES:
                            fetcher.fetch_futures(STOCK_CODES[index], start_d - datetime.timedelta(days=WARMUP_DAYS), end_d)
                    except Stopped as exc:
                        notes.append(f"Fetch stopped early: {exc}")
                    finally:
                        calls = fetcher.calls
                        store.add_calls(now.date(), calls)
            for text in notes:
                jobs._log(text)  # noqa: SLF001
            result = run_backtest(
                start_d, end_d, run_id=run_id, period=period, holidays=hol, notes=notes, calls=calls,
                cancelled=jobs._cancel.is_set, log=jobs._log,  # noqa: SLF001
            )
            headline = _headline(result["summary"])
            update_run(run_id, status="completed", finished_at=now_ist().isoformat(timespec="seconds"),
                       summary=result["summary"], notes=notes, calls=calls, zip_path=result["zip_path"])
            _prune_zips()
            from icici_breeze_backend.app.services.index_signal import gate

            gate.invalidate()
            jobs._finish("completed", message=headline, calls=calls, run_id=run_id)  # noqa: SLF001
        except Cancelled:
            update_run(run_id, status="stopped", finished_at=now_ist().isoformat(timespec="seconds"),
                       notes=notes, calls=calls, error="Stopped at your request.")
            jobs._finish("stopped", message="Stopped at your request.", calls=calls, run_id=run_id)  # noqa: SLF001
        except Exception as exc:
            update_run(run_id, status="failed", finished_at=now_ist().isoformat(timespec="seconds"),
                       notes=notes, calls=calls, error=str(exc)[:500])
            raise

    try:
        return jobs._start("signal", target, run_id=run_id, from_date=start_d.isoformat(),  # noqa: SLF001
                           to_date=end_d.isoformat(), period=period)
    except jobs.Busy:
        update_run(run_id, status="failed", error="Another backtest was already running.")
        raise


def _headline(summaries: dict[str, dict[str, Any]]) -> str:
    calls = sum(int(s.get("calls") or 0) for s in summaries.values())
    edges = [sid for sid, s in summaries.items() if s.get("verdict") == "edge"]
    tail = f" {len(edges)} series showed an edge." if edges else " No series showed an edge."
    return f"Replayed {len(summaries)} series; {calls} calls in total.{tail}"
