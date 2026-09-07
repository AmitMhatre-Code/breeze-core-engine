"""Run the comparison: ICICI once per case, then every local method combination.

Broker calls go through the ordinary `breeze` client, so they inherit the per-user lock, the
rolling-minute pacer and the daily budget accounting that every other ICICI call obeys
(design-decisions #24). They are marked advisory: a margin study must never be the reason an
exit order cannot be placed.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import uuid
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.services.icici_call_class import advisory_calls
from icici_breeze_backend.app.services.margin_harness import store
from icici_breeze_backend.app.services.margin_harness.cases import (
    HarnessCase,
    build_generated_cases,
    build_open_position_cases,
)
from icici_breeze_backend.app.services.margin_harness.methods import (
    ELM_METHODS,
    SPAN_METHODS,
    compute_elm,
    compute_span_variant,
    method_catalog,
)
from icici_breeze_backend.app.services.reference_data.span_baseline_store import (
    get_span_baseline_sheet,
)
from icici_breeze_backend.app.services.reference_data.span_portfolio_scan import SpanLeg

_logger = logging.getLogger(__name__)
_lock = threading.RLock()
_thread: threading.Thread | None = None

HARNESS_SCHEMA_VERSION = 1


def _as_float(raw: Any) -> float | None:
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _margin_input(case: HarnessCase) -> list[dict[str, str]]:
    """The leg shape ICICI's margin calculator expects, matching the portfolio netting path."""
    return [
        {
            "strike_price": str(leg.strike_price),
            "quantity": str(leg.quantity),
            "right": leg.right,
            "action": leg.action,
            "product": cfg.OPTIONS,
            "expiry_date": leg.expiry_date,
            "stock_code": leg.stock_code,
            "cover_order_flow": "N",
            "fresh_order_type": "N",
            "cover_limit_rate": "0",
            "cover_sltp_price": "0",
            "fresh_limit_rate": "0",
            "open_quantity": "0",
        }
        for leg in case.legs
    ]


