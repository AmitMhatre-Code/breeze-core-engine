"""Each option contract's latest book, kept from the socket in the API process.

Two rooms feed it, both keyed by (exchange, token): the quote room (`4.1!` NFO / `8.1!` BFO) for
the LTP, the best bid/ask with their sizes and the last-trade time, and the depth room (`4.2!` /
`8.2!`) for five levels a side. It lives in the socket's own process because everything that asks
-- the order tickets' check, the Strategy Builder, the bots -- runs there too, and a few hundred
small dicts need no Redis round trip.

The listener runs on the SDK's socket thread for every message in the process, so it does a
symbol split and a dict write and never raises. Parsing the depth block keys on breeze_connect's
`BestBuyQty-k`/`BestSellRate-k` names, which the NSE and BSE layouts share (NSE rows add order
counts and flags), not on field position: **the live depth payload has never been captured**
(#33), only read from the SDK's parser.

A book is usable only while it describes this session: received today, the market open, and the
socket still delivering (#56's arrival clock). Outside that the check answers "unknown" -- BSE
wipes its book at the close and the last depth message of the day is not a book anyone can
trade against tomorrow.
"""
from __future__ import annotations

import datetime
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.liquidity.estimate import (
    SOURCE_DEPTH,
    SOURCE_TOP_OF_BOOK,
    Book,
    Level,
)

_logger = logging.getLogger(__name__)

# Room prefixes for option contracts. The quote room ends in `.1`, the depth room in `.2`.
_PREFIX_EXCHANGE = {"4": "NFO", "8": "BFO"}
_LEVEL_KEY_RE = re.compile(r"^Best(Buy|Sell)(Rate|Qty)-(\d+)$")
_MAX_LEVELS = 5
# The socket counts as delivering while something arrived this recently.
FEED_ALIVE_SECONDS = 60.0


@dataclass
class _Entry:
    ltp: Optional[float] = None
    best_bid: Optional[float] = None
    best_bid_qty: int = 0
    best_ask: Optional[float] = None
    best_ask_qty: int = 0
    last_trade_raw: Any = None
    quote_at: Optional[float] = None
    bids: tuple[Level, ...] = ()
    asks: tuple[Level, ...] = ()
    depth_at: Optional[float] = None


_lock = threading.Lock()
_entries: dict[tuple[str, int], _Entry] = {}
_listener_registered = False
_depth_messages = 0
_quote_messages = 0


def _split_symbol(symbol: Any) -> Optional[tuple[str, str, int]]:
    """`"4.2!71472"` -> ("NFO", "2", 71472). None for anything that is not an option room."""
    prefix, sep, token = str(symbol or "").partition("!")
    if not sep or not token.isdigit():
        return None
    exch, dot, room = prefix.partition(".")
    if not dot:
        return None
    exchange = _PREFIX_EXCHANGE.get(exch)
    if exchange is None or room not in ("1", "2"):
        return None
    return exchange, room, int(token)


def is_depth_payload(payload: Any) -> bool:
    """True for a parsed depth message. The SDK tags these `quotes: "Market Depth"`; the `.2!`
    room is the fallback should one arrive untagged."""
    if not isinstance(payload, dict):
        return False
    if payload.get("quotes") == "Market Depth":
        return True
    prefix, sep, _token = str(payload.get("symbol") or "").partition("!")
    return bool(sep) and prefix.endswith(".2") and "depth" in payload


def depth_symbol(quote_symbol: str) -> Optional[str]:
    """The depth room for a quote room: `4.1!123` -> `4.2!123`."""
    prefix, sep, token = str(quote_symbol or "").partition("!")
    if not sep or not prefix.endswith(".1"):
        return None
    return f"{prefix[:-2]}.2!{token}"


def _num(raw: Any) -> Optional[float]:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def parse_depth_levels(depth: Any) -> tuple[tuple[Level, ...], tuple[Level, ...]] | None:
    """(bids, asks), best first, from the SDK's depth block -- a list of per-level dicts, or one
    flattened dict. None when it carries no level fields at all (malformed, not empty)."""
    rows = [depth] if isinstance(depth, dict) else depth
    if not isinstance(rows, list):
        return None
    cells: dict[tuple[str, int], dict[str, float]] = {}
    found = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            m = _LEVEL_KEY_RE.match(str(key))
            if m is None:
                continue
            level = int(m.group(3))
            if level < 1 or level > _MAX_LEVELS:
                continue
            v = _num(value)
            if v is None:
                continue
            found = True
            cells.setdefault((m.group(1), level), {})[m.group(2)] = v
    if not found:
        return None

    def side(name: str) -> tuple[Level, ...]:
        out = []
        for level in range(1, _MAX_LEVELS + 1):
            cell = cells.get((name, level)) or {}
            price, qty = cell.get("Rate"), cell.get("Qty")
            if price is None or qty is None or price <= 0 or qty <= 0:
                continue
            out.append(Level(price=float(price), qty=int(qty)))
        # Best first whatever order the feed used: highest bid, lowest ask.
        out.sort(key=lambda l: -l.price if name == "Buy" else l.price)
        return tuple(out)

    return side("Buy"), side("Sell")


