"""Background jobs behind Bots -> Backtest (docs/bots-scalping-plan.md section 8.11).

One job at a time, in a daemon thread of the API process -- the margin harness's shape. The
page polls `state()` while a job runs.

**Fetching goes through the app's own Breeze session**, unlike the command-line script. Every
call is therefore counted by the per-user limiter, serialized with live orders, and marked
advisory, so it is the first thing shed when the daily budget runs short. Serialized is not
free, though: each call in flight delays a live order by one call's latency. So fetching is
still refused 09:00-15:45 IST on trading days (decided 2026-09-13), and the rule is checked
before *every* call -- a backfill started at 08:40 stops itself at 09:00.

Replays need no broker and run any time, except that the fly and Bot 2 price one lot's margin
at today's levels before they start (`backtest_service.price_lots`): a handful of advisory
calls, which is also what the margin harness spends.
"""
from __future__ import annotations

import collections
import datetime
import logging
import threading
import uuid
from typing import Any, Callable, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.services.bots import backtest_service as service
from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
from icici_breeze_backend.app.services.bots.scalping.backtest_fetch import (
    DEFAULT_MAX_CALLS,
    Fetcher,
    Stopped,
    market_hours_refusal,
)
from icici_breeze_backend.app.services.bots.scalping.backtest_options import (
    ModelPricer,
    OptionBook,
    RealPricer,
)

_logger = logging.getLogger(__name__)

_LOG_LINES = 200
_lock = threading.Lock()
_cancel = threading.Event()
_job: Optional[dict[str, Any]] = None
_thread: Optional[threading.Thread] = None
_store_ready = False


class Busy(RuntimeError):
    """Another backtest job is running."""


def ensure_store() -> None:
    """Create the cache tables, and fail any run a restart left marked as running."""
    global _store_ready
    store.ensure_tables()
    if not _store_ready:
        _store_ready = True
        if not is_running():
            store.fail_unfinished_runs()


def broker_live() -> bool:
    return str(cfg.ICICI_BROKER_MODE or "").strip().lower() == "live"


def market_hours_reason(now: Optional[datetime.datetime] = None) -> Optional[str]:
    from icici_breeze_backend.app.services.market_calendar import is_trading_day

    now = now or now_ist()
    return market_hours_refusal(now.replace(tzinfo=None), trading_day=is_trading_day(now))


def state() -> Optional[dict[str, Any]]:
    with _lock:
        if _job is None:
            return None
        out = dict(_job)
        out["log"] = list(_job["log"])
        out["running"] = _thread is not None and _thread.is_alive()
        return out


def is_running() -> bool:
    with _lock:
        return _thread is not None and _thread.is_alive()


def cancel() -> bool:
    if not is_running():
        return False
    _cancel.set()
    _log("Stop requested; stopping after the current call.")
    return True


def _log(line: str) -> None:
    with _lock:
        if _job is not None:
            _job["log"].append(f"{now_ist():%H:%M:%S}  {line}")


def _update(**fields: Any) -> None:
    with _lock:
        if _job is not None:
            _job.update(fields)


def _finish(status: str, *, message: Optional[str] = None, error: Optional[str] = None, **extra: Any) -> None:
    with _lock:
        if _job is None or _job["status"] != "running":
            return
        _job.update(
            status=status,
            message=message,
            error=error,
            finished_at=now_ist().isoformat(timespec="seconds"),
            **extra,
        )


def _start(kind: str, target: Callable[[], None], **info: Any) -> dict[str, Any]:
    global _job, _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            raise Busy("A backtest job is already running. Wait for it, or stop it.")
        _cancel.clear()
        _job = {
            "id": str(uuid.uuid4()),
            "kind": kind,
            "status": "running",
            "started_at": now_ist().isoformat(timespec="seconds"),
            "finished_at": None,
            "message": None,
            "error": None,
            "calls": 0,
            "log": collections.deque(maxlen=_LOG_LINES),
            **info,
        }

        def run() -> None:
            try:
                target()
            except Exception as exc:  # noqa: BLE001 -- reported on the page, and logged
                _logger.exception("backtest %s job failed", kind)
                _finish("failed", error=str(exc))

        _thread = threading.Thread(target=run, name=f"backtest-{kind}", daemon=True)
        _thread.start()
    return state() or {}


