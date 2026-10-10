"""Adjust tickets: templates, normalising, and the preview (docs/dynamic-iron-condor-plan.md 6a).

A ticket is a list of orders a user can edit freely -- action, strike, right, quantity, and an
expiry for a time roll's second half. Whatever it started as, it is turned back into a target
position and diffed against what the ledger holds (`orders.diff_orders`), so opening and
closing are always classified from the position, never from what the user called them, and
two rows on one contract net out.

The preview prices the ticket on the chain the card reads, and lists what it would change:
net cash after charges, the position after, its net delta, worst loss and break-evens, the
margin before and after, liquidity per order (#62), and the engine's rules as **warnings**
the user may override. The one **block** is a position left with a short no long caps.
"""
from __future__ import annotations

import dataclasses
import datetime
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.repositories import condor as repo
from icici_breeze_backend.app.services.bots.charges import ChargesModel, load_charges
from icici_breeze_backend.app.services.condor import campaigns, ledger, live
from icici_breeze_backend.app.services.condor import orders as order_ops
from icici_breeze_backend.app.services.condor.engine import (
    _nearest_money_short,
    _shorts,
    _weighted_abs_delta,
    _wing_width,
    entry_orders,
    entry_strikes,
    premium_refusal,
)
from icici_breeze_backend.app.services.condor.model import Leg, MarketSnapshot, OrderLeg
from icici_breeze_backend.app.services.condor.pricing import GreeksModel, build_greeks_model
from icici_breeze_backend.app.services.condor.strikes import cycle_expiry, strike_for_delta, wing_at_width

_logger = logging.getLogger(__name__)

KINDS = (
    "suggested", "roll_selected", "iron_fly", "add_tranche", "close_side",
    "close_selected", "close_all", "time_roll", "blank",
)
ROLL_KINDS = {"roll_selected", "iron_fly"}
CLOSE_KINDS = {"close_side", "close_selected", "close_all"}
_FMT = "%d-%b-%Y"


class Refused(ValueError):
    pass


# --------------------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------------------


@dataclass
class Ctx:
    campaign: repo.Campaign
    expiry: datetime.date
    expiry_display: str
    legs: tuple[Leg, ...]
    cash: float
    lot_size: int
    snap: live.LiveSnapshot
    market: MarketSnapshot
    model: Optional[GreeksModel]


def context(proc: Any, campaign: repo.Campaign) -> Ctx:
    cycle = campaign.cycle
    if cycle is None:
        raise Refused("The campaign has no open cycle.")
    fills = repo.list_fills(campaign.id)
    legs = campaigns.legs_from(campaigns.ledger_position(fills, cycle.expiry))
    snap = live.snapshot(proc, campaign.user_id, cycle.expiry, [(l.strike, l.right) for l in legs])
    market = snap.market if snap.live else live.with_ltp_stand_ins(snap.market)
    expiry = campaigns.parse_expiry(cycle.expiry)
    lot = int(proc.fetch_lot_size("NIFTY", cycle.expiry, exchange_code=cfg.NFO) or 0) or 65
    model = build_greeks_model(market.chain, market.spot, expiry, market.now) if market.spot else None
    return Ctx(campaign, expiry, cycle.expiry, legs, campaigns.ledger_cash(fills), lot, snap, market, model)


def _order_json(o: OrderLeg) -> dict[str, Any]:
    return {
        "action": o.action, "strike": o.strike, "right": o.right, "quantity": o.quantity,
        "opening": o.opening, "price": o.price,
        "expiry": o.expiry.strftime(_FMT) if o.expiry else None,
    }


# --------------------------------------------------------------------------------------
# Normalising
# --------------------------------------------------------------------------------------


