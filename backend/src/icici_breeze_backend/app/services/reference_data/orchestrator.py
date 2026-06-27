"""Unified reference data batch loader."""
from __future__ import annotations

import logging
import threading
import uuid
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.services.nsccl_baseline import refresh_exchange_risk_baseline
from icici_breeze_backend.app.services.processor import processor
from icici_breeze_backend.app.services.reference_data import bhavcopy_bse, bhavcopy_nse, bhavcopy_store, scrip_index
from icici_breeze_backend.app.services.reference_data.state import (
    append_ingest_history,
    load_progress_state,
    save_progress_state,
)

_logger = logging.getLogger(__name__)
_lock = threading.RLock()
_refresh_thread: threading.Thread | None = None

_SOURCE_LABELS = {
    "nse_fo": ("nse_fo_bhavcopy", "NSE FO BhavCopy"),
    "bse_fo": ("bse_fo_bhavcopy", "BSE FO BhavCopy"),
    "scrip": ("icici_scrip_master", "ICICI Scrip Master"),
    "span": ("nse_span_baseline", "NSE SPAN Baseline"),
}


def _merge_state(updates: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        state = load_progress_state()
        state.update(updates)
        save_progress_state(state)
        return dict(state)


def _set_source(source: str, **updates: Any) -> None:
    prefix = {
        "nse_fo": "nse_fo",
        "bse_fo": "bse_fo",
        "scrip": "scrip",
        "span": "span",
    }.get(source)
    if not prefix:
        return
    mapped: dict[str, Any] = {}
    for k, v in updates.items():
        if k == "in_progress":
            mapped[f"{prefix}_refresh_in_progress"] = v
        elif k == "progress_pct":
            mapped[f"{prefix}_progress_pct"] = v
        elif k == "message":
            mapped[f"{prefix}_message"] = v
        else:
            mapped[f"{prefix}_{k}"] = v
    _merge_state(mapped)


def _record_ingest(source: str, *, ok: bool, source_date: str | None, row_count: int, url: str | None, notes: str | None = None) -> None:
    kind, label = _SOURCE_LABELS[source]
    append_ingest_history(
        {
            "id": str(uuid.uuid4()),
            "kind": kind,
            "display_name": label,
            "source_file_date": source_date,
            "row_count": row_count,
            "ingested_at": now_ist().isoformat(timespec="seconds"),
            "ok": ok,
            "notes": notes,
            "source_url": url,
        }
    )


def run_reference_data_load(*, force: bool = False, trigger_mode: str = "manual") -> dict[str, Any]:
    _logger.info("Reference data load started (mode=%s force=%s)", trigger_mode, force)
    _merge_state(
        {
            "refresh_in_progress": True,
            "last_refresh_message": f"Reference data load started ({trigger_mode})",
        }
    )
    ok_all = True
    from icici_breeze_backend.app.services.reference_data.scrip_index import _next_version

    batch_version = _next_version()
    try:
        # NSE FO Bhavcopy
        _set_source("nse_fo", in_progress=True, progress_pct=0, message="Starting NSE FO BhavCopy")

        def nse_progress(cur: int, total: int, msg: str) -> None:
            pct = int((cur * 70) / max(1, total))
            _set_source("nse_fo", progress_pct=pct, message=msg)

        nse = bhavcopy_nse.fetch_latest_nse_fo_bhavcopy(progress_cb=nse_progress)
        if nse:
            rows, day, url = nse
            bhavcopy_store.publish_bhavcopy_rows(
                rows, segment="nfo", source_date=day, source_url=url, version=batch_version
            )
            _set_source("nse_fo", in_progress=False, progress_pct=100, message=f"Loaded {len(rows)} rows from {day.isoformat()}")
            _record_ingest("nse_fo", ok=True, source_date=day.isoformat(), row_count=len(rows), url=url)
        else:
            ok_all = False
            _set_source("nse_fo", in_progress=False, progress_pct=100, message="No NSE FO BhavCopy found")
            _record_ingest("nse_fo", ok=False, source_date=None, row_count=0, url=None, notes="not_found")

        # BSE FO Bhavcopy
        _set_source("bse_fo", in_progress=True, progress_pct=0, message="Starting BSE FO BhavCopy")

        def bse_progress(cur: int, total: int, msg: str) -> None:
            pct = int((cur * 70) / max(1, total))
            _set_source("bse_fo", progress_pct=pct, message=msg)

        bse = bhavcopy_bse.fetch_latest_bse_fo_bhavcopy(progress_cb=bse_progress)
        if bse:
            rows, day, url = bse
            bhavcopy_store.publish_bhavcopy_rows(
                rows, segment="bfo", source_date=day, source_url=url, version=batch_version
            )
            _set_source("bse_fo", in_progress=False, progress_pct=100, message=f"Loaded {len(rows)} rows from {day.isoformat()}")
            _record_ingest("bse_fo", ok=True, source_date=day.isoformat(), row_count=len(rows), url=url)
        else:
            ok_all = False
            _set_source("bse_fo", in_progress=False, progress_pct=100, message="No BSE FO BhavCopy found")
            _record_ingest("bse_fo", ok=False, source_date=None, row_count=0, url=None, notes="not_found")

        # Scrip master
        _set_source("scrip", in_progress=True, progress_pct=10, message="Downloading ICICI scrip master")
        if getattr(cfg, "ICICI_BROKER_MODE", "live") != "mock":
            try:
                processor().update_ICICImaster()
                _set_source("scrip", progress_pct=80, message="Publishing scrip index to cache")
                scrip_index.publish_scrip_index_from_db(version=batch_version)
                _set_source("scrip", in_progress=False, progress_pct=100, message="Scrip master loaded")
                _record_ingest("scrip", ok=True, source_date=now_ist().date().isoformat(), row_count=0, url=cfg.ICICI_MASTERFILE_URL)
            except Exception as exc:
                ok_all = False
                _set_source("scrip", in_progress=False, progress_pct=100, message=f"Scrip master failed: {exc}")
                _record_ingest("scrip", ok=False, source_date=None, row_count=0, url=cfg.ICICI_MASTERFILE_URL, notes=str(exc))
        else:
            scrip_index.publish_scrip_index_from_db(version=batch_version)
            _set_source("scrip", in_progress=False, progress_pct=100, message="Scrip index published (mock mode)")
            _record_ingest("scrip", ok=True, source_date=now_ist().date().isoformat(), row_count=0, url=None)

        # SPAN baseline
        _set_source("span", in_progress=True, progress_pct=20, message="Refreshing NSE SPAN baseline")
        span_out = refresh_exchange_risk_baseline()
        span_ok = span_out.get("Status") == 200
        if not span_ok:
            ok_all = False
        _set_source(
            "span",
            in_progress=False,
            progress_pct=100,
            message="SPAN baseline refreshed" if span_ok else (span_out.get("Error") or "SPAN refresh failed"),
        )
        success = span_out.get("Success") if isinstance(span_out.get("Success"), dict) else {}
        _record_ingest(
            "span",
            ok=span_ok,
            source_date=str(success.get("archive_date") or "") or None,
            row_count=int(success.get("contracts_loaded") or 0),
            url=str(success.get("source_url") or "") or None,
            notes=None if span_ok else str(span_out.get("Error") or ""),
        )

        msg = "Reference data load completed" if ok_all else "Reference data load completed with errors"
        _merge_state({"refresh_in_progress": False, "last_refresh_message": msg})
        return {"ok": ok_all, "message": msg, "version": batch_version}
    except Exception as exc:
        _logger.exception("Reference data load failed")
        _merge_state({"refresh_in_progress": False, "last_refresh_message": f"Load failed: {exc}"})
        return {"ok": False, "message": str(exc)}


def trigger_reference_data_load_now(*, force: bool = False) -> dict[str, Any]:
    global _refresh_thread
    with _lock:
        state = load_progress_state()
        if state.get("refresh_in_progress"):
            return {"started": False, "reason": "already_in_progress"}

    def _run() -> None:
        global _refresh_thread
        try:
            run_reference_data_load(force=force, trigger_mode="manual")
        finally:
            _refresh_thread = None

    t = threading.Thread(target=_run, name="reference-data-load", daemon=True)
    with _lock:
        _refresh_thread = t
    t.start()
    return {"started": True}


def is_load_in_progress() -> bool:
    return bool(load_progress_state().get("refresh_in_progress"))
