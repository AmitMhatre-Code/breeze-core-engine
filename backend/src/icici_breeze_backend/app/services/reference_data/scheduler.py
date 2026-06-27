"""Daily IST scheduler for reference data loads."""
from __future__ import annotations

import logging
import threading
import time

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.services.reference_data.orchestrator import run_reference_data_load
from icici_breeze_backend.app.services.reference_data.state import load_schedule, save_schedule

_logger = logging.getLogger(__name__)
_thread: threading.Thread | None = None
_stop = threading.Event()
_last_run_date: str | None = None


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
                run_reference_data_load(force=True, trigger_mode="scheduled")
        _stop.wait(30)


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
    from icici_breeze_backend.app.services.reference_data.state import ensure_reference_data_tables

    ensure_reference_data_tables()
    sch = load_schedule()
    if sch.get("enabled", True):
        start_reference_data_scheduler()


def bootstrap_reference_data_on_startup() -> None:
    """Non-blocking startup load when cache is empty."""
    from icici_breeze_backend.app.services.reference_data.bhavcopy_store import load_local_from_redis
    from icici_breeze_backend.app.services.reference_data.orchestrator import trigger_reference_data_load_now
    from icici_breeze_backend.app.services.reference_data.scrip_index import current_version, get_underlyings

    bootstrap_reference_data_schedule()
    load_local_from_redis()
    if current_version() <= 0 or not get_underlyings(cfg.NFO):
        trigger_reference_data_load_now(force=False)
