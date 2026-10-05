"""Running a condor backtest inside the app (docs/dynamic-iron-condor-plan.md section 5).

Shares Bots -> Backtest's job slot, cache and rules (`bots/backtest_jobs`): one job at a time,
fetching only on a live broker, outside 09:00-15:45 IST on trading days, within the smaller of
today's backtest budget and the deployment's shared ICICI allowance (#42), and stopping before
the data volume or the container's memory runs out (#41, #44). What it cannot fetch it does not
pretend to have: the replay stops at the first check missing data and the run is `partial`,
saying how far it got.

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
from icici_breeze_backend.app.services.bots.scalping.spreads import spread_stats
from icici_breeze_backend.app.services.condor.backtest import CondorReplay, ExitAction, StoreSource
from icici_breeze_backend.app.services.condor.engine import ENGINE_VERSION

_logger = logging.getLogger(__name__)

BOT = "condor"
LABEL = "Dynamic Iron Condor"
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
) -> dict[str, Any]:
    """Start a replay in the shared job slot. `run_id`, `period` and `on_finish` are the card's
    (`start_card`): the stored run then shares its id with the Activity row, and `on_finish`
    records that row once the run is saved, whatever its outcome."""
    check_startable(from_date, to_date, lots_per_tranche)
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
                    fetcher.fetch_spot("NIFTY", from_date, end)

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
                source = StoreSource(service.holidays(), from_date, end, data_until=last_session)
                replay = CondorReplay(
                    settings, from_date, end,
                    lots_per_tranche=lots, exit_action=exit_action, source=source, listed_band=band,
                    charges=load_charges(), spread=spread_stats(), holidays=service.holidays(),
                    on_day=lambda d: jobs._update(phase="replaying", day=d.isoformat()),  # noqa: SLF001
                )
                status = _drive(replay, source, fetcher, notes)
                status = _spot_gap(replay, status, notes)
            finally:
                if fetcher is not None:
                    calls = fetcher.calls
                    store.add_calls(now.date(), calls)
                if scope is not None:
                    scope.__exit__(None, None, None)
            verdict = five_minute_verdict()
            if verdict:
                notes.append(verdict)
            summary = replay.summary()
            summary["notes"] = notes
            summary["calls"] = calls
            run.update(status=status, summary=summary, trades=replay.events)
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


def check_startable(from_date: datetime.date, to_date: datetime.date, lots_per_tranche: Optional[int]) -> None:
    """Everything that refuses a run before it takes the job slot."""
    jobs.ensure_store()
    jobs.refuse_if_storage_blocked()
    jobs._memory_check("before starting")  # noqa: SLF001
    if from_date < regime.HISTORY_START:
        raise ValueError(f"ICICI's option history starts {regime.HISTORY_START:%d %b %Y}.")
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
    if first < regime.HISTORY_START:
        notes.append(
            f"ICICI's option history starts {regime.HISTORY_START:%d %b %Y}, so the replay starts there, "
            f"not {first:%d %b %Y}."
        )
        first = regime.HISTORY_START
    check_startable(first, last, config.lots_per_tranche)
    with jobs._lock:  # noqa: SLF001
        if jobs._thread is not None and jobs._thread.is_alive():  # noqa: SLF001
            raise jobs.Busy("A backtest is already running. Wait for it, or stop it.")

    run_id = bots_repo.start_run(user_id, BOT_DYNAMIC_CONDOR, "backtest")
    period_text = (
        f"{service.PERIOD_LABELS[period]} · {first}" if first == last
        else f"{service.PERIOD_LABELS[period]} · {first} to {last}"
    )

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
        run["headline"] = _headline(period_text, summary, partial)
        bots_repo.finish_run(
            run_id, status="completed",
            reason_code="backtest_gaps" if partial else "backtest_complete",
            reason_text=run["headline"],
            detail={**detail, "summary": summary},
        )

    try:
        return start(
            user_id, config.campaign, first, last,
            exit_action=config.exit_action, lots_per_tranche=config.lots_per_tranche,
            run_id=run_id, period=period, notes=notes, on_finish=record,
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
    """The row's download, as the other bots' rows have one: the run, its campaigns and every
    action the replay took."""
    import json

    from icici_breeze_backend.app.db.bots_migrate import BOT_DYNAMIC_CONDOR
    from icici_breeze_backend.audit import bot_audit

    writer = bot_audit.BacktestZipWriter(user_id, BOT_DYNAMIC_CONDOR, run["id"])
    try:
        writer.add_all({
            "run.json": json.dumps({k: v for k, v in run.items() if k != "trades"}, indent=2, default=str),
            "campaigns.csv": run.get("trades") or [{"note": "no campaigns"}],
            "actions.csv": [
                {k: (json.dumps(v, default=str) if isinstance(v, (list, dict)) else v) for k, v in e.items()}
                for e in events
            ] or [{"note": "no actions"}],
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


def _drive(replay: CondorReplay, source: StoreSource, fetcher: Any, notes: list[str]) -> str:
    """Replay, fetching what each stopped check lacked, until done or unable to continue."""
    for _ in range(MAX_ROUNDS):
        jobs._memory_check("mid-replay")  # noqa: SLF001
        needs = replay.run()
        if not needs:
            return "completed"
        if fetcher is None:
            notes.append(_stopped_at(replay, "its option prices are not cached and nothing can be fetched now"))
            return "partial"
        store.add_needs(needs)
        try:
            stats = fetcher.fetch_needs(needs)
        except (AllowanceSpent, BudgetExhausted) as exc:
            notes.append(f"Fetch stopped: {exc}")
            notes.append(_stopped_at(replay, "the call budget ran out"))
            return "partial"
        except Stopped as exc:
            notes.append(f"Fetch stopped: {exc}")
            notes.append(_stopped_at(replay, "fetching stopped"))
            return "partial"
        if stats["fetched"] == 0:
            notes.append(_stopped_at(replay, "every request for its data errored (see the job log)"))
            return "partial"
        source.refresh()
    notes.append(_stopped_at(replay, f"{MAX_ROUNDS} fetch rounds were used"))
    return "partial"


def _spot_gap(replay: CondorReplay, status: str, notes: list[str]) -> str:
    """A check with no cached NIFTY index price was not replayed. Holidays are not checks, so
    every one of these is missing data: the run is `partial`, never a completed ₹0."""
    missing = int(replay.skipped.get("no_spot", 0))
    if not missing:
        return status
    notes.append(
        f"{missing} of {len(replay.checks)} checks had no cached NIFTY index price and were not "
        "replayed. The index bars are fetched on the live instance outside market hours; run it "
        "there to fill them."
    )
    return "partial" if status == "completed" else status


def _stopped_at(replay: CondorReplay, why: str) -> str:
    day, kind, ts = replay.checks[replay.i]
    return f"Replay stopped at the {ts:%d %b %Y %H:%M} check because {why}. Run it again to continue."


def _message(summary: dict[str, Any], status: str) -> str:
    pnl = (summary.get("closed_pnl") or 0) + (summary.get("open_campaign_cash") or 0)
    head = "Replayed in full." if status == "completed" else "Replayed in part."
    return f"{head} Cash P&L ₹{pnl:,.0f}, max drawdown ₹{summary.get('max_drawdown', 0):,.0f}."


def list_runs(user_id: str, limit: Optional[int] = 20) -> list[dict[str, Any]]:
    """Newest first. `limit=None` reads them all: the bot's gate accepts a matching run of any
    age, so its search must not stop at an arbitrary count."""
    runs = [r for r in store.list_runs(user_id, limit=1_000_000) if r.get("bot") == BOT]
    return runs if limit is None else runs[:limit]


def get_run(run_id: str, user_id: str) -> Optional[dict[str, Any]]:
    run = store.get_run(run_id, user_id)
    return run if run and run.get("bot") == BOT else None

