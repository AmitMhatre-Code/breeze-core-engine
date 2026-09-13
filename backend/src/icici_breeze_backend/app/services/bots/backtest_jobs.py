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
