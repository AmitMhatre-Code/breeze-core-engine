"""What a quantity would pay against the visible book, judged against the LTP. Pure.

The benchmark is the LTP (decision 2 of docs/liquidity-checks-plan.md): a trader reads the LTP
and expects to trade near it. A sell fills against the bids best first and a buy against the
asks; the average over the levels it consumes is the estimate. It fails when it is further from
the LTP, adversely, than **both** `max_deviation_pct` of the LTP and `min_deviation_ticks` ticks,
so a single tick on a Rs 0.80 option does not warn. A fill better than the LTP never fails.

Separately, the LTP itself may not be a fair benchmark: a last trade from long ago, or an LTP
outside the current bid and ask (the 2026-06-29 NIFTY capture has `last` 61.20 under a 61.40
bid). That is its own reason, `stale_ltp`, and it does not change the size a book can absorb, so
`max_quantity` ignores it.

Quantities are contract units throughout; `lot_size` only rounds `max_quantity` to whole lots.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.liquidity.settings import LiquiditySettings

SOURCE_DEPTH = "depth"
SOURCE_TOP_OF_BOOK = "top_of_book"
SOURCE_UNKNOWN = "unknown"

REASON_THIN = "thin"
REASON_BEYOND_BOOK = "beyond_book"
REASON_NO_BOOK = "no_book"
REASON_STALE_LTP = "stale_ltp"
REASON_MARKET_CLOSED = "market_closed"
REASON_NO_DATA = "no_data"

# The lead sentence of every size warning, in the words the ticket's tooltip uses.
SIZE_LEAD = "Quantity is large enough that the fill could be very different from LTP"

_EPS = 1e-9


@dataclass(frozen=True)
class Level:
    price: float
    qty: int


@dataclass(frozen=True)
class Book:
    """One contract's book as last seen. `bids`/`asks` are best first."""

    bids: tuple[Level, ...]
    asks: tuple[Level, ...]
    source: str
    ltp: Optional[float] = None
    # Epoch seconds of the last trade, when the feed said.
    last_trade_at: Optional[float] = None
    # Best bid/ask from the quote room, which can be fresher than a depth message.
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None

    def side_levels(self, side: str) -> tuple[Level, ...]:
        return self.bids if _is_sell(side) else self.asks


@dataclass
class Verdict:
    """The check's answer for one contract, side and quantity.

    `ok` is None when there is nothing to judge (market closed, no data) -- never read that as a
    pass or a fail; the UI shows nothing and a caller sizing a trade leaves its size alone.
    """

    side: str
    quantity: int
    ok: Optional[bool]
    source: str
    reasons: list[str] = field(default_factory=list)
    ltp: Optional[float] = None
    est_avg_price: Optional[float] = None
    deviation: Optional[float] = None
    deviation_pct: Optional[float] = None
    deviation_ticks: Optional[float] = None
    visible_qty: int = 0
    max_quantity: Optional[int] = None
    ltp_age_seconds: Optional[float] = None
    messages: list[str] = field(default_factory=list)

    @property
    def size_ok(self) -> Optional[bool]:
        """The size half of the check alone: thin / beyond the book / no other side."""
        if self.ok is None:
            return None
        return not ({REASON_THIN, REASON_BEYOND_BOOK, REASON_NO_BOOK} & set(self.reasons))

    def as_dict(self) -> dict:
        return {
            "side": self.side,
            "quantity": self.quantity,
            "ok": self.ok,
            "size_ok": self.size_ok,
            "source": self.source,
            "reasons": list(self.reasons),
            "ltp": self.ltp,
            "est_avg_price": self.est_avg_price,
            "deviation": self.deviation,
            "deviation_pct": self.deviation_pct,
            "deviation_ticks": self.deviation_ticks,
            "visible_qty": self.visible_qty,
            "max_quantity": self.max_quantity,
            "ltp_age_seconds": self.ltp_age_seconds,
            "message": " ".join(self.messages),
        }


def _is_sell(side: str) -> bool:
    return str(side or "").strip().lower() == "sell"


def unknown(side: str, quantity: int, reason: str) -> Verdict:
    return Verdict(side=side, quantity=int(quantity), ok=None, source=SOURCE_UNKNOWN, reasons=[reason])


def walk(levels: tuple[Level, ...], quantity: int) -> tuple[Optional[float], int]:
    """(average price over what the visible levels fill, quantity they fill)."""
    remaining = int(quantity)
    filled = 0
    notional = 0.0
    for level in levels:
        if remaining <= 0:
            break
        if level.qty <= 0 or level.price <= 0:
            continue
        take = min(remaining, level.qty)
        notional += take * level.price
        filled += take
        remaining -= take
    if filled <= 0:
        return None, 0
    return notional / filled, filled


def _adverse(side: str, benchmark: float, avg: float) -> float:
    return (benchmark - avg) if _is_sell(side) else (avg - benchmark)


def _too_far(deviation: float, benchmark: float, settings: LiquiditySettings, tick: float) -> bool:
    pct_limit = benchmark * settings.max_deviation_pct / 100.0
    tick_limit = settings.min_deviation_ticks * tick
    return deviation > pct_limit + _EPS and deviation > tick_limit + _EPS


def _size_passes(
    levels: tuple[Level, ...], side: str, quantity: int, benchmark: float,
    settings: LiquiditySettings, tick: float,
) -> bool:
    avg, filled = walk(levels, quantity)
    if avg is None or filled < quantity:
        return False
    return not _too_far(_adverse(side, benchmark, avg), benchmark, settings, tick)


