"""The calculation methods under test, each one self-describing.

The point of the harness is not a single number but a ranking: this codebase contains more
than one way to compute both halves of an initial margin, they disagree, and nobody has
measured which is closest to what ICICI actually charges. So every case is priced by every
SPAN variant crossed with every ELM variant, and each result carries the identity and
parameters of the method that produced it. A JSON export is then enough on its own to rank
them -- no need to remember what the code looked like on the day it ran.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.reference_data.span_portfolio_scan import (
    DEFAULT_SIGMA,
    SpanLeg,
    compute_net_option_value,
    compute_net_option_value_from_file,
    compute_portfolio_scanning_risk,
    compute_short_option_minimum,
)


@dataclass(frozen=True)
class SpanMethod:
    """One way of turning risk arrays into a SPAN figure."""

    id: str
    label: str
    nov_source: str  # "none" | "span_file" | "black_scholes"
    apply_short_option_minimum: bool
    scope: str = "single_expiry_single_underlying"
    notes: str = ""
    params: dict[str, Any] = field(default_factory=dict)


SPAN_METHODS: tuple[SpanMethod, ...] = (
    SpanMethod(
        id="scan_only",
        label="Scanning risk only",
        nov_source="none",
        apply_short_option_minimum=False,
        notes=(
            "Worst of the 16 published scenarios, no net-option-value adjustment. The floor "
            "case: what the engine produced before NOV was subtracted at all."
        ),
    ),
    SpanMethod(
        id="scan_minus_nov_bs",
        label="Scanning risk − NOV (Black-Scholes)",
        nov_source="black_scholes",
        apply_short_option_minimum=False,
        notes=(
            "The engine as it shipped before this harness: NOV priced with Black-Scholes at a "
            "fixed vol, because the file's own settlement premium was not being ingested."
        ),
        params={"sigma": DEFAULT_SIGMA},
    ),
    SpanMethod(
        id="scan_minus_nov_file",
        label="Scanning risk − NOV (exchange settlement premium)",
        nov_source="span_file",
        apply_short_option_minimum=False,
        notes="NOV summed from the SPAN file's own `<p>` per option. No pricing model involved.",
    ),
    SpanMethod(
        id="som_floor_minus_nov_file",
        label="max(scan, short-option minimum) − NOV (exchange premium)",
        nov_source="span_file",
        apply_short_option_minimum=True,
        notes=(
            "Full single-expiry SPAN as this app implements it. The SOM rate has been 0 in "
            "every NSCCL/ICCL file seen so far, so this usually equals scan_minus_nov_file -- "
            "a divergence between the two means the exchange started charging it."
        ),
    ),
)


@dataclass(frozen=True)
class ElmMethod:
    """One way of turning a position into an exposure-margin figure."""

    id: str
    label: str
    index_rate: float
    stock_rate: float
    index_deep_otm_rate: float | None
    stock_deep_otm_rate: float | None
    index_deep_otm_threshold: float | None
    stock_deep_otm_threshold: float | None
    applies_to_stocks: bool
    waived_on_expiry_day: bool
    notional_basis: str  # "underlying_spot"
    notes: str = ""


ELM_METHODS: tuple[ElmMethod, ...] = (
    ElmMethod(
        id="none",
        label="No exposure margin",
        index_rate=0.0,
        stock_rate=0.0,
        index_deep_otm_rate=None,
        stock_deep_otm_rate=None,
        index_deep_otm_threshold=None,
        stock_deep_otm_threshold=None,
        applies_to_stocks=False,
        waived_on_expiry_day=True,
        notional_basis="underlying_spot",
        notes=(
            "The hypothesis that ICICI's quoted figure is already complete and this app's ELM "
            "is a pure overlay on top of it."
        ),
    ),
    ElmMethod(
        id="portfolio_flat_index_only",
        label="Portfolio page model: flat 2%, index shorts only",
        index_rate=cfg.ELM,
        stock_rate=0.0,
        index_deep_otm_rate=None,
        stock_deep_otm_rate=None,
        index_deep_otm_threshold=None,
        stock_deep_otm_threshold=None,
        applies_to_stocks=False,
        waived_on_expiry_day=True,
        notional_basis="underlying_spot",
        notes="What processor.get_positions charges today. Stock shorts get nothing.",
    ),
    ElmMethod(
        id="strategy_builder_tiered",
        label="Strategy Builder model: tiered index/stock with deep-OTM step-up",
        index_rate=cfg.ELM_INDEX_STD,
        stock_rate=cfg.ELM_STOCK_STD,
        index_deep_otm_rate=cfg.ELM_INDEX_DEEP_OTM,
        stock_deep_otm_rate=cfg.ELM_STOCK_DEEP_OTM,
        index_deep_otm_threshold=cfg.ELM_INDEX_DEEP_OTM_THRESHOLD,
        stock_deep_otm_threshold=cfg.ELM_STOCK_DEEP_OTM_THRESHOLD,
        applies_to_stocks=True,
        waived_on_expiry_day=True,
        notional_basis="underlying_spot",
        notes="What options_strategy_engine charges today. Assumes ICICI's 5% stock rate.",
    ),
    ElmMethod(
        id="exchange_prescribed",
        label="Exchange-prescribed: 2% index, 3.5% stock",
        index_rate=0.02,
        stock_rate=0.035,
        index_deep_otm_rate=None,
        stock_deep_otm_rate=None,
        index_deep_otm_threshold=None,
        stock_deep_otm_threshold=None,
        applies_to_stocks=True,
        waived_on_expiry_day=True,
        notional_basis="underlying_spot",
        notes=(
            "SEBI/exchange rates, no broker uplift. Tests the hypothesis that ICICI charges "
            "exactly what the exchange prescribes."
        ),
    ),
    ElmMethod(
        id="exchange_prescribed_no_expiry_waiver",
        label="Exchange-prescribed, charged on expiry day too",
        index_rate=0.02,
        stock_rate=0.035,
        index_deep_otm_rate=None,
        stock_deep_otm_rate=None,
        index_deep_otm_threshold=None,
        stock_deep_otm_threshold=None,
        applies_to_stocks=True,
        waived_on_expiry_day=False,
        notional_basis="underlying_spot",
        notes=(
            "Identical to exchange_prescribed except on an expiry-day case. This codebase "
            "asserts ICICI folds ELM into SPAN on expiry day; running the harness on an "
            "expiry day is what separates these two."
        ),
    ),
)


def _elm_rate_for_leg(
    method: ElmMethod,
    *,
    is_index: bool,
    right: str,
    strike: float,
    spot: float,
) -> float:
    if not is_index and not method.applies_to_stocks:
        return 0.0
    base = method.index_rate if is_index else method.stock_rate
    deep_rate = method.index_deep_otm_rate if is_index else method.stock_deep_otm_rate
    threshold = method.index_deep_otm_threshold if is_index else method.stock_deep_otm_threshold
    if deep_rate is None or threshold is None or spot <= 0:
        return base
    if str(right).upper().startswith("C"):
        otm_frac = max(0.0, (strike - spot) / spot)
    else:
        otm_frac = max(0.0, (spot - strike) / spot)
    return deep_rate if otm_frac > threshold else base


def compute_elm(
    method: ElmMethod,
    legs: list[SpanLeg],
    *,
    spot: float,
    is_index: bool,
    is_expiry_day: bool,
) -> float:
    """Exposure margin on short legs only, as a percentage of underlying notional.

    Notional is spot × quantity, not premium × quantity: exposure margin covers the risk of
    the underlying moving, so a short option is exposed to the full contract value.
    """
    if method.waived_on_expiry_day and is_expiry_day:
        return 0.0
    if spot <= 0:
        return 0.0
    total = 0.0
    for leg in legs:
        if leg.quantity <= 0 or str(leg.side or "").strip().lower() != "sell":
            continue
        rate = _elm_rate_for_leg(
            method, is_index=is_index, right=leg.right, strike=leg.strike, spot=spot
        )
        total += rate * spot * leg.quantity
    return total


def compute_span_variant(
    method: SpanMethod,
    contracts: dict[str, Any],
    legs: list[SpanLeg],
    *,
    spot: float | None,
    time_years: float | None,
    som_rate: float | None,
) -> dict[str, Any]:
    """One SPAN method's answer for one case, with the intermediate terms it used."""
    scanning_risk, warnings = compute_portfolio_scanning_risk(contracts, legs)
    if scanning_risk is None:
        return {"found": False, "warnings": warnings}

    nov = 0.0
    nov_source = "none"
    if method.nov_source == "span_file":
        file_nov, nov_warnings = compute_net_option_value_from_file(contracts, legs)
        if file_nov is None:
            return {"found": False, "warnings": warnings + nov_warnings}
        nov, nov_source = file_nov, "span_file"
    elif method.nov_source == "black_scholes":
        if spot is None or spot <= 0 or time_years is None or time_years < 0:
            return {"found": False, "warnings": warnings + ["spot/time unavailable for NOV"]}
        nov = compute_net_option_value(
            legs,
            spot=float(spot),
            time_years=float(time_years),
            sigma=DEFAULT_SIGMA,
        )
        nov_source = "black_scholes"

    som = compute_short_option_minimum(legs, som_rate) if method.apply_short_option_minimum else 0.0
    risk_charge = max(scanning_risk, som)
    return {
        "found": True,
        "span_margin": round(max(0.0, risk_charge - nov), 2),
        "scanning_risk": round(scanning_risk, 2),
        "net_option_value": round(nov, 2),
        "net_option_value_source": nov_source,
        "short_option_minimum": round(som, 2),
        "warnings": warnings,
    }


def method_catalog() -> dict[str, Any]:
    """The full method definitions, embedded in every export so results stay interpretable."""
    return {
        "span_methods": [asdict(m) for m in SPAN_METHODS],
        "elm_methods": [asdict(m) for m in ELM_METHODS],
    }
