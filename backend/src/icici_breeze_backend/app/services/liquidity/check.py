"""The liquidity check as everything else calls it: a contract, a side, a quantity.

Order tickets ask through `POST /api/liquidity/check` (`check_legs`) and only warn. The Strategy
Builder caps a proposal's lots and excludes strikes that cannot take one lot. Bots shrink to what
the book absorbs, or skip (`fit_lots`). Exits never call any of this (decision 9).

"Unknown" (market closed, no book seen this session) is never a pass or a fail: tickets show
nothing, and sizing callers leave the size alone. A bot already refuses to trade on a quote that
is not live (#52), and its quote and this book come off the same socket, so a live quote with no
book here means the store missed it rather than that the market is thin.
"""
from __future__ import annotations

import datetime
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.liquidity import book as book_store
from icici_breeze_backend.app.services.liquidity.estimate import (
    REASON_MARKET_CLOSED,
    REASON_NO_DATA,
    REASON_STALE_LTP,
    Verdict,
    evaluate,
    unknown,
)
from icici_breeze_backend.app.services.liquidity.settings import (
    LiquiditySettings,
    load_liquidity_settings,
)

_logger = logging.getLogger(__name__)

BUY = "Buy"
SELL = "Sell"


@dataclass(frozen=True)
class LegQuery:
    """One leg to judge. `side` None asks for both sides (Place Order before Buy/Sell is
    chosen). `quantity` is in contract units."""

    ref: str
    strike: float
    right: str
    side: Optional[str]
    quantity: int


def normalize_side(raw: Any) -> Optional[str]:
    t = str(raw or "").strip().lower()
    if t == "buy":
        return BUY
    if t == "sell":
        return SELL
    return None


def _option_type(right: Any) -> str:
    return "CE" if str(right or "").strip().lower().startswith("c") else "PE"


def tick_size() -> float:
    return float(cfg.AGGRESSIVE_LIMIT_TICK_SIZE)


def _market_open() -> bool:
    from icici_breeze_backend.app.services.market_calendar import is_market_open

    try:
        return bool(is_market_open())
    except Exception:  # noqa: BLE001
        return False


def resolve_token(exchange: str, stock: str, expiry: str, strike: float, right: str) -> Optional[int]:
    from icici_breeze_backend.app.services.reference_data.ws_token_index import (
        lookup_token_for_contract,
    )

    try:
        return lookup_token_for_contract(exchange, stock, expiry, float(strike), _option_type(right))
    except Exception:  # noqa: BLE001
        _logger.debug("liquidity: token lookup failed", exc_info=True)
        return None


def check_contract(
    exchange: str,
    stock: str,
    expiry: str,
    strike: float,
    right: str,
    side: str,
    quantity: int,
    *,
    lot_size: int,
    settings: Optional[LiquiditySettings] = None,
    now: Optional[float] = None,
    market_open: Optional[bool] = None,
) -> Verdict:
    side = normalize_side(side) or BUY
    is_open = _market_open() if market_open is None else market_open
    if not is_open:
        return unknown(side, quantity, REASON_MARKET_CLOSED)
    token = resolve_token(exchange, stock, expiry, strike, right)
    if token is None:
        return unknown(side, quantity, REASON_NO_DATA)
    now = time.time() if now is None else now
    book = book_store.book_for(exchange, token, now=now)
    if book is None:
        return unknown(side, quantity, REASON_NO_DATA)
    return evaluate(
        book, side, int(quantity),
        lot_size=max(1, int(lot_size or 1)),
        settings=settings or load_liquidity_settings(),
        tick=tick_size(),
        now=now,
    )


def check_legs(
    exchange: str,
    stock: str,
    expiry: str,
    legs: Iterable[LegQuery],
    *,
    lot_size: int,
) -> dict[str, dict[str, dict[str, Any]]]:
    """{ref: {side: verdict dict}} for a ticket's legs.

    Legs on the same contract and side are summed first and each gets the combined verdict:
    two basket rows selling the same strike take liquidity from the same bids.
    """
    settings = load_liquidity_settings()
    is_open = _market_open()
    now = time.time()
    legs = list(legs)
    totals: dict[tuple[float, str, str], int] = {}
    for leg in legs:
        sides = [leg.side] if leg.side else [BUY, SELL]
        for side in sides:
            key = (float(leg.strike), _option_type(leg.right), side)
            totals[key] = totals.get(key, 0) + max(0, int(leg.quantity))
    verdicts: dict[tuple[float, str, str], Verdict] = {}
    for (strike, opt, side), qty in totals.items():
        verdicts[(strike, opt, side)] = check_contract(
            exchange, stock, expiry, strike, opt, side, qty,
            lot_size=lot_size, settings=settings, now=now, market_open=is_open,
        )
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for leg in legs:
        sides = [leg.side] if leg.side else [BUY, SELL]
        out[leg.ref] = {
            side: verdicts[(float(leg.strike), _option_type(leg.right), side)].as_dict()
            for side in sides
        }
    return out


