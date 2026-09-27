"""The calibration sweep's case set (pilot): a structured sample across underlyings.

The standard run prices 5 underlyings. The sweep asks the cross-sectional question -- is ICICI's
charge above the SPAN file (#48) a per-underlying number, and does it follow something we can
read ourselves (volatility, VaR/ELM rates, VIX, moneyness, side)? -- so it samples wide rather
than dense:

- **core** -- every NSE and BSE index (two expiries) and a volatility-stratified set of stocks
  (nearest expiry): short calls and puts at 5% ITM, ATM, 5% and 10% OTM and just past the
  deep-OTM line, plus four multi-leg structures that test whether legs add up.
- **dense** -- a 1%-step strike ladder from 5% ITM to 15% OTM on a handful of underlyings, to
  see the shape between the core points.
- **quantity** -- the same few at 10 and 50 lots, because ICICI's margin is not linear in size
  (project memory: basket scale overshoot).

Everything is deterministic for a given scrip master, SPAN file and market context, so two
sweeps on different days price the same grid.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import today_ist_date
from icici_breeze_backend.app.services.margin_harness.cases import (
    CaseLeg,
    HarnessCase,
    _expiries,
    _lot_size,
    _nearest,
    _parse_expiry,
    _scrip_conn,
    _tradeable_strikes,
)
from icici_breeze_backend.app.services.margin_harness.market_context import features_for
from icici_breeze_backend.app.services.reference_data.span_baseline_store import (
    get_underlying_facts_for,
)
from icici_breeze_backend.app.services.reference_data.symbol_registry import (
    KIND_INDEX,
    aliases_for,
    resolve,
    underlyings,
)

_logger = logging.getLogger(__name__)

STOCK_COUNT = 20
_STOCK_POOL = 80  # most-listed stocks the stratified sample is drawn from
DENSE_STOCK_COUNT = 3
_INDEX_EXPIRIES = 2
_STOCK_EXPIRIES = 1
# Core single-leg points, as fractions out of the money (negative = in the money). The last
# point is placed just past each kind's deep-OTM line (10% index, 30% stock).
_CORE_POINTS = (-0.05, 0.0, 0.05, 0.10)
_DEEP_POINT = {True: 0.12, False: 0.32}
_DENSE_POINTS = tuple(p / 100 for p in range(-5, 16))
_QUANTITY_LOTS = (10, 50)
# A listed strike further than this from the target is not that point -- skip it rather than
# record a "10% OTM" case that is really 4% OTM.
_MAX_POINT_MISS = 0.025


def _otm(right: str, strike: float, spot: float) -> float:
    return (strike / spot - 1.0) if right == "Call" else (1.0 - strike / spot)


def _strike_at(strikes: dict[str, list[float]], right: str, spot: float, point: float) -> float | None:
    listed = strikes.get("CE" if right == "Call" else "PE") or []
    target = spot * (1 + point) if right == "Call" else spot * (1 - point)
    strike = _nearest(listed, target)
    if strike is None or abs(_otm(right, strike, spot) - point) > _MAX_POINT_MISS:
        return None
    return strike


def _traded(stock_code: str, expiry: str, right: str, strike: float, exchange: str) -> bool | None:
    """Whether the contract traded last session (bhavcopy open > 0).

    An untraded strike's SPAN premium is a flat-vol theoretical, not a settlement price
    (project memory: SPAN file premium mixed basis), so it is a control worth recording.
    """
    try:
        from icici_breeze_backend.app.services.reference_data.bhavcopy_store import _lookup_bhav_row

        row = _lookup_bhav_row(stock_code, expiry, right, strike, exchange)
    except Exception:  # noqa: BLE001 - a missing bhavcopy only loses the control
        return None
    if not row:
        return None
    try:
        return float(row.get("open") or 0) > 0
    except (TypeError, ValueError):
        return None


def _index_targets() -> list[tuple[str, str]]:
    out = []
    for segment in (cfg.NFO, cfg.BFO):
        for sym in underlyings(segment=segment, kind=KIND_INDEX):
            out.append((sym.short_name, segment))
    return out


def _stock_targets(conn, context: dict[str, Any], count: int) -> list[str]:
    """`count` stocks spread evenly across the volatility range of the most-listed pool."""
    from icici_breeze_backend.app.services.margin_harness.cases import _liquid_stocks

    pool = []
    for short_name in _liquid_stocks(conn, _STOCK_POOL):
        vol = features_for(context, aliases_for(short_name, cfg.NFO)).get("annual_vol")
        facts = get_underlying_facts_for(cfg.NFO, short_name) or {}
        if vol is None or not facts.get("spot_price"):
            continue
        pool.append((float(vol), short_name))
    pool.sort()
    if len(pool) <= count:
        return [name for _v, name in pool]
    step = (len(pool) - 1) / (count - 1)
    return [pool[round(i * step)][1] for i in range(count)]


def _case(
    *,
    stock_code: str,
    exchange: str,
    expiry: str,
    is_index: bool,
    lot_size: int,
    spot: float,
    facts: dict[str, Any],
    grid: str,
    structure: str,
    label: str,
    spec: list[tuple[float, str, str, int]],
    features: dict[str, Any],
) -> HarnessCase:
    expiry_date = _parse_expiry(expiry)
    legs = [
        CaseLeg(
            stock_code=stock_code,
            exchange_code=exchange,
            expiry_date=expiry,
            strike_price=strike,
            right=right,
            action=action,
            quantity=lots * lot_size,
        )
        for strike, right, action, lots in spec
    ]
    leg_features = [
        {
            "strike": strike,
            "right": right,
            "action": action,
            "lots": lots,
            "otm_pct": round(100 * _otm(right, strike, spot), 3),
            "traded": _traded(stock_code, expiry, right, strike, exchange),
        }
        for strike, right, action, lots in spec
    ]
    return HarnessCase(
        id=f"{stock_code}:{expiry}:{grid}:{structure}",
        label=f"{stock_code} {expiry} — {label}",
        structure=structure,
        source=f"sweep_{grid}",
        stock_code=stock_code,
        exchange_code=exchange,
        expiry_date=expiry,
        is_index=is_index,
        is_expiry_day=expiry_date == today_ist_date(),
        lot_size=lot_size,
        spot_price=spot,
        spot_source=f"span_file:{facts.get('source_file') or ''}",
        som_rate=facts.get("som_rate"),
        legs=legs,
        features={
            **features,
            "grid": grid,
            "days_to_expiry": (expiry_date - today_ist_date()).days if expiry_date else None,
            "legs": leg_features,
        },
    )


def _core_specs(strikes, spot: float, is_index: bool) -> list[tuple[str, str, list[tuple]]]:
    specs = []
    seen: set[tuple[str, float]] = set()
    for right in ("Call", "Put"):
        for point in _CORE_POINTS + (_DEEP_POINT[is_index],):
            strike = _strike_at(strikes, right, spot, point)
            if strike is None or (right, strike) in seen:
                continue
            seen.add((right, strike))
            tag = f"{'itm' if point < 0 else 'otm'}{abs(round(point * 100))}"
            specs.append((f"short_{right.lower()}_{tag}", f"Short {right.lower()} {tag.upper()}", [(strike, right, "Sell", 1)]))
    atm_c = _strike_at(strikes, "Call", spot, 0.0)
    atm_p = _strike_at(strikes, "Put", spot, 0.0)
    c2, p2 = _strike_at(strikes, "Call", spot, 0.02), _strike_at(strikes, "Put", spot, 0.02)
    c5, p5 = _strike_at(strikes, "Call", spot, 0.05), _strike_at(strikes, "Put", spot, 0.05)
    c10, p10 = _strike_at(strikes, "Call", spot, 0.10), _strike_at(strikes, "Put", spot, 0.10)
    if atm_p and p2 and atm_p != p2:
        specs.append(("bull_put_spread", "Bull put spread (ATM / 2%)", [(atm_p, "Put", "Sell", 1), (p2, "Put", "Buy", 1)]))
    if atm_c and c2 and atm_c != c2:
        specs.append(("bear_call_spread", "Bear call spread (ATM / 2%)", [(atm_c, "Call", "Sell", 1), (c2, "Call", "Buy", 1)]))
    if c5 and p5:
        specs.append(("short_strangle", "Short 5% strangle", [(c5, "Call", "Sell", 1), (p5, "Put", "Sell", 1)]))
        if c10 and p10 and c10 != c5 and p10 != p5:
            specs.append(
                (
                    "iron_condor",
                    "Iron condor (5% / 10%)",
                    [(c5, "Call", "Sell", 1), (c10, "Call", "Buy", 1), (p5, "Put", "Sell", 1), (p10, "Put", "Buy", 1)],
                )
            )
    return specs


def _dense_specs(strikes, spot: float, skip: set[tuple[str, float]]) -> list[tuple[str, str, list[tuple]]]:
    specs = []
    for right in ("Call", "Put"):
        for point in _DENSE_POINTS:
            strike = _strike_at(strikes, right, spot, point)
            if strike is None or (right, strike) in skip:
                continue
            skip.add((right, strike))
            specs.append(
                (f"short_{right.lower()}_{round(point * 100):+d}pct", f"Short {right.lower()} {point:+.0%}", [(strike, right, "Sell", 1)])
            )
    return specs


def _quantity_specs(strikes, spot: float) -> list[tuple[str, str, list[tuple]]]:
    specs = []
    atm_c = _strike_at(strikes, "Call", spot, 0.0)
    p5 = _strike_at(strikes, "Put", spot, 0.05)
    for lots in _QUANTITY_LOTS:
        if atm_c:
            specs.append((f"short_call_atm_{lots}lots", f"Short ATM call × {lots} lots", [(atm_c, "Call", "Sell", lots)]))
        if p5:
            specs.append((f"short_put_otm5_{lots}lots", f"Short 5% OTM put × {lots} lots", [(p5, "Put", "Sell", lots)]))
    return specs


def resolve_underlyings(raw: str | list[str] | None) -> tuple[list[tuple[str, str, bool]], list[str]]:
    """A typed list of underlyings -> ([(ICICI short name, segment, is_index)], unrecognised).

    Any spelling the registry knows is accepted (YESBAN, YESBANK, "YES BANK LIMITED"), so a list
    can be pasted straight from the haircut file or from NSE's symbols. Duplicates collapse.
    """
    if isinstance(raw, str):
        raw = [p for p in raw.replace("\n", ",").split(",")]
    out: list[tuple[str, str, bool]] = []
    unknown: list[str] = []
    seen: set[tuple[str, str]] = set()
    for item in raw or []:
        name = str(item or "").strip()
        if not name:
            continue
        sym = resolve(name, cfg.NFO) or resolve(name, cfg.BFO)
        if sym is None:
            unknown.append(name)
            continue
        key = (sym.short_name, sym.segment)
        if key in seen:
            continue
        seen.add(key)
        out.append((sym.short_name, sym.segment, sym.kind == KIND_INDEX))
    return out, unknown


def build_sweep_cases(
    context: dict[str, Any],
    *,
    only: list[tuple[str, str, bool]] | None = None,
) -> list[HarnessCase]:
    """The pilot grid, or with `only`, a targeted sweep: the core grid on exactly those
    underlyings (nearest expiry), no dense ladder and no quantity cases -- the shape for testing
    one hypothesis on a chosen set of names cheaply."""
    cases: list[HarnessCase] = []
    with _scrip_conn() as conn:
        if only:
            targets = [(name, seg, is_index, 1) for name, seg, is_index in only]
            dense_names: set[str] = set()
        else:
            stocks = _stock_targets(conn, context, STOCK_COUNT)
            targets = [(name, seg, True, _INDEX_EXPIRIES) for name, seg in _index_targets()]
            targets += [(name, cfg.NFO, False, _STOCK_EXPIRIES) for name in stocks]
            # Dense and quantity grids: the headline indices plus the lowest-, middle- and
            # highest-volatility stocks (`stocks` is sorted by volatility).
            picks = [stocks[round(i * (len(stocks) - 1) / max(1, DENSE_STOCK_COUNT - 1))] for i in range(DENSE_STOCK_COUNT)] if stocks else []
            dense_names = {"NIFTY", "CNXBAN", "BSESEN", *picks}

        for stock_code, exchange, is_index, expiry_count in targets:
            facts = get_underlying_facts_for(exchange, stock_code) or {}
            try:
                spot = float(facts.get("spot_price") or 0)
            except (TypeError, ValueError):
                spot = 0.0
            if spot <= 0:
                _logger.info("Sweep: no SPAN spot for %s %s, skipping", exchange, stock_code)
                continue
            underlying_features = features_for(context, aliases_for(stock_code, exchange))
            for n, expiry in enumerate(_expiries(conn, stock_code, exchange, expiry_count)):
                lot_size = _lot_size(conn, stock_code, exchange, expiry)
                if lot_size <= 0:
                    continue
                strikes = _tradeable_strikes(conn, stock_code, exchange, expiry)
                common = dict(
                    stock_code=stock_code,
                    exchange=exchange,
                    expiry=expiry,
                    is_index=is_index,
                    lot_size=lot_size,
                    spot=spot,
                    facts=facts,
                    features=underlying_features,
                )
                core = _core_specs(strikes, spot, is_index)
                for structure, label, spec in core:
                    cases.append(_case(grid="core", structure=structure, label=label, spec=spec, **common))
                if n == 0 and stock_code in dense_names:
                    seen = {(spec[0][1], spec[0][0]) for _s, _l, spec in core if len(spec) == 1}
                    for structure, label, spec in _dense_specs(strikes, spot, seen):
                        cases.append(_case(grid="dense", structure=structure, label=label, spec=spec, **common))
                    for structure, label, spec in _quantity_specs(strikes, spot):
                        cases.append(_case(grid="quantity", structure=structure, label=label, spec=spec, **common))
    return cases


def sweep_plan(cases: list[HarnessCase]) -> dict[str, Any]:
    """What a sweep would cost before any broker call: one call per case."""
    by_grid: dict[str, int] = {}
    underlyings_seen: dict[str, int] = {}
    for case in cases:
        grid = (case.features or {}).get("grid", "core")
        by_grid[grid] = by_grid.get(grid, 0) + 1
        key = f"{case.exchange_code}:{case.stock_code}"
        underlyings_seen[key] = underlyings_seen.get(key, 0) + 1
    return {
        "case_count": len(cases),
        "broker_calls": len(cases),
        "by_grid": by_grid,
        "underlyings": underlyings_seen,
        "underlying_count": len(underlyings_seen),
    }
