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
from icici_breeze_backend.app.services.margin_harness import marginism_engine
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

# 2: adds `app_margin` per case and `summary.app_method` -- the SPAN-file figure plus the
# portal's ICICI add-on, i.e. what the app itself charges (design-decisions #48).
HARNESS_SCHEMA_VERSION = 2

#: The SPAN method the app's margin paths use (portfolio scan, file NOV, SOM floor).
APP_SPAN_METHOD = "som_floor_minus_nov_file"


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


def _short_sides(case: HarnessCase) -> str:
    rights = {str(l.right).strip().lower()[:1] for l in case.legs if str(l.action).lower() == "sell"}
    if not rights:
        return "long_only"
    if rights == {"c"}:
        return "calls"
    if rights == {"p"}:
        return "puts"
    return "both"


def _app_margin(
    case: HarnessCase,
    span_results: dict[str, Any],
    reference_total: float | None,
    addon_rates: Any,
) -> dict[str, Any]:
    """What the app charges for this case: SPAN from the file plus the ICICI add-on.

    Priced with the add-on rates the deployment held when the run started, off the SPAN file's
    own underlying price -- the same inputs margin_addon.addon_for_underlying uses. Not part of
    `combinations`: the add-on is not an exposure-margin model, and keeping it apart leaves the
    method ranking comparable with every earlier export.
    """
    from icici_breeze_backend.app.services.margin_addon import compute_addon

    if addon_rates is None:
        return {
            "available": False,
            "reason": "No ICICI add-on received from the portal in the last 24 hours; the app is pricing from ICICI.",
        }
    span_out = span_results.get(APP_SPAN_METHOD) or {}
    if not span_out.get("found"):
        return {"available": False, "reason": "SPAN file could not price this case."}
    spot = float(case.spot_price or 0.0)
    if spot <= 0:
        return {"available": False, "reason": "No underlying price in the SPAN file."}
    addon = compute_addon(
        addon_rates,
        [
            {
                "strike_price": l.strike_price,
                "right": l.right,
                "action": l.action,
                "quantity": l.quantity,
                "expiry_date": l.expiry_date,
            }
            for l in case.legs
        ],
        spot=spot,
        is_index=case.is_index,
    )
    total = round(span_out["span_margin"] + addon, 2)
    out: dict[str, Any] = {
        "available": True,
        "span_method": APP_SPAN_METHOD,
        "span_file_margin": span_out["span_margin"],
        "icici_addon": addon,
        "icici_addon_version": addon_rates.version,
        "total": total,
        "short_sides": _short_sides(case),
    }
    if reference_total is not None:
        diff = total - reference_total
        out["diff_vs_icici_total"] = round(diff, 2)
        out["abs_diff_vs_icici_total"] = round(abs(diff), 2)
        out["pct_vs_icici_total"] = (
            round(100.0 * diff / reference_total, 3) if reference_total else None
        )
        # What ICICI actually charged above the SPAN file, per rupee of gross short notional --
        # the quantity the add-on rates are calibrated to.
        short_notional = spot * sum(
            l.quantity for l in case.legs if str(l.action).strip().lower() == "sell"
        )
        if short_notional > 0:
            out["implied_addon_rate"] = round(
                (reference_total - span_out["span_margin"]) / short_notional, 6
            )
    return out