def on_raw_tick(payload: Any) -> None:
    """Raw socket listener. Cheap, and never raises."""
    global _depth_messages, _quote_messages
    try:
        if not isinstance(payload, dict):
            return
        parts = _split_symbol(payload.get("symbol"))
        if parts is None:
            return
        product = str(payload.get("product_type") or "").strip().lower()
        if product and not product.startswith("opt"):
            return
        exchange, room, token = parts
        now = time.time()
        if room == "2":
            levels = parse_depth_levels(payload.get("depth"))
            if levels is None:
                return
            with _lock:
                entry = _entries.setdefault((exchange, token), _Entry())
                entry.bids, entry.asks = levels
                entry.depth_at = now
                _depth_messages += 1
            return
        if payload.get("quotes") not in (None, "Quotes Data"):
            return
        # NSE cash shares the `4.1!` room and its token numbers with NFO, and the SDK labels a
        # colliding token as cash. Only an F&O quote carries open interest.
        if "OI" not in payload and not product.startswith("opt"):
            return
        ltp = _num(payload.get("last") if payload.get("last") is not None else payload.get("ltp"))
        bid, ask = _num(payload.get("bPrice")), _num(payload.get("sPrice"))
        bid_qty, ask_qty = _num(payload.get("bQty")), _num(payload.get("sQty"))
        with _lock:
            entry = _entries.setdefault((exchange, token), _Entry())
            entry.ltp = ltp
            entry.best_bid = bid
            entry.best_bid_qty = int(bid_qty or 0)
            entry.best_ask = ask
            entry.best_ask_qty = int(ask_qty or 0)
            if payload.get("ltt") not in (None, ""):
                entry.last_trade_raw = payload.get("ltt")
            entry.quote_at = now
            _quote_messages += 1
    except Exception:  # noqa: BLE001 -- the socket thread must never see one of ours
        _logger.debug("liquidity book: tick handling failed", exc_info=True)


def register_listener_once() -> None:
    global _listener_registered
    from icici_breeze_backend.app.services.ws_tick_pipeline import register_raw_tick_listener

    with _lock:
        if _listener_registered:
            return
        _listener_registered = True
    register_raw_tick_listener(on_raw_tick)


def parse_last_trade(raw: Any) -> Optional[float]:
    """Epoch seconds from the feed's `ltt`.

    breeze_connect formats it with `strftime('%c')` in this process's own time zone, so parsing
    it back with `%c` and `time.mktime` round-trips whatever that zone is. The mock sends epoch
    seconds."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else None
    text = str(raw).strip()
    if text.replace(".", "", 1).isdigit():
        return float(text)
    try:
        return time.mktime(datetime.datetime.strptime(text, "%c").timetuple())
    except ValueError:
        return None


def _today_start(now: float) -> float:
    d = datetime.datetime.fromtimestamp(now, IST).date()
    return datetime.datetime.combine(d, datetime.time(0, 0), IST).timestamp()


def _feed_alive() -> bool:
    from icici_breeze_backend.app.services.ws_tick_pipeline import last_ingest_age_seconds

    age = last_ingest_age_seconds()
    return age is not None and age <= FEED_ALIVE_SECONDS


def book_for(exchange: str, token: int, *, now: Optional[float] = None) -> Optional[Book]:
    """This session's book for a contract: depth when a depth message arrived today, else the
    top of book from the quote room, else None."""
    now = time.time() if now is None else now
    with _lock:
        entry = _entries.get((str(exchange).upper(), int(token)))
        if entry is None:
            return None
        snap = _Entry(**entry.__dict__)
    if not _feed_alive():
        return None
    start = _today_start(now)
    quote_today = snap.quote_at is not None and snap.quote_at >= start
    depth_today = snap.depth_at is not None and snap.depth_at >= start
    ltp = snap.ltp if quote_today else None
    last_trade = parse_last_trade(snap.last_trade_raw) if quote_today else None
    best_bid = snap.best_bid if quote_today else None
    best_ask = snap.best_ask if quote_today else None
    if depth_today:
        return Book(
            bids=snap.bids, asks=snap.asks, source=SOURCE_DEPTH, ltp=ltp,
            last_trade_at=last_trade, best_bid=best_bid, best_ask=best_ask,
        )
    if quote_today:
        bids = (Level(best_bid, snap.best_bid_qty),) if best_bid and snap.best_bid_qty > 0 else ()
        asks = (Level(best_ask, snap.best_ask_qty),) if best_ask and snap.best_ask_qty > 0 else ()
        return Book(
            bids=bids, asks=asks, source=SOURCE_TOP_OF_BOOK, ltp=ltp,
            last_trade_at=last_trade, best_bid=best_bid, best_ask=best_ask,
        )
    return None


def stats() -> dict[str, Any]:
    with _lock:
        with_depth = sum(1 for e in _entries.values() if e.depth_at is not None)
        return {
            "contracts": len(_entries),
            "contracts_with_depth": with_depth,
            "depth_messages": _depth_messages,
            "quote_messages": _quote_messages,
        }


def put_for_tests(exchange: str, token: int, **fields: Any) -> None:
    with _lock:
        entry = _entries.setdefault((exchange.upper(), int(token)), _Entry())
        for k, v in fields.items():
            setattr(entry, k, v)


def reset_state_for_tests() -> None:
    global _depth_messages, _quote_messages
    with _lock:
        _entries.clear()
        _depth_messages = 0
        _quote_messages = 0