def _stop_reason() -> Optional[str]:
    if _cancel.is_set():
        return "Stopped at your request."
    return market_hours_reason()


def _guard_broker() -> None:
    if not broker_live():
        raise ValueError(
            f"Fetching needs live ICICI calls; this instance is in '{cfg.ICICI_BROKER_MODE}' mode. "
            "It works only on the production instance, whose IP is registered with ICICI."
        )
    reason = market_hours_reason()
    if reason:
        raise ValueError(reason)


def _fetcher(user_id: str) -> Fetcher:
    from icici_breeze_backend.app.services.processor import processor

    sdk = processor().get_session_breeze(user_id)
    if sdk is None:
        raise RuntimeError("No active ICICI session. Log in to the broker, then try again.")
    fetcher = Fetcher(sdk, holidays=service.holidays(), max_calls=DEFAULT_MAX_CALLS, stop=_stop_reason)

    def log(line: str) -> None:
        _log(line.strip())
        _update(calls=fetcher.calls)

    fetcher.log = log
    return fetcher


def _broker_scope(user_id: str):
    """The user whose limiter counts these calls, marked advisory so they shed first."""
    from contextlib import ExitStack

    from icici_breeze_backend.app.services.icici_api_pacing import icici_user_scope
    from icici_breeze_backend.app.services.icici_call_class import advisory_calls

    stack = ExitStack()
    stack.enter_context(icici_user_scope(user_id))
    stack.enter_context(advisory_calls())
    return stack


# --------------------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------------------


def start_probe(user_id: str) -> dict[str, Any]:
    _guard_broker()
    ensure_store()

    def target() -> None:
        with _broker_scope(user_id):
            fetcher = _fetcher(user_id)
            _log("Probing ICICI's history API (about a dozen calls)…")
            try:
                report = fetcher.probe(now_ist().date())
            except Stopped as exc:
                _finish("stopped", message=str(exc), calls=fetcher.calls)
                return
        verdict = (report.get("expired_nifty_weekly") or {}).get("verdict") or "unknown"
        _finish("completed", message=f"Expired NIFTY weekly contracts: {verdict}.", calls=fetcher.calls)

    return _start("probe", target)


def start_fetch(user_id: str, bot: str, start: datetime.date, end: datetime.date) -> dict[str, Any]:
    _guard_broker()
    ensure_store()
    config = service.saved_config(bot, user_id)
    indices = service.indices_for(bot, config)
    if not indices:
        raise ValueError("No index is enabled in Bot 2's settings, so there is nothing to fetch.")

    def target() -> None:
        with _broker_scope(user_id):
            fetcher = _fetcher(user_id)
            _log(f"Fetching {service.BOT_LABELS[bot]} data, {start} to {end}")
            try:
                service.fetch_underlying(fetcher, bot, start, end, indices)
                outcome = service.backfill(fetcher, bot, start=start, end=end, config=config, log=fetcher.log)
            except Stopped as exc:
                _finish(
                    "stopped",
                    message=f"{exc} Everything fetched so far is kept; fetch again to resume.",
                    calls=fetcher.calls,
                )
                return
        _finish("completed", message=outcome["message"], calls=fetcher.calls)

    return _start("fetch", target, bot=bot, from_date=start.isoformat(), to_date=end.isoformat())