def _evaluate_case(case: HarnessCase, icici: dict[str, Any], addon_rates: Any = None) -> dict[str, Any]:
    """Every SPAN method × ELM method for one case, each scored against ICICI's answer, plus
    what the app itself charges (`app_margin`)."""
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

    # Second opinion from the marginism library, read straight off the retained raw archive. It
    # sits beside `span_methods` rather than inside it so `combinations` and the ranking -- and
    # therefore every earlier export -- stay directly comparable.
    source_file = sheet.get("source_file")
    marginism_out = marginism_engine.evaluate(
        case,
        source_date=sheet.get("source_date"),
        archive_name=str(source_file or "").split(":", 1)[0] or None,
    )
    if marginism_out.get("available"):
        legacy = span_results.get("som_floor_minus_nov_file") or {}
        legacy_span = legacy.get("span_margin") if legacy.get("found") else None
        if legacy_span is not None:
            gap = marginism_out["span_margin"] - legacy_span
            marginism_out["diff_vs_legacy_span"] = round(gap, 2)
            marginism_out["pct_vs_legacy_span"] = (
                round(100.0 * gap / legacy_span, 3) if legacy_span else None
            )
        if reference_span is not None:
            gap = marginism_out["span_margin"] - reference_span
            marginism_out["diff_vs_icici_span"] = round(gap, 2)
            marginism_out["pct_vs_icici_span"] = (
                round(100.0 * gap / reference_span, 3) if reference_span else None
            )

    return {
        "case": case.as_dict(),
        "icici": icici,
        "baseline_source_file": source_file,
        "baseline_source_date": sheet.get("source_date"),
        "span_methods": span_results,
        "marginism": marginism_out,
        "elm_methods": elm_results,
        "combinations": combinations,
        "closest_combination": best,
        "app_margin": _app_margin(case, span_results, reference_total, addon_rates),
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
        # The headline: how far what the app charges sits from ICICI (design-decisions #48).
        "app_method": _summarise_app_method(results),
        "ranking": ranking[:12],
        "best_combination": ranking[0] if ranking else None,
        # The headline question: does ICICI break exposure margin out at all?
        "icici_non_span_seen_non_zero": any(v for v in non_span_values),
        "icici_non_span_sample_count": len(non_span_values),
        "marginism": _summarise_marginism(results),
    }


