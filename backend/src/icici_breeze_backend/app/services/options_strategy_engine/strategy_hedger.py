"""Strategy-level hedging: aggregate portfolio buckets and rank protective wings."""
from __future__ import annotations

import math
from typing import Any, Literal

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.iv_compute import implied_volatility
from icici_breeze_backend.app.services.options_strategy_engine.greeks import (
    bs_delta,
    bs_gamma,
)

Right = Literal["Call", "Put"]
RiskProfile = Literal["net_short_call", "net_short_put", "multi_leg_reopt", "defined"]

_W1_PREMIUM = 1.0
_W2_DISTANCE = 5000.0
_W3_BUDGET = 2.0


def flatten_chain_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert routed chain payload chain_rows into flat option scrip list."""
    rows: list[dict[str, Any]] = []
    for chain_row in payload.get("chain_rows") or []:
        for side in ("call", "put"):
            cell = chain_row.get(side)
            if not cell or not isinstance(cell, dict):
                continue
            strike = _parse_int(cell.get("strike_price"))
            if strike is None or strike <= 0:
                continue
            right: Right = "Call" if side == "call" else "Put"
            rows.append(
                {
                    "stock_code": cell.get("stock_code"),
                    "strike_price": strike,
                    "right": right,
                    "expiry_date": cell.get("expiry_date"),
                    "ltp": _parse_float(cell.get("ltp")) or 0.0,
                    "best_bid_price": _parse_float(cell.get("best_bid_price")),
                    "best_offer_price": _parse_float(cell.get("best_offer_price")),
                    "total_buy_qty": int(cell.get("total_buy_qty") or 0),
                    "total_sell_qty": int(cell.get("total_sell_qty") or 0),
                    "spot_price": _parse_float(cell.get("spot_price")),
                    "open_interest": int(cell.get("open_interest") or 0),
                }
            )
    return rows


def generate_strategy_level_hedges(
    positions: list[dict],
    options_chain: list[dict],
    spot_price: float,
    user_max_loss: float,
    *,
    days_to_expiry: int,
    lot_size: int,
    exchange_code: str = cfg.NFO,
) -> dict[str, Any]:
    """Evaluate one (stock_code, expiry) bucket and return summary + top 3 hedge candidates."""
    del exchange_code
    t_years = max(days_to_expiry, 1) / 365.0
    fallback_sigma = _atm_sigma_from_chain(options_chain, spot_price, t_years)

    net_legs = _aggregate_legs(positions)
    strategy_delta, strategy_gamma, net_premium = _aggregate_greeks(
        net_legs, spot_price, t_years, fallback_sigma, lot_size
    )
    risk_profile, exposures = _detect_risk_profile(net_legs, spot_price)

    candidates: list[dict[str, Any]] = []
    if risk_profile == "defined":
        pass
    elif risk_profile == "multi_leg_reopt":
        if exposures:
            for exposure in exposures:
                candidates.extend(
                    _generate_wing_candidates(
                        exposure,
                        options_chain,
                        spot_price,
                        user_max_loss,
                        lot_size,
                        t_years,
                        fallback_sigma,
                    )
                )
        else:
            candidates = _generate_reopt_candidates(
            net_legs,
            options_chain,
            spot_price,
            user_max_loss,
            lot_size,
            t_years,
            fallback_sigma,
        )
    else:
        for exposure in exposures:
            candidates.extend(
                _generate_wing_candidates(
                    exposure,
                    options_chain,
                    spot_price,
                    user_max_loss,
                    lot_size,
                    t_years,
                    fallback_sigma,
                )
            )

    candidates = [c for c in candidates if c["max_loss_estimate"] <= user_max_loss]
    candidates.sort(key=lambda c: c["score"])
    top = candidates[:3]

    return {
        "summary": {
            "strategy_delta": round(strategy_delta, 4),
            "strategy_gamma": round(strategy_gamma, 6),
            "net_premium_cash": round(net_premium, 2),
            "risk_profile": risk_profile,
            "spot_price": spot_price,
            "days_to_expiry": days_to_expiry,
            "lot_size": lot_size,
        },
        "candidates": top,
    }


def _parse_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _parse_int(v: Any) -> int | None:
    f = _parse_float(v)
    if f is None:
        return None
    return int(f)


def _atm_sigma_from_chain(
    options_chain: list[dict],
    spot: float,
    t_years: float,
) -> float:
    """Back out ATM IV from chain; fallback 0.20 (matches sigma_for_pop default)."""
    if spot <= 0 or not options_chain:
        return 0.20
    best_strike: int | None = None
    best_dist = float("inf")
    for opt in options_chain:
        strike = _parse_int(opt.get("strike_price"))
        if strike is None or strike <= 0:
            continue
        dist = abs(strike - spot)
        if dist < best_dist:
            best_dist = dist
            best_strike = strike
    if best_strike is None:
        return 0.20
    ivs: list[float] = []
    for opt in options_chain:
        if _parse_int(opt.get("strike_price")) != best_strike:
            continue
        ltp = _parse_float(opt.get("ltp")) or 0.0
        if ltp <= 0:
            continue
        right = _normalize_right(opt.get("right"))
        if right is None:
            continue
        opt_type = "call" if right == "Call" else "put"
        iv = implied_volatility(ltp, spot, float(best_strike), t_years, opt_type)
        if iv and iv > 0:
            ivs.append(iv)
    if not ivs:
        return 0.20
    return sum(ivs) / len(ivs)


def _normalize_right(raw: Any) -> Right | None:
    t = str(raw or "").strip().lower()
    if t in {"call", "ce", "c"} or t == cfg.CALL.lower():
        return "Call"
    if t in {"put", "pe", "p"} or t == cfg.PUT.lower():
        return "Put"
    return None


def _signed_qty(row: dict) -> float:
    qty = _parse_float(row.get("quantity")) or 0.0
    action = str(row.get("action") or "").strip().lower()
    if action == cfg.SELL.lower() or action == "sell":
        return -abs(qty)
    return abs(qty)


def _aggregate_legs(positions: list[dict]) -> dict[tuple[int, Right], dict[str, Any]]:
    """Net quantity and metadata per (strike, right)."""
    merged: dict[tuple[int, Right], dict[str, Any]] = {}
    for row in positions:
        right = _normalize_right(row.get("right"))
        strike = _parse_int(row.get("strike_price"))
        if right is None or strike is None or strike <= 0:
            continue
        key = (strike, right)
        signed = _signed_qty(row)
        ltp = _parse_float(row.get("ltp")) or _parse_float(row.get("average_price")) or 0.0
        span = _parse_float(row.get("span_margin_required")) or 0.0
        if key not in merged:
            merged[key] = {
                "strike": strike,
                "right": right,
                "net_qty": 0.0,
                "ltp": ltp,
                "span_margin": 0.0,
            }
        merged[key]["net_qty"] += signed
        merged[key]["span_margin"] += span
        if ltp > 0:
            merged[key]["ltp"] = ltp
    return merged


def _aggregate_greeks(
    net_legs: dict[tuple[int, Right], dict[str, Any]],
    spot: float,
    t_years: float,
    fallback_sigma: float,
    lot_size: int,
) -> tuple[float, float, float]:
    total_delta = 0.0
    total_gamma = 0.0
    net_premium = 0.0
    for leg in net_legs.values():
        qty = leg["net_qty"]
        if abs(qty) < 1e-9:
            continue
        strike = float(leg["strike"])
        right: Right = leg["right"]
        ltp = leg["ltp"] or 0.0
        opt = "call" if right == "Call" else "put"
        iv = implied_volatility(ltp, spot, strike, t_years, opt) if ltp > 0 else None
        sigma = iv if iv and iv > 0 else fallback_sigma
        delta = bs_delta(spot, strike, t_years, sigma, right)
        gamma = bs_gamma(spot, strike, t_years, sigma)
        contracts = qty * lot_size
        total_delta += delta * contracts
        total_gamma += gamma * contracts
        # Buy = cash out (negative), Sell = cash in (positive)
        net_premium += -qty * ltp * lot_size
    return total_delta, total_gamma, net_premium


def _detect_risk_profile(
    net_legs: dict[tuple[int, Right], dict[str, Any]],
    spot: float,
) -> tuple[RiskProfile, list[dict[str, Any]]]:
    """Identify naked short tails or already-defined spreads."""
    exposures: list[dict[str, Any]] = []

    for (strike, right), leg in net_legs.items():
        qty = leg["net_qty"]
        if qty >= 0:
            continue
        short_qty = abs(qty)
        if right == "Call":
            covered = _covered_qty(net_legs, "Call", strike, higher=True)
            uncovered = max(0.0, short_qty - covered)
            if uncovered > 0:
                exposures.append(
                    {
                        "right": "Call",
                        "short_strike": strike,
                        "short_qty": uncovered,
                        "span_margin": leg["span_margin"],
                        "short_ltp": leg["ltp"] or 0.0,
                        "hedge_type": "bear_call_spread_wing",
                        "min_wing_strike": max(strike, int(spot)) + 1,
                    }
                )
        else:
            covered = _covered_qty(net_legs, "Put", strike, higher=False)
            uncovered = max(0.0, short_qty - covered)
            if uncovered > 0:
                exposures.append(
                    {
                        "right": "Put",
                        "short_strike": strike,
                        "short_qty": uncovered,
                        "span_margin": leg["span_margin"],
                        "short_ltp": leg["ltp"] or 0.0,
                        "hedge_type": "bull_put_spread_wing",
                        "max_wing_strike": min(strike, int(spot)) - 1,
                    }
                )

    if not exposures:
        return "defined", []

    if len(exposures) > 1:
        return "multi_leg_reopt", exposures
    return (
        "net_short_call" if exposures[0]["right"] == "Call" else "net_short_put",
        exposures,
    )


def _covered_qty(
    net_legs: dict[tuple[int, Right], dict[str, Any]],
    right: Right,
    short_strike: int,
    *,
    higher: bool,
) -> float:
    """Long qty at strikes that hedge a short (higher for calls, lower for puts)."""
    covered = 0.0
    for (strike, leg_right), leg in net_legs.items():
        if leg_right != right or leg["net_qty"] <= 0:
            continue
        if higher and strike > short_strike:
            covered += leg["net_qty"]
        elif not higher and strike < short_strike:
            covered += leg["net_qty"]
    return covered


def _is_liquid_wing(option: dict, spot: float, short_strike: int, right: Right) -> bool:
    ltp = _parse_float(option.get("ltp")) or 0.0
    offer = _parse_float(option.get("best_offer_price")) or ltp
    strike = _parse_int(option.get("strike_price")) or 0
    sell_qty = int(option.get("total_sell_qty") or 0)
    if offer <= 0 or ltp <= 0 or sell_qty <= 0 or strike <= 0:
        return False
    if right == "Call":
        return strike > max(short_strike, spot)
    return strike < min(short_strike, spot)


def _generate_wing_candidates(
    exposure: dict[str, Any],
    options_chain: list[dict],
    spot: float,
    user_max_loss: float,
    lot_size: int,
    t_years: float,
    fallback_sigma: float,
) -> list[dict[str, Any]]:
    right: Right = exposure["right"]
    short_strike = int(exposure["short_strike"])
    short_qty = exposure["short_qty"]
    short_ltp = exposure["short_ltp"]
    hedge_type = exposure["hedge_type"]
    span_margin = exposure.get("span_margin") or 0.0
    hedge_qty = _round_to_lots(short_qty, lot_size)

    candidates: list[dict[str, Any]] = []
    for opt in options_chain:
        opt_right = _normalize_right(opt.get("right"))
        if opt_right != right:
            continue
        if not _is_liquid_wing(opt, spot, short_strike, right):
            continue
        wing_strike = int(opt["strike_price"])
        wing_ask = _parse_float(opt.get("best_offer_price")) or _parse_float(opt.get("ltp")) or 0.0
        width = abs(wing_strike - short_strike)
        credit_per_unit = short_ltp - wing_ask
        max_loss_per_unit = width - credit_per_unit
        if max_loss_per_unit <= 0:
            continue
        max_loss = max_loss_per_unit * hedge_qty * lot_size
        if max_loss > user_max_loss:
            continue
        net_premium = wing_ask * hedge_qty
        spread_span = max_loss
        margin_relief = max(0.0, span_margin - spread_span)
        distance = abs(wing_strike - short_strike) / spot if spot > 0 else 1.0
        budget_penalty = max(0.0, max_loss - user_max_loss * 0.8)
        score = (
            _W1_PREMIUM * net_premium
            + _W2_DISTANCE * distance
            + _W3_BUDGET * budget_penalty
        )
        candidates.append(
            {
                "strike_price": wing_strike,
                "right": right,
                "ltp": round(wing_ask, 2),
                "net_premium_cost": round(net_premium, 2),
                "estimated_margin_relief": round(margin_relief, 2),
                "max_loss_estimate": round(max_loss, 2),
                "score": round(score, 4),
                "action": "Buy",
                "hedge_quantity": int(hedge_qty),
                "short_strike": short_strike,
                "hedge_type": hedge_type,
            }
        )
    return candidates


def _generate_reopt_candidates(
    net_legs: dict[tuple[int, Right], dict[str, Any]],
    options_chain: list[dict],
    spot: float,
    user_max_loss: float,
    lot_size: int,
    t_years: float,
    fallback_sigma: float,
) -> list[dict[str, Any]]:
    """Re-optimize wings for multi-leg spreads when LTP shifts."""
    candidates: list[dict[str, Any]] = []
    for leg in net_legs.values():
        qty = leg["net_qty"]
        if qty >= 0:
            continue
        exposure = {
            "right": leg["right"],
            "short_strike": leg["strike"],
            "short_qty": abs(qty),
            "span_margin": leg["span_margin"],
            "short_ltp": leg["ltp"] or 0.0,
            "hedge_type": (
                "bear_call_spread_wing"
                if leg["right"] == "Call"
                else "bull_put_spread_wing"
            ),
        }
        candidates.extend(
            _generate_wing_candidates(
                exposure,
                options_chain,
                spot,
                user_max_loss,
                lot_size,
                t_years,
                fallback_sigma,
            )
        )
    return candidates


def _round_to_lots(qty: float, lot_size: int) -> float:
    if lot_size <= 0:
        return qty
    lots = max(1, math.ceil(abs(qty) / lot_size))
    return lots * lot_size
