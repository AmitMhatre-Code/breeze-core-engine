"""The market state a calibration sweep was priced in, from NSE's public files.

A sweep asks whether ICICI's charge above the SPAN file (#48) follows something readable: a
stock's own volatility, its cash-market VaR/ELM rates, India VIX, a ban. Every input here comes
from NSE's archives, never from ICICI, so recording it costs no broker quota and a later run can
be compared against an earlier one on the same footing.

All best effort: a file NSE has not published (a holiday, a late revision) is reported in
`errors` and leaves its fields empty. It never stops a sweep.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
from typing import Any

import requests

from icici_breeze_backend.app.services.reference_data.bhavcopy_common import (
    NSE_ARCHIVES_HTTP_HEADERS,
    request_timeout,
)

_logger = logging.getLogger(__name__)

_BASE = "https://nsearchives.nseindia.com"
_INDICES_URL = _BASE + "/content/indices/ind_close_all_{ddmmyyyy}.csv"
_VOLATILITY_URL = _BASE + "/archives/nsccl/volt/FOVOLT_{ddmmyyyy}.csv"
_VAR_URL = _BASE + "/archives/nsccl/var/C_VAR1_{ddmmyyyy}_{version}.DAT"
_BAN_URL = _BASE + "/archives/fo/sec_ban/fo_secban_{ddmmyyyy}.csv"
_VAR_MAX_VERSION = 6


def _get(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=dict(NSE_ARCHIVES_HTTP_HEADERS), timeout=request_timeout())
    except requests.RequestException as exc:
        _logger.info("market context: %s unavailable: %s", url, exc)
        return None
    if resp.status_code != 200 or "text/html" in str(resp.headers.get("Content-Type") or "").lower():
        return None
    return resp.content.decode("utf-8", errors="replace")


def _num(raw: Any) -> float | None:
    try:
        text = str(raw).strip()
        return float(text) if text not in ("", "-") else None
    except (TypeError, ValueError):
        return None


def parse_india_vix(text: str) -> dict[str, float | None] | None:
    for row in csv.DictReader(io.StringIO(text)):
        name = str(row.get("Index Name") or "").strip().lower()
        if name == "india vix":
            return {
                "open": _num(row.get("Open Index Value")),
                "high": _num(row.get("High Index Value")),
                "low": _num(row.get("Low Index Value")),
                "close": _num(row.get("Closing Index Value")),
            }
    return None


def parse_volatility(text: str) -> dict[str, dict[str, float | None]]:
    """FOVOLT: per symbol, the applicable daily and annualised volatility NSE uses."""
    out: dict[str, dict[str, float | None]] = {}
    reader = csv.reader(io.StringIO(text))
    next(reader, None)
    for row in reader:
        if len(row) < 16:
            continue
        symbol = row[1].strip().upper()
        out[symbol] = {"daily_vol": _num(row[14]), "annual_vol": _num(row[15])}
    return out


def parse_var(text: str) -> dict[str, dict[str, float | None]]:
    """C_VAR1: per EQ-series symbol, the cash-market VaR, ELM and applicable margin rates (%)."""
    out: dict[str, dict[str, float | None]] = {}
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 10 or parts[0] != "20" or parts[2] != "EQ":
            continue
        out[parts[1].upper()] = {
            "security_var_pct": _num(parts[4]),
            "index_var_pct": _num(parts[5]),
            "var_margin_pct": _num(parts[6]),
            "elm_pct": _num(parts[7]),
            "adhoc_margin_pct": _num(parts[8]),
            "applicable_margin_pct": _num(parts[9]),
        }
    return out


def parse_ban(text: str) -> list[str]:
    out = []
    for line in text.splitlines()[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[1]:
            out.append(parts[1].upper())
    return out


def fetch_market_context(trade_date: dt.date, ban_date: dt.date | None = None) -> dict[str, Any]:
    """Everything a sweep records about the market, for `trade_date`'s close.

    `ban_date` is the session the ban list is for -- the next trading day when the sweep runs
    after the close, since that is the list ICICI is enforcing on new orders.
    """
    ddmmyyyy = trade_date.strftime("%d%m%Y")
    ctx: dict[str, Any] = {
        "trade_date": trade_date.isoformat(),
        "india_vix": None,
        "volatility": {},
        "var": {},
        "fo_ban": [],
        "ban_date": (ban_date or trade_date).isoformat(),
        "files": {},
        "errors": [],
    }

    url = _INDICES_URL.format(ddmmyyyy=ddmmyyyy)
    text = _get(url)
    ctx["india_vix"] = parse_india_vix(text) if text else None
    if ctx["india_vix"]:
        ctx["files"]["india_vix"] = url
    else:
        ctx["errors"].append(f"India VIX not found in {url}")

    url = _VOLATILITY_URL.format(ddmmyyyy=ddmmyyyy)
    text = _get(url)
    if text:
        ctx["volatility"] = parse_volatility(text)
        ctx["files"]["volatility"] = url
    else:
        ctx["errors"].append(f"Volatility file unavailable: {url}")

    for version in range(_VAR_MAX_VERSION, 0, -1):
        url = _VAR_URL.format(ddmmyyyy=ddmmyyyy, version=version)
        text = _get(url)
        if text:
            ctx["var"] = parse_var(text)
            ctx["files"]["var"] = url
            break
    else:
        ctx["errors"].append(f"VaR file unavailable for {trade_date.isoformat()}")

    url = _BAN_URL.format(ddmmyyyy=(ban_date or trade_date).strftime("%d%m%Y"))
    text = _get(url)
    if text is None and ban_date and ban_date != trade_date:
        # The next session's list is published late in the evening; fall back to today's.
        url = _BAN_URL.format(ddmmyyyy=ddmmyyyy)
        text = _get(url)
        ctx["ban_date"] = trade_date.isoformat()
    if text is not None:
        ctx["fo_ban"] = parse_ban(text)
        ctx["files"]["fo_ban"] = url
    else:
        ctx["errors"].append("F&O ban list unavailable")
    return ctx


def features_for(ctx: dict[str, Any], names: str | list[str] | tuple[str, ...] | None) -> dict[str, Any]:
    """The per-underlying slice of `ctx` a case carries.

    `names` are every spelling the underlying answers to (symbol_registry.aliases_for): NSE's
    files key stocks by symbol (RELIANCE) but indices by their F&O ticker (BANKNIFTY), which is
    not the registry's exchange symbol (NIFTY BANK).
    """
    if isinstance(names, str) or names is None:
        names = [names or ""]
    wanted = [str(n).strip().upper() for n in names if str(n or "").strip()]
    vol_map = ctx.get("volatility") or {}
    var_map = ctx.get("var") or {}
    vol_key = next((n for n in wanted if n in vol_map), None)
    var_key = next((n for n in wanted if n in var_map), None)
    vix = ctx.get("india_vix") or {}
    return {
        "exchange_symbol": vol_key or var_key or (wanted[0] if wanted else None),
        "india_vix_close": vix.get("close"),
        **vol_map.get(vol_key or "", {"daily_vol": None, "annual_vol": None}),
        **var_map.get(var_key or "", {}),
        "in_fo_ban": any(n in set(ctx.get("fo_ban") or []) for n in wanted),
    }
