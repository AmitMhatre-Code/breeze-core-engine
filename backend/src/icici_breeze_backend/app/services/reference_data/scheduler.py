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
# Monotonic time of the last catch-up load for a stale scrip master (see `_scrip_retry_due`).
_last_scrip_retry: float | None = None
# A start-up load that fails is tried again after these waits, in the background.
_STARTUP_RETRY_WAITS_SECONDS = (120, 600)


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
    while not _stop.is_set():
        try:
            _scheduler_tick()
        except Exception:  # noqa: BLE001 -- e.g. "database is locked" must not end the thread
            _logger.exception("Reference data scheduler tick failed")
        _stop.wait(30)


def _scheduler_tick() -> None:
    global _last_run_date
    sch = load_schedule()
    if not sch.get("enabled"):
        return
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
    elif _scrip_retry_due(now, sch):
        _retry_stale_scrip_master()
    elif _bhavcopy_retry_due(now, sch):
        _retry_stale_bhavcopy()


def _scrip_retry_due(now: dt.datetime, sch: dict) -> bool:
    """The scrip master is older than the latest concluded session and the scheduled load for
    that session has had its turn: it was missed or it failed (B-27, B-57). Same spacing and
    the same market-hours rule as the bhavcopy retry below; a start-up load runs regardless
    (`bootstrap_reference_data_on_startup`)."""
    from icici_breeze_backend.app.services.market_calendar import is_market_open
    from icici_breeze_backend.app.services.quote_source_router import latest_concluded_trading_day
    from icici_breeze_backend.app.services.reference_data.orchestrator import scrip_master_is_stale

    if _last_scrip_retry is not None and time.monotonic() - _last_scrip_retry < _BHAVCOPY_RETRY_SECONDS:
        return False
    if is_market_open(now):
        return False
    concluded = latest_concluded_trading_day(now)
    scheduled_at = dt.datetime.combine(
        concluded, dt.time(int(sch["hour_ist"]), int(sch["minute_ist"])), tzinfo=IST
    )
    if now < scheduled_at:
        return False
    return scrip_master_is_stale(now)


def _retry_stale_scrip_master() -> None:
    """A full load, not a patch into the live generation: a new master changes the tradeable
    set, the token map and lot sizes, so it is built as a new version and flipped once whole."""
    global _last_scrip_retry
    from icici_breeze_backend.app.services.reference_data.orchestrator import run_reference_data_load

    _last_scrip_retry = time.monotonic()
    _logger.info("Scrip master is older than the last session; running a catch-up load")
    try:
        run_reference_data_load(force=True, trigger_mode="catch_up")
    except Exception:
        _logger.exception("Catch-up reference data load failed")


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

    from icici_breeze_backend.app.services.reference_data.orchestrator import stale_reference_sources

    bootstrap_reference_data_schedule()
    ensure_all_reference_data_cached()
    complete = is_reference_data_complete()
    stale = stale_reference_sources() if complete else []
    if complete and not stale:
        _logger.info("Reference data complete and current; skipping startup network load")
        return
    if complete:
        # Serve on what is loaded while the new data is fetched, then flip to it whole. This
        # runs in market hours too: the scrip master decides which contracts get a quote,
        # and waiting for the evening left new strikes unquoted all session (B-57; #46 as
        # revised on 2026-09-30).
        _logger.warning(
            "Reference data is older than the last session (%s); refreshing in the background",
            ", ".join(stale),
        )
        _start_startup_refresh(first_wait=0)
        return
    try:
        result = run_reference_data_load(force=True, trigger_mode="startup")
    except Exception:
        _logger.exception("Startup reference data load failed")
        result = {"ok": False}
    if not (result or {}).get("ok") or stale_reference_sources():
        _start_startup_refresh(first_wait=_STARTUP_RETRY_WAITS_SECONDS[0])


def _start_startup_refresh(*, first_wait: int) -> None:
    """Load in the background until nothing is stale, within a few attempts. After that the
    scheduler's catch-up retry takes over."""
    from icici_breeze_backend.app.services.reference_data.orchestrator import (
        run_reference_data_load,
        stale_reference_sources,
    )

    waits = [first_wait] + [w for w in _STARTUP_RETRY_WAITS_SECONDS if w > first_wait]

    def _run() -> None:
        for attempt, wait in enumerate(waits, start=1):
            if wait and _stop.wait(wait):
                return
            try:
                run_reference_data_load(force=True, trigger_mode="startup")
            except Exception:
                _logger.exception("Startup reference data load failed (attempt %d)", attempt)
            left = stale_reference_sources()
            if not left:
                _logger.info("Startup reference data refresh complete (attempt %d)", attempt)
                return
            _logger.warning(
                "Reference data still older than the last session after attempt %d: %s",
                attempt, ", ".join(left),
            )

    threading.Thread(target=_run, name="reference-data-startup", daemon=True).start()
