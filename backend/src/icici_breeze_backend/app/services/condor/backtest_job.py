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
from typing import Any, Optional

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

    legs = [
        ("call", strike("Call", settings.wing_delta), lot_size, cfg.BUY),
        ("put", strike("Put", settings.wing_delta), lot_size, cfg.BUY),
        ("call", strike("Call", settings.short_delta), lot_size, cfg.SELL),
        ("put", strike("Put", settings.short_delta), lot_size, cfg.SELL),
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
) -> dict[str, Any]:
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
            f"'{cfg.ICICI_BROKER_MODE}' mode. Enter the lots per tranche instead."
        )
    now = now_ist()
    run_id = str(uuid.uuid4())
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
        },
    }
    store.save_run(run)

    def target() -> None:
        notes: list[str] = []
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

                source = StoreSource(service.holidays(), from_date, end, data_until=last_session)
                replay = CondorReplay(
                    settings, from_date, end,
                    lots_per_tranche=lots, exit_action=exit_action, source=source,
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
            jobs._finish(status if status != "partial" else "completed", message=_message(summary, status), calls=calls, run_id=run_id)  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001 -- reported on the run and the page
            _logger.exception("condor backtest failed")
            run.update(status="failed", error=str(exc), summary={"notes": notes})
            store.save_run(run)
            jobs._finish("failed", error=str(exc), calls=calls, run_id=run_id)  # noqa: SLF001

    return jobs._start("condor", target, bot=BOT, run_id=run_id, from_date=from_date.isoformat(), to_date=to_date.isoformat())  # noqa: SLF001


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

