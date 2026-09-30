"""Unified reference data batch loader."""
from __future__ import annotations

import datetime as dt
import logging
import threading
import uuid
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.services.processor import processor
from icici_breeze_backend.app.services.reference_data import bhavcopy_bse, bhavcopy_nse, bhavcopy_store, scrip_index
from icici_breeze_backend.app.services.reference_data.span_refresh_runner import refresh_all_span_baselines
from icici_breeze_backend.app.services.reference_data.state import (
    append_ingest_history,
    load_progress_state,
    save_progress_state,
)

_logger = logging.getLogger(__name__)
_lock = threading.RLock()
_refresh_thread: threading.Thread | None = None
# Held for the whole of a full load or a late-publish retry, so the two never interleave their
# bhavcopy publishes. In-process on purpose: the persisted `refresh_in_progress` flag survives a
# process killed mid-load and would then block the retry for good.
_load_mutex = threading.Lock()

_SOURCE_LABELS = {
    "nse_fo": ("nse_fo_bhavcopy", "NSE FO BhavCopy"),
    "bse_fo": ("bse_fo_bhavcopy", "BSE FO BhavCopy"),
    "scrip": ("icici_scrip_master", "ICICI Scrip Master"),
    "span": ("nse_span_baseline", "NSE SPAN Baseline"),
    "span_bse": ("bse_span_baseline", "BSE SPAN Baseline"),
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
        "span_bse": "span",
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
    with _load_mutex:
        return _run_reference_data_load(force=force, trigger_mode=trigger_mode)


def _run_reference_data_load(*, force: bool, trigger_mode: str) -> dict[str, Any]:
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

        # Scrip master -- ICICI_MASTERFILE_URL is an unauthenticated public download (no
        # BreezeConnect session, API key, or static IP required; see update_ICICImaster()),
        # so this always runs regardless of ICICI_BROKER_MODE. Mock mode only affects
        # genuinely authenticated ICICI calls (trading, portfolio, WS ticks) elsewhere.
        _set_source("scrip", in_progress=True, progress_pct=10, message="Downloading ICICI scrip master")
        try:
            processor().update_ICICImaster(publish_scrip_index=False)
            _set_source("scrip", progress_pct=80, message="Publishing scrip index to cache")
            scrip_index.publish_scrip_index_from_db(version=batch_version)
            scrip_rows = scrip_index.scrip_master_row_count()
            _set_source(
                "scrip",
                in_progress=False,
                progress_pct=100,
                message=f"Scrip master loaded ({scrip_rows} rows)",
            )
            _record_ingest(
                "scrip",
                ok=True,
                source_date=now_ist().date().isoformat(),
                row_count=scrip_rows,
                url=cfg.ICICI_MASTERFILE_URL,
            )
        except Exception as exc:
            # No network (offline dev, airgapped CI): fall back to whatever's already
            # loaded locally rather than leaving the scrip index unpublished.
            ok_all = False
            _logger.warning("ICICI scrip master download failed, republishing existing local data: %s", exc)
            scrip_index.publish_scrip_index_from_db(version=batch_version)
            scrip_rows = scrip_index.scrip_master_row_count()
            _set_source(
                "scrip",
                in_progress=False,
                progress_pct=100,
                message=f"Scrip master download failed, republished existing local data ({scrip_rows} rows): {exc}",
            )
            _record_ingest(
                "scrip",
                ok=False,
                source_date=None,
                row_count=scrip_rows,
                url=cfg.ICICI_MASTERFILE_URL,
                notes=str(exc),
            )

        # SPAN baselines (both exchanges). The intraday scheduler refreshes these on its own
        # cadence too; this keeps the full load self-contained for a manual or startup run.
        _set_source("span", in_progress=True, progress_pct=20, message="Refreshing SPAN baselines")
        span_results = refresh_all_span_baselines()
        span_messages: list[str] = []
        for market, span_out in span_results.items():
            span_ok = span_out.get("Status") == 200
            if not span_ok:
                ok_all = False
            success = span_out.get("Success") if isinstance(span_out.get("Success"), dict) else {}
            if span_ok and success.get("skipped"):
                span_messages.append(f"{market.upper()} already current")
                continue
            span_messages.append(
                f"{market.upper()} refreshed" if span_ok else f"{market.upper()}: {span_out.get('Error') or 'failed'}"
            )
            _record_ingest(
                "span" if market == "nse" else "span_bse",
                ok=span_ok,
                source_date=str(success.get("source_date") or "") or None,
                row_count=int(success.get("inserted_rows") or 0),
                url=str(success.get("source_url") or success.get("source_file") or "") or None,
                notes=None if span_ok else str(span_out.get("Error") or ""),
            )
        _set_source(
            "span",
            in_progress=False,
            progress_pct=100,
            message="; ".join(span_messages) or "SPAN baselines unchanged",
        )

        msg = "Reference data load completed" if ok_all else "Reference data load completed with errors"
        _merge_state({"refresh_in_progress": False, "last_refresh_message": msg})
        return {"ok": ok_all, "message": msg, "version": batch_version}
    except Exception as exc:
        _logger.exception("Reference data load failed")
        _merge_state({"refresh_in_progress": False, "last_refresh_message": f"Load failed: {exc}"})
        return {"ok": False, "message": str(exc)}


def last_scrip_master_ingest() -> dt.datetime | None:
    """When the ICICI scrip master last loaded successfully, from the ingest history."""
    from icici_breeze_backend.app.services.reference_data.state import fetch_ingest_history

    kind = _SOURCE_LABELS["scrip"][0]
    for row in fetch_ingest_history():
        if row.get("kind") != kind or not row.get("ok"):
            continue
        try:
            at = dt.datetime.fromisoformat(str(row.get("ingested_at") or ""))
        except ValueError:
            continue
        from icici_breeze_backend.app.core.timezone import IST

        return at if at.tzinfo else at.replace(tzinfo=IST)
    return None


def scrip_master_is_stale(now: dt.datetime | None = None) -> bool:
    """True when the scrip master last loaded before the latest concluded session closed.

    The scrip master decides which contracts are tradeable (`MarginPercentage > 0`), and so
    which get a live quote at all, their tokens and lot sizes; that set changes from one
    session to the next. An instance off at the scheduled load kept an old one indefinitely:
    on 2026-09-30 a master from 27-Sep left SENSEX 71,900 PE with no quote (B-57).
    """
    from icici_breeze_backend.app.core.timezone import IST
    from icici_breeze_backend.app.services.market_calendar import get_calendar_config
    from icici_breeze_backend.app.services.quote_source_router import latest_concluded_trading_day

    now = now or now_ist()
    concluded = latest_concluded_trading_day(now)
    close_at = get_calendar_config().close_time(
        dt.datetime.combine(concluded, dt.time(0, 0), tzinfo=IST)
    )
    last = last_scrip_master_ingest()
    return last is None or last < close_at


def stale_reference_sources(now: dt.datetime | None = None) -> list[str]:
    """Which loaded sources are older than the latest concluded session: "scrip", and the
    options exchanges whose bhavcopy is behind."""
    out: list[str] = []
    try:
        if scrip_master_is_stale(now):
            out.append("scrip")
    except Exception:  # noqa: BLE001 -- unreadable history: reload rather than trust it
        _logger.warning("Could not judge the scrip master's age", exc_info=True)
        out.append("scrip")
    out.extend(stale_bhavcopy_segments(now))
    return out


def stale_bhavcopy_segments(now: dt.datetime | None = None) -> list[str]:
    """Options exchanges whose loaded bhavcopy predates the latest concluded session."""
    from icici_breeze_backend.app.services.quote_source_router import bhavcopy_is_fresh

    return [ex for ex in (cfg.NFO, cfg.BFO) if not bhavcopy_is_fresh(ex, now)]


def retry_stale_bhavcopy(now: dt.datetime | None = None) -> dict[str, str]:
    """Fetch the latest concluded session's bhavcopy for each segment still holding an older one.

    The daily load runs once, and `fetch_latest_*` quietly falls back to the previous session's
    file when the exchange has not published yet -- which then stood for another whole day
    (2026-09-29: Friday's closes all Tuesday). Only the missing day's file is asked for, so an
    attempt before it is published costs one 404 rather than re-publishing the old file.

    Published into the live generation, like a standalone SPAN refresh: a new version would
    purge the scrip index, the other segment and SPAN, none of which this rewrites."""
    if not _load_mutex.acquire(blocking=False):
        return {}
    try:
        return _retry_stale_bhavcopy(now)
    finally:
        _load_mutex.release()


def _retry_stale_bhavcopy(now: dt.datetime | None) -> dict[str, str]:
    from icici_breeze_backend.app.services.quote_source_router import latest_concluded_trading_day

    target = latest_concluded_trading_day(now)
    segments = (
        ("nse_fo", "nfo", cfg.NFO, bhavcopy_nse.fetch_nse_fo_bhavcopy_for_date),
        ("bse_fo", "bfo", cfg.BFO, bhavcopy_bse.fetch_bse_fo_bhavcopy_for_date),
    )
    stale = set(stale_bhavcopy_segments(now))
    out: dict[str, str] = {}
    for source, seg, exchange_code, fetch_for_date in segments:
        if exchange_code not in stale:
            continue
        fetched = fetch_for_date(target)
        if not fetched:
            _logger.info("Bhavcopy %s for %s not published yet; will retry", seg, target.isoformat())
            out[seg] = "not_published"
            continue
        rows, url = fetched
        live = scrip_index.current_version()
        bhavcopy_store.publish_bhavcopy_rows(
            rows,
            segment=seg,
            source_date=target,
            source_url=url,
            version=live if live > 0 else None,
        )
        _set_source(
            source,
            in_progress=False,
            progress_pct=100,
            message=f"Loaded {len(rows)} rows from {target.isoformat()} (late-publish retry)",
        )
        _record_ingest(
            source, ok=True, source_date=target.isoformat(), row_count=len(rows), url=url,
            notes="late_publish_retry",
        )
        _logger.info("Bhavcopy %s for %s loaded on retry (%s rows)", seg, target.isoformat(), len(rows))
        out[seg] = "loaded"
    return out


def trigger_reference_data_load_now(*, force: bool = False, trigger_mode: str = "manual") -> dict[str, Any]:
    global _refresh_thread
    with _lock:
        state = load_progress_state()
        if state.get("refresh_in_progress"):
            return {"started": False, "reason": "already_in_progress"}

    def _run() -> None:
        global _refresh_thread
        try:
            run_reference_data_load(force=force, trigger_mode=trigger_mode)
        finally:
            _refresh_thread = None

    t = threading.Thread(target=_run, name="reference-data-load", daemon=True)
    with _lock:
        _refresh_thread = t
    t.start()
    return {"started": True}


def is_load_in_progress() -> bool:
    return bool(load_progress_state().get("refresh_in_progress"))