def _icici_figures(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Everything ICICI returned, not just the field this app normally reads.

    `non_span_margin_required` is the open question the harness exists to answer: if it carries
    exposure margin, ICICI's ELM is directly readable and no inference is needed.
    """
    success = (raw or {}).get("Success") if isinstance(raw, dict) else None
    if not isinstance(success, dict):
        return {"ok": False, "raw": raw}
    span = _as_float(success.get("span_margin_required"))
    non_span = _as_float(success.get("non_span_margin_required"))
    order_value = _as_float(success.get("order_value"))
    implied_rate = None
    if non_span is not None and order_value:
        implied_rate = round(non_span / order_value, 6)
    return {
        "ok": True,
        "span_margin_required": span,
        "non_span_margin_required": non_span,
        "order_value": order_value,
        "order_margin": _as_float(success.get("order_margin")),
        "trade_margin": _as_float(success.get("trade_margin")),
        "block_trade_margin": _as_float(success.get("block_trade_margin")),
        "total": None if span is None else round(span + (non_span or 0.0), 2),
        "implied_non_span_rate_of_order_value": implied_rate,
        "raw": success,
    }


def _span_legs(case: HarnessCase) -> list[SpanLeg]:
    return [
        SpanLeg(strike=leg.strike_price, right=leg.right, side=leg.action, quantity=leg.quantity)
        for leg in case.legs
    ]


def _time_years(case: HarnessCase) -> float | None:
    try:
        expiry = dt.datetime.strptime(case.expiry_date, "%d-%b-%Y").date()
    except (TypeError, ValueError):
        return None
    return max(0.0, (expiry - now_ist().date()).days / 365.0)


def _evaluate_case(case: HarnessCase, icici: dict[str, Any]) -> dict[str, Any]:
    """Every SPAN method × ELM method for one case, each scored against ICICI's answer."""
    sheet = get_span_baseline_sheet(
        case.exchange_code, case.stock_code, case.expiry_date, include_risk_arrays=True
    )
    contracts = sheet.get("contracts") or {}
    legs = _span_legs(case)
    reference_total = icici.get("total") if icici.get("ok") else None
    reference_span = icici.get("span_margin_required") if icici.get("ok") else None

    span_results: dict[str, Any] = {}
    for method in SPAN_METHODS:
        out = compute_span_variant(
            method,
            contracts,
            legs,
            spot=case.spot_price,
            time_years=_time_years(case),
            som_rate=case.som_rate,
        )
        if out.get("found") and reference_span is not None:
            diff = out["span_margin"] - reference_span
            out["diff_vs_icici_span"] = round(diff, 2)
            out["pct_vs_icici_span"] = (
                round(100.0 * diff / reference_span, 3) if reference_span else None
            )
        span_results[method.id] = out

    elm_results: dict[str, Any] = {}
    for method in ELM_METHODS:
        elm = compute_elm(
            method,
            legs,
            spot=float(case.spot_price or 0.0),
            is_index=case.is_index,
            is_expiry_day=case.is_expiry_day,
        )
        elm_results[method.id] = {"elm": round(elm, 2)}

    combinations: list[dict[str, Any]] = []
    for span_method in SPAN_METHODS:
        span_out = span_results[span_method.id]
        if not span_out.get("found"):
            continue
        for elm_method in ELM_METHODS:
            total = span_out["span_margin"] + elm_results[elm_method.id]["elm"]
            row: dict[str, Any] = {
                "span_method": span_method.id,
                "elm_method": elm_method.id,
                "total": round(total, 2),
            }
            if reference_total is not None:
                diff = total - reference_total
                row["diff_vs_icici_total"] = round(diff, 2)
                row["abs_diff_vs_icici_total"] = round(abs(diff), 2)
                row["pct_vs_icici_total"] = (
                    round(100.0 * diff / reference_total, 3) if reference_total else None
                )
            combinations.append(row)

    best = None
    scored = [c for c in combinations if c.get("abs_diff_vs_icici_total") is not None]
    if scored:
        best = min(scored, key=lambda c: c["abs_diff_vs_icici_total"])

    return {
        "case": case.as_dict(),
        "icici": icici,
        "baseline_source_file": sheet.get("source_file"),
        "baseline_source_date": sheet.get("source_date"),
        "span_methods": span_results,
        "elm_methods": elm_results,
        "combinations": combinations,
        "closest_combination": best,
    }


def _summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Rank method combinations by how close they land across every priced case."""
    tally: dict[tuple[str, str], dict[str, Any]] = {}
    wins: dict[tuple[str, str], int] = {}
    for res in results:
        for row in res.get("combinations") or []:
            if row.get("abs_diff_vs_icici_total") is None:
                continue
            key = (row["span_method"], row["elm_method"])
            entry = tally.setdefault(
                key, {"cases": 0, "abs_diff_sum": 0.0, "max_abs_diff": 0.0, "pct_sum": 0.0}
            )
            entry["cases"] += 1
            entry["abs_diff_sum"] += row["abs_diff_vs_icici_total"]
            entry["max_abs_diff"] = max(entry["max_abs_diff"], row["abs_diff_vs_icici_total"])
            if row.get("pct_vs_icici_total") is not None:
                entry["pct_sum"] += abs(row["pct_vs_icici_total"])
        best = res.get("closest_combination")
        if best:
            wins[(best["span_method"], best["elm_method"])] = (
                wins.get((best["span_method"], best["elm_method"]), 0) + 1
            )

    ranking = []
    for (span_id, elm_id), entry in tally.items():
        n = max(1, entry["cases"])
        ranking.append(
            {
                "span_method": span_id,
                "elm_method": elm_id,
                "cases": entry["cases"],
                "mean_abs_diff": round(entry["abs_diff_sum"] / n, 2),
                "mean_abs_pct": round(entry["pct_sum"] / n, 3),
                "max_abs_diff": round(entry["max_abs_diff"], 2),
                "closest_on_cases": wins.get((span_id, elm_id), 0),
            }
        )
    ranking.sort(key=lambda r: (r["mean_abs_pct"], r["mean_abs_diff"]))

    non_span_values = [
        r["icici"].get("non_span_margin_required")
        for r in results
        if r.get("icici", {}).get("ok") and r["icici"].get("non_span_margin_required") is not None
    ]
    return {
        "ranking": ranking[:12],
        "best_combination": ranking[0] if ranking else None,
        # The headline question: does ICICI break exposure margin out at all?
        "icici_non_span_seen_non_zero": any(v for v in non_span_values),
        "icici_non_span_sample_count": len(non_span_values),
    }


def _fetch_open_positions(user_id: str) -> list[dict[str, Any]]:
    from icici_breeze_backend.app.services.processor import processor

    try:
        out = processor().get_positions(user_id)
    except Exception as exc:
        _logger.warning("Harness: open positions unavailable: %s", exc)
        return []
    if isinstance(out, dict) and out.get("Status") == 200:
        success = out.get("Success")
        if isinstance(success, list):
            return success
        if isinstance(success, dict):
            for key in ("positions", "legs", "data"):
                if isinstance(success.get(key), list):
                    return success[key]
    return []


def run_harness(user_id: str, *, include_open_positions: bool = True) -> dict[str, Any]:
    """Price every case with ICICI and with every local method. Blocking; call off-thread."""
    from icici_breeze_backend.app.services.nsccl_baseline import (
        ensure_exchange_margin_baseline_table,
    )
    from icici_breeze_backend.app.services.processor import processor

    ensure_exchange_margin_baseline_table()
    run_id = str(uuid.uuid4())
    started_at = now_ist().isoformat(timespec="seconds")

    if str(cfg.ICICI_BROKER_MODE or "").strip().lower() != "live":
        return {
            "ok": False,
            "error": (
                "The margin harness needs live broker calls. This instance is running in "
                f"'{cfg.ICICI_BROKER_MODE}' mode, where margin figures are fabricated."
            ),
        }

    try:
        breeze = processor().get_session_breeze(user_id)
    except Exception as exc:
        return {"ok": False, "error": f"No active ICICI session: {exc}"}
    if breeze is None:
        return {"ok": False, "error": "No active ICICI session. Log in to the broker and retry."}

    cases = build_generated_cases()
    broker_calls = 0
    if include_open_positions:
        # get_positions also runs its own netted margin_calculator calls per group, so this
        # costs a handful of broker calls rather than one; `broker_calls` below counts only
        # the harness's own pricing calls.
        with advisory_calls():
            positions = _fetch_open_positions(user_id)
        cases = cases + build_open_position_cases(positions)

    if not cases:
        return {
            "ok": False,
            "error": (
                "No cases could be built. The SPAN baseline supplies the underlying spot used "
                "to pick strikes — refresh it and retry."
            ),
        }

    store.create_run(run_id, user_id, started_at, len(cases))
    results: list[dict[str, Any]] = []
    priced = failed = 0

    try:
        for case in cases:
            raw: dict[str, Any] | None = None
            error: str | None = None
            try:
                # Serialized and paced by the shared client; never parallelised.
                with advisory_calls():
                    raw = breeze.margin_calculator(
                        _margin_input(case), exchange_code=case.exchange_code
                    )
                broker_calls += 1
            except Exception as exc:
                error = str(exc)
            icici = _icici_figures(raw)
            if error:
                icici = {"ok": False, "error": error, "raw": None}
            if icici.get("ok"):
                priced += 1
            else:
                failed += 1
            results.append(_evaluate_case(case, icici))
            store.update_progress(
                run_id, priced_count=priced, failed_count=failed, broker_calls=broker_calls
            )
    except Exception as exc:
        _logger.exception("Margin harness run failed")
        store.finish_run(
            run_id,
            status="failed",
            finished_at=now_ist().isoformat(timespec="seconds"),
            priced_count=priced,
            failed_count=failed,
            broker_calls=broker_calls,
            summary={},
            payload=None,
            error=str(exc),
        )
        return {"ok": False, "error": str(exc), "run_id": run_id}

    summary = _summarise(results)
    payload = {
        "schema_version": HARNESS_SCHEMA_VERSION,
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": now_ist().isoformat(timespec="seconds"),
        "broker_mode": cfg.ICICI_BROKER_MODE,
        "case_count": len(cases),
        "priced_count": priced,
        "failed_count": failed,
        "broker_calls": broker_calls,
        # Embedded so an exported file stays interpretable after the code has moved on.
        "method_catalog": method_catalog(),
        "summary": summary,
        "results": results,
    }
    store.finish_run(
        run_id,
        status="completed",
        finished_at=payload["finished_at"],
        priced_count=priced,
        failed_count=failed,
        broker_calls=broker_calls,
        summary=summary,
        payload=payload,
    )
    return {"ok": True, "run_id": run_id, "summary": summary}


def start_harness_run(user_id: str, *, include_open_positions: bool = True) -> dict[str, Any]:
    """Kick the run off in the background; a full run takes a minute or two of paced calls."""
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return {"started": False, "reason": "already_running"}
        if store.active_run_id():
            return {"started": False, "reason": "already_running"}

    def _run() -> None:
        global _thread
        try:
            run_harness(user_id, include_open_positions=include_open_positions)
        finally:
            _thread = None

    t = threading.Thread(target=_run, name="margin-harness", daemon=True)
    with _lock:
        _thread = t
    t.start()
    return {"started": True}


def is_running() -> bool:
    return bool(_thread and _thread.is_alive())