def _error_stats(pcts: list[float]) -> dict[str, Any]:
    """Mean/median/p90 of |error| plus signed mean (bias): one outlier must not be the story."""
    if not pcts:
        return {"cases": 0}
    abs_sorted = sorted(abs(p) for p in pcts)
    n = len(abs_sorted)
    p90 = abs_sorted[min(n - 1, int(round(0.9 * (n - 1))))]
    return {
        "cases": n,
        "mean_abs_pct": round(sum(abs_sorted) / n, 3),
        "median_abs_pct": round(
            abs_sorted[n // 2] if n % 2 else (abs_sorted[n // 2 - 1] + abs_sorted[n // 2]) / 2, 3
        ),
        "p90_abs_pct": round(p90, 3),
        "max_abs_pct": round(abs_sorted[-1], 3),
        "mean_pct": round(sum(pcts) / n, 3),
    }


def _summarise_app_method(results: list[dict[str, Any]]) -> dict[str, Any]:
    """SPAN file + ICICI add-on against ICICI, overall and by index/stock, exchange and side.

    Long-only cases are left out: ICICI and the SPAN file both charge 0 there, so they carry
    no percentage error and would only dilute the average.
    """
    rows = []
    versions: set[str] = set()
    unavailable: set[str] = set()
    for res in results:
        app = res.get("app_margin") or {}
        if not app.get("available"):
            if app.get("reason"):
                unavailable.add(str(app["reason"]))
            continue
        versions.add(str(app.get("icici_addon_version")))
        if app.get("pct_vs_icici_total") is None or app.get("short_sides") == "long_only":
            continue
        case = res.get("case") or {}
        rows.append((case, app["short_sides"], float(app["pct_vs_icici_total"]), app.get("implied_addon_rate")))

    def group(pred) -> dict[str, Any]:
        return _error_stats([p for case, sides, p, _r in rows if pred(case, sides)])

    def implied_median(members: list) -> float | None:
        rates = sorted(r for _c, _s, _p, r in members if r is not None)
        if not rates:
            return None
        n = len(rates)
        return round(rates[n // 2] if n % 2 else (rates[n // 2 - 1] + rates[n // 2]) / 2, 6)

    by_underlying: dict[str, dict[str, Any]] = {}
    keys = sorted({f"{c.get('exchange_code')}:{c.get('stock_code')}" for c, _s, _p, _r in rows})
    for key in keys:
        members = [x for x in rows if f"{x[0].get('exchange_code')}:{x[0].get('stock_code')}" == key]
        singles = [x for x in members if len(x[0].get("legs") or []) == 1]
        by_underlying[key] = {
            **_error_stats([p for _c, _s, p, _r in members]),
            "is_index": bool(members[0][0].get("is_index")),
            # Single short legs only: a multi-leg case's rate mixes two strikes' charges.
            "implied_addon_rate_median": implied_median(singles),
            "implied_addon_rate_median_calls": implied_median([x for x in singles if x[1] == "calls"]),
            "implied_addon_rate_median_puts": implied_median([x for x in singles if x[1] == "puts"]),
        }
    grids = sorted({str((c.get("features") or {}).get("grid") or "standard") for c, _s, _p, _r in rows})

    return {
        "id": "span_file_plus_icici_addon",
        "label": f"SPAN file ({APP_SPAN_METHOD}) + ICICI add-on",
        "addon_versions": sorted(versions),
        "overall": group(lambda c, s: True),
        "by_group": {
            "index": group(lambda c, s: c.get("is_index")),
            "stock": group(lambda c, s: not c.get("is_index")),
            "nse_index": group(lambda c, s: c.get("is_index") and c.get("exchange_code") == cfg.NFO),
            "bse_index": group(lambda c, s: c.get("is_index") and c.get("exchange_code") == cfg.BFO),
            "short_calls": group(lambda c, s: s == "calls"),
            "short_puts": group(lambda c, s: s == "puts"),
            "short_both_sides": group(lambda c, s: s == "both"),
        },
        "by_grid": {
            g: group(lambda c, s, g=g: str((c.get("features") or {}).get("grid") or "standard") == g)
            for g in grids
        },
        "by_underlying": by_underlying,
        "unavailable_reasons": sorted(unavailable),
    }


def _summarise_marginism(results: list[dict[str, Any]]) -> dict[str, Any]:
    """How the independent SPAN implementation compares, across the run.

    Two different questions, kept apart on purpose:

    * against the **legacy engine** -- these are two implementations of the same published
      algorithm, so on a single-expiry option book they should agree to the rupee. Any drift is a
      defect in one of them, and `agreeing_cases` is the number to watch.
    * against **ICICI** -- both engines will show the same gap, because they compute the same
      thing. That gap is not something a second SPAN implementation can close.

    Comparisons drawn from a neighbouring revision of the day's file are counted but excluded from
    the agreement figures: intraday SPAN drift would read as engine disagreement.
    """
    blocks = [r.get("marginism") or {} for r in results]
    available = [b for b in blocks if b.get("available")]
    exact = [b for b in available if b.get("exact_snapshot")]
    comparable = [b for b in exact if b.get("pct_vs_legacy_span") is not None]
    agreeing = [b for b in comparable if abs(b["pct_vs_legacy_span"]) < 0.01]
    worst = max((abs(b["pct_vs_legacy_span"]) for b in comparable), default=None)
    icici_pcts = [
        abs(b["pct_vs_icici_span"]) for b in exact if b.get("pct_vs_icici_span") is not None
    ]
    return {
        "library_version": marginism_engine.library_version(),
        "compared_cases": len(available),
        "exact_snapshot_cases": len(exact),
        "agreeing_cases": len(agreeing),
        "comparable_cases": len(comparable),
        "max_abs_pct_vs_legacy": round(worst, 4) if worst is not None else None,
        "mean_abs_pct_vs_icici_span": (
            round(sum(icici_pcts) / len(icici_pcts), 3) if icici_pcts else None
        ),
        # A zero `compared_cases` on an otherwise healthy run means the raw archive for that day
        # was never retained, not that anything failed.
        "unavailable_reasons": sorted(
            {str(b.get("reason")) for b in blocks if not b.get("available")} - {"None"}
        ),
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


MODE_STANDARD = "standard"
MODE_SWEEP = "sweep"

#: Default cap on broker calls one sweep session may spend before pausing (resumable). The
#: shared advisory shed at 4,500 calls/day (api_usage) still applies on top.
DEFAULT_SWEEP_MAX_CALLS = 1000

#: The harness keeps its own calls below this many in the trailing minute, under the pacer's
#: 90 (and ICICI's ~100), so the rest of the app -- WS reconnects, a user placing an order --
#: always finds room. Harness calls are advisory: the pacer refuses rather than queues them
#: when the window is full, so the harness has to wait for headroom itself.
_HARNESS_WINDOW_CALLS = 60
_THROTTLE_RETRY_WAIT_SEC = 65.0

_cancel = threading.Event()


def _session(user_id: str) -> tuple[Any, str | None]:
    from icici_breeze_backend.app.services.processor import processor

    if str(cfg.ICICI_BROKER_MODE or "").strip().lower() != "live":
        return None, (
            "The margin harness needs live broker calls. This instance is running in "
            f"'{cfg.ICICI_BROKER_MODE}' mode, where margin figures are fabricated."
        )
    try:
        breeze = processor().get_session_breeze(user_id)
    except Exception as exc:
        return None, f"No active ICICI session: {exc}"
    if breeze is None:
        return None, "No active ICICI session. Log in to the broker and retry."
    return breeze, None


def build_sweep_context() -> dict[str, Any]:
    """NSE market data for the latest session's close, and the ban list ICICI is enforcing."""
    from icici_breeze_backend.app.services.margin_harness.market_context import (
        fetch_market_context,
    )
    from icici_breeze_backend.app.services.reference_data.span_freshness import latest_trading_day
    from icici_breeze_backend.app.services.reference_data.span_sources import next_trading_day

    today = now_ist().date()
    trade_date = latest_trading_day(today)
    ban_date = next_trading_day(trade_date) if trade_date != today or now_ist().hour >= 16 else today
    return fetch_market_context(trade_date, ban_date=ban_date)


def plan_sweep() -> dict[str, Any]:
    """The sweep's case set and cost, without any broker call."""
    from icici_breeze_backend.app.services.margin_harness.sweep import build_sweep_cases, sweep_plan

    context = build_sweep_context()
    cases = build_sweep_cases(context)
    return {**sweep_plan(cases), "market_context_errors": context.get("errors") or []}


def _cases_from_meta(meta: dict[str, Any]) -> list[HarnessCase]:
    from icici_breeze_backend.app.services.margin_harness.cases import CaseLeg

    out = []
    for raw in meta.get("cases") or []:
        legs = [CaseLeg(**leg) for leg in raw.get("legs") or []]
        out.append(HarnessCase(**{**raw, "legs": legs}))
    return out


def _rates_from_meta(meta: dict[str, Any]) -> Any:
    from icici_breeze_backend.app.services.margin_addon import parse_rates

    status = meta.get("icici_addon") or {}
    if not status.get("available") or not status.get("rates"):
        return None
    return parse_rates(status["rates"])


def run_harness(
    user_id: str,
    *,
    include_open_positions: bool = True,
    mode: str = MODE_STANDARD,
    max_calls: int | None = None,
) -> dict[str, Any]:
    """Price every case with ICICI and with every local method. Blocking; call off-thread."""
    from icici_breeze_backend.app.services import margin_addon
    from icici_breeze_backend.app.services.nsccl_baseline import (
        ensure_exchange_margin_baseline_table,
    )

    ensure_exchange_margin_baseline_table()
    run_id = str(uuid.uuid4())
    started_at = now_ist().isoformat(timespec="seconds")

    breeze, problem = _session(user_id)
    if problem:
        return {"ok": False, "error": problem}

    context: dict[str, Any] | None = None
    if mode == MODE_SWEEP:
        from icici_breeze_backend.app.services.margin_harness.sweep import build_sweep_cases

        context = build_sweep_context()
        cases = build_sweep_cases(context)
    else:
        cases = build_generated_cases()
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

    # The add-on the app is using right now; recorded so the export says which rates were
    # scored, and so a resumed run scores the rest of its cases with the same rates.
    meta = {
        "mode": mode,
        "max_calls": max_calls,
        "include_open_positions": include_open_positions,
        "icici_addon": margin_addon.status(),
        "market_context": context,
        "cases": [c.as_dict() for c in cases],
    }
    store.create_run(run_id, user_id, started_at, len(cases), mode=mode, meta=meta)
    return _execute(run_id, user_id, breeze, cases, meta, started_at=started_at)


def resume_harness_run(run_id: str, user_id: str) -> dict[str, Any]:
    run = store.get_run(run_id)
    if not run or not run.get("meta"):
        return {"ok": False, "error": "Run not found or cannot be resumed."}
    breeze, problem = _session(user_id)
    if problem:
        return {"ok": False, "error": problem}
    store.set_status(run_id, "running")
    meta = run["meta"]
    return _execute(
        run_id,
        user_id,
        breeze,
        _cases_from_meta(meta),
        meta,
        started_at=run["started_at"],
        priced=int(run.get("priced_count") or 0),
        failed=int(run.get("failed_count") or 0),
        broker_calls=int(run.get("broker_calls") or 0),
    )


def _await_headroom(user_id: str) -> None:
    """Block until the trailing minute has room for one more harness call (or a stop)."""
    import time

    from icici_breeze_backend.app.services.icici_api_pacing import GlobalIciciApiPacer

    while GlobalIciciApiPacer.calls_in_window(user_id) >= _HARNESS_WINDOW_CALLS:
        if _cancel.wait(1.0):
            return


def _throttled(icici: dict[str, Any]) -> bool:
    """ICICI's per-minute refusal, or the pacer shedding an advisory call."""
    from icici_breeze_backend.app.services.icici_api_pacing import is_icici_daily_limit_exceeded

    raw = icici.get("raw") if isinstance(icici.get("raw"), dict) else {}
    text = f"{icici.get('error') or ''} {raw.get('Error') or ''}".lower()
    return (
        "api call per minute" in text
        or "shed" in text
        or bool(raw.get("advisory_shed"))
        or (is_icici_daily_limit_exceeded(text) and "per minute" in text)
    )


def _execute(
    run_id: str,
    user_id: str,
    breeze: Any,
    cases: list[HarnessCase],
    meta: dict[str, Any],
    *,
    started_at: str,
    priced: int = 0,
    failed: int = 0,
    broker_calls: int = 0,
) -> dict[str, Any]:
    """Price cases from the first one without a stored result; stop, pause or finish.

    Runs inside the user's ICICI scope. Without it the pacer cannot tell whose calls these
    are, and skips the per-minute window, the per-user lock and usage counting altogether:
    sweep 6350d21b fired 547 calls back to back and ICICI refused 105 of them.
    """
    from icici_breeze_backend.app.services.icici_api_pacing import icici_user_scope

    with icici_user_scope(user_id):
        return _execute_scoped(
            run_id, user_id, breeze, cases, meta,
            started_at=started_at, priced=priced, failed=failed, broker_calls=broker_calls,
        )


def _execute_scoped(
    run_id: str,
    user_id: str,
    breeze: Any,
    cases: list[HarnessCase],
    meta: dict[str, Any],
    *,
    started_at: str,
    priced: int,
    failed: int,
    broker_calls: int,
) -> dict[str, Any]:
    mode = meta.get("mode") or MODE_STANDARD
    addon_rates = _rates_from_meta(meta)
    max_calls = meta.get("max_calls")
    start = store.result_count(run_id)
    session_calls = 0
    _cancel.clear()

    try:
        for idx in range(start, len(cases)):
            if _cancel.is_set():
                store.set_status(run_id, "stopped", error="Stopped at your request; resume to continue.")
                return {"ok": False, "run_id": run_id, "status": "stopped"}
            if max_calls and session_calls >= int(max_calls):
                store.set_status(
                    run_id,
                    "paused",
                    error=f"Paused after {session_calls} broker calls (the run's cap); resume to continue.",
                )
                return {"ok": False, "run_id": run_id, "status": "paused"}
            case = cases[idx]
            icici: dict[str, Any] = {}
            # A throttled case is retried once after the minute clears, not recorded as a
            # failure: it says nothing about margin, only about pace.
            for attempt in range(2):
                _await_headroom(user_id)
                raw: dict[str, Any] | None = None
                error: str | None = None
                try:
                    # Serialized and paced by the shared client; never parallelised.
                    with advisory_calls():
                        raw = breeze.margin_calculator(
                            _margin_input(case), exchange_code=case.exchange_code
                        )
                    broker_calls += 1
                    session_calls += 1
                except Exception as exc:
                    error = str(exc)
                icici = _icici_figures(raw)
                if error:
                    icici = {"ok": False, "error": error, "raw": raw}
                elif not icici.get("ok"):
                    icici = {**icici, "error": (raw or {}).get("Error") if isinstance(raw, dict) else None}
                if icici.get("ok") or attempt == 1 or not _throttled(icici) or _cancel.is_set():
                    break
                _logger.warning("Harness: ICICI throttled %s; retrying after the minute clears", case.id)
                _cancel.wait(_THROTTLE_RETRY_WAIT_SEC)
            if icici.get("ok"):
                priced += 1
            else:
                failed += 1
            store.append_result(run_id, idx, _evaluate_case(case, icici, addon_rates))
            store.update_progress(
                run_id, priced_count=priced, failed_count=failed, broker_calls=broker_calls
            )
    except Exception as exc:
        _logger.exception("Margin harness run failed")
        store.set_status(run_id, "interrupted", error=f"{exc}; resume to continue.")
        return {"ok": False, "error": str(exc), "run_id": run_id}

    results = store.load_results(run_id)
    summary = _summarise(results)
    payload = {
        "schema_version": HARNESS_SCHEMA_VERSION,
        "run_id": run_id,
        "mode": mode,
        "started_at": started_at,
        "finished_at": now_ist().isoformat(timespec="seconds"),
        "broker_mode": cfg.ICICI_BROKER_MODE,
        "case_count": len(cases),
        "priced_count": priced,
        "failed_count": failed,
        "broker_calls": broker_calls,
        # Embedded so an exported file stays interpretable after the code has moved on.
        "method_catalog": method_catalog(),
        "icici_addon": meta.get("icici_addon"),
        "market_context": meta.get("market_context"),
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
        compress=mode == MODE_SWEEP,
    )
    store.clear_results(run_id)
    return {"ok": True, "run_id": run_id, "summary": summary}


def _launch(target) -> dict[str, Any]:
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return {"started": False, "reason": "already_running"}
        # No live thread, so any 'running' row was cut off by a restart.
        store.reap_interrupted_runs()

        def _run() -> None:
            global _thread
            try:
                target()
            finally:
                _thread = None

        t = threading.Thread(target=_run, name="margin-harness", daemon=True)
        _thread = t
        t.start()
    return {"started": True}


def start_harness_run(
    user_id: str,
    *,
    include_open_positions: bool = True,
    mode: str = MODE_STANDARD,
    max_calls: int | None = None,
) -> dict[str, Any]:
    """Kick the run off in the background; a standard run takes a minute or two of paced
    calls, a sweep an hour or more."""
    return _launch(
        lambda: run_harness(
            user_id, include_open_positions=include_open_positions, mode=mode, max_calls=max_calls
        )
    )


def start_resume(run_id: str, user_id: str) -> dict[str, Any]:
    run = store.get_run(run_id)
    if not run:
        return {"started": False, "reason": "not_found"}
    if run.get("status") not in store.RESUMABLE_STATUSES:
        return {"started": False, "reason": "not_resumable"}
    return _launch(lambda: resume_harness_run(run_id, user_id))


def request_stop() -> bool:
    """Ask the running harness to stop after its current case (resumable)."""
    if not is_running():
        return False
    _cancel.set()
    return True


def is_running() -> bool:
    return bool(_thread and _thread.is_alive())
