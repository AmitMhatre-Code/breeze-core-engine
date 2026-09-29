"""Daily IST scheduler for reference data loads."""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time

from icici_breeze_backend.app.core.timezone import IST, now_ist
from icici_breeze_backend.app.services.reference_data.state import load_schedule, save_schedule

_logger = logging.getLogger(__name__)
_thread: threading.Thread | None = None
_stop = threading.Event()
_last_run_date: str | None = None
# Monotonic time of the last late-publish bhavcopy retry (see `_bhavcopy_retry_due`).
_last_bhavcopy_retry: float | None = None
_BHAVCOPY_RETRY_SECONDS = 30 * 60


def configure_reference_data_schedule(enabled: bool, hour_ist: int, minute_ist: int) -> dict:
    save_schedule(enabled, hour_ist, minute_ist)
    if enabled:
        start_reference_data_scheduler()
    else:
        stop_reference_data_scheduler()
    return get_scheduler_status()


def get_scheduler_status() -> dict:
    sch = load_schedule()
    return {
        "enabled": sch["enabled"],
        "hour_ist": sch["hour_ist"],
        "minute_ist": sch["minute_ist"],
        "running": bool(_thread and _thread.is_alive()),
    }


def _scheduler_loop() -> None:
    global _last_run_date
    while not _stop.is_set():
        sch = load_schedule()
        if sch.get("enabled"):
            now = now_ist()
            today = now.date().isoformat()
            if (
                now.hour == int(sch["hour_ist"])
                and now.minute == int(sch["minute_ist"])
                and _last_run_date != today
            ):
                _last_run_date = today
                _logger.info("Scheduled reference data load at %s IST", now.isoformat(timespec="seconds"))
                from icici_breeze_backend.app.services.reference_data.orchestrator import (
                    run_reference_data_load,
                )

                run_reference_data_load(force=True, trigger_mode="scheduled")
            elif _bhavcopy_retry_due(now, sch):
                _retry_stale_bhavcopy()
        _stop.wait(30)


def _bhavcopy_retry_due(now: dt.datetime, sch: dict) -> bool:
    """A segment still holds an older session's bhavcopy, and the scheduled load for the latest
    concluded session has already had its turn. Runs through the evening and overnight until
    the file lands; never in market hours, when parsing it in-process would compete with the
    tick feed (design-decisions #46) and the websocket is the source anyway."""
    from icici_breeze_backend.app.services.market_calendar import is_market_open
    from icici_breeze_backend.app.services.quote_source_router import latest_concluded_trading_day
    from icici_breeze_backend.app.services.reference_data.orchestrator import stale_bhavcopy_segments

    if _last_bhavcopy_retry is not None and time.monotonic() - _last_bhavcopy_retry < _BHAVCOPY_RETRY_SECONDS:
        return False
    if is_market_open(now):
        return False
    concluded = latest_concluded_trading_day(now)
    scheduled_at = dt.datetime.combine(
        concluded, dt.time(int(sch["hour_ist"]), int(sch["minute_ist"])), tzinfo=IST
    )
    if now < scheduled_at:
        return False
    return bool(stale_bhavcopy_segments(now))


def _retry_stale_bhavcopy() -> None:
    global _last_bhavcopy_retry
    from icici_breeze_backend.app.services.reference_data.orchestrator import retry_stale_bhavcopy

    _last_bhavcopy_retry = time.monotonic()
    try:
        retry_stale_bhavcopy()
    except Exception:
        _logger.exception("Late-publish bhavcopy retry failed")


def start_reference_data_scheduler() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_scheduler_loop, name="reference-data-scheduler", daemon=True)
    _thread.start()


def stop_reference_data_scheduler() -> None:
    _stop.set()
    global _thread
    _thread = None


def bootstrap_reference_data_schedule() -> None:
    from icici_breeze_backend.app.services.reference_data.span_scheduler import start_span_scheduler
    from icici_breeze_backend.app.services.reference_data.state import ensure_reference_data_tables

    ensure_reference_data_tables()
    sch = load_schedule()
    if sch.get("enabled", True):
        start_reference_data_scheduler()
    # The SPAN slots run on their own fixed cadence and are not covered by the daily
    # schedule's enabled flag -- disabling the once-a-day full load should not silently stop
    # margins tracking the exchanges' intraday risk files.
    start_span_scheduler()


def bootstrap_reference_data_on_startup() -> None:
    """Load all reference data sources during application startup."""
    from icici_breeze_backend.app.services.reference_data.cache_bootstrap import (
        ensure_all_reference_data_cached,
        is_reference_data_complete,
    )
    from icici_breeze_backend.app.services.reference_data.orchestrator import run_reference_data_load

    bootstrap_reference_data_schedule()
    ensure_all_reference_data_cached()
    if is_reference_data_complete():
        _logger.info("Reference data already complete in Redis; skipping startup network load")
        return
    try:
        run_reference_data_load(force=True, trigger_mode="startup")
    except Exception:
        _logger.exception("Startup reference data load failed")