def pin_depth(proc: Any, user_id: str, exchange: str, stock: str, expiry: str, strikes_rights: Iterable[tuple[float, str]]) -> None:
    """Subscribe the depth room of each contract as a lookup, so a ticket's legs have a book
    even when chain-wide depth is capped (decision 1). Released after 15 idle minutes like any
    lookup (B-41). Best effort."""
    from icici_breeze_backend.app.services.liquidity.subscriptions import subscribe_contract_depth

    for strike, right in strikes_rights:
        try:
            subscribe_contract_depth(proc, user_id, exchange, stock, expiry, strike, right)
        except Exception:  # noqa: BLE001
            _logger.debug("liquidity: depth pin failed", exc_info=True)


# --------------------------------------------------------------------------------------
# Sizing callers: the Strategy Builder and the bots
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SizedLeg:
    """One leg of a structure, sized together with its siblings. `lots_per_unit` is the leg's
    lots per lot of the structure (1 for every structure today)."""

    exchange: str
    stock: str
    expiry: str
    strike: float
    right: str
    side: str
    lots_per_unit: int = 1


@dataclass
class Fit:
    """How many lots of a structure the books allow.

    `lots` is the requested size when nothing could be judged, the largest passing size when the
    books are thin, and 0 when a leg's LTP cannot be trusted or a leg has no other side.
    """

    requested: int
    lots: int
    verdicts: list[Verdict] = field(default_factory=list)
    judged: bool = False

    @property
    def shrunk(self) -> bool:
        return self.judged and 0 < self.lots < self.requested

    @property
    def refused(self) -> bool:
        return self.judged and self.lots <= 0 < self.requested

    def reason_text(self) -> str:
        msgs = [v.as_dict()["message"] for v in self.verdicts if v.ok is False]
        return " ".join(m for m in msgs if m)


def fit_lots(
    legs: Iterable[SizedLeg],
    lots: int,
    lot_size: int,
    *,
    min_lots: int = 1,
    treat_stale_as_fail: bool = True,
) -> Fit:
    """The largest lot count, up to `lots`, that every leg's book absorbs within the threshold.

    Below `min_lots` it is 0. With `treat_stale_as_fail` (bots, decision 4) a leg whose LTP
    cannot be trusted makes it 0 whatever the size; the Strategy Builder passes False and lets
    the ticket warn instead.
    """
    legs = list(legs)
    requested = int(lots)
    fit = Fit(requested=requested, lots=requested)
    if requested <= 0 or not legs:
        return fit
    settings = load_liquidity_settings()
    is_open = _market_open()
    now = time.time()
    allowed = requested
    for leg in legs:
        per = max(1, int(leg.lots_per_unit))
        v = check_contract(
            leg.exchange, leg.stock, leg.expiry, leg.strike, leg.right, leg.side,
            requested * per * int(lot_size),
            lot_size=lot_size, settings=settings, now=now, market_open=is_open,
        )
        fit.verdicts.append(v)
        if v.ok is None:
            continue
        fit.judged = True
        if treat_stale_as_fail and REASON_STALE_LTP in v.reasons:
            allowed = 0
            continue
        leg_lots = int(v.max_quantity or 0) // (max(1, int(lot_size)) * per)
        allowed = min(allowed, leg_lots)
    if fit.judged:
        fit.lots = allowed if allowed >= max(1, int(min_lots)) else 0
    return fit


# --------------------------------------------------------------------------------------
# Bot notes
# --------------------------------------------------------------------------------------

_alert_lock = threading.Lock()
# (user, bot, what) -> IST date it was last sent. A scalper re-plans every few seconds; one
# Telegram per contract per day is the note, not a stream.
_alerted: dict[tuple[str, str, str], datetime.date] = {}


def note_bot_fit(user_id: str, bot_type: str, what: str, fit: Fit) -> Optional[str]:
    """Log a bot's shrink or skip, send one Telegram for it per day, and return the text for
    the bot's Activity row (None when nothing changed)."""
    if not (fit.shrunk or fit.refused):
        return None
    if fit.refused:
        text = f"Skipped {what}: the order book is too thin. {fit.reason_text()}".strip()
    else:
        text = (
            f"Reduced {what} from {fit.requested} to {fit.lots} lot(s): the order book is too "
            f"thin for more. {fit.reason_text()}"
        ).strip()
    _logger.info("liquidity [%s]: %s", bot_type, text)
    today = datetime.datetime.now(IST).date()
    key = (str(user_id), str(bot_type), f"{what}|{'skip' if fit.refused else 'shrink'}")
    with _alert_lock:
        if _alerted.get(key) == today:
            return text
        _alerted[key] = today
    try:
        from icici_breeze_backend.app.services import telegram_alerts

        telegram_alerts.notify_bot_liquidity(user_id, bot_type, text)
    except Exception:  # noqa: BLE001
        _logger.debug("liquidity: telegram note failed", exc_info=True)
    return text


def reset_state_for_tests() -> None:
    with _alert_lock:
        _alerted.clear()