def normalize(ctx: Ctx, raw: Iterable[dict[str, Any]]) -> list[OrderLeg]:
    """Ticket rows -> canonical orders, opening/closing decided against the ledger."""
    by_expiry: dict[Optional[datetime.date], dict[tuple[float, str], int]] = {}
    for r in raw:
        try:
            qty = int(r["quantity"])
            strike = float(r["strike"])
        except (KeyError, TypeError, ValueError) as e:
            raise Refused("Every row needs a strike and a quantity.") from e
        if qty <= 0 or strike <= 0:
            raise Refused("Quantities and strikes must be positive.")
        right = "Call" if str(r.get("right", "")).lower().startswith("c") else "Put"
        action = "Buy" if str(r.get("action", "")).lower() == "buy" else "Sell"
        exp = campaigns.parse_expiry(r["expiry"]) if r.get("expiry") else None
        if exp == ctx.expiry:
            exp = None
        delta = qty if action == "Buy" else -qty
        book = by_expiry.setdefault(exp, {})
        book[(strike, right)] = book.get((strike, right), 0) + delta
    out: list[OrderLeg] = []
    current = order_ops.net_position(ctx.legs)
    for exp, changes in by_expiry.items():
        base = current if exp is None else {}
        target = dict(base)
        for k, d in changes.items():
            target[k] = target.get(k, 0) + d
        target = {k: v for k, v in target.items() if v}
        for o in order_ops.diff_orders(base, target):
            out.append(dataclasses.replace(o, expiry=exp))
    # Whole lots only for what a ticket opens. Closing takes whatever is held: a partial fill
    # leaves an odd quantity, and leaving a position must never be refused for its size.
    odd = [o for o in out if o.opening and o.quantity % ctx.lot_size]
    if odd:
        raise Refused(f"New positions must be whole lots of {ctx.lot_size}.")
    return out


def target_position(ctx: Ctx, orders: list[OrderLeg]) -> dict[Optional[datetime.date], dict[tuple[float, str], int]]:
    pos: dict[Optional[datetime.date], dict[tuple[float, str], int]] = {None: order_ops.net_position(ctx.legs)}
    for o in orders:
        book = pos.setdefault(o.expiry, {})
        k = (float(o.strike), o.right)
        book[k] = book.get(k, 0) + (o.quantity if o.action == "Buy" else -o.quantity)
    return {e: {k: v for k, v in b.items() if v} for e, b in pos.items()}


# --------------------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------------------


def refuse_owned_expiry(campaign: repo.Campaign, expiry_display: str) -> None:
    """One live campaign per NIFTY expiry (#67, the user's rule): a ticket may not open legs on
    an expiry another live campaign manages, because the broker nets the two into one position
    no ledger can split. Paper campaigns hold nothing at the broker and are never refused."""
    if campaign.mode != "live":
        return
    owner = repo.active_owner(campaign.user_id, campaign.underlying, expiry_display)
    if owner and owner != campaign.id:
        raise Refused(
            f"NIFTY {expiry_display} is already managed by another campaign, and an expiry can have only one. "
            "Close this cycle instead (Close all), or close the other campaign first."
        )


def _close(legs: Iterable[Leg]) -> list[OrderLeg]:
    return order_ops.diff_orders(order_ops.net_position(legs), {})


def _tested(ctx: Ctx) -> tuple[Optional[str], Optional[float]]:
    """(tested right, its quantity-weighted |delta|) from the current shorts."""
    if ctx.model is None:
        return None, None
    call_d = _weighted_abs_delta(ctx.model, _shorts(ctx.legs, "Call"))
    put_d = _weighted_abs_delta(ctx.model, _shorts(ctx.legs, "Put"))
    if call_d is None or put_d is None or call_d == put_d:
        return None, None
    return ("Call", call_d) if call_d > put_d else ("Put", put_d)


