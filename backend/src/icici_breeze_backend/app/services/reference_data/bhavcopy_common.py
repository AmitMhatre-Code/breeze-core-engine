"""Shared bhavcopy parsing utilities."""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import parse_strike, strike_key

NSE_ARCHIVES_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/octet-stream,*/*;q=0.8",
    "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
    "Referer": "https://www.nseindia.com/",
}

BSE_HTTP_HEADERS = {
    "User-Agent": NSE_ARCHIVES_HTTP_HEADERS["User-Agent"],
    "Accept": "text/csv,*/*;q=0.8",
    "Referer": "https://www.bseindia.com/",
}


def request_timeout() -> tuple[float, float]:
    read_s = max(60.0, float(cfg.REFERENCE_DATA_REQUEST_TIMEOUT_SECONDS))
    connect_s = max(5.0, min(60.0, float(cfg.REFERENCE_DATA_CONNECT_TIMEOUT_SECONDS)))
    return (connect_s, read_s)


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(str(v or "").replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(str(v or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def fmt2(v: float) -> str:
    return f"{v:.2f}"


def date_key_from_any(raw: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    if "T" in s:
        s = s.split("T", 1)[0].strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d-%b-%Y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    if re.match(r"^\d{8}$", s):
        try:
            return dt.datetime.strptime(s, "%Y%m%d").date().isoformat()
        except ValueError:
            return ""
    return ""


def display_from_iso_date(date_key: str) -> str:
    try:
        return dt.datetime.strptime(date_key, "%Y-%m-%d").strftime("%d-%b-%Y")
    except ValueError:
        return ""


def normalize_opt_type(v: str) -> str:
    s = str(v or "").strip().upper()
    if s in {"CE", "CALL"}:
        return cfg.CALL
    if s in {"PE", "PUT"}:
        return cfg.PUT
    return ""


def bhav_row_get(raw: dict[str, str], *candidates: str) -> str:
    if not raw:
        return ""
    norm: dict[str, str] = {}
    for k, v in raw.items():
        nk = str(k).strip().lstrip("\ufeff").upper().replace(" ", "_")
        norm[nk] = str(v if v is not None else "").strip()
    for c in candidates:
        key = str(c).strip().upper().replace(" ", "_")
        if key in norm:
            return norm[key]
    return ""


def normalize_nse_fo_bhavcopy_row(raw_row: dict[str, str]) -> dict[str, str] | None:
    right = normalize_opt_type(raw_row.get("OptnTp") or bhav_row_get(raw_row, "OptnTp", "OPTNTP"))
    if not right:
        return None
    symbol = str(raw_row.get("TckrSymb") or bhav_row_get(raw_row, "TckrSymb", "TCKRSYMB")).strip().upper()
    if not symbol:
        return None
    expiry_key = date_key_from_any(
        raw_row.get("XpryDt") or raw_row.get("FininstrmActlXpryDt")
        or bhav_row_get(raw_row, "XpryDt", "FININSTRMACTLXPRYDT")
    )
    if not expiry_key:
        return None
    strike = parse_strike(raw_row.get("StrkPric") or bhav_row_get(raw_row, "StrkPric", "STRKPRIC"))
    if strike is None:
        return None
    cls = safe_float(raw_row.get("ClsPric") or bhav_row_get(raw_row, "ClsPric", "CLSPRIC"), 0.0)
    if cls <= 0:
        return None
    # `ltp` must be the option's LAST TRADED price, not `ClsPric`. On expiry day the
    # exchange's ClsPric (settlement) for an index option carries the *underlying*
    # settlement value (e.g. the SENSEX level), not the option premium -- reading it as
    # ltp produced a ~index-magnitude "previous close" and blew up Day's P&L (see
    # dashboard_day_pnl). LastPric is the real traded price; fall back to ClsPric only
    # when the contract genuinely did not trade (LastPric absent/0).
    last = safe_float(
        raw_row.get("LastPric")
        or bhav_row_get(raw_row, "LastPric", "LstPric", "LAST_PRICE", "LAST_TRADED_PRICE", "LTP", "LAST"),
        0.0,
    )
    # Depth is deliberately NOT synthesized. Bhavcopy is an EOD price file with no
    # order-book columns; the previous `buy_qty = vol // 2` split invented a book
    # out of traded volume, and defaulting bid/ask to the close invented a
    # zero-width spread. Both read as real data downstream. Absent keys mean
    # "unknown", which consumers must treat differently from "no interest".
    bid_raw = raw_row.get("BidPric") or bhav_row_get(raw_row, "BidPric", "BIDPRIC")
    ask_raw = raw_row.get("AskPric") or bhav_row_get(raw_row, "AskPric", "ASKPRIC")
    bid = safe_float(bid_raw, 0.0) if bid_raw else 0.0
    ask = safe_float(ask_raw, 0.0) if ask_raw else 0.0
    oi = max(0, safe_int(raw_row.get("OpnIntrst") or bhav_row_get(raw_row, "OpnIntrst"), 0))
    spot = safe_float(raw_row.get("UndrlygPric") or bhav_row_get(raw_row, "UndrlygPric"), 0.0)
    prev_close = safe_float(
        raw_row.get("PrvsClsgPric") or bhav_row_get(raw_row, "PrvsClsgPric"), cls
    )
    row = {
        "stock_code": symbol,
        "expiry_display": display_from_iso_date(expiry_key),
        "expiry_date": expiry_key,
        "right": right,
        "strike_price": strike_key(strike),
        "ltp": fmt2(last if last > 0 else cls),
        "open_interest": str(oi),
        "spot_price": fmt2(spot if spot > 0 else cls),
        "open": fmt2(safe_float(raw_row.get("OpnPric"), cls)),
        "high": fmt2(safe_float(raw_row.get("HghPric"), cls)),
        "low": fmt2(safe_float(raw_row.get("LwPric"), cls)),
        "previous_close": fmt2(prev_close if prev_close > 0 else cls),
        "segment": cfg.NFO,
    }
    # Quoted prices only when the file actually carried them.
    if bid > 0:
        row["best_bid_price"] = fmt2(bid)
    if ask > 0:
        row["best_offer_price"] = fmt2(ask)
    return row


def normalize_bse_fo_bhavcopy_row(raw_row: dict[str, str]) -> dict[str, str] | None:
    """BSE derivative bhavcopy uses different column names; map to shared schema."""
    right = normalize_opt_type(
        bhav_row_get(
            raw_row,
            "OPTN_TP",
            "OptnTp",
            "OPTION_TYPE",
            "OPTIONTYPE",
            "OP_TYP",
        )
    )
    if not right:
        return None
    symbol = bhav_row_get(
        raw_row,
        "TCKR_SYMB",
        "TckrSymb",
        "SYMBOL",
        "SCRIP_CD",
        "SCRIP_CODE",
        "UNDERLYING",
    ).strip().upper()
    if not symbol:
        return None
    expiry_key = date_key_from_any(
        bhav_row_get(raw_row, "XPRY_DT", "XpryDt", "EXPIRY_DT", "EXPIRY_DATE", "EXP_DATE")
    )
    if not expiry_key:
        return None
    strike_raw = bhav_row_get(raw_row, "STRK_PRIC", "StrkPric", "STRIKE_PR", "STRIKE_PRICE")
    strike = parse_strike(strike_raw)
    if strike is None:
        return None
    cls = safe_float(bhav_row_get(raw_row, "CLSPRIC", "CLS_PRIC", "CLOSE", "CLOSE_PR"), 0.0)
    if cls <= 0:
        return None
    # BSE `CLSPRIC` is a settlement price that, on expiry day, carries the underlying
    # index level for index options -- not the option premium. Use `LastPric` (real last
    # traded price) as the option ltp, keeping ClsPric only as a fallback for contracts
    # that did not trade. See normalize_nse_fo_bhavcopy_row / dashboard_day_pnl.
    last = safe_float(
        bhav_row_get(raw_row, "LastPric", "LstPric", "LAST_PRICE", "LAST_TRADED_PRICE", "LTP", "LAST"),
        0.0,
    )
    # BSE wipes its order book at close, so these columns are routinely absent or
    # zero in the published file -- pass them through only when genuinely present
    # rather than defaulting them to the close price.
    bid_raw = bhav_row_get(raw_row, "BIDPRIC", "BID_PR", "BID_PRICE")
    ask_raw = bhav_row_get(raw_row, "ASKPRIC", "ASK_PR", "ASK_PRICE")
    vol = max(0, safe_int(bhav_row_get(raw_row, "TTL_TRADG_VOL", "TtlTradgVol", "VOLUME"), 0))
    oi = max(0, safe_int(bhav_row_get(raw_row, "OPN_INTRST", "OpnIntrst", "OPEN_INT"), 0))
    spot = safe_float(bhav_row_get(raw_row, "UNDRLYGPRIC", "UndrlygPric", "UNDERLYING_VAL"), 0.0)
    prev_close = safe_float(bhav_row_get(raw_row, "PRVSCLSNGPRIC", "PrvsClsgPric", "PREV_CLOSE"), cls)
    row = normalize_nse_fo_bhavcopy_row(
        {
            "TckrSymb": symbol,
            "XpryDt": expiry_key,
            "OptnTp": right,
            "StrkPric": strike_raw or strike_key(strike),
            "ClsPric": str(cls),
            "LastPric": str(last),
            "BidPric": bid_raw or "",
            "AskPric": ask_raw or "",
            "TtlTradgVol": str(vol),
            "OpnIntrst": str(oi),
            "UndrlygPric": str(spot),
            "PrvsClsgPric": str(prev_close),
        }
    )
    if row:
        row["segment"] = cfg.BFO
    return row
