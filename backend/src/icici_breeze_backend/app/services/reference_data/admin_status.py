"""Admin status aggregation for Reference Data Loads settings."""
from __future__ import annotations

from typing import Any

from icici_breeze_backend.app.services.reference_data.bhavcopy_store import get_bhavcopy_source_date
from icici_breeze_backend.app.services.reference_data.scheduler import get_scheduler_status
from icici_breeze_backend.app.services.reference_data.state import fetch_ingest_history, load_progress_state
import icici_breeze_backend.app.core.config as cfg


def get_reference_data_admin_status() -> dict[str, Any]:
    sch = get_scheduler_status()
    prog = load_progress_state()
    nse_date = get_bhavcopy_source_date(cfg.NFO)
    bse_date = get_bhavcopy_source_date(cfg.BFO)
    return {
        "enabled": sch["enabled"],
        "hour_ist": sch["hour_ist"],
        "minute_ist": sch["minute_ist"],
        "running": sch["running"],
        "refresh_in_progress": bool(prog.get("refresh_in_progress")),
        "last_refresh_message": prog.get("last_refresh_message"),
        "nse_fo_refresh_in_progress": bool(prog.get("nse_fo_refresh_in_progress")),
        "nse_fo_progress_pct": int(prog.get("nse_fo_progress_pct") or 0),
        "nse_fo_message": prog.get("nse_fo_message"),
        "nse_fo_source_date": nse_date.isoformat() if nse_date else None,
        "bse_fo_refresh_in_progress": bool(prog.get("bse_fo_refresh_in_progress")),
        "bse_fo_progress_pct": int(prog.get("bse_fo_progress_pct") or 0),
        "bse_fo_message": prog.get("bse_fo_message"),
        "bse_fo_source_date": bse_date.isoformat() if bse_date else None,
        "scrip_refresh_in_progress": bool(prog.get("scrip_refresh_in_progress")),
        "scrip_progress_pct": int(prog.get("scrip_progress_pct") or 0),
        "scrip_message": prog.get("scrip_message"),
        "span_refresh_in_progress": bool(prog.get("span_refresh_in_progress")),
        "span_progress_pct": int(prog.get("span_progress_pct") or 0),
        "span_message": prog.get("span_message"),
        "ingest_history": fetch_ingest_history(),
    }
