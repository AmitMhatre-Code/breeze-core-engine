"""Portfolio-level SPAN scanning risk and Net Option Value from NSCCL risk arrays."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, parse_strike, strike_key
from icici_breeze_backend.app.services.iv_compute import DEFAULT_R, bs_price_call, bs_price_put
from icici_breeze_backend.app.services.reference_data.span_baseline_store import (
    compute_span_margin_required,
    get_span_baseline_sheet,
    get_underlying_facts_for,
)

DEFAULT_SIGMA = 0.20


@dataclass(frozen=True)
class SpanLeg:
    strike: float
    right: str
    side: str
    quantity: int


def _right_to_option_type(right: str) -> str:
    r = str(right or "").strip()
    if r in (cfg.CALL, "Call", "CE", "C"):
        return "CE"
    return "PE"


def _contract_key(strike_price: Strike | float | int | str, option_type: str) -> str:
    return f"{strike_key(strike_price)}:{option_type.upper()}"


def _signed_quantity(side: str, quantity: int) -> int:
    q = int(quantity)
    if q <= 0:
        return 0
    s = str(side or "").strip().lower()
    return q if s == "buy" else -q


def _standalone_sell_margin(
    contracts: dict[str, Any],
    leg: SpanLeg,
) -> float:
    if str(leg.side or "").strip().lower() != "sell":
        return 0.0
    out = compute_span_margin_required(contracts, leg.strike, leg.right, leg.quantity)
    if not out.get("found"):
        return 0.0
    return float(out.get("span_margin_required") or 0.0)


def compute_portfolio_scanning_risk(
    contracts: dict[str, Any],
    legs: list[SpanLeg],
) -> tuple[float | None, list[str]]:
    """Combine per-contract risk arrays with signed quantities; return max scenario loss."""
    warnings: list[str] = []
    active = [leg for leg in legs if leg.quantity > 0]
    if not active:
        return None, warnings

    arrays: list[tuple[int, list[float]]] = []
    for leg in active:
        opt = _right_to_option_type(leg.right)
        entry = contracts.get(_contract_key(leg.strike, opt))
        if not entry or not isinstance(entry, dict):
            warnings.append(
                f"Contract missing in baseline: {leg.strike} {opt}",
            )
            return None, warnings
        raw = entry.get("risk_array")
        if not isinstance(raw, list) or not raw:
            warnings.append(
                "Portfolio SPAN unavailable — refresh exchange baseline (risk arrays missing).",
            )
            return None, warnings
        try:
            ra = [float(x) for x in raw]
        except (TypeError, ValueError):
            warnings.append("Invalid risk array in baseline.")
            return None, warnings
        signed = _signed_quantity(leg.side, leg.quantity)
        if signed == 0:
            continue
        arrays.append((signed, ra))

    if not arrays:
        return None, warnings

    n = min(len(ra) for _qty, ra in arrays)
    if n <= 0:
        warnings.append("Empty risk arrays in baseline.")
        return None, warnings

    worst = float("-inf")
    for i in range(n):
        scenario = 0.0
        for signed, ra in arrays:
            scenario += signed * ra[i]
        if scenario > worst:
            worst = scenario

    if not math.isfinite(worst):
        return None, warnings
    return max(0.0, worst), warnings


def compute_net_option_value_from_file(
    contracts: dict[str, Any],
    legs: list[SpanLeg],
) -> tuple[float | None, list[str]]:
    """NOV from the exchange's own settlement premiums (`<p>` in the SPAN file).

    SPAN's final step subtracts the portfolio's net option value, and the file states the
    premium it was computed against. Returns None when any leg's contract predates the
    settle-price column, so the caller can fall back rather than silently mix a modelled
    premium into an otherwise exact figure.
    """
    warnings: list[str] = []
    long_mtm = 0.0
    short_mtm = 0.0
    for leg in legs:
        if leg.quantity <= 0:
            continue
        opt = _right_to_option_type(leg.right)
        entry = contracts.get(_contract_key(leg.strike, opt))
        if not isinstance(entry, dict):
            warnings.append(f"Contract missing in baseline: {leg.strike} {opt}")
            return None, warnings
        raw = entry.get("settle_price")
        if raw is None:
            warnings.append("Settlement premium missing from baseline; refresh the SPAN baseline.")
            return None, warnings
        try:
            premium = float(raw)
        except (TypeError, ValueError):
            warnings.append("Invalid settlement premium in baseline.")
            return None, warnings
        mtm = premium * leg.quantity
        if str(leg.side or "").strip().lower() == "buy":
            long_mtm += mtm
        else:
            short_mtm += mtm
    return long_mtm - short_mtm, warnings


def compute_short_option_minimum(
    legs: list[SpanLeg],
    som_rate: float | None,
) -> float:
    """`som_rate` × short option units. Zero in every NSCCL/ICCL file seen so far, but the
    term is real SPAN and the rate is per-underlying and per-day, so it is read, not assumed."""
    try:
        rate = float(som_rate or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if rate <= 0:
        return 0.0
    short_units = sum(
        leg.quantity
        for leg in legs
        if leg.quantity > 0 and str(leg.side or "").strip().lower() == "sell"
    )
    return rate * short_units


def compute_net_option_value(
    legs: list[SpanLeg],
    *,
    spot: float,
    time_years: float,
    r: float = DEFAULT_R,
    sigma: float = DEFAULT_SIGMA,
) -> float:
    """MTM(long options) − MTM(short options) using Black–Scholes.

    The fallback for contracts with no settlement premium on file; prefer
    `compute_net_option_value_from_file`, which needs no vol assumption.
    """
    if spot <= 0 or time_years < 0:
        return 0.0
    sig = sigma if sigma > 0 else DEFAULT_SIGMA
    t = max(time_years, 0.0)
    long_mtm = 0.0
    short_mtm = 0.0
    for leg in legs:
        if leg.quantity <= 0:
            continue
        strike = float(leg.strike)
        if strike <= 0:
            continue
        is_call = _right_to_option_type(leg.right) == "CE"
        if is_call:
            px = bs_price_call(spot, strike, t, sig, r=r)
        else:
            px = bs_price_put(spot, strike, t, sig, r=r)
        mtm = px * leg.quantity
        if str(leg.side or "").strip().lower() == "buy":
            long_mtm += mtm
        else:
            short_mtm += mtm
    return long_mtm - short_mtm


def compute_portfolio_span_margin(
    contracts: dict[str, Any],
    legs: list[SpanLeg],
    *,
    spot: float | None = None,
    time_years: float | None = None,
    sigma: float | None = None,
    som_rate: float | None = None,
) -> dict[str, Any]:
    """Portfolio SPAN: max(scanning risk, short option minimum) − net option value.

    Scoped to one underlying and one expiry (design-decisions #23) -- the calendar-spread
    charge and cross-expiry netting are deliberately not modelled here.
    """
    warnings: list[str] = []
    active = [leg for leg in legs if leg.quantity > 0]
    if not active:
        return {
            "found": False,
            "span_margin_required": None,
            "scanning_risk": None,
            "net_option_value": None,
            "net_option_value_source": "none",
            "short_option_minimum": None,
            "margin_benefit": None,
            "per_leg_standalone": {},
            "warnings": warnings,
        }

    scanning_risk, scan_warnings = compute_portfolio_scanning_risk(contracts, active)
    warnings.extend(scan_warnings)
    if scanning_risk is None:
        return {
            "found": False,
            "span_margin_required": None,
            "scanning_risk": None,
            "net_option_value": None,
            "net_option_value_source": "none",
            "short_option_minimum": None,
            "margin_benefit": None,
            "per_leg_standalone": {},
            "warnings": warnings,
        }

    # Prefer the exchange's own settlement premiums; Black-Scholes is only a fallback for
    # contracts ingested before the settle-price column existed.
    nov, nov_source = 0.0, "none"
    file_nov, _file_warnings = compute_net_option_value_from_file(contracts, active)
    if file_nov is not None:
        nov, nov_source = file_nov, "span_file"
    elif spot is not None and spot > 0 and time_years is not None and time_years >= 0:
        nov = compute_net_option_value(
            active,
            spot=float(spot),
            time_years=float(time_years),
            sigma=float(sigma) if sigma and sigma > 0 else DEFAULT_SIGMA,
        )
        nov_source = "black_scholes"
        warnings.append(
            "Settlement premium missing from baseline; NOV estimated with Black-Scholes."
        )
    elif spot is not None and spot > 0:
        warnings.append("Time to expiry missing; NOV adjustment skipped.")

    short_option_minimum = compute_short_option_minimum(active, som_rate)
    risk_charge = max(scanning_risk, short_option_minimum)
    span_margin = max(0.0, risk_charge - nov)

    per_leg_standalone: dict[str, float] = {}
    sell_standalone_sum = 0.0
    for idx, leg in enumerate(active):
        standalone = _standalone_sell_margin(contracts, leg)
        per_leg_standalone[str(idx)] = standalone
        sell_standalone_sum += standalone

    margin_benefit = None
    if sell_standalone_sum > 0:
        margin_benefit = max(0.0, sell_standalone_sum - span_margin)

    return {
        "found": True,
        "span_margin_required": round(span_margin, 2),
        "scanning_risk": round(scanning_risk, 2),
        "net_option_value": round(nov, 2),
        "net_option_value_source": nov_source,
        "short_option_minimum": round(short_option_minimum, 2),
        "margin_benefit": round(margin_benefit, 2) if margin_benefit is not None else None,
        "per_leg_standalone": per_leg_standalone,
        "warnings": warnings,
    }


def span_legs_from_margin_input(legs: list[dict[str, Any]]) -> list[SpanLeg]:
    out: list[SpanLeg] = []
    for leg in legs:
        try:
            strike = parse_strike(leg.get("strike_price"))
            if strike is None:
                continue
            qty = int(str(leg.get("quantity") or "0").strip())
            if qty <= 0:
                continue
            out.append(
                SpanLeg(
                    strike=float(strike),
                    right=str(leg.get("right") or ""),
                    side=str(leg.get("action") or leg.get("side") or ""),
                    quantity=qty,
                )
            )
        except (TypeError, ValueError):
            continue
    return out


def resolve_portfolio_span_margin(
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    legs: list[dict[str, Any]],
    *,
    spot: float | None = None,
    time_years: float | None = None,
    sigma: float | None = None,
) -> dict[str, Any]:
    sheet = get_span_baseline_sheet(
        exchange_code,
        stock_code,
        expiry_display,
        include_risk_arrays=True,
    )
    if not sheet.get("found"):
        return {
            "found": False,
            "span_margin_required": None,
            "scanning_risk": None,
            "net_option_value": None,
            "net_option_value_source": "none",
            "short_option_minimum": None,
            "margin_benefit": None,
            "per_leg_standalone": {},
            "warnings": ["Contract missing in Exchange Risk Baseline."],
        }
    span_legs = span_legs_from_margin_input(legs)
    facts = get_underlying_facts_for(exchange_code, stock_code) or {}
    return compute_portfolio_span_margin(
        sheet.get("contracts") or {},
        span_legs,
        spot=spot,
        time_years=time_years,
        sigma=sigma,
        som_rate=facts.get("som_rate"),
    )
