"""Intraday IST scheduler for the SPAN margin baseline.

Separate from the daily reference-data scheduler because the cadence is different in kind:
bhavcopy and the ICICI scrip master only change end-of-day, while both exchanges republish
their risk file several times a session. Runs in the API process only -- the chain-builder
worker must not also fetch and ingest, or the two would race on the same SQLite table.
"""
from __future__ import annotations

import logging
import threading
import uuid

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.services.reference_data.state import append_ingest_history

_logger = logging.getLogger(__name__)
_thread: threading.Thread | None = None
_stop = threading.Event()
_lock = threading.RLock()
# slot "HH:MM" -> ISO date it last fired on, so a slot fires once a day even though the loop
# wakes several times inside its minute.
_fired: dict[str, str] = {}

_MARKET_LABELS = {"nse": ("nse_span_baseline", "NSE SPAN Baseline"), "bse": ("bse_span_baseline", "BSE SPAN Baseline")}


def _slot_key(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def get_span_scheduler_status() -> dict:
    return {
        "enabled": bool(cfg.SPAN_INTRADAY_REFRESH_ENABLED),
        "slots_ist": [_slot_key(h, m) for h, m in cfg.SPAN_REFRESH_SLOTS_IST],
        "running": bool(_thread and _thread.is_alive()),
    }


def _record(market: str, out: dict, *, slot: str) -> None:
    """History gets the ingests, not the no-ops.

    A slot that finds the archive it already holds is the expected outcome most of the time;
    logging those would bury the loads that actually changed the margins.
    """
    success = out.get("Success") if isinstance(out.get("Success"), dict) else {}
    ok = out.get("Status") == 200
    if ok and success.get("skipped"):
        return
    kind, label = _MARKET_LABELS[market]
    append_ingest_history(
        {
            "id": str(uuid.uuid4()),
            "kind": kind,
            "display_name": label,
            "source_file_date": str(success.get("source_date") or "") or None,
            "row_count": int(success.get("inserted_rows") or 0),
            "ingested_at": now_ist().isoformat(timespec="seconds"),
            "ok": ok,
            "notes": (
                f"{slot} IST slot: {success.get('source_file') or ''}"
                if ok
                else f"{slot} IST slot: {out.get('Error') or 'refresh failed'}"
            ),
            "source_url": str(success.get("source_url") or "") or None,
        }
    )


def run_span_refresh_slot(slot: str) -> dict[str, dict]:
    """Refresh both markets for one slot and log whatever actually landed."""
    from icici_breeze_backend.app.services.nsccl_baseline import refresh_all_span_baselines

    results = refresh_all_span_baselines()
    for market, out in results.items():
        try:
            _record(market, out, slot=slot)
        except Exception:
            _logger.exception("Recording SPAN refresh history failed for %s", market)
        if out.get("Status") != 200:
            _logger.warning("SPAN refresh failed for %s at %s IST: %s", market, slot, out.get("Error"))
    return results


def _scheduler_loop() -> None:
    while not _stop.is_set():
        try:
            now = now_ist()
            today = now.date().isoformat()
            for hour, minute in cfg.SPAN_REFRESH_SLOTS_IST:
                if now.hour != int(hour) or now.minute != int(minute):
                    continue
                slot = _slot_key(hour, minute)
                with _lock:
                    if _fired.get(slot) == today:
                        continue
                    _fired[slot] = today
                _logger.info("Scheduled SPAN baseline refresh for the %s IST slot", slot)
                run_span_refresh_slot(slot)
        except Exception:
            _logger.exception("SPAN baseline scheduler tick failed")
        _stop.wait(30)


def start_span_scheduler() -> None:
    global _thread
    if not cfg.SPAN_INTRADAY_REFRESH_ENABLED:
        _logger.info("Intraday SPAN baseline refresh is disabled")
        return
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_scheduler_loop, name="span-baseline-scheduler", daemon=True)
    _thread.start()
    _logger.info(
        "Intraday SPAN baseline scheduler started for slots %s IST",
        ", ".join(_slot_key(h, m) for h, m in cfg.SPAN_REFRESH_SLOTS_IST),
    )


def stop_span_scheduler() -> None:
    global _thread
    _stop.set()
    _thread = None
