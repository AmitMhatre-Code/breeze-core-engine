"""ICICI margin add-on: what ICICI's margin_calculator charges above the exchange SPAN file.

Three margin-harness runs (docs/design-decisions.md #48) showed ICICI's quoted figure sitting
above the SPAN file's own margin by a fixed share of each *short* leg's notional -- ~2% for
index options, ~3% once a strike is more than 10% out of the money, more for single stocks --
identical for a naked short and an iron condor at the same strike, and nil on long-only books.
A SPAN-file margin is only a stand-in for ICICI's number once that charge is added back.

ICICI's margin_calculator does not include exposure margin (ELM) except on the option's own
expiry day, so this is modelled as an ICICI add-on in its own right, never as ELM, and the
app's ELM overlays stay exactly where they were.

The rates are operator-set in breeze-saas-portal and arrive inside the signed heartbeat
policy. They are never baked in: a deployment that has not received them within
`STALE_AFTER` has no add-on, and every "use the SPAN file" toggle then falls back to
ICICI's margin_calculator (margin_source_prefs.effective_margin_source) and says why.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
import time
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import IST, today_ist_date

_logger = logging.getLogger(__name__)

#: Values older than this no longer count as received. Rates change rarely; this is about
#: noticing a heartbeat channel that has stopped working, not about the rates going stale.
STALE_AFTER = dt.timedelta(hours=24)

# Sanity bounds on what a signed policy may set. A rate is a fraction of notional.
_RATE_MAX = 0.25
_THRESHOLD_MAX = 1.0

_RATE_FIELDS = (
    "index_rate",
    "index_deep_otm_rate",
    "index_deep_otm_threshold",
    "stock_rate",
    "stock_deep_otm_rate",
    "stock_deep_otm_threshold",
    "expiry_day_extra_rate",
)


@dataclass(frozen=True)
class MarginAddonRates:
    version: str
    index_rate: float
    index_deep_otm_rate: float
    index_deep_otm_threshold: float
    stock_rate: float
    stock_deep_otm_rate: float
    stock_deep_otm_threshold: float
    expiry_day_extra_rate: float


def _db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


def ensure_margin_addon_table(db_path: str | None = None) -> None:
    with sqlite3.connect(db_path or _db_path()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS margin_addon_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                rates_json TEXT NOT NULL,
                version TEXT NOT NULL,
                received_at_epoch REAL NOT NULL
            )
            """
        )
        conn.commit()


def parse_rates(value: Any) -> MarginAddonRates | None:
    """Validate a `margin_addon` policy claim. None (and a log line) when it is unusable."""
    if not isinstance(value, Mapping):
        return None
    version = str(value.get("version") or "").strip()
    if not version or len(version) > 64:
        return None
    out: dict[str, Any] = {"version": version}
    for name in _RATE_FIELDS:
        raw = value.get(name)
        if isinstance(raw, bool):
            return None
        try:
            num = float(raw)
        except (TypeError, ValueError):
            return None
        bound = _THRESHOLD_MAX if name.endswith("_threshold") else _RATE_MAX
        if not (0.0 <= num <= bound):
            return None
        out[name] = num
    return MarginAddonRates(**out)


def record_from_policy(policy: Mapping[str, Any]) -> bool:
    """Persist the add-on carried by a *verified* heartbeat policy. Returns True if stored.

    A policy without the claim (a portal that predates it) or with a malformed one stores
    nothing, so the last good values age out on their own schedule rather than being wiped.
    """
    raw = policy.get("margin_addon")
    if raw is None:
        return False
    rates = parse_rates(raw)
    if rates is None:
        _logger.warning("portal heartbeat carried an unusable margin_addon claim; ignored")
        return False
    try:
        ensure_margin_addon_table()
        with sqlite3.connect(_db_path()) as conn:
            conn.execute(
                """
                INSERT INTO margin_addon_state (id, rates_json, version, received_at_epoch)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    rates_json = excluded.rates_json,
                    version = excluded.version,
                    received_at_epoch = excluded.received_at_epoch
                """,
                (json.dumps(asdict(rates)), rates.version, time.time()),
            )
            conn.commit()
    except sqlite3.Error:
        _logger.exception("could not persist the portal margin add-on")
        return False
    return True


def _load() -> tuple[MarginAddonRates, float] | None:
    try:
        ensure_margin_addon_table()
        with sqlite3.connect(_db_path()) as conn:
            row = conn.execute(
                "SELECT rates_json, received_at_epoch FROM margin_addon_state WHERE id = 1"
            ).fetchone()
    except sqlite3.Error:
        _logger.debug("margin add-on state unreadable", exc_info=True)
        return None
    if not row:
        return None
    try:
        rates = parse_rates(json.loads(row[0]))
    except (TypeError, ValueError):
        rates = None
    if rates is None:
        return None
    return rates, float(row[1])


def get_active_rates(*, now_epoch: float | None = None) -> MarginAddonRates | None:
    """The add-on to apply, or None when none has been received within STALE_AFTER."""
    loaded = _load()
    if loaded is None:
        return None
    rates, received = loaded
    now = time.time() if now_epoch is None else now_epoch
    if now - received > STALE_AFTER.total_seconds():
        return None
    return rates