def template(proc: Any, campaign: repo.Campaign, kind: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    if kind not in KINDS:
        raise Refused(f"Unknown ticket template '{kind}'.")
    params = params or {}
    ctx = context(proc, campaign)
    lots = max(1, int(params.get("lots") or 1))
    note = ""
    orders: list[OrderLeg] = []

    if kind == "suggested":
        ev = campaigns.evaluate(proc, campaign, "on_demand")
        d = ev["decision"]
        if d["action"] == "enter_tranche" and d.get("tranche_strikes"):
            orders = entry_orders(d["tranche_strikes"], lots * ctx.lot_size, ctx.market) or []
            note = f"Tranche at {lots} lot(s); change the quantities to size it."
        else:
            orders = [OrderLeg(**{k: v for k, v in o.items() if k in ("strike", "right", "action", "quantity", "opening", "price")}) for o in d["orders"]]
            if d["action"] == "exit_or_roll":
                note = "This closes the cycle. Choose the Time roll template to close and open the next cycle in one ticket."
        if not orders:
            note = d["text"]

    elif kind in ("close_all", "close_side", "close_selected"):
        legs = list(ctx.legs)
        if kind == "close_side":
            right = "Call" if str(params.get("right", "")).lower().startswith("c") else "Put"
            legs = [l for l in legs if l.right == right]
        elif kind == "close_selected":
            chosen = {(float(x["strike"]), "Call" if str(x["right"]).lower().startswith("c") else "Put") for x in params.get("legs") or []}
            legs = [l for l in legs if (l.strike, l.right) in chosen]
        orders = _close(legs)

    elif kind == "add_tranche":
        strikes = entry_strikes(ctx.model, ctx.market.strikes, campaign.settings) if ctx.model else None
        if strikes is None:
            raise Refused("The chain cannot price a tranche's strikes right now.")
        qty = lots * ctx.lot_size
        orders = entry_orders(strikes, qty, ctx.market) or [
            # Unpriced is still a ticket: the preview says which leg has no price.
            OrderLeg(strikes["long_call"], "Call", "Buy", qty, True),
            OrderLeg(strikes["long_put"], "Put", "Buy", qty, True),
            OrderLeg(strikes["short_call"], "Call", "Sell", qty, True),
            OrderLeg(strikes["short_put"], "Put", "Sell", qty, True),
        ]

    elif kind == "roll_selected":
        orders = _roll_selected(ctx, params)

    elif kind == "iron_fly":
        orders = _iron_fly(ctx)

    elif kind == "time_roll":
        orders = _close(ctx.legs)
        later = [e for e in campaigns.listed_expiries(proc) if e > ctx.expiry]
        nxt = cycle_expiry(later, datetime.datetime.now(live.IST).date(), campaign.settings)
        if nxt is None:
            raise Refused("No later NIFTY expiry qualifies for the next cycle.")
        refuse_owned_expiry(campaign, nxt.strftime(_FMT))
        snap2 = live.snapshot(proc, campaign.user_id, nxt.strftime(_FMT))
        m2 = snap2.market if snap2.live else live.with_ltp_stand_ins(snap2.market)
        model2 = build_greeks_model(m2.chain, m2.spot, nxt, m2.now) if m2.spot else None
        # The premium gate (#78) governs opening the next cycle, never closing this one: refused,
        # the ticket only closes, which ends the campaign as Close does, and the next cycle's
        # tranches go in when premium is rich.
        refused = premium_refusal(model2, m2, campaign.settings) if model2 else None
        if refused is not None:
            return {"kind": kind, "orders": [_order_json(o) for o in orders],
                    "note": f"Closes {ctx.expiry_display}. The {nxt:%d-%b-%Y} cycle is not opened now: "
                            f"{refused[1]} Its tranches go in when premium is rich."}
        strikes = entry_strikes(model2, m2.strikes, campaign.settings) if model2 else None
        if strikes is None:
            raise Refused(f"The {nxt:%d-%b-%Y} chain cannot price the next cycle's strikes right now.")
        entry = entry_orders(strikes, lots * ctx.lot_size, m2) or []
        orders += [dataclasses.replace(o, expiry=nxt) for o in entry]
        note = f"Closes {ctx.expiry_display}, then opens {nxt:%d-%b-%Y} at {lots} lot(s)."

    return {"kind": kind, "orders": [_order_json(o) for o in orders], "note": note}


def _roll_selected(ctx: Ctx, params: dict[str, Any]) -> list[OrderLeg]:
    chosen = [
        (float(x["strike"]), "Call" if str(x["right"]).lower().startswith("c") else "Put")
        for x in params.get("legs") or []
    ]
    if not chosen:
        raise Refused("Tick the legs to roll first.")
    held = {(l.strike, l.right): l for l in ctx.legs}
    target = order_ops.net_position(ctx.legs)
    follow = bool(params.get("wing_follows", True))
    for key in chosen:
        leg = held.get(key)
        if leg is None:
            continue
        if params.get("to_strike"):
            new = float(params["to_strike"])
        elif params.get("to_delta") and ctx.model is not None:
            new = strike_for_delta(ctx.model, ctx.market.strikes, leg.right, float(params["to_delta"]))
        else:
            raise Refused("Choose a strike or a delta to roll to.")
        if new is None or new == leg.strike:
            continue
        units = leg.signed_qty
        target[key] = target.get(key, 0) - units
        target[(new, leg.right)] = target.get((new, leg.right), 0) + units
        if follow and leg.is_short:
            short = leg.strike
            beyond = [l for l in ctx.legs if not l.is_short and l.right == leg.right
                      and (l.strike > short if leg.right == "Call" else l.strike < short)
                      and (l.strike, l.right) not in chosen]
            if beyond:
                wing = min(beyond, key=lambda l: abs(l.strike - short))
                moved = wing_at_width(ctx.market.strikes, leg.right, new, abs(wing.strike - short))
                if moved is not None and moved != wing.strike:
                    target[(wing.strike, wing.right)] = target.get((wing.strike, wing.right), 0) - wing.quantity
                    target[(moved, wing.right)] = target.get((moved, wing.right), 0) + wing.quantity
    target = {k: v for k, v in target.items() if v}
    return order_ops.diff_orders(order_ops.net_position(ctx.legs), target)


def _iron_fly(ctx: Ctx) -> list[OrderLeg]:
    tested, _ = _tested(ctx)
    if tested is None:
        raise Refused("Neither side is tested right now, so there is no side to move to the straddle.")
    untested = "Put" if tested == "Call" else "Call"
    cap = _nearest_money_short(tested, _shorts(ctx.legs, tested))
    qty = sum(l.quantity for l in _shorts(ctx.legs, untested)) or sum(l.quantity for l in _shorts(ctx.legs, tested))
    width = _wing_width(tested, list(ctx.legs))
    if width is None:
        raise Refused("The tested side has no wing to take the width from.")
    wing = wing_at_width(ctx.market.strikes, untested, cap, width)
    if wing is None:
        raise Refused("No listed strike for the moved wing.")
    current = order_ops.net_position(l for l in ctx.legs if l.right == untested)
    return order_ops.diff_orders(current, {(cap, untested): -qty, (wing, untested): qty})


# --------------------------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------------------------


def _price(o: OrderLeg, market: MarketSnapshot) -> Optional[float]:
    q = market.quote(o.strike, o.right)
    if q is None:
        return None
    p = q.ask if o.action == "Buy" else q.bid
    return float(p) if p and p > 0 else None


def _legs_after(held: Iterable[Leg], pos: dict[tuple[float, str], int], priced: list[OrderLeg]) -> tuple[Leg, ...]:
    """The position after, each leg at the price it is held at: the ledger's average for
    what stays, the order's price for what opens."""
    avg = {(l.strike, l.right): l.avg_price for l in held}
    for o in priced:
        if o.opening and o.price:
            avg[(float(o.strike), o.right)] = o.price
    return tuple(
        Leg(k[0], k[1], "Buy" if u > 0 else "Sell", abs(u), avg.get(k, 0.0)) for k, u in sorted(pos.items())
    )


def _margin(proc: Any, user_id: str, expiry_display: str, legs: Iterable[Leg]) -> Optional[float]:
    from icici_breeze_backend.app.services.bots.scalping.margin import margin_for_mixed_legs

    rows = [("call" if l.right == "Call" else "put", l.strike, l.quantity, cfg.BUY if l.side == "Buy" else cfg.SELL) for l in legs]
    if not rows:
        return 0.0
    try:
        return margin_for_mixed_legs(proc, user_id, exchange_code=cfg.NFO, stock_code="NIFTY",
                                     expiry_display=expiry_display, legs=rows)
    except Exception:  # noqa: BLE001 -- a preview without margin is still a preview
        _logger.warning("condor: preview margin failed", exc_info=True)
        return None


def _liquidity(ctx: Ctx, o: OrderLeg) -> Optional[str]:
    from icici_breeze_backend.app.services.liquidity.check import check_contract

    try:
        v = check_contract(cfg.NFO, "NIFTY", (o.expiry.strftime(_FMT) if o.expiry else ctx.expiry_display),
                           o.strike, o.right, o.action, o.quantity, lot_size=ctx.lot_size)
    except Exception:  # noqa: BLE001
        return None
    if v.ok is False:
        return f"{int(o.strike)} {'CE' if o.right == 'Call' else 'PE'} {o.action.lower()} {o.quantity}: " + " ".join(v.messages)
    return None


def preview(
    proc: Any,
    campaign: repo.Campaign,
    raw: Iterable[dict[str, Any]],
    kind: str = "blank",
    *,
    charges: Optional[ChargesModel] = None,
    with_margin: bool = True,
) -> dict[str, Any]:
    ctx = context(proc, campaign)
    charges = charges or load_charges()
    canonical = normalize(ctx, raw)
    if not canonical:
        raise Refused("The ticket has no orders, or they cancel out.")

    markets: dict[Optional[datetime.date], MarketSnapshot] = {None: ctx.market}
    for exp in {o.expiry for o in canonical if o.expiry}:
        s = live.snapshot(proc, campaign.user_id, exp.strftime(_FMT))
        markets[exp] = s.market if s.live else live.with_ltp_stand_ins(s.market)

    sequenced: list[OrderLeg] = []
    for exp in [None] + sorted(e for e in markets if e is not None):
        group = [o for o in canonical if o.expiry == exp]
        for o in order_ops.safe_sequence(group, ctx.market.spot):
            sequenced.append(dataclasses.replace(o, price=_price(o, markets[exp])))

    unpriced = [o for o in sequenced if o.price is None]
    cash = 0.0
    for o in sequenced:
        p = float(o.price or 0.0)
        cash += (p if o.action == "Sell" else -p) * o.quantity
        cash -= charges.leg_charges(p, o.quantity, is_buy=o.action == "Buy")
    moved = max((o.quantity for o in sequenced), default=0)

    targets = target_position(ctx, sequenced)
    blocked: list[str] = []
    for exp, pos in targets.items():
        for right in order_ops.unhedged_rights(pos):
            label = (exp.strftime(_FMT) if exp else ctx.expiry_display)
            blocked.append(f"{label}: a short {'call' if right == 'Call' else 'put'} would have no long to cap it.")

    # The position the campaign would be left holding: the next cycle's when this closes the
    # current one and opens another, else the current one.
    live_exp = None if targets.get(None) or len(targets) == 1 else next(e for e in targets if e is not None and targets[e])
    after_pos = targets.get(live_exp, {})
    after_legs = _legs_after(
        ctx.legs if live_exp is None else (), after_pos, [o for o in sequenced if o.expiry == live_exp]
    )
    cash_after = ctx.cash + cash
    after_market = markets[live_exp]
    after_expiry = live_exp or ctx.expiry
    model_after = build_greeks_model(after_market.chain, after_market.spot, after_expiry, after_market.now) if after_market.spot else None
    net_after = None
    short_units = max(sum(l.quantity for l in after_legs if l.is_short and l.right == r) for r in ("Call", "Put")) if after_legs else 0
    if model_after is not None and short_units:
        deltas = [model_after.delta(l.right, l.strike) for l in after_legs]
        if all(d is not None for d in deltas):
            net_after = sum(l.signed_qty * d for l, d in zip(after_legs, deltas)) / short_units
    lower, upper = (ledger.breakevens(after_legs, cash_after, ledger.centre(after_legs, after_market.spot or 0))
                    if after_legs and after_market.spot else (None, None))

    warnings: list[str] = []
    s = campaign.settings
    if not ctx.snap.live:
        warnings.append(
            "Prices here are stand-ins (" + ", ".join(ctx.snap.stand_ins) + "). Execution re-prices every order on "
            "live quotes and will not open a position without one."
        )
    if unpriced:
        warnings.append("No price for " + ", ".join(f"{int(o.strike)} {'CE' if o.right == 'Call' else 'PE'}" for o in unpriced) + "; the totals leave it out.")
    tested, _ = _tested(ctx)
    # The rules never touch the tested side -- its short or its wing -- except to close the
    # whole cycle, so a closing or time-roll ticket is not warned about it. With re-centre on,
    # moving it is something the rules do too (#80), so it is not warned about either.
    if tested and not s.recentre_enabled and kind not in CLOSE_KINDS and kind != "time_roll":
        if any(o.expiry is None and o.right == tested for o in sequenced):
            warnings.append(f"Moves the tested side ({'calls' if tested == 'Call' else 'puts'}). The rules never do.")
    short_calls = [k[0] for k, u in after_pos.items() if u < 0 and k[1] == "Call"]
    short_puts = [k[0] for k, u in after_pos.items() if u < 0 and k[1] == "Put"]
    if short_calls and short_puts and max(short_puts) > min(short_calls):
        warnings.append("Inverts the strangle: a short put above a short call locks in a loss at expiry.")
    if kind in ROLL_KINDS and moved and cash / moved < s.min_roll_credit_points:
        warnings.append(f"The roll adds {cash / moved:.1f} points a unit, under the {s.min_roll_credit_points:g} minimum.")
    widths = [_wing_width(r, list(after_legs)) for r in ("Call", "Put")]
    if all(w is not None for w in widths) and widths[0] != widths[1]:
        warnings.append(f"Wings at unequal widths: calls {widths[0]:g}, puts {widths[1]:g} points.")
    for o in sequenced:
        msg = _liquidity(ctx, o)
        if msg:
            warnings.append(msg)

    margin_before = margin_after = None
    if with_margin:
        margin_before = _margin(proc, campaign.user_id, ctx.expiry_display, ctx.legs)
        margin_after = _margin(proc, campaign.user_id, (live_exp.strftime(_FMT) if live_exp else ctx.expiry_display), after_legs)
        if margin_after is not None and margin_after > s.margin_ceiling_inr:
            warnings.append(f"Margin after (₹{margin_after:,.0f}) is above the campaign's ₹{s.margin_ceiling_inr:,.0f} ceiling.")

    return {
        "kind": kind,
        "orders": [_order_json(o) for o in sequenced],
        "cash_inr": round(cash, 2),
        "credit_points": round(cash / moved, 2) if moved else None,
        "after": {
            "expiry": live_exp.strftime(_FMT) if live_exp else ctx.expiry_display,
            "legs": [dataclasses.asdict(l) for l in after_legs],
            "ledger_cash_inr": round(cash_after, 2),
            "net_delta_per_lot": net_after,
            "worst_loss_inr": ledger.worst_loss_at_expiry(after_legs, cash_after) if after_legs else None,
            "lower_breakeven": lower,
            "upper_breakeven": upper,
        },
        "margin_before_inr": margin_before,
        "margin_after_inr": margin_after,
        "indicative": not ctx.snap.live,
        "warnings": warnings,
        "blocked": blocked,
    }
