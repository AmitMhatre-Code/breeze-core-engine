"""Running a condor backtest inside the app (docs/dynamic-iron-condor-plan.md section 5).

Shares Bots -> Backtest's job slot, cache and rules (`bots/backtest_jobs`): one job at a time,
fetching only on a live broker, outside 09:00-15:45 IST on trading days, within the smaller of
today's backtest budget and the deployment's shared ICICI allowance (#42), and stopping before
the data volume or the container's memory runs out (#41, #44). What it cannot fetch it does not
pretend to have: the replay stops at the first check missing data and the run is `partial`,
saying how far it got.

Every run replays the saved settings' neighbours too (`backtest_combos`, #71): one replay per
combination, all on one price source, fetched for together a round at a time. Each combination's
row stores what it changed and its status, so the bot's evidence (#66) finds it once those
settings are saved; only the saved combination keeps its action log, and the others' campaigns
go to the card's zip.

Sizing follows #36: one lot of today's condor is priced on ICICI's margin calculator (strikes at
the settings' deltas, from today's VIX, on the listed expiry nearest the entry DTE), and the
lots per tranche follow from the ceiling. A caller may pass the lots instead -- the only way to
run one on a mock instance, which has no calculator.
"""
from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any, Callable, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.domain.condor import CondorSettings
from icici_breeze_backend.app.services import api_usage
from icici_breeze_backend.app.services.bots import backtest_jobs as jobs
from icici_breeze_backend.app.services.bots import backtest_service as service
from icici_breeze_backend.app.services.bots.charges import load_charges
from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime
from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
from icici_breeze_backend.app.services.bots.scalping.backtest_fetch import (
    AllowanceSpent,
    BudgetExhausted,
    Stopped,
)
from icici_breeze_backend.app.services.bots.scalping.backtest_store import Need
from icici_breeze_backend.app.services.bots.scalping.spreads import spread_stats
from icici_breeze_backend.app.services.condor import backtest_combos
from icici_breeze_backend.app.services.condor import daily_history
from icici_breeze_backend.app.services.condor.backtest import CondorReplay, DailySource, ExitAction, StoreSource
from icici_breeze_backend.app.services.condor.engine import ENGINE_VERSION

_logger = logging.getLogger(__name__)

BOT = "condor"
LABEL = "Dynamic Iron Condor"
#: Where a run's prices come from: ICICI's intraday option bars (from 5 Jan 2026), or NSE's daily
#: closes (from 1 Jan 2020, docs/condor-daily-history-plan.md).
PRICES = ("icici", "nse_daily")
PRICES_LABEL = {"icici": "ICICI intraday prices", "nse_daily": "NSE daily closes, close-only checks"}
DAILY_NOTES = (
    "Prices: NSE's daily closing prices. Every decision is made once a session, at the close "
    "(entries, rolls, exits and the max-loss stop), so a 10:30 check never reads prices before "
    "they happened; a tranche set to enter at the start-of-day check enters at the close.",
    "Each cycle is sized to today's money: its lot is today's lot x today's NIFTY / NIFTY when it "
    "opened, so a 2% move costs the same in every year. Settings in index points (minimum roll "
    "credit) stay in points, so they are relatively larger at older, lower index levels.",
    "A contract that did not trade that session (often a far wing) is priced off the session's "
    "smile: the implied volatility of that expiry's contracts that did trade, as NSE prices "
    "untraded contracts. A traded close always wins. Spreads are modelled on today's, which "
    "understates older far-strike spreads.",
)
META_FIVE_MIN = "five_minute_bars"
# Rounds of fetch-and-resume before a run gives up; each round fetches what one check lacked.
MAX_ROUNDS = 2000


class SizingError(RuntimeError):
    pass


