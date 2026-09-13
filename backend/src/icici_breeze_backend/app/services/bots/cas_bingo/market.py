"""Index-parameterised market reads for CAS Bingo (docs/bots-cas-bingo-plan.md section 11).

Bots 3/4's helpers in `scalping/momentum_bot` are NIFTY-only by construction -- the stock code
and exchange are module constants. CAS Bingo trades SENSEX on BFO as well, so the same reads
are restated here with the index as a parameter rather than widening those helpers under two
bots that have no use for it.

Quotes are cache-first through `quote_source_router`, which serves the WS feed during market
hours -- the exit loop reads every leg each pass, and REST at that cadence would spend the
account's minute budget on one position.
"""
from __future__ import annotations

import datetime
import logging
import time
from typing import Any, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import parse_strike
from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.services.bots.scalping.momentum_bot import Quote, _row_quote

_logger = logging.getLogger(__name__)

INDEX_EXCHANGE = {"NIFTY": cfg.NFO, "BSESEN": cfg.BFO}
INDEX_LABEL = {"NIFTY": "NIFTY", "BSESEN": "SENSEX"}
# The index signal and the index spot feed key on lower-case labels.
SIGNAL_LABEL = {"NIFTY": "nifty", "BSESEN": "sensex"}

# A cached index level older than this is not "the spot at the time of deploying".
_SPOT_MAX_AGE_SECONDS = 15.0


def expiring_today(proc: Any) -> dict[str, str]:
    """Index code -> expiry display for contracts expiring today, from the scrip master.

    Delegates to Bot 2's reader so the two expiry-day bots can never disagree about what day
    it is -- SEBI has moved expiry days before, which is why neither uses a weekday rule.
    """
    from icici_breeze_backend.app.services.bots import scheduler

    return scheduler._expiring_today(proc)


def chain_rows(
    proc: Any, user_id: str, index_code: str, expiry_display: str, right: str
) -> list[dict[str, Any]]:
    from icici_breeze_backend.app.services.quote_source_router import (
        fetch_chain_side_icici_response,
    )

    chain = fetch_chain_side_icici_response(
        proc, user_id, index_code, INDEX_EXCHANGE[index_code], expiry_display, right
    )
    if (chain or {}).get("Status") != 200 or not chain.get("Success"):
        return []
    return [r for r in chain["Success"] if isinstance(r, dict)]


def spot_from(rows: list[dict[str, Any]]) -> Optional[float]:
    for row in rows:
        try:
            spot = float(row.get("spot_price") or 0)
        except (TypeError, ValueError):
            continue
        if spot > 0:
            return spot
    return None


def index_spot(index_code: str, *, now: Optional[float] = None) -> Optional[float]:
    """The live index level from the index spot feed, or None when it is not a fresh tick.

    Preferred over a chain row's `spot_price`: WS chain rows carry no spot at all (only
    bhavcopy/REST cells do), and after 15:15 this is the exchange's indicative auction value,
    which is exactly what the expiry settles on.
    """
    from icici_breeze_backend.app.db.redis_client import cache_get_json
    from icici_breeze_backend.app.services.reference_data.keys import index_spot_key

    payload = cache_get_json(index_spot_key(SIGNAL_LABEL[index_code]))
    if not isinstance(payload, dict):
        return None
    try:
        ltp = float(payload.get("ltp"))
        updated_at = float(payload.get("updated_at"))
    except (TypeError, ValueError):
        return None
    ts = time.time() if now is None else now
    if ltp <= 0 or ts - updated_at > _SPOT_MAX_AGE_SECONDS:
        return None
    return ltp


def last_index_level(index_code: str) -> Optional[float]:
    """The last index level cached, however old. For marking an expired position after the
    close, when no fresh tick is coming and the last one is the auction's own close."""
    from icici_breeze_backend.app.db.redis_client import cache_get_json
    from icici_breeze_backend.app.services.reference_data.keys import index_spot_key

    payload = cache_get_json(index_spot_key(SIGNAL_LABEL[index_code]))
    try:
        ltp = float((payload or {}).get("ltp"))
    except (TypeError, ValueError):
        return None
    return ltp if ltp > 0 else None


def live_quote(
    proc: Any, user_id: str, index_code: str, expiry_display: str, strike_price: float, right: str
) -> Quote:
    from icici_breeze_backend.app.services.quote_source_router import (
        fetch_quote_icici_response,
    )

    response = fetch_quote_icici_response(
        proc, user_id, index_code, INDEX_EXCHANGE[index_code], expiry_display, right, strike_price
    )
    rows = (response or {}).get("Success") or []
    if (response or {}).get("Status") != 200 or not rows:
        return Quote(None, None, None)
    return _row_quote(rows[0])


def lot_size(proc: Any, index_code: str, expiry_display: str) -> int:
    try:
        return int(
            proc.fetch_lot_size(index_code, expiry_display, exchange_code=INDEX_EXCHANGE[index_code])
            or 0
        )
    except Exception:  # noqa: BLE001
        _logger.warning("cas bingo: lot size lookup failed for %s", index_code, exc_info=True)
        return 0


def pick_strike(
    rows: list[dict[str, Any]], target: float, *, outward_up: bool
) -> Optional[dict[str, Any]]:
    """The nearest listed strike at or beyond `target`, away from the reference price.

    Away, never toward: a distance the user set is then always at least what they asked for,
    the same rule Bot 2's `_pick_strike` and Bot 4's wings use.
    """
    best, best_distance = None, float("inf")
    for row in rows:
        strike = parse_strike(row.get("strike_price"))
        if strike is None:
            continue
        s = float(strike)
        if outward_up and s < target:
            continue
        if not outward_up and s > target:
            continue
        if abs(s - target) < best_distance:
            best, best_distance = row, abs(s - target)
    return best


def row_for_strike(rows: list[dict[str, Any]], strike: float) -> Optional[dict[str, Any]]:
    for row in rows:
        parsed = parse_strike(row.get("strike_price"))
        if parsed is not None and float(parsed) == float(strike):
            return row
    return None


def expiry_date(expiry_display: str) -> Optional[datetime.date]:
    try:
        return datetime.datetime.strptime(expiry_display, "%d-%b-%Y").date()
    except (TypeError, ValueError):
        return None


def is_today(expiry_display: str) -> bool:
    return expiry_date(expiry_display) == now_ist().date()
