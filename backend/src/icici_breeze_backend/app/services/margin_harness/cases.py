"""Build the position set the harness prices.

Two sources. The generated matrix is deterministic and repeatable across dates, which is what
makes two runs comparable (an expiry-day run against an ordinary-day run, say). The open-book
cases are whatever the account actually holds -- less comparable between runs, but the only
ones that measure the structures this user really trades.

Strikes are chosen from `scrip_master` with `MarginPercentage > 0`, the same tradeability
filter the chain builder uses, so the harness never asks ICICI to price a contract that is
listed but not tradeable.
"""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import today_ist_date
from icici_breeze_backend.app.services.reference_data.symbol_registry import (
    KIND_INDEX,
    is_index as symbol_is_index,
    underlyings,
)
from icici_breeze_backend.app.services.reference_data.span_baseline_store import (
    get_underlying_facts_for,
)

_logger = logging.getLogger(__name__)

MAX_GENERATED_CASES = 60

# Index underlyings to cover, as ICICI short names. SENSEX exercises the BSE baseline, which is
# a separate ingest with its own pfCode mapping and is otherwise never measured.
_INDEX_UNDERLYINGS: tuple[tuple[str, str], ...] = (
    ("NIFTY", cfg.NFO),
    ("CNXBAN", cfg.NFO),
    ("BSESEN", cfg.BFO),
)
_STOCK_COUNT = 2


@dataclass(frozen=True)
class CaseLeg:
    stock_code: str
    exchange_code: str
    expiry_date: str
    strike_price: float
    right: str  # "Call" | "Put"
    action: str  # "Buy" | "Sell"
    quantity: int


@dataclass(frozen=True)
class HarnessCase:
    id: str
    label: str
    structure: str
    source: str  # "generated" | "open_positions"
    stock_code: str
    exchange_code: str
    expiry_date: str
    is_index: bool
    is_expiry_day: bool
    lot_size: int
    spot_price: float | None
    spot_source: str
    som_rate: float | None
    legs: list[CaseLeg] = field(default_factory=list)
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "legs": [asdict(leg) for leg in self.legs]}


def _scrip_conn() -> sqlite3.Connection:
    return sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB)