def start_replay(
    user_id: str, bot: str, start: datetime.date, end: datetime.date, *, model: bool
) -> dict[str, Any]:
    ensure_store()
    config = service.saved_config(bot, user_id)
    if bot == "expiry" and not service.expiry_scope(config):
        raise ValueError("No index is enabled in Bot 2's settings, so there is nothing to backtest.")
    if bot in ("fly", "expiry") and not broker_live():
        raise ValueError(
            f"{service.BOT_LABELS[bot]} is sized from one lot's margin at today's levels, which "
            f"needs ICICI's margin calculator. This instance is in '{cfg.ICICI_BROKER_MODE}' mode."
        )
    run_id = str(uuid.uuid4())
    run: dict[str, Any] = {
        "id": run_id,
        "user_id": user_id,
        "bot": bot,
        "created_at": now_ist().isoformat(timespec="seconds"),
        "status": "running",
        "params": {
            "bot": bot,
            "label": service.BOT_LABELS[bot],
            "from": start.isoformat(),
            "to": end.isoformat(),
            "model": model,
            "config": config.model_dump(mode="json"),
        },
    }
    store.save_run(run)

    def target() -> None:
        try:
            lots, scopes = None, None
            if bot in ("fly", "expiry"):
                from icici_breeze_backend.app.services.processor import processor

                _log("Pricing one lot's margin at today's levels…")
                with _broker_scope(user_id):
                    sizing = service.price_lots(bot, config, user_id, processor())
                run["params"]["sizing"] = sizing["describe"]
                lots, scopes = sizing.get("lots"), sizing.get("scopes")
                _log(sizing["describe"])
            _log(f"Replaying {service.BOT_LABELS[bot]}, {start} to {end}…")
            pricer = ModelPricer() if model else RealPricer(OptionBook())
            result = service.replay(bot, start=start, end=end, config=config, pricer=pricer, lots=lots, scopes=scopes)
            summary = result.summary()
            book = getattr(pricer, "book", None)
            if book is not None and book.needs:
                store.add_needs(book.needs)
            summary["option_windows_missing"] = len(book.needs) if book is not None else 0
            run.update(status="completed", summary=summary, trades=service.trade_rows(result))
            store.save_run(run)
            waiting = summary.get("days_awaiting_data", 0)
            _finish(
                "completed",
                message=(
                    f"Done. {waiting} day(s) are waiting for option prices; fetch data, then run again."
                    if waiting
                    else "Done."
                ),
                run_id=run_id,
            )
        except Exception as exc:
            run.update(status="failed", error=str(exc))
            store.save_run(run)
            raise

    return _start("replay", target, bot=bot, run_id=run_id)