def status(*, now_epoch: float | None = None) -> dict[str, Any]:
    """What the Settings screen shows under the margin toggles."""
    loaded = _load()
    now = time.time() if now_epoch is None else now_epoch
    if loaded is None:
        return {
            "available": False,
            "reason": "never_received",
            "message": (
                "The app has not been able to fetch the ICICI margin add-on from the portal, "
                "because the heartbeat to the portal is not working. Margins use ICICI's "
                "margin calculator until it arrives."
            ),
            "received_at": None,
            "rates": None,
        }
    rates, received = loaded
    received_iso = dt.datetime.fromtimestamp(received, tz=IST).isoformat(timespec="seconds")
    if now - received > STALE_AFTER.total_seconds():
        return {
            "available": False,
            "reason": "stale",
            "message": (
                "The app has not been able to fetch the ICICI margin add-on from the portal "
                "for more than 24 hours, because the heartbeat to the portal is not working. "
                "Margins use ICICI's margin calculator until it is fetched again."
            ),
            "received_at": received_iso,
            "rates": asdict(rates),
        }
    return {
        "available": True,
        "reason": None,
        "message": None,
        "received_at": received_iso,
        "rates": asdict(rates),
    }


# --------------------------------------------------------------------------------------
# Arithmetic
# --------------------------------------------------------------------------------------


def _norm_right(raw: Any) -> str | None:
    text = str(raw or "").strip().lower()
    if text in ("call", "ce", "c"):
        return "call"
    if text in ("put", "pe", "p"):
        return "put"
    return None


def _signed_qty(leg: Mapping[str, Any]) -> int:
    try:
        qty = abs(int(float(leg.get("quantity") or 0)))
    except (TypeError, ValueError):
        return 0
    action = str(leg.get("action") or "").strip().lower()
    return -qty if action == "sell" else qty


def _expiry_date(raw: Any) -> dt.date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    text = text.removesuffix("T06:00:00.000Z")
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text[:11] if fmt == "%d-%b-%Y" else text[:10], fmt).date()
        except ValueError:
            continue
    return None


def leg_rate(rates: MarginAddonRates, *, right: str, strike: float, spot: float, is_index: bool) -> float:
    """Rate for one short leg: standard, or the deep-OTM tier past the threshold."""
    if right == "call":
        otm = (strike - spot) / spot
    else:
        otm = (spot - strike) / spot
    if is_index:
        return rates.index_deep_otm_rate if otm > rates.index_deep_otm_threshold else rates.index_rate
    return rates.stock_deep_otm_rate if otm > rates.stock_deep_otm_threshold else rates.stock_rate


def compute_addon(
    rates: MarginAddonRates,
    legs: Iterable[Mapping[str, Any]],
    *,
    spot: float,
    is_index: bool,
    today: dt.date | None = None,
) -> float:
    """Add-on for a set of legs on one underlying.

    Legs are netted per contract first (a buy closing part of a short reduces the short
    quantity), then every net-short contract is charged its rate on spot x quantity. Longs
    carry nothing and a long wing does not reduce a short leg's charge -- both measured.
    """
    if spot <= 0:
        return 0.0
    today = today or today_ist_date()
    net: dict[tuple, int] = {}
    meta: dict[tuple, tuple[str, float, dt.date | None]] = {}
    for leg in legs:
        right = _norm_right(leg.get("right"))
        try:
            strike = float(leg.get("strike_price"))
        except (TypeError, ValueError):
            continue
        if right is None:
            continue
        expiry = _expiry_date(leg.get("expiry_date"))
        key = (expiry, right, strike)
        net[key] = net.get(key, 0) + _signed_qty(leg)
        meta[key] = (right, strike, expiry)
    total = 0.0
    for key, qty in net.items():
        if qty >= 0:
            continue
        right, strike, expiry = meta[key]
        rate = leg_rate(rates, right=right, strike=strike, spot=spot, is_index=is_index)
        if expiry is not None and expiry == today:
            rate += rates.expiry_day_extra_rate
        total += rate * spot * abs(qty)
    return round(total, 2)


def addon_for_underlying(
    exchange_code: str,
    stock_code: str,
    legs: Iterable[Mapping[str, Any]],
    *,
    rates: MarginAddonRates,
    spot: float | None = None,
) -> dict[str, Any] | None:
    """Add-on for one underlying's legs, priced off the SPAN file's own underlying price.

    That price is what the rates were measured against; `spot` is only a fallback for an
    underlying the file does not carry one for. None when neither is known, or when the
    registry cannot say whether the underlying is an index -- guessing the tier is a money
    error, so the caller falls back to ICICI instead.
    """
    from icici_breeze_backend.app.services.reference_data.span_baseline_store import (
        get_underlying_facts_for,
    )
    from icici_breeze_backend.app.services.reference_data.symbol_registry import is_index

    facts = get_underlying_facts_for(exchange_code, stock_code) or {}
    base = facts.get("spot_price")
    try:
        base_f = float(base) if base else 0.0
    except (TypeError, ValueError):
        base_f = 0.0
    if base_f <= 0 and spot:
        base_f = float(spot)
    if base_f <= 0:
        return None
    index = is_index(stock_code, segment=exchange_code)
    if index is None:
        return None
    return {
        "amount": compute_addon(rates, legs, spot=base_f, is_index=bool(index)),
        "version": rates.version,
        "spot": base_f,
        "is_index": bool(index),
    }