def _parse_expiry(raw: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(str(raw).strip(), "%d-%b-%Y").date()
    except (TypeError, ValueError):
        return None


def _tradeable_strikes(
    conn: sqlite3.Connection, stock_code: str, segment: str, expiry: str
) -> dict[str, list[float]]:
    rows = conn.execute(
        """
        SELECT DISTINCT OptionType, StrikePrice
        FROM scrip_master
        WHERE ShortName = ? AND SegmentCode = ? AND ExpiryDate = ?
          AND OptionType IN ('CE', 'PE') AND MarginPercentage > 0
        """,
        (stock_code, segment, expiry),
    ).fetchall()
    out: dict[str, list[float]] = {"CE": [], "PE": []}
    for opt_type, strike in rows:
        try:
            out[str(opt_type).upper()].append(float(strike))
        except (TypeError, ValueError, KeyError):
            continue
    for key in out:
        out[key].sort()
    return out


def _nearest(strikes: list[float], target: float) -> float | None:
    if not strikes:
        return None
    return min(strikes, key=lambda s: abs(s - target))


def _lot_size(conn: sqlite3.Connection, stock_code: str, segment: str, expiry: str) -> int:
    row = conn.execute(
        """
        SELECT LotSize FROM scrip_master
        WHERE ShortName = ? AND SegmentCode = ? AND ExpiryDate = ? AND LotSize > 0
        LIMIT 1
        """,
        (stock_code, segment, expiry),
    ).fetchone()
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0


def _expiries(conn: sqlite3.Connection, stock_code: str, segment: str, count: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT ExpiryDate FROM scrip_master
        WHERE ShortName = ? AND SegmentCode = ? AND OptionType IN ('CE', 'PE')
          AND MarginPercentage > 0
        """,
        (stock_code, segment),
    ).fetchall()
    today = today_ist_date()
    dated = []
    for (raw,) in rows:
        d = _parse_expiry(raw)
        # ExpiryDate is stored as display text, so ordering has to happen on parsed dates --
        # a lexical sort puts 'Apr' before 'Jan'.
        if d and d >= today:
            dated.append((d, str(raw)))
    dated.sort()
    return [raw for _d, raw in dated[:count]]


def _liquid_stocks(conn: sqlite3.Connection, limit: int) -> list[str]:
    """Stand-ins for "actively traded": the underlyings the exchange lists most strikes for."""
    rows = conn.execute(
        """
        SELECT ShortName, COUNT(*) AS n
        FROM scrip_master
        WHERE SegmentCode = ? AND OptionType IN ('CE', 'PE') AND MarginPercentage > 0
        GROUP BY ShortName
        ORDER BY n DESC
        """,
        (cfg.NFO,),
    ).fetchall()
    index_names = {name for name, _ex in _INDEX_UNDERLYINGS} | {
        sym.short_name for sym in underlyings(kind=KIND_INDEX)
    }
    out = []
    for name, _n in rows:
        sn = str(name).strip().upper()
        if sn in index_names:
            continue
        out.append(sn)
        if len(out) >= limit:
            break
    return out


def _structures(
    *,
    strikes: dict[str, list[float]],
    spot: float,
    is_index: bool,
    lot_size: int,
) -> list[tuple[str, str, list[tuple[float, str, str, int]]]]:
    """(structure id, label, [(strike, right, action, lots)]) for one underlying-expiry."""
    ce, pe = strikes.get("CE") or [], strikes.get("PE") or []
    atm_ce, atm_pe = _nearest(ce, spot), _nearest(pe, spot)
    if atm_ce is None or atm_pe is None:
        return []
    # Deep-OTM offsets are chosen to straddle the ELM deep-OTM thresholds (10% index, 30%
    # stock), so a run can tell the tiered model apart from the flat one.
    deep_frac = 0.15 if is_index else 0.35
    otm_ce = _nearest(ce, spot * 1.05)
    otm_pe = _nearest(pe, spot * 0.95)
    deep_ce = _nearest(ce, spot * (1 + deep_frac))
    wing_ce = _nearest(ce, spot * 1.10)
    wing_pe = _nearest(pe, spot * 0.90)
    lower_pe = _nearest(pe, spot * 0.98)

    out: list[tuple[str, str, list[tuple[float, str, str, int]]]] = [
        ("short_atm_ce", "Short ATM call", [(atm_ce, "Call", "Sell", 1)]),
        ("short_otm_pe", "Short 5% OTM put", [(otm_pe, "Put", "Sell", 1)]),
        ("long_atm_ce", "Long ATM call", [(atm_ce, "Call", "Buy", 1)]),
    ]
    if deep_ce is not None and deep_ce != atm_ce:
        out.append(("short_deep_otm_ce", f"Short {int(deep_frac * 100)}% OTM call", [(deep_ce, "Call", "Sell", 1)]))
    if lower_pe is not None and lower_pe != atm_pe:
        out.append(
            (
                "bull_put_spread",
                "Bull put spread",
                [(atm_pe, "Put", "Sell", 1), (lower_pe, "Put", "Buy", 1)],
            )
        )
    if otm_ce is not None and otm_pe is not None:
        out.append(
            (
                "short_strangle",
                "Short 5% strangle",
                [(otm_ce, "Call", "Sell", 1), (otm_pe, "Put", "Sell", 1)],
            )
        )
        if wing_ce is not None and wing_pe is not None and wing_ce != otm_ce and wing_pe != otm_pe:
            out.append(
                (
                    "iron_condor",
                    "Iron condor",
                    [
                        (otm_ce, "Call", "Sell", 1),
                        (wing_ce, "Call", "Buy", 1),
                        (otm_pe, "Put", "Sell", 1),
                        (wing_pe, "Put", "Buy", 1),
                    ],
                )
            )
    return out


def build_generated_cases(*, max_cases: int = MAX_GENERATED_CASES) -> list[HarnessCase]:
    cases: list[HarnessCase] = []
    with _scrip_conn() as conn:
        stocks = [(name, cfg.NFO) for name in _liquid_stocks(conn, _STOCK_COUNT)]
        targets = [(name, exch, True, 2) for name, exch in _INDEX_UNDERLYINGS]
        targets += [(name, exch, False, 1) for name, exch in stocks]

        for stock_code, exchange_code, is_index, expiry_count in targets:
            segment = exchange_code
            facts = get_underlying_facts_for(exchange_code, stock_code) or {}
            spot = facts.get("spot_price")
            if not spot or float(spot) <= 0:
                _logger.info("Harness: no SPAN spot for %s, skipping", stock_code)
                continue
            spot = float(spot)
            for expiry in _expiries(conn, stock_code, segment, expiry_count):
                lot_size = _lot_size(conn, stock_code, segment, expiry)
                if lot_size <= 0:
                    continue
                strikes = _tradeable_strikes(conn, stock_code, segment, expiry)
                expiry_date = _parse_expiry(expiry)
                is_expiry_day = expiry_date == today_ist_date()
                for structure, label, spec in _structures(
                    strikes=strikes, spot=spot, is_index=is_index, lot_size=lot_size
                ):
                    legs = [
                        CaseLeg(
                            stock_code=stock_code,
                            exchange_code=exchange_code,
                            expiry_date=expiry,
                            strike_price=strike,
                            right=right,
                            action=action,
                            quantity=lots * lot_size,
                        )
                        for strike, right, action, lots in spec
                    ]
                    cases.append(
                        HarnessCase(
                            id=f"{stock_code}:{expiry}:{structure}",
                            label=f"{stock_code} {expiry} — {label}",
                            structure=structure,
                            source="generated",
                            stock_code=stock_code,
                            exchange_code=exchange_code,
                            expiry_date=expiry,
                            is_index=is_index,
                            is_expiry_day=is_expiry_day,
                            lot_size=lot_size,
                            spot_price=spot,
                            spot_source=f"span_file:{facts.get('source_file') or ''}",
                            som_rate=facts.get("som_rate"),
                            legs=legs,
                        )
                    )
    if len(cases) > max_cases:
        _logger.info("Harness: trimming %s generated cases to %s", len(cases), max_cases)
        cases = cases[:max_cases]
    return cases


def build_open_position_cases(positions: list[dict[str, Any]]) -> list[HarnessCase]:
    """One case per (exchange, underlying, expiry) group actually held.

    Grouped, not per leg: a naked-leg case would price each short as if the rest of the book
    did not exist, which is the thing SPAN netting exists to avoid measuring wrongly.
    """
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for pos in positions or []:
        try:
            qty = abs(int(float(pos.get("quantity") or 0)))
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        right = str(pos.get("right") or "").strip()
        if right not in (cfg.CALL, cfg.PUT, "Call", "Put"):
            continue
        key = (
            str(pos.get("exchange_code") or cfg.NFO).strip().upper(),
            str(pos.get("stock_code") or "").strip().upper(),
            str(pos.get("expiry_date") or "").strip(),
        )
        if not key[1] or not key[2]:
            continue
        groups.setdefault(key, []).append(pos)

    cases: list[HarnessCase] = []
    for (exchange_code, stock_code, expiry), members in groups.items():
        # The broker's own classification when the position carries one; the name set is the
        # fallback for a leg that arrived without it.
        indicator = str((members[0] or {}).get("stock_index_indicator") or "").strip()
        is_index = (
            indicator == cfg.INDEX
            if indicator
            else bool(symbol_is_index(stock_code, segment=exchange_code))
        )
        facts = get_underlying_facts_for(exchange_code, stock_code) or {}
        spot = facts.get("spot_price")
        expiry_date = _parse_expiry(expiry)
        legs = []
        lot_size = 0
        for pos in members:
            try:
                strike = float(pos.get("strike_price") or 0)
                qty = abs(int(float(pos.get("quantity") or 0)))
            except (TypeError, ValueError):
                continue
            if strike <= 0 or qty <= 0:
                continue
            action = "Buy" if str(pos.get("action") or "").strip().lower() == "buy" else "Sell"
            legs.append(
                CaseLeg(
                    stock_code=stock_code,
                    exchange_code=exchange_code,
                    expiry_date=expiry,
                    strike_price=strike,
                    right="Call" if str(pos.get("right")).strip().lower().startswith("c") else "Put",
                    action=action,
                    quantity=qty,
                )
            )
            try:
                lot_size = max(lot_size, int(float(pos.get("lot_size") or 0)))
            except (TypeError, ValueError):
                pass
        if not legs:
            continue
        cases.append(
            HarnessCase(
                id=f"open:{exchange_code}:{stock_code}:{expiry}",
                label=f"Open book — {stock_code} {expiry} ({len(legs)} legs)",
                structure="open_positions_group",
                source="open_positions",
                stock_code=stock_code,
                exchange_code=exchange_code,
                expiry_date=expiry,
                is_index=is_index,
                is_expiry_day=expiry_date == today_ist_date(),
                lot_size=lot_size,
                spot_price=float(spot) if spot else None,
                spot_source=f"span_file:{facts.get('source_file') or ''}",
                som_rate=facts.get("som_rate"),
                legs=legs,
                notes="Built from the account's live positions at run time.",
            )
        )
    return cases