def max_passing_quantity(
    levels: tuple[Level, ...], side: str, upto: int, lot_size: int, benchmark: float,
    settings: LiquiditySettings, tick: float,
) -> int:
    """Largest whole-lot quantity up to `upto` that the book absorbs within the threshold.

    The average only worsens as quantity grows, so the pass/fail boundary is a single point and
    a binary search over lots finds it.
    """
    lot = max(1, int(lot_size))
    hi = int(upto) // lot
    lo = 0
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _size_passes(levels, side, mid * lot, benchmark, settings, tick):
            lo = mid
        else:
            hi = mid - 1
    return lo * lot


def _fmt_price(v: float) -> str:
    return f"₹{v:,.2f}"


def _fmt_qty(v: int) -> str:
    # Indian grouping matters less than legibility here; the UI shows the same figure.
    return f"{int(v):,}"


def _fmt_time(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(epoch, IST).strftime("%H:%M")


def evaluate(
    book: Book,
    side: str,
    quantity: int,
    *,
    lot_size: int,
    settings: LiquiditySettings,
    tick: float,
    now: float,
) -> Verdict:
    """Judge `quantity` on `side` against `book`. See the module docstring."""
    qty = int(quantity)
    selling = _is_sell(side)
    verb = "Selling" if selling else "Buying"
    levels = book.side_levels(side)
    verdict = Verdict(side=side, quantity=qty, ok=True, source=book.source, ltp=book.ltp)
    suffix = " (estimate: top of book only)" if book.source == SOURCE_TOP_OF_BOOK else ""

    visible = sum(l.qty for l in levels if l.qty > 0 and l.price > 0)
    verdict.visible_qty = visible
    touch = next((l.price for l in levels if l.qty > 0 and l.price > 0), None)
    ltp = book.ltp if book.ltp is not None and book.ltp > 0 else None

    # ---- the LTP as a benchmark --------------------------------------------------------
    bid = book.best_bid if book.best_bid else (book.bids[0].price if book.bids else None)
    ask = book.best_ask if book.best_ask else (book.asks[0].price if book.asks else None)
    if book.last_trade_at is not None:
        verdict.ltp_age_seconds = round(max(0.0, now - book.last_trade_at), 1)
    if ltp is None:
        verdict.reasons.append(REASON_STALE_LTP)
        verdict.messages.append(
            "There is no trade yet today, so there is no LTP to compare the fill with."
        )
    else:
        outside = (
            bid is not None and ask is not None and bid > 0 and ask > 0
            and (ltp < bid - _EPS or ltp > ask + _EPS)
        )
        old = (
            verdict.ltp_age_seconds is not None
            and verdict.ltp_age_seconds > settings.ltp_stale_seconds
        )
        if outside or old:
            verdict.reasons.append(REASON_STALE_LTP)
            when = (
                f", last traded at {_fmt_time(book.last_trade_at)}"
                if book.last_trade_at is not None else ""
            )
            if outside:
                verdict.messages.append(
                    f"The LTP ({_fmt_price(ltp)}{when}) is outside the current bid/ask "
                    f"({_fmt_price(bid)}–{_fmt_price(ask)}), so the fill will be near the book, "
                    f"not the LTP."
                )
            else:
                verdict.messages.append(
                    f"The LTP ({_fmt_price(ltp)}{when}) is "
                    f"{int(verdict.ltp_age_seconds // 60)} minutes old, so the fill may be far "
                    f"from it."
                )

    # ---- the size --------------------------------------------------------------------
    benchmark = ltp if ltp is not None else touch
    if touch is None or benchmark is None:
        verdict.reasons.append(REASON_NO_BOOK)
        verdict.max_quantity = 0
        verdict.messages.insert(
            0,
            f"{SIZE_LEAD}: there is no {'bid to sell into' if selling else 'offer to buy from'}"
            f"{suffix}.",
        )
        verdict.ok = False
        return verdict

    avg, filled = walk(levels, qty)
    if avg is not None:
        verdict.est_avg_price = round(avg, 2)
        dev = _adverse(side, benchmark, avg)
        verdict.deviation = round(dev, 2)
        verdict.deviation_pct = round(dev / benchmark * 100.0, 2) if benchmark > 0 else None
        verdict.deviation_ticks = round(dev / tick, 1) if tick > 0 else None
    verdict.max_quantity = max_passing_quantity(
        levels, side, qty, lot_size, benchmark, settings, tick
    )

    if filled < qty:
        verdict.reasons.append(REASON_BEYOND_BOOK)
        verdict.messages.insert(
            0,
            f"{SIZE_LEAD}: the visible book holds only {_fmt_qty(visible)} on the "
            f"{'bid' if selling else 'offer'} side, so the rest of the {_fmt_qty(qty)} has no "
            f"visible price{suffix}.",
        )
    elif verdict.deviation is not None and _too_far(verdict.deviation, benchmark, settings, tick):
        verdict.reasons.append(REASON_THIN)
        direction = "below" if selling else "above"
        against = f"the LTP of {_fmt_price(benchmark)}" if ltp is not None else "the best price"
        verdict.messages.insert(
            0,
            f"{SIZE_LEAD}: {verb.lower()} {_fmt_qty(qty)} would average about "
            f"{_fmt_price(avg)}, {abs(verdict.deviation_pct or 0):.1f}% {direction} {against}"
            f"{suffix}.",
        )

    verdict.ok = not verdict.reasons
    return verdict