def size_from_margin(settings: CondorSettings, user_id: str, *, today: Optional[datetime.date] = None) -> dict[str, Any]:
    """Lots per tranche from one lot's margin at today's levels."""
    from icici_breeze_backend.app.services.bots.scalping.margin import margin_for_mixed_legs
    from icici_breeze_backend.app.services.options_strategy_engine.greeks import strike_for_abs_delta
    from icici_breeze_backend.app.services.processor import processor

    proc = processor()
    today = today or now_ist().date()
    spot = store.latest_close("NIFTY", path=None)
    if not spot:
        raise SizingError("No cached NIFTY index bars to place today's strikes. Fetch data first.")
    expiry = _listed_expiry_near(proc, today, settings.entry_dte)
    if expiry is None:
        raise SizingError("No NIFTY expiry is listed in the scrip master.")
    lot_size = int(proc.fetch_lot_size("NIFTY", expiry.strftime("%d-%b-%Y"), exchange_code=cfg.NFO) or 0)
    if lot_size <= 0:
        raise SizingError("No NIFTY lot size in the scrip master.")
    vix = store.load_vix()
    sigma = (vix[max(vix)] / 100.0) if vix else 0.13
    t = max(1, (expiry - today).days) / 365.0

    def strike(right: str, delta: float) -> float:
        k = strike_for_abs_delta(spot, t, sigma, right, delta)
        return regime.strike_beyond(k, "NIFTY", up=right == "Call")

    short_call, short_put = strike("Call", settings.short_delta), strike("Put", settings.short_delta)
    width = spot * settings.wing_width_pct / 100.0
    legs = [
        ("call", regime.strike_beyond(short_call + width, "NIFTY", up=True), lot_size, cfg.BUY),
        ("put", regime.strike_beyond(short_put - width, "NIFTY", up=False), lot_size, cfg.BUY),
        ("call", short_call, lot_size, cfg.SELL),
        ("put", short_put, lot_size, cfg.SELL),
    ]
    per_lot = margin_for_mixed_legs(
        proc, user_id, exchange_code=cfg.NFO, stock_code="NIFTY",
        expiry_display=expiry.strftime("%d-%b-%Y"), legs=legs,
    )
    if per_lot is None:
        raise SizingError("ICICI's margin calculator did not price today's condor.")
    lots = int(settings.margin_ceiling_inr / settings.tranches // per_lot)
    if lots < 1:
        raise SizingError(
            f"One lot of today's condor needs ₹{per_lot:,.0f}, more than a tranche's share "
            f"(₹{settings.margin_ceiling_inr / settings.tranches:,.0f}) of the ceiling."
        )
    return {
        "lots": lots,
        "margin_per_lot": round(per_lot, 2),
        "describe": (
            f"{lots} lot(s) a tranche: one lot of today's condor "
            f"({int(legs[1][1])}/{int(legs[3][1])} PE · {int(legs[2][1])}/{int(legs[0][1])} CE, "
            f"{expiry:%d-%b-%Y}) needs ₹{per_lot:,.0f}; the ₹{settings.margin_ceiling_inr:,.0f} "
            f"ceiling over {settings.tranches} tranche(s)."
        ),
    }


def _listed_expiry_near(proc: Any, today: datetime.date, dte: int) -> Optional[datetime.date]:
    from icici_breeze_backend.app.services.reference_data.scrip_master_sql import _expiry_api_to_display

    best: Optional[datetime.date] = None
    for entry in proc.fetch_stock_codes(cfg.NFO) or []:
        if str(entry.get("stock_code") or "").strip().upper() != "NIFTY":
            continue
        for raw in entry.get("expiry_dates") or []:
            try:
                d = datetime.datetime.strptime(_expiry_api_to_display(str(raw)), "%d-%b-%Y").date()
            except (TypeError, ValueError):
                continue
            if d >= today and (best is None or abs((d - today).days - dte) < abs((best - today).days - dte)):
                best = d
    return best


class _ScripIndex:
    """`_listed_expiry_near` reads expiries off a processor; the scrip index has the same list
    without importing the broker client."""

    @staticmethod
    def fetch_stock_codes(exchange_code: str) -> list[dict[str, Any]]:
        from icici_breeze_backend.app.services.reference_data.scrip_index import get_underlyings

        return get_underlyings(exchange_code) or []


def tradeable_band(settings: CondorSettings, today: datetime.date) -> tuple[Optional[tuple[float, float]], str]:
    """How far below and above spot ICICI lists tradeable NIFTY strikes today, as fractions of
    spot, on the expiry nearest the entry DTE -- and the note that says so (#69).

    A replay may only open legs inside this band, so the backtest never trades a strike live
    trading could not. History's own lists are not kept, so today's band stands in for every
    past day; the note says that too. None when the list cannot be read: the replay then runs
    unlimited and says so."""
    try:
        from icici_breeze_backend.app.services.reference_data.tradable_contracts import list_tradeable_strikes

        expiry = _listed_expiry_near(_ScripIndex(), today, settings.entry_dte)
        spot = store.latest_close("NIFTY", path=None)
        strikes = [float(k) for k in list_tradeable_strikes("NIFTY", expiry.strftime("%d-%b-%Y"))] if expiry else []
    except Exception:  # noqa: BLE001 -- a missing scrip master only loosens the replay
        _logger.debug("condor backtest: tradeable strikes unreadable", exc_info=True)
        expiry, spot, strikes = None, None, []
    if not (expiry and spot and strikes):
        return None, (
            "ICICI's list of tradeable NIFTY strikes could not be read, so the replay was not limited to it "
            "and may use strikes live trading cannot."
        )
    below, above = max(0.0, (spot - min(strikes)) / spot), max(0.0, (max(strikes) - spot) / spot)
    return (below, above), (
        f"Strikes limited to what ICICI lists today for NIFTY {expiry:%d-%b-%Y}: {below:.1%} below to "
        f"{above:.1%} above spot, applied to every check of the period."
    )


def five_minute_verdict(path: Optional[str] = None) -> Optional[str]:
    """What the cached 5-minute option bars look like: their spacing and how late in the day
    they run. Plan section 5 asks for this before the replay is trusted at 15:31."""
    import sqlite3

    with sqlite3.connect(path or store.db_path()) as conn:
        rows = conn.execute(
            "SELECT ts FROM option_candles WHERE interval = ? ORDER BY stock_code, expiry, strike, right, ts LIMIT 5000",
            (store.INTERVAL_5MIN,),
        ).fetchall()
    if len(rows) < 20:
        return None
    times = [datetime.datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S") for r in rows]
    gaps = [
        (b - a).total_seconds() / 60 for a, b in zip(times, times[1:]) if a.date() == b.date() and b > a
    ]
    modal = max(set(gaps), key=gaps.count) if gaps else None
    latest = max(t.time() for t in times)
    ok = modal == 5 and latest >= datetime.time(15, 30)
    verdict = (
        f"5-minute bars {'verified' if ok else 'NOT as expected'}: modal spacing "
        f"{modal:g} min, latest bar of the day starts {latest:%H:%M}."
    )
    store.set_meta(META_FIVE_MIN, verdict, path=path)
    return verdict


def start(
    user_id: str,
    settings: CondorSettings,
    from_date: datetime.date,
    to_date: datetime.date,
    *,
    exit_action: ExitAction,
    lots_per_tranche: Optional[int] = None,
    run_id: Optional[str] = None,
    period: Optional[str] = None,
    notes: Optional[list[str]] = None,
    on_finish: Optional[Callable[[dict[str, Any]], None]] = None,
    prices: str = "icici",
) -> dict[str, Any]:
    """Start a replay in the shared job slot. `prices` "nse_daily" replays on NSE's daily
    closes from 2020 (close-only checks, notional-scaled) instead of ICICI's intraday bars. `run_id`, `period` and `on_finish` are the card's
    (`start_card`): the stored run then shares its id with the Activity row, and `on_finish`
    records that row once the run is saved, whatever its outcome."""
    check_startable(from_date, to_date, lots_per_tranche, prices=prices)
    combos = backtest_combos.combos_for(settings, exit_action)
    now = now_ist()
    run_id = run_id or str(uuid.uuid4())
    run: dict[str, Any] = {
        "id": run_id,
        "user_id": user_id,
        "bot": BOT,
        "created_at": now.isoformat(timespec="seconds"),
        "status": "running",
        "params": {
            "bot": BOT,
            "label": LABEL,
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "exit_action": exit_action,
            "lots_per_tranche": lots_per_tranche,
            "settings": settings.model_dump(mode="json"),
            "engine_version": ENGINE_VERSION,
            "combinations": len(combos),
            "prices": prices,
            **({"period": period} if period else {}),
        },
    }
    store.save_run(run)
    first_notes = list(notes or [])

    def finished() -> None:
        if on_finish is None:
            return
        try:
            on_finish(run)
        except Exception:  # noqa: BLE001 -- the run is saved; only its Activity row is late
            _logger.exception("condor backtest: could not record run %s", run_id)

    def target() -> None:
        notes: list[str] = list(first_notes)
        calls = 0
        try:
            last_session = service.last_completed_session(now, service.holidays())
            end = min(to_date, last_session)
            if prices == "nse_daily":
                _run_daily(user_id, settings, from_date, end, combos, lots_per_tranche, notes, run, now)
                finished()
                message = run.get("headline") or _message(run.get("summary") or {}, run["status"])
                jobs._finish("completed", message=message, calls=0, run_id=run_id)  # noqa: SLF001
                return
            fetcher = None
            remaining = _fetch_allowance(user_id, now, notes)
            scope = jobs._broker_scope(user_id) if remaining > 0 else None  # noqa: SLF001
            if scope is not None:
                scope.__enter__()
            try:
                if remaining > 0:
                    fetcher = jobs._fetcher(user_id)  # noqa: SLF001
                    fetcher.max_calls = remaining
                    jobs._update(phase="fetching")  # noqa: SLF001
                    jobs._log("Fetching NIFTY index bars…")  # noqa: SLF001
                    # From a month and a half before the range: the premium gate's forecast reads
                    # the sessions before each check (#78).
                    from icici_breeze_backend.app.services.premium_gate.replay import WARMUP_DAYS

                    fetcher.fetch_spot("NIFTY", from_date - datetime.timedelta(days=WARMUP_DAYS), end)

                lots = lots_per_tranche
                sizing_text = f"{lots} lot(s) a tranche, as entered."
                if lots is None:
                    jobs._update(phase="sizing")  # noqa: SLF001
                    sizing = size_from_margin(settings, user_id)
                    lots, sizing_text = sizing["lots"], sizing["describe"]
                jobs._log(sizing_text)  # noqa: SLF001
                notes.append(sizing_text)

                band, band_note = tradeable_band(settings, now.date())
                notes.append(band_note)
                hol = service.holidays()
                # One source for every combination: the anchors and most held legs are the same
                # contracts, so each is read (and fetched) once whichever combinations want it.
                source = StoreSource(hol, from_date, end, data_until=last_session)
                charges, spread = load_charges(), spread_stats()
                replays = [
                    (combo, CondorReplay(
                        combo.settings, from_date, end,
                        lots_per_tranche=lots, exit_action=combo.exit_action, source=source,
                        listed_band=band, charges=charges, spread=spread, holidays=hol,
                    ))
                    for combo in combos
                ]
                notes.append(
                    f"{len(combos)} settings combinations replayed: {_grid_text(settings)}. "
                    "Every other setting as saved."
                )
                stopped = _drive(replays, source, fetcher, notes)
                missing = _spot_gap(replays[0][1], notes)
            finally:
                if fetcher is not None:
                    calls = fetcher.calls
                    store.add_calls(now.date(), calls)
                if scope is not None:
                    scope.__exit__(None, None, None)
            verdict = five_minute_verdict()
            if verdict:
                notes.append(verdict)
            rows, campaigns_by_combo = [], {}
            for combo, replay in replays:
                combo_summary = replay.summary()
                campaigns = card_trades(combo_summary)
                rows.append(backtest_combos.comparison_row(
                    combo, combo_summary, campaigns,
                    complete=replay.done and not missing, stopped_at=stopped.get(combo.id),
                ))
                campaigns_by_combo[combo.id] = campaigns
            status = "completed" if all(r["complete"] for r in rows) else "partial"
            saved_combo, replay = next(((c, r) for c, r in replays if c.is_saved), replays[0])
            summary = replay.summary()
            summary["notes"] = notes
            summary["calls"] = calls
            summary["comparison"] = rows
            summary["setting_label"] = saved_combo.label
            # Only the saved combination keeps its action log; the others keep their summary
            # and campaigns (the user's call, 2026-10-05: 108 logs a run would fill the volume).
            # `combos` is not a stored column: the card's zip writes it out, and that is all.
            run.update(status=status, summary=summary, trades=replay.events, combos=campaigns_by_combo)
            del replays
            store.save_run(run)
            finished()
            # A card run's dialog says what its Activity row says (`record` sets the headline).
            message = run.get("headline") or _message(summary, status)
            jobs._finish(status if status != "partial" else "completed", message=message, calls=calls, run_id=run_id)  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001 -- reported on the run and its Activity row
            _logger.exception("condor backtest failed")
            run.update(status="failed", error=str(exc), summary={"notes": notes})
            store.save_run(run)
            finished()
            jobs._finish("failed", error=str(exc), calls=calls, run_id=run_id)  # noqa: SLF001

    return jobs._start(  # noqa: SLF001
        "condor" if period is None else "backtest", target, bot=BOT, run_id=run_id,
        from_date=from_date.isoformat(), to_date=to_date.isoformat(), **({"period": period} if period else {}),
    )


def check_startable(
    from_date: datetime.date, to_date: datetime.date, lots_per_tranche: Optional[int], *, prices: str = "icici",
) -> None:
    """Everything that refuses a run before it takes the job slot."""
    jobs.ensure_store()
    jobs.refuse_if_storage_blocked()
    jobs._memory_check("before starting")  # noqa: SLF001
    if prices not in PRICES:
        raise ValueError(f"Unknown prices {prices!r}; expected one of {', '.join(PRICES)}.")
    first = daily_history.HISTORY_START if prices == "nse_daily" else regime.HISTORY_START
    if from_date < first:
        source = "NSE daily-price history" if prices == "nse_daily" else "ICICI's option history"
        raise ValueError(f"{source} starts {first:%d %b %Y}.")
    if to_date < from_date:
        raise ValueError("The end date is before the start date.")
    if lots_per_tranche is None and not jobs.broker_live():
        raise ValueError(
            f"Sizing from today's margin needs ICICI's margin calculator; this instance is in "
            f"'{cfg.ICICI_BROKER_MODE}' mode. Set the lots per tranche in the bot's settings instead."
        )


# --------------------------------------------------------------------------------------
# The card's clock (#36, #67): a period, the bot's saved settings, an Activity row
# --------------------------------------------------------------------------------------


def start_card(
    user_id: str,
    period: str,
    from_date: Optional[datetime.date] = None,
    to_date: Optional[datetime.date] = None,
    prices: str = "icici",
) -> dict[str, Any]:
    """The bot card's backtest, as the other bots have it: one choice of period, and the rest
    is the bot's saved settings -- campaign settings, exit action and lots per tranche (blank
    sizes from today's margin). The result is an Activity row whose id is the stored run's, so
    the row opens its campaigns and the bot's gate (#66) finds it like any other run.

    History starts at `regime.HISTORY_START`; a period reaching earlier is cut there and says so,
    rather than refused, because "last month" must never fail for a reason the user cannot see."""
    from icici_breeze_backend.app.db.bots_migrate import BOT_DYNAMIC_CONDOR
    from icici_breeze_backend.app.repositories import bots as bots_repo
    from icici_breeze_backend.app.services.condor import bot as condor_bot

    if period not in service.PERIODS:
        raise ValueError(f"Unknown period {period!r}.")
    config = condor_bot.config_of(bots_repo.get_or_create_bot(user_id, BOT_DYNAMIC_CONDOR).config)
    now = now_ist()
    first, last = service.resolve_period(period, from_date, to_date, now, service.holidays())
    notes: list[str] = []
    history_start = daily_history.HISTORY_START if prices == "nse_daily" else regime.HISTORY_START
    source_name = "NSE's daily-price history" if prices == "nse_daily" else "ICICI's option history"
    if first < history_start:
        notes.append(
            f"{source_name} starts {history_start:%d %b %Y}, so the replay starts there, "
            f"not {first:%d %b %Y}."
        )
        first = history_start
    check_startable(first, last, config.lots_per_tranche, prices=prices)
    with jobs._lock:  # noqa: SLF001
        if jobs._thread is not None and jobs._thread.is_alive():  # noqa: SLF001
            raise jobs.Busy("A backtest is already running. Wait for it, or stop it.")

    run_id = bots_repo.start_run(user_id, BOT_DYNAMIC_CONDOR, "backtest")
    period_text = (
        f"{service.PERIOD_LABELS[period]} · {first}" if first == last
        else f"{service.PERIOD_LABELS[period]} · {first} to {last}"
    )
    if prices == "nse_daily":
        period_text += f" · {PRICES_LABEL[prices]}"

    def record(run: dict[str, Any]) -> None:
        detail = {"backtest_run_id": run_id, "period": period, "from": first.isoformat(), "to": last.isoformat()}
        if run.get("status") == "failed":
            bots_repo.finish_run(
                run_id, status="failed", reason_code="backtest_failed",
                reason_text=f"{period_text}: {run.get('error')}", detail=detail,
            )
            return
        # The replay's own events become the zip's action log; the row's trades are campaigns.
        events = run.get("trades") or []
        trades = card_trades(run.get("summary") or {})
        summary = {**(run.get("summary") or {}), **card_totals(trades)}
        run.update(summary=summary, trades=trades)
        store.save_run(run)
        _write_zip(user_id, run, events)
        partial = run.get("status") == "partial"
        run["headline"] = (
            backtest_combos.headline(period_text, summary["comparison"], partial)
            if summary.get("comparison") else _headline(period_text, summary, partial)
        )
        bots_repo.finish_run(
            run_id, status="completed",
            reason_code="backtest_gaps" if partial else "backtest_complete",
            reason_text=run["headline"],
            # The comparison stays on the stored run, which the row opens: Activity lists every
            # row's detail, and 108 rows a backtest would weigh down every page of it.
            detail={**detail, "summary": {k: v for k, v in summary.items() if k != "comparison"}},
        )

    try:
        return start(
            user_id, config.campaign, first, last,
            exit_action=config.exit_action, lots_per_tranche=config.lots_per_tranche,
            run_id=run_id, period=period, notes=notes, on_finish=record, prices=prices,
        )
    except Exception as exc:
        bots_repo.finish_run(
            run_id, status="skipped",
            reason_code="backtest_busy" if isinstance(exc, jobs.Busy) else "backtest_failed",
            reason_text=str(exc),
        )
        raise


def card_trades(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """One Activity trade per replayed campaign, in the shape the other bots' rows use.

    A finished campaign's P&L is its ledger cash, every leg closed. One still open when the
    period ends is marked at its last check -- what closing then would have left, closing
    charges deducted -- and says so; its cash alone would count premium not yet earned."""
    rows = []
    for c in summary.get("campaigns") or []:
        cycles = c.get("cycles") or []
        open_now = not c.get("ended")
        net = c.get("pnl_at_last_check") if open_now else c.get("pnl")
        if net is None:
            continue
        charges = float(c.get("charges") or 0)
        rows.append({
            "entered_at": c.get("started"),
            "exited_at": c.get("ended"),
            "cycles": ", ".join(str(y.get("expiry")) for y in cycles) or "—",
            "tranches": sum(int(y.get("tranches") or 0) for y in cycles),
            "rolls": sum(int(y.get("rolls") or 0) for y in cycles),
            "exit_reason": "open_at_period_end" if open_now else (c.get("end_reason") or ""),
            "worst_pnl_at_check": c.get("worst_pnl_at_check"),
            "gross_pnl": round(float(net) + charges, 2),
            "friction": round(charges, 2),
            "net_pnl": round(float(net), 2),
        })
    return rows


def card_totals(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """`net_pnl` and `trades`, the two figures the card's "Last backtest" line and Activity read."""
    return {"net_pnl": round(sum(t["net_pnl"] for t in trades), 2), "trades": len(trades)}


def _headline(period_text: str, summary: dict[str, Any], partial: bool) -> str:
    n = int(summary.get("trades") or 0)
    pnl = float(summary.get("net_pnl") or 0)
    head = f"{period_text}: {n} campaign(s), net ₹{pnl:,.0f}"
    if summary.get("max_drawdown"):
        head += f", max drawdown ₹{summary['max_drawdown']:,.0f}"
    return head + (". Replayed in part: see the notes." if partial else ".")


def _write_zip(user_id: str, run: dict[str, Any], events: list[dict[str, Any]]) -> None:
    """The row's download, as the other bots' rows have one: the run, the comparison, and the
    saved combination's campaigns and every action its replay took. The other combinations
    have their campaigns only; their action logs are not kept (#71)."""
    import json

    from icici_breeze_backend.app.db.bots_migrate import BOT_DYNAMIC_CONDOR
    from icici_breeze_backend.audit import bot_audit

    writer = bot_audit.BacktestZipWriter(user_id, BOT_DYNAMIC_CONDOR, run["id"])
    try:
        writer.add_all({
            "run.json": json.dumps(
                {k: v for k, v in run.items() if k not in ("trades", "combos")}
                | {"summary": {k: v for k, v in (run.get("summary") or {}).items() if k != "comparison"}},
                indent=2, default=str,
            ),
            "campaigns.csv": run.get("trades") or [{"note": "no campaigns"}],
            "actions.csv": [
                {k: (json.dumps(v, default=str) if isinstance(v, (list, dict)) else v) for k, v in e.items()}
                for e in events
            ] or [{"note": "no actions"}],
            "summary.csv": [
                {**{k: v for k, v in r.items() if k not in ("varied", "overrides")}, **(r.get("varied") or {})}
                for r in (run.get("summary") or {}).get("comparison") or []
            ] or [{"note": "no combinations"}],
            **{
                f"combinations/{combo_id}.csv": campaigns or [{"note": "no campaigns"}]
                for combo_id, campaigns in (run.get("combos") or {}).items()
            },
        })
        writer.close()
    except Exception:  # noqa: BLE001 -- the row stands without its download
        writer.abandon()
        _logger.warning("condor backtest: could not write the results zip for %s", run["id"])



def _fetch_allowance(user_id: str, now: datetime.datetime, notes: list[str]) -> int:
    """Calls this run may spend, after saying why it is none when it is none (#42)."""
    if not jobs.broker_live():
        notes.append(f"Nothing fetched: this instance is in '{cfg.ICICI_BROKER_MODE}' mode; replayed on cached data.")
        return 0
    block = jobs.market_hours_reason()
    if block:
        notes.append(f"Nothing fetched: {block} Replayed on cached data only.")
        return 0
    budget_left = store.calls_remaining(now.date())
    headroom = api_usage.advisory_headroom(user_id)
    if budget_left <= 0:
        notes.append(
            f"Nothing fetched: today's backtest budget of {store.daily_call_budget()} ICICI calls is "
            "spent. It resets at IST midnight, or raise it in Settings → API Usage."
        )
        return 0
    if headroom <= 0:
        notes.append("Nothing fetched: today's shared ICICI allowance is held back for orders.")
        return 0
    return min(budget_left, headroom)


def _grid_text(settings: CondorSettings) -> str:
    loss = "max loss % of ceiling" if backtest_combos.max_loss_field(settings) == "max_loss_pct_of_ceiling" else "max loss ₹"
    return (
        f"net-Δ band ±{backtest_combos.BAND_STEP:g}, minimum roll credit ±{backtest_combos.ROLL_CREDIT_STEP:g} points, "
        f"{loss} × 0.5 / 1 / 1.5, no-roll window 0 and 3 days (and the saved value), time roll and close"
    )


def _run_daily(
    user_id: str,
    settings: CondorSettings,
    from_date: datetime.date,
    end: datetime.date,
    combos: list[Any],
    lots_per_tranche: Optional[int],
    notes: list[str],
    run: dict[str, Any],
    now: datetime.datetime,
) -> None:
    """The long-history replay (docs/condor-daily-history-plan.md): download what NSE sessions
    are missing, then replay every combination on daily closes, close-only, notional-scaled."""
    notes.extend(DAILY_NOTES)
    block = jobs.market_hours_reason(now)
    if block:
        notes.append(f"Nothing downloaded: {block} Replayed on the NSE prices already stored.")
    else:
        jobs._update(phase="fetching")  # noqa: SLF001
        jobs._log("Downloading NSE daily prices that are not stored yet…")  # noqa: SLF001

        def stop() -> Optional[str]:
            if jobs._cancel.is_set():  # noqa: SLF001
                return "Stopped at your request."
            return jobs.market_hours_reason()

        fetcher = daily_history.DailyFetcher(
            stop=stop, log=jobs._log,  # noqa: SLF001
            progress=lambda n, total: jobs._update(phase="fetching", step=n, steps=total),  # noqa: SLF001
        )
        try:
            # A month and a half before the range too: the premium gate's forecast reads the
            # sessions before each check (#78).
            from icici_breeze_backend.app.services.premium_gate.replay import WARMUP_DAYS

            stats = fetcher.fetch_range(from_date - datetime.timedelta(days=WARMUP_DAYS), end)
            notes.append(
                f"NSE daily prices: {stats[daily_history.OK]} session(s) downloaded, "
                f"{stats[daily_history.NO_FILE]} holiday(s), {stats[daily_history.ERROR]} error(s) "
                "(retried by the next run)."
            )
        except daily_history.Stopped as exc:
            notes.append(f"Download stopped: {exc} Replayed on the NSE prices already stored.")
    sessions = daily_history.sessions(from_date, end)
    if not sessions:
        raise ValueError("No NSE daily prices are stored for this range yet. Run it outside market hours.")
    reference = daily_history.latest_index_close()
    notes.append(
        f"{len(sessions)} session(s) from {sessions[0]:%d %b %Y} to {sessions[-1]:%d %b %Y}; "
        f"sized to NIFTY {reference:,.0f} (the latest stored close)."
    )

    lots = lots_per_tranche
    sizing_text = f"{lots} lot(s) a tranche, as entered."
    if lots is None:
        jobs._update(phase="sizing")  # noqa: SLF001
        sizing = size_from_margin(settings, user_id)
        lots, sizing_text = sizing["lots"], sizing["describe"]
    notes.append(sizing_text)

    band, band_note = tradeable_band(settings, now.date())
    notes.append(band_note)
    source = DailySource(from_date, end, reference_spot=reference)
    charges, spread = load_charges(), spread_stats()
    hol = service.holidays()
    replays = [
        (combo, CondorReplay(
            combo.settings, from_date, end, lots_per_tranche=lots, exit_action=combo.exit_action,
            source=source, listed_band=band, charges=charges, spread=spread, holidays=hol,
            checks=("eod",),
        ))
        for combo in combos
    ]
    notes.append(f"{len(combos)} settings combinations replayed: {_grid_text(settings)}. Every other setting as saved.")
    stopped = _drive(replays, source, None, notes)
    estimated_days = {day for day, _c in source.estimated}
    notes.append(
        f"{len(source.estimated)} option price(s) on {len(estimated_days)} session(s) were estimated "
        "off the session's smile because the contract did not trade; every other price traded."
    )
    rows, campaigns_by_combo = [], {}
    for combo, replay in replays:
        combo_summary = replay.summary()
        campaigns = card_trades(combo_summary)
        rows.append(backtest_combos.comparison_row(
            combo, combo_summary, campaigns, complete=replay.done, stopped_at=stopped.get(combo.id),
        ))
        campaigns_by_combo[combo.id] = campaigns
    status = "completed" if all(r["complete"] for r in rows) else "partial"
    saved_combo, replay = next(((c, r) for c, r in replays if c.is_saved), replays[0])
    summary = replay.summary()
    summary.update(notes=notes, calls=0, comparison=rows, setting_label=saved_combo.label,
                   prices="nse_daily", prices_label=PRICES_LABEL["nse_daily"])
    run.update(status=status, summary=summary, trades=replay.events, combos=campaigns_by_combo)
    store.save_run(run)


def _drive(
    replays: list[tuple[backtest_combos.Combo, CondorReplay]],
    source: StoreSource,
    fetcher: Any,
    notes: list[str],
) -> dict[str, str]:
    """Replay every combination, fetching what their stopped checks lacked, until all are done
    or nothing more can be fetched. Each round runs every unfinished combination as far as the
    cache allows and fetches what all of them lack together, once, so the budget is spread over
    the grid rather than spent on one combination first (the user's call, 2026-10-05).

    Returns {combo id: where it stopped} for the combinations left unfinished."""
    pending = list(replays)
    for _ in range(MAX_ROUNDS):
        waiting: list[tuple[backtest_combos.Combo, CondorReplay]] = []
        needs: dict[Need, None] = {}
        for n, (combo, replay) in enumerate(pending, start=1):
            if jobs._cancel.is_set():  # noqa: SLF001
                raise RuntimeError("Stopped at your request.")
            jobs._memory_check("mid-replay")  # noqa: SLF001
            jobs._update(phase="replaying", step=n, steps=len(pending), day=None)  # noqa: SLF001
            got = replay.run()
            if got:
                waiting.append((combo, replay))
                needs.update(dict.fromkeys(got))
        pending = waiting
        if not pending:
            return {}
        if fetcher is None:
            return _stopped(pending, replays, notes, "their option prices are not cached and nothing can be fetched now")
        wanted = list(needs)
        store.add_needs(wanted)
        jobs._update(phase="fetching", step=None, steps=None)  # noqa: SLF001
        jobs._log(f"{len(pending)} combination(s) wait for {len(wanted)} option window(s); fetching…")  # noqa: SLF001
        try:
            stats = fetcher.fetch_needs(wanted)
        except (AllowanceSpent, BudgetExhausted) as exc:
            notes.append(f"Fetch stopped: {exc}")
            return _stopped(pending, replays, notes, "the call budget ran out")
        except Stopped as exc:
            notes.append(f"Fetch stopped: {exc}")
            return _stopped(pending, replays, notes, "fetching stopped")
        if stats["fetched"] == 0:
            return _stopped(pending, replays, notes, "every request for their data errored (see the job log)")
        source.refresh()
    return _stopped(pending, replays, notes, f"{MAX_ROUNDS} fetch rounds were used")


def _stopped(
    pending: list[tuple[backtest_combos.Combo, CondorReplay]],
    replays: list[tuple[backtest_combos.Combo, CondorReplay]],
    notes: list[str],
    why: str,
) -> dict[str, str]:
    where = {combo.id: _check_time(replay) for combo, replay in pending}
    saved = next((where[c.id] for c, _r in pending if c.is_saved), None)
    text = f"{len(pending)} of {len(replays)} combination(s) stopped part-way because {why}"
    text += f"; yours at the {saved} check." if saved else "; yours finished."
    notes.append(text + " Run it again to continue.")
    return where


def _check_time(replay: CondorReplay) -> str:
    _day, _kind, ts = replay.checks[replay.i]
    return f"{ts:%d %b %Y %H:%M}"


def _spot_gap(replay: CondorReplay, notes: list[str]) -> int:
    """Checks with no cached NIFTY index price, which were not replayed. Holidays are not checks,
    so every one is missing data, and every combination misses the same ones (they share the
    index bars): none of them is then complete, never a completed ₹0."""
    missing = int(replay.skipped.get("no_spot", 0))
    if missing:
        notes.append(
            f"{missing} of {len(replay.checks)} checks had no cached NIFTY index price and were not "
            "replayed. The index bars are fetched on the live instance outside market hours; run it "
            "there to fill them."
        )
    return missing


def _message(summary: dict[str, Any], status: str) -> str:
    pnl = (summary.get("closed_pnl") or 0) + (summary.get("open_campaign_cash") or 0)
    head = "Replayed in full." if status == "completed" else "Replayed in part."
    n = len(summary.get("comparison") or [])
    return (f"{head} {n} combination(s); your settings: cash P&L ₹{pnl:,.0f}, "
            f"max drawdown ₹{summary.get('max_drawdown', 0):,.0f}.")


def list_runs(user_id: str, limit: Optional[int] = 20) -> list[dict[str, Any]]:
    """Newest first. `limit=None` reads them all: the bot's gate accepts a matching run of any
    age, so its search must not stop at an arbitrary count."""
    runs = [r for r in store.list_runs(user_id, limit=1_000_000) if r.get("bot") == BOT]
    return runs if limit is None else runs[:limit]


def get_run(run_id: str, user_id: str) -> Optional[dict[str, Any]]:
    run = store.get_run(run_id, user_id)
    return run if run and run.get("bot") == BOT else None

