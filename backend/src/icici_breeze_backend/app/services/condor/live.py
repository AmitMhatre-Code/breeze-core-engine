"""The engine's market snapshot, from the app's own chain feed.

The chain comes through `quote_source_router.fetch_chain_payload_routed` -- the WebSocket cache
in market hours, bhavcopy or REST outside them -- and spot from `live_index_spot`, which is set
only by a live index tick (#50). Every cell carries where it came from.

A scheduled check decides only on live prices: a stand-in spot, or a stand-in quote on any
held leg, makes the snapshot "not live" and the engine answers `unavailable` (#52). An
on-demand evaluation may run on stand-ins so the card has numbers outside market hours, and
is then marked **indicative**: it is never acted on.
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from icici_breeze_backend.app.core.strike import parse_strike
from icici_breeze_backend.app.services.condor.model import MarketSnapshot
from icici_breeze_backend.app.services.condor.pricing import ChainRow, Quote

_logger = logging.getLogger(__name__)

LIVE_SOURCE = "websocket"
UNDERLYING = "NIFTY"
EXCHANGE = "NFO"
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class LiveSnapshot:
    market: MarketSnapshot
    # True when every input the decision rests on is live: spot from a tick, and every held
    # leg's quote from the WebSocket feed.
    live: bool
    stand_ins: tuple[str, ...]
    spot_source: Optional[str]
    chain_ready: bool


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _quote(cell: Optional[dict[str, Any]]) -> Optional[Quote]:
    if not cell:
        return None
    return Quote(
        bid=_f(cell.get("best_bid_price")),
        ask=_f(cell.get("best_offer_price")),
        ltp=_f(cell.get("ltp")),
        bid_qty=_f(cell.get("total_buy_qty")),
        ask_qty=_f(cell.get("total_sell_qty")),
    )


def snapshot(
    proc: Any,
    user_id: str,
    expiry_display: str,
    held: Iterable[tuple[float, str]] = (),
    *,
    now: Optional[datetime.datetime] = None,
) -> LiveSnapshot:
    from icici_breeze_backend.app.services.bots.scalping.momentum_bot import live_index_spot
    from icici_breeze_backend.app.services.quote_source_router import fetch_chain_payload_routed

    now = now or datetime.datetime.now(IST)
    payload = None
    try:
        payload = fetch_chain_payload_routed(
            proc, user_id, UNDERLYING, EXCHANGE, expiry_display, holder_id=f"condor:{user_id}"
        )
    except Exception:  # noqa: BLE001 -- an unreadable chain is "not ready", never a crash
        _logger.warning("condor: chain fetch failed for %s", expiry_display, exc_info=True)
    payload = payload or {}
    chain_source = payload.get("quote_source")
    rows: list[ChainRow] = []
    sources: dict[tuple[float, str], Optional[str]] = {}
    for raw in payload.get("chain_rows") or []:
        strike = parse_strike(raw.get("strike_price"))
        if not strike:
            continue
        call, put = raw.get("call"), raw.get("put")
        rows.append(ChainRow(float(strike), call=_quote(call), put=_quote(put)))
        for right, cell in (("Call", call), ("Put", put)):
            if cell:
                sources[(float(strike), right)] = cell.get("quote_source") or chain_source

    tick_spot = live_index_spot()
    spot = tick_spot if tick_spot is not None else _f(payload.get("spot_price"))
    stand_ins: list[str] = []
    if tick_spot is None:
        stand_ins.append(f"NIFTY spot ({payload.get('spot_source') or 'none'})")
    for strike, right in held:
        if sources.get((float(strike), right)) != LIVE_SOURCE:
            stand_ins.append(f"{int(strike)} {'CE' if right == 'Call' else 'PE'}")
    forecast, forecast_reason = premium_forecast(user_id, expiry_display, now)
    market = MarketSnapshot(
        now=now, spot=spot, spot_live=tick_spot is not None, feeds_ok=True, chain=tuple(rows),
        forecast_variance=forecast, forecast_reason=forecast_reason,
    )
    return LiveSnapshot(
        market=market,
        live=not stand_ins,
        stand_ins=tuple(stand_ins),
        spot_source="live" if tick_spot is not None else payload.get("spot_source"),
        chain_ready=bool(rows),
    )


def premium_forecast(
    user_id: str, expiry_display: str, now: datetime.datetime
) -> tuple[Optional[float], Optional[str]]:
    """The premium gate's forecast to this expiry (#78): the cash index's own recent sessions,
    the same reading the other gated bots use (#75). (variance, None) or (None, why)."""
    from icici_breeze_backend.app.services.premium_gate import live as premium_live
    from icici_breeze_backend.app.services.premium_gate import reading as premium

    try:
        expiry = datetime.datetime.strptime(expiry_display, "%d-%b-%Y").date()
        sessions = premium_live.sessions_for(UNDERLYING, now.date(), user_id=user_id)
        fc = premium.forecast(sessions, now.replace(tzinfo=None),
                              premium_live.sessions_after(now.date(), expiry))
    except Exception:  # noqa: BLE001 -- no forecast is no reading, never a crash
        _logger.warning("condor: premium forecast failed for %s", expiry_display, exc_info=True)
        return None, premium.REASON_NO_HISTORY
    if fc is None:
        return None, premium.REASON_NO_HISTORY
    return fc.variance, None


def with_ltp_stand_ins(market: MarketSnapshot) -> MarketSnapshot:
    """Every quote without a two-sided book priced at its LTP, both sides.

    Only for an **indicative** evaluation: outside market hours the chain has closes and no
    book, and a card full of dashes says nothing. Never used by a scheduled check or a trade.
    """
    import dataclasses

    def fix(q: Optional[Quote]) -> Optional[Quote]:
        if q is None or (q.bid and q.ask and q.bid > 0 and q.ask > 0):
            return q
        if q.ltp is None or not q.ltp > 0:
            return q
        return Quote(bid=q.ltp, ask=q.ltp, ltp=q.ltp, bid_qty=1, ask_qty=1)

    rows = tuple(ChainRow(r.strike, call=fix(r.call), put=fix(r.put)) for r in market.chain)
    return dataclasses.replace(market, chain=rows)