def start_bot_backtest(
    user_id: str,
    bot: str,
    period: str,
    from_date: Optional[datetime.date] = None,
    to_date: Optional[datetime.date] = None,
) -> dict[str, Any]:
    """The card's clock: one choice of period, and everything else follows (#35, #36).

    1. **Fetch what is missing, within today's budget.** Only when the broker is live, outside
       market hours, and `backtest_store.DAILY_CALL_BUDGET` has calls left. Anything that stops
       the fetch -- the budget, the market opening, a cancel -- is recorded as a note, never as a
       failure: the replay then runs on what is cached and reports the gap (the user's rule).
    2. **Size from today's margin** for the fly and Bot 2, as the live bots do.
    3. **Replay on real ICICI prices only.** The Black-Scholes path is not offered here.
    4. **Record it where live runs are recorded**: an Activity row (`trigger="backtest"`, never
       counted by a live guard -- see `repositories/bots.LIVE_RUNS_ONLY`) and a run-scoped zip
       of results, sharing one id with the stored run so the row opens its trades.

    A bot that reads a signal is replayed once per signal setting it could use
    (`backtest_combos`), so the row compares them; the saved setting's trades are the row's own.
    """
    from icici_breeze_backend.app.services.bots import backtest_combos
    from icici_breeze_backend.app.repositories import bots as repo
    from icici_breeze_backend.audit import bot_audit

    if bot not in service.BOT_TYPES:
        raise ValueError(f"{bot!r} has no backtest.")
    if period not in service.PERIODS:
        raise ValueError(f"Unknown period {period!r}.")
    ensure_store()
    config = service.saved_config(bot, user_id)
    if bot == "expiry" and not service.expiry_scope(config):
        raise ValueError("No index is enabled in Bot 2's settings, so there is nothing to backtest.")
    if bot in ("fly", "expiry") and not broker_live():
        raise ValueError(
            f"{service.BOT_LABELS[bot]} is sized from one lot's margin at today's levels, which "
            f"needs ICICI's margin calculator. This instance is in '{cfg.ICICI_BROKER_MODE}' mode."
        )
    now = now_ist()
    hol = service.holidays()
    start, end = service.resolve_period(period, from_date, to_date, now, hol)
    bot_type = service.BOT_TYPES[bot]
    combos = backtest_combos.combos_for(bot, config)
    with _lock:
        if _thread is not None and _thread.is_alive():
            raise Busy("A backtest is already running. Wait for it, or stop it.")

    run_id = repo.start_run(user_id, bot_type, "backtest")
    period_text = (
        f"{service.PERIOD_LABELS[period]} · {start}" if start == end
        else f"{service.PERIOD_LABELS[period]} · {start} to {end}"
    )
    run: dict[str, Any] = {
        "id": run_id,
        "user_id": user_id,
        "bot": bot,
        "created_at": now.isoformat(timespec="seconds"),
        "status": "running",
        "params": {
            "bot": bot,
            "label": service.BOT_LABELS[bot],
            "period": period,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "model": False,
            "config": config.model_dump(mode="json"),
        },
    }
    store.save_run(run)
    trail: list[dict[str, Any]] = [
        {
            "event": "backtest_started",
            "at": now.isoformat(timespec="seconds"),
            "run_id": run_id,
            "bot_type": bot_type,
            "period": period,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "config": run["params"]["config"],
        }
    ]

    def note(text: str, **extra: Any) -> None:
        _log(text)
        trail.append({"event": "note", "at": now_ist().isoformat(timespec="seconds"), "text": text, **extra})

    def target() -> None:
        notes: list[str] = []
        calls = 0
        try:
            # 1. fetch within budget
            remaining = store.calls_remaining(now.date())
            block = market_hours_reason()
            indices = service.indices_for(bot, config)
            if not broker_live():
                notes.append(
                    f"Nothing fetched: this instance is in '{cfg.ICICI_BROKER_MODE}' mode, so only "
                    "data already cached was replayed."
                )
            elif block:
                notes.append(f"Nothing fetched: {block} Replayed on cached data only.")
            elif remaining <= 0:
                notes.append(
                    f"Nothing fetched: today's backtest budget of {store.DAILY_CALL_BUDGET} ICICI "
                    "calls is spent. Replayed on cached data only."
                )
            else:
                note(f"Fetching missing data, up to {remaining} calls…")
                with _broker_scope(user_id):
                    fetcher = _fetcher(user_id)
                    fetcher.max_calls = remaining
                    try:
                        service.fetch_underlying(fetcher, bot, start, end, indices)
                        if bot != "expiry":
                            # Every signal setting reads the index futures, the warm-up included.
                            fetcher.fetch_futures(
                                "NIFTY", start - datetime.timedelta(days=service._SIGNAL_WARMUP_DAYS), end  # noqa: SLF001
                            )
                        outcome = service.backfill(
                            fetcher, bot, start=start, end=end, config=config, log=fetcher.log,
                            configs=[c.config for c in combos],
                        )
                        notes.append(outcome["message"])
                    except Stopped as exc:
                        notes.append(f"Fetch stopped early: {exc} Replayed on what was cached.")
                    finally:
                        calls = fetcher.calls
                        store.add_calls(now.date(), calls)
            for text in notes:
                note(text)

            # 2. size from today's margin
            lots, scopes = None, None
            if bot in ("fly", "expiry"):
                from icici_breeze_backend.app.services.processor import processor

                note("Pricing one lot's margin at today's levels…")
                with _broker_scope(user_id):
                    sizing = service.price_lots(bot, config, user_id, processor())
                run["params"]["sizing"] = sizing["describe"]
                lots, scopes = sizing.get("lots"), sizing.get("scopes")
                note(sizing["describe"])

            # 3. replay on real prices, once per signal setting
            book = OptionBook()
            readings_cache: dict[str, Any] = {}
            results: list[tuple[Any, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]] = []
            for n, combo in enumerate(combos, start=1):
                if _cancel.is_set():
                    raise RuntimeError("Stopped at your request.")
                note(f"Replaying {service.BOT_LABELS[bot]} ({n}/{len(combos)}: {combo.label}), "
                     f"{start} to {end}, on real ICICI prices…")
                try:
                    result = service.replay(
                        bot, start=start, end=end, config=combo.config, pricer=RealPricer(book),
                        lots=lots, scopes=scopes, holidays_=hol, readings_cache=readings_cache,
                        record_decisions=True,
                    )
                except service.NoCachedData as exc:
                    raise service.NoCachedData(
                        f"{exc} " + (" ".join(notes) if notes else "")
                    ) from exc
                results.append((combo, result.summary(), service.trade_rows(result),
                                list(getattr(result, "decisions", []) or [])))
            if book.needs:
                store.add_needs(book.needs)
            rows = [backtest_combos.comparison_row(c, s_, t) for c, s_, t, _d in results]
            saved_combo, summary, trades, _decisions = next(
                (r for r in results if r[0].is_saved), results[0]
            )
            summary = dict(summary)
            summary["signal_setting"] = saved_combo.label
            summary["comparison"] = rows
            summary["option_windows_missing"] = len(book.needs)
            summary["calls_spent"] = calls
            summary["notes"] = notes
            run.update(status="completed", summary=summary, trades=trades,
                       combos=[{**row, "trades_list": t} for row, (_c, _s, t, _d) in zip(rows, results)])
            store.save_run(run)

            # 4. record
            for trade in trades:
                trail.append({"event": "trade", **trade})
            trail.append({"event": "backtest_finished", "at": now_ist().isoformat(timespec="seconds"), "summary": summary})
            bot_audit.write_backtest_zip(
                user_id, bot_type, run_id,
                backtest_combos.zip_members(
                    {**run, "summary": {k: v for k, v in summary.items() if k != "comparison"}},
                    results, rows, trail,
                ),
            )

            waiting = int(summary.get("days_awaiting_data") or 0)
            reason_text = backtest_combos.headline(period_text, rows)
            repo.finish_run(
                run_id,
                status="completed",
                reason_code="backtest_gaps" if waiting else "backtest_complete",
                reason_text=reason_text,
                detail={
                    "backtest_run_id": run_id,
                    "period": period,
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                    "summary": summary,
                },
            )
            store.enforce_cache_cap()
            _finish("completed", message=reason_text, run_id=run_id, calls=calls)
        except Exception as exc:
            run.update(status="failed", error=str(exc))
            store.save_run(run)
            trail.append({"event": "backtest_failed", "at": now_ist().isoformat(timespec="seconds"), "error": str(exc)})
            try:
                bot_audit.write_backtest_audit(user_id, bot_type, run_id, trail)
            except OSError:
                _logger.warning("backtest: could not write the audit trail for %s", run_id)
            repo.finish_run(
                run_id,
                status="failed",
                reason_code="backtest_failed",
                reason_text=f"{period_text}: {exc}",
                detail={"backtest_run_id": run_id, "period": period, "from": start.isoformat(), "to": end.isoformat()},
            )
            raise

    try:
        return _start(
            "backtest", target, bot=bot, run_id=run_id,
            from_date=start.isoformat(), to_date=end.isoformat(), period=period,
        )
    except Busy:
        # Lost a race to another job between the check above and here: close the row this
        # call opened rather than leave a replay that never ran looking as if it is running.
        repo.finish_run(
            run_id, status="skipped", reason_code="backtest_busy",
            reason_text="Another backtest was already running.",
        )
        run.update(status="failed", error="Another backtest was already running.")
        store.save_run(run)
        raise
