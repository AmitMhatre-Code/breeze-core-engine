"""Synthetic NIFTY chains for the condor engine tests.

Every quote is a Black-Scholes price on a known volatility smile, with a 2% two-sided book,
so implied volatility, delta and the engine's strike choices are reproducible and every
quote is "trusted" (relative spread <= 10%).
"""
from __future__ import annotations

import datetime
import math

from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.condor.model import CampaignState, Leg, MarketSnapshot
from icici_breeze_backend.app.services.condor.pricing import ChainRow, Quote, bs_price, years_to_expiry_close

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
EXPIRY = datetime.date(2026, 9, 29)
LOT = 65
STEP = 50
CHARGES = ChargesModel()


def at(days_before_expiry: int, hhmm: str = "10:30") -> datetime.datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    d = EXPIRY - datetime.timedelta(days=days_before_expiry)
    return datetime.datetime(d.year, d.month, d.day, h, m, tzinfo=IST)


def smile_sigma(strike: float, spot: float, base: float = 0.13) -> float:
    """A put skew: richer volatility below spot, flatter above."""
    x = math.log(strike / spot)
    return max(0.06, base - 0.6 * x + 1.5 * x * x)


def chain(spot: float, now: datetime.datetime, *, base: float = 0.13, lo: int = 21000, hi: int = 27500,
          drop: frozenset = frozenset(), expiry: datetime.date = EXPIRY) -> tuple[ChainRow, ...]:
    t = years_to_expiry_close(expiry, now)
    rows = []
    for k in range(lo, hi + 1, STEP):
        cells = {}
        for right in ("Call", "Put"):
            if (k, right) in drop:
                cells[right] = None
                continue
            p = bs_price(right, spot, k, t, smile_sigma(k, spot, base))
            p = max(p, 0.05)
            cells[right] = Quote(bid=p * 0.99, ask=p * 1.01, ltp=p, bid_qty=1000, ask_qty=1000)
        rows.append(ChainRow(strike=float(k), call=cells["Call"], put=cells["Put"]))
    return tuple(rows)


def market(spot: float, now: datetime.datetime, **kw) -> MarketSnapshot:
    live = kw.pop("spot_live", True)
    feeds = kw.pop("feeds_ok", True)
    return MarketSnapshot(now=now, spot=spot, spot_live=live, feeds_ok=feeds, chain=chain(spot, now, **kw))


def mid(m: MarketSnapshot, strike: float, right: str) -> float:
    q = m.quote(strike, right)
    return (q.bid + q.ask) / 2


def apply(state: CampaignState, orders) -> CampaignState:
    """The state after `orders` fill at their decision prices (legs re-netted, cash moved)."""
    from collections import defaultdict

    pos: dict = defaultdict(int)
    price: dict = {}
    for leg in state.legs:
        pos[(leg.strike, leg.right)] += leg.signed_qty
        price[(leg.strike, leg.right)] = leg.avg_price
    cash = state.ledger_cash_inr
    for o in orders:
        k = (o.strike, o.right)
        pos[k] += o.quantity if o.action == "Buy" else -o.quantity
        if o.opening:
            price[k] = o.price
        cash += (-1 if o.action == "Buy" else 1) * o.price * o.quantity
        cash -= CHARGES.leg_charges(o.price, o.quantity, is_buy=o.action == "Buy")
    legs = tuple(
        Leg(k[0], k[1], "Buy" if q > 0 else "Sell", abs(q), price[k]) for k, q in sorted(pos.items()) if q
    )
    return CampaignState(state.expiry, state.lot_size, legs, cash, state.tranches_entered)


def condor(m: MarketSnapshot, strikes: dict, lots: int = 1) -> CampaignState:
    """A condor entered at mid in `m`, ledger cash net of charges."""
    qty = lots * LOT
    legs = (
        Leg(strikes["long_put"], "Put", "Buy", qty, mid(m, strikes["long_put"], "Put")),
        Leg(strikes["short_put"], "Put", "Sell", qty, mid(m, strikes["short_put"], "Put")),
        Leg(strikes["short_call"], "Call", "Sell", qty, mid(m, strikes["short_call"], "Call")),
        Leg(strikes["long_call"], "Call", "Buy", qty, mid(m, strikes["long_call"], "Call")),
    )
    cash = 0.0
    for leg in legs:
        cash += (-1 if leg.side == "Buy" else 1) * leg.avg_price * leg.quantity
        cash -= CHARGES.leg_charges(leg.avg_price, leg.quantity, is_buy=leg.side == "Buy")
    return CampaignState(expiry=EXPIRY, lot_size=LOT, legs=legs, ledger_cash_inr=cash, tranches_entered=1)
