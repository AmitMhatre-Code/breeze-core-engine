"""Second-opinion SPAN margin from the `marginism` library, for the harness only.

Why a second engine at all: our SPAN math and marginism's are independent implementations of the
same CME-SPAN algorithm, so running both against the same snapshot is a continuous cross-check that
neither has drifted. On single-expiry option books the two are expected to agree to the rupee --
that agreement *is* the signal. Where they can diverge is the ground marginism covers and we do not:
the intra-commodity (calendar) spread charge from `dSpread`, combined-commodity netting across
expiries via `ccDef`/`pfLink`, and futures legs from `futPf`.

Only `span_margin` is read. marginism's own `exposure.py` is deliberately ignored -- it classifies
index-vs-stock from a hardcoded `index_symbols` tuple, which is exactly the hand-kept name set that
design-decisions #28 records as a live money bug. Exposure margin stays ours, from symbol_registry.

Nothing in the production margin path calls this module.
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import os
import threading
from typing import Any

from icici_breeze_backend.app.services.nsccl_baseline import (
    find_span_archive,
    open_span_xml_payload,
)
from icici_breeze_backend.app.services.margin_harness.cases import HarnessCase
from icici_breeze_backend.app.services.reference_data.symbol_registry import aliases_for

_logger = logging.getLogger(__name__)

# One parsed engine per (archive path, resolved commodity). Parsing a 48MB file filtered to a
# single commodity costs ~1.5s, so cases sharing an underlying reuse the engine within a run.
_engines: dict[tuple[str, str], Any] = {}
_lock = threading.RLock()


def library_version() -> str | None:
    try:
        import marginism  # noqa: PLC0415
    except Exception:
        return None
    return getattr(marginism, "__version__", "unknown")


def _expiry_yyyymmdd(expiry_display: str) -> str | None:
    try:
        return dt.datetime.strptime(expiry_display, "%d-%b-%Y").strftime("%Y%m%d")
    except (TypeError, ValueError):
        return None


def _load(archive_path: str, archive_name: str, candidates: tuple[str, ...]):
    """Parse `archive_path` keeping only `candidates`, and return (calculator, resolved_code)."""
    from marginism import SpanCalculator, parse_spn  # noqa: PLC0415

    with open(archive_path, "rb") as fh:
        payload = fh.read()
    opened = open_span_xml_payload(payload, archive_name)
    if not opened:
        raise ValueError(f"could not read SPAN XML from {archive_name}")
    stream, _display, _inner = opened
    span_file = parse_spn(io.BytesIO(stream.read()), symbols=candidates)
    codes = list(getattr(span_file, "commodities", {}) or {})
    if not codes:
        raise LookupError(f"none of {candidates} present in {archive_name}")
    return SpanCalculator(span_file), codes[0]


def evaluate(case: HarnessCase, *, source_date: str | None, archive_name: str | None) -> dict[str, Any]:
    """marginism's SPAN for one harness case, or a reason it could not be produced.

    Never raises: a harness run must survive a missing archive or an unresolvable symbol.
    """
    version = library_version()
    if version is None:
        return {"available": False, "reason": "marginism is not installed"}
    if not source_date:
        return {"available": False, "reason": "case has no baseline source date"}

    found = find_span_archive(source_date, archive_name)
    if not found:
        return {
            "available": False,
            "reason": (
                f"raw SPAN archive for {source_date} was not retained "
                "(retention began after this snapshot)"
            ),
        }
    archive_path, exact_snapshot = found

    # The SPAN file names an underlying by its exchange code (BANKNIFTY), the case by ICICI's
    # ShortName (CNXBAN). Hand the parser every spelling the registry knows and let the file pick.
    candidates = tuple(aliases_for(case.stock_code, case.exchange_code))
    if not candidates:
        return {"available": False, "reason": f"no known aliases for {case.stock_code}"}

    expiry = _expiry_yyyymmdd(case.expiry_date)
    if not expiry:
        return {"available": False, "reason": f"unparseable expiry {case.expiry_date!r}"}

    try:
        from marginism import Position  # noqa: PLC0415

        key = (archive_path, ",".join(sorted(candidates)))
        with _lock:
            entry = _engines.get(key)
            if entry is None:
                # The reader dispatches on the file's own extension, so hand it the name that
                # is actually on disk -- which is not the requested one when a same-day fallback
                # revision was used.
                entry = _load(archive_path, os.path.basename(archive_path), candidates)
                _engines[key] = entry
        calculator, code = entry

        positions = [
            Position(
                code,
                "CE" if str(leg.right).strip().lower().startswith("c") else "PE",
                leg.quantity * (-1 if str(leg.action).strip().lower() == "sell" else 1),
                expiry,
                float(leg.strike_price),
            )
            for leg in case.legs
        ]
        result = calculator.calculate(positions)
    except Exception as exc:  # noqa: BLE001 - a second opinion must never fail the run
        _logger.warning("marginism failed for %s: %s", case.id, exc)
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    commodity = (getattr(result, "by_commodity", {}) or {}).get(code)
    unmatched = [str(u) for u in (getattr(result, "unmatched", []) or [])]
    return {
        "available": True,
        "library_version": version,
        "resolved_symbol": code,
        "source_archive": os.path.basename(archive_path),
        # False means the exact revision this case was priced against was not retained and a
        # neighbouring revision of the same day was used. SPAN is republished six times a day and
        # revisions drift a percent or two, so a gap against the legacy engine is not comparable.
        "exact_snapshot": exact_snapshot,
        # Only the risk-based figure is consumed; marginism's exposure/expiry-day ELM is ignored
        # on purpose (see module docstring).
        "span_margin": round(float(result.span_margin), 2),
        "net_option_value": round(float(result.net_option_value), 2),
        "scanning_risk": round(float(getattr(commodity, "scan_risk", 0.0)), 2) if commodity else None,
        "calendar_spread_charge": (
            round(float(getattr(commodity, "calendar_spread_charge", 0.0)), 2) if commodity else None
        ),
        "short_option_minimum": (
            round(float(getattr(commodity, "short_option_minimum", 0.0)), 2) if commodity else None
        ),
        "worst_scenario": getattr(commodity, "worst_scenario_label", None) if commodity else None,
        "unmatched_legs": unmatched,
    }


def reset_cache() -> None:
    with _lock:
        _engines.clear()
