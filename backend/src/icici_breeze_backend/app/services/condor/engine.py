"""The Dynamic Iron Condor rule engine (docs/dynamic-iron-condor-plan.md section 3).

`decide()` is a pure function: campaign state + market snapshot + settings in, one decision
out. The backtest, the Portfolio card and the bot all call it, so what is tested is exactly
what trades. It places nothing, sizes nothing that needs the broker, and reads no clock but
`market.now`.

Priority, first match wins:

    P0  unpriceable / spot not live / feed down -> unavailable (never "nothing to do")
    P1  campaign P&L <= -max-loss                -> close all
    P2  DTE <= exit DTE                          -> exit or time roll
    P3  at the straddle cap, beyond break-even   -> exit or time roll   (EOD check only)
    P4  leg rule or block rule                   -> roll the untested side
    P5  tranche due                              -> enter a tranche
    P6  otherwise                                -> no action

Rolls move only the untested side: its shorts go, together, to the tested side's |delta|,
never past the tested strike, with a wing at the tested side's width. The tested side is
never moved by an adjustment (plan section 1).
"""
from __future__ import annotations

import datetime
from dataclasses import replace
from typing import Iterable, Optional

from icici_breeze_backend.app.domain.condor import CondorSettings
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.condor import ledger, orders as order_ops
from icici_breeze_backend.app.services.condor.model import (
    CampaignState,
    CheckKind,
    Decision,
    Leg,
    MarketSnapshot,
    Metrics,
    OrderLeg,
)
from icici_breeze_backend.app.services.condor.pricing import GreeksModel, Right, build_greeks_model
from icici_breeze_backend.app.services.condor.strikes import (
    strike_for_delta,
    tranche_due_dte,
    wing_at_width,
)

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

#: The rules' version. **Raise it in any release that changes what `decide` (or the strike,
#: order or ledger helpers it uses) would do for the same inputs** -- a bug fix included. Every
#: condor backtest records it, and it is part of the bot's settings fingerprint, so a backtest,
#: a paper cycle or an approval earned under older rules stops unlocking the bot (#66).
ENGINE_VERSION = 1
_OTHER: dict[Right, Right] = {"Call": "Put", "Put": "Call"}


def _ist_date(now: datetime.datetime) -> datetime.date:
    return (now if now.tzinfo else now.replace(tzinfo=_IST)).astimezone(_IST).date()


def _shorts(legs: Iterable[Leg], right: Right) -> list[Leg]:
    return [leg for leg in legs if leg.is_short and leg.right == right]


def _longs(legs: Iterable[Leg], right: Right) -> list[Leg]:
    return [leg for leg in legs if not leg.is_short and leg.right == right]


def _weighted_abs_delta(model: GreeksModel, legs: list[Leg]) -> Optional[float]:
    """Quantity-weighted |delta| of a set of legs; None if any cannot be priced; 0 for none."""
    units = sum(leg.quantity for leg in legs)
    if not units:
        return 0.0
    total = 0.0
    for leg in legs:
        d = model.delta(leg.right, leg.strike)
        if d is None:
            return None
        total += abs(d) * leg.quantity
    return total / units


def _nearest_money_short(right: Right, shorts: list[Leg]) -> Optional[float]:
    if not shorts:
        return None
    return min(s.strike for s in shorts) if right == "Call" else max(s.strike for s in shorts)


def _wing_width(right: Right, legs: list[Leg]) -> Optional[float]:
    """Points from the side's nearest-the-money short to the nearest long beyond it."""
    short = _nearest_money_short(right, _shorts(legs, right))
    if short is None:
        return None
    beyond = [
        leg.strike
        for leg in _longs(legs, right)
        if (leg.strike > short if right == "Call" else leg.strike < short)
    ]
    if not beyond:
        return None
    wing = min(beyond) if right == "Call" else max(beyond)
    return abs(wing - short)


def _price_orders(orders: list[OrderLeg], market: MarketSnapshot) -> Optional[list[OrderLeg]]:
    """Cost each order at the touch it would trade at: ask for buys, bid for sells."""
    priced = []
    for o in orders:
        quote = market.quote(o.strike, o.right)
        price = (quote.ask if o.action == "Buy" else quote.bid) if quote else None
        if price is None or not price > 0:
            return None
        priced.append(replace(o, price=float(price)))
    return priced


def _net_cash(orders: list[OrderLeg], charges: ChargesModel) -> float:
    cash = 0.0
    for o in orders:
        sign = 1.0 if o.action == "Sell" else -1.0
        cash += sign * float(o.price or 0) * o.quantity
        cash -= charges.leg_charges(float(o.price or 0), o.quantity, is_buy=o.action == "Buy")
    return cash


def _close_orders(legs: Iterable[Leg], market: MarketSnapshot) -> list[OrderLeg]:
    orders = order_ops.diff_orders(order_ops.net_position(legs), {})
    priced = []
    for o in order_ops.safe_sequence(orders, market.spot):
        quote = market.quote(o.strike, o.right)
        price = (quote.ask if o.action == "Buy" else quote.bid) if quote else None
        # A close is never held for want of a price (#58): it goes out unpriced and the
        # executor prices it at send time.
        priced.append(replace(o, price=float(price) if price and price > 0 else None))
    return priced


def _decision(action, reason, text, metrics, **kw) -> Decision:
    return Decision(action=action, reason=reason, text=text, metrics=metrics, **kw)


def decide(
    state: CampaignState,
    market: MarketSnapshot,
    settings: CondorSettings,
    check_kind: CheckKind,
    charges: ChargesModel,
) -> Decision:
    dte = (state.expiry - _ist_date(market.now)).days
    limit = settings.max_loss_limit_inr()
    base = Metrics(dte=dte, max_loss_limit_inr=limit)
    legs = list(state.legs)

    # ---- P0: nothing is decided on a price the engine does not have ----------------------
    if not market.feeds_ok:
        return _decision("unavailable", "feed_down", "A feed this campaign needs is down.", base)
    if not market.spot_live or market.spot is None:
        return _decision("unavailable", "spot_not_live", "NIFTY spot is not live.", base)
    model = build_greeks_model(market.chain, market.spot, state.expiry, market.now)
    if model is None:
        return _decision("unavailable", "no_greeks", "The chain cannot be priced.", base)

    close = ledger.close_value(legs, market, charges)
    if close.unpriced:
        names = ", ".join(f"{int(l.strike)} {l.right}" for l in close.unpriced)
        return _decision("unavailable", "leg_unpriced", f"No ask to buy back: {names}.", base)

    call_shorts, put_shorts = _shorts(legs, "Call"), _shorts(legs, "Put")
    call_d = _weighted_abs_delta(model, call_shorts)
    put_d = _weighted_abs_delta(model, put_shorts)
    if call_d is None or put_d is None:
        return _decision("unavailable", "delta_unpriced", "A short leg's delta cannot be computed.", base)

    pnl = ledger.campaign_pnl(state.ledger_cash_inr, close)
    lower_be, upper_be = (
        ledger.breakevens(legs, state.ledger_cash_inr, ledger.centre(legs, market.spot)) if legs else (None, None)
    )
    worst = ledger.worst_loss_at_expiry(legs, state.ledger_cash_inr) if legs else None

    short_units = max(sum(l.quantity for l in call_shorts), sum(l.quantity for l in put_shorts))
    net_per_lot: Optional[float] = None
    if short_units:
        net = 0.0
        for leg in legs:
            d = model.delta(leg.right, leg.strike)
            if d is None:
                net = None
                break
            net += leg.signed_qty * d
        net_per_lot = None if net is None else net / short_units

    tested: Optional[Right] = None
    if (call_shorts or put_shorts) and call_d != put_d:
        tested = "Call" if call_d > put_d else "Put"
    untested: Optional[Right] = _OTHER[tested] if tested else None
    untested_shorts = _shorts(legs, untested) if untested else []
    decay: Optional[float] = None
    if untested_shorts:
        sold = sum(l.avg_price * l.quantity for l in untested_shorts)
        now_cost = sum(market.quote(l.strike, l.right).ask * l.quantity for l in untested_shorts)
        decay = (1.0 - now_cost / sold) * 100.0 if sold > 0 else None
    cap = _nearest_money_short(tested, _shorts(legs, tested)) if tested else None
    at_cap = bool(cap is not None and untested_shorts and {l.strike for l in untested_shorts} == {cap})

    metrics = Metrics(
        dte=dte,
        call_short_abs_delta=call_d if call_shorts else None,
        put_short_abs_delta=put_d if put_shorts else None,
        net_delta_per_lot=net_per_lot,
        tested_side=tested,
        untested_decay_pct=decay,
        open_value_inr=close.value_inr,
        campaign_pnl_inr=pnl,
        max_loss_limit_inr=limit,
        lower_breakeven=lower_be,
        upper_breakeven=upper_be,
        at_cap=at_cap,
        worst_loss_at_wings_inr=worst,
    )

    if legs:
        # ---- P1: the campaign stop ----------------------------------------------------
        if pnl is not None and pnl <= -limit:
            return _decision(
                "close_all", "max_loss",
                f"Campaign P&L ₹{pnl:,.0f} is past the ₹{limit:,.0f} max-loss. Close everything.",
                metrics, orders=tuple(_close_orders(legs, market)),
            )
        # ---- P2: the cycle's clock ----------------------------------------------------
        if dte <= settings.exit_dte:
            return _decision(
                "exit_or_roll", "exit_dte",
                f"{dte} DTE reached the exit at {settings.exit_dte}. Close, or time-roll to the next cycle.",
                metrics, orders=tuple(_close_orders(legs, market)),
            )
        # ---- P3: at the cap and through a break-even, at the end of the day -----------
        if at_cap and check_kind in ("eod", "on_demand"):
            spot = market.spot
            # Beyond a break-even means expiry settling here would lose. That holds for any
            # shape, including one with no break-even at all (every price loses).
            if ledger.expiry_pnl(legs, state.ledger_cash_inr, spot) < 0:
                return _decision(
                    "exit_or_roll", "beyond_breakeven",
                    f"At the straddle cap and NIFTY {spot:,.0f} is beyond break-even. Close, or time-roll.",
                    metrics, orders=tuple(_close_orders(legs, market)),
                )
        # ---- P4: roll the untested side ----------------------------------------------
        roll = _roll(legs, market, model, settings, charges, tested, untested, call_d, put_d, decay, net_per_lot, metrics)
        if roll is not None:
            return roll

    # ---- P5: a tranche ------------------------------------------------------------------
    tranche = _tranche(state, market, model, settings, check_kind, dte, metrics)
    if tranche is not None:
        return tranche

    return _decision("no_action", "nothing_due", "Nothing to do at this check.", metrics)


def _roll(
    legs: list[Leg],
    market: MarketSnapshot,
    model: GreeksModel,
    settings: CondorSettings,
    charges: ChargesModel,
    tested: Optional[Right],
    untested: Optional[Right],
    call_d: float,
    put_d: float,
    decay: Optional[float],
    net_per_lot: Optional[float],
    metrics: Metrics,
) -> Optional[Decision]:
    if tested is None or untested is None:
        return None
    untested_d = put_d if untested == "Put" else call_d
    tested_d = call_d if tested == "Call" else put_d
    untested_shorts = _shorts(legs, untested)
    leg_rule = (
        not untested_shorts
        or untested_d < settings.leg_rule_delta_floor
        or (decay is not None and decay >= settings.leg_rule_decay_pct)
    )
    block_rule = net_per_lot is not None and abs(net_per_lot) > settings.net_delta_band_per_lot
    if not (leg_rule or block_rule):
        return None
    why = "leg_rule" if leg_rule else "block_rule"

    cap = _nearest_money_short(tested, _shorts(legs, tested))
    new_short = strike_for_delta(model, market.strikes, untested, tested_d, not_inside=cap)
    if new_short is None:
        return _decision("no_action", "roll_unpriced", "No untested strike could be priced for a roll.", metrics)
    if {l.strike for l in untested_shorts} == {new_short}:
        # Already where the roll would put it -- at the cap, or the market is back.
        return None

    qty = sum(l.quantity for l in untested_shorts) or sum(l.quantity for l in _shorts(legs, tested))
    width = _wing_width(tested, legs)
    if width is not None:
        new_wing = wing_at_width(market.strikes, untested, new_short, width)
    else:
        beyond = [s for s in market.strikes if (s > new_short if untested == "Call" else s < new_short)]
        new_wing = strike_for_delta(model, beyond, untested, settings.wing_delta, outward=True)
    if new_wing is None:
        return _decision("no_action", "roll_no_wing", "No listed strike for the rolled wing.", metrics)

    current = order_ops.net_position(l for l in legs if l.right == untested)
    target = {(new_short, untested): -qty, (new_wing, untested): qty}
    sequenced = order_ops.safe_sequence(order_ops.diff_orders(current, target), market.spot)
    priced = _price_orders(sequenced, market)
    if priced is None:
        # The unpriced orders go back with the decision: the backtest fetches exactly those
        # contracts, and the card can name the leg that has no price.
        return _decision(
            "no_action", "roll_unpriced", "A contract in the roll has no price on the side it trades.",
            metrics, orders=tuple(sequenced),
        )
    credit = _net_cash(priced, charges) / qty
    trigger = (
        f"untested {untested} at {untested_d:.2f}Δ" if why == "leg_rule" and untested_shorts else
        f"untested {untested} side has no short" if why == "leg_rule" else
        f"net delta {net_per_lot:+.2f} per lot outside ±{settings.net_delta_band_per_lot:.2f}"
    )
    dte = metrics.dte
    if settings.no_roll_within_days_of_exit and dte is not None and dte < settings.exit_dte + settings.no_roll_within_days_of_exit:
        return _decision(
            "no_action", "roll_near_exit",
            f"Roll due ({trigger}) but {dte} DTE is within {settings.no_roll_within_days_of_exit} day(s) of the "
            f"{settings.exit_dte}-DTE exit, so it is not rolled.",
            metrics, orders=tuple(priced), roll_credit_points=credit,
        )
    if credit < settings.min_roll_credit_points:
        return _decision(
            "no_action", "roll_credit_below_min",
            f"Roll due ({trigger}) but it adds {credit:.1f} points, under the {settings.min_roll_credit_points:.0f} minimum.",
            metrics, orders=tuple(priced), roll_credit_points=credit,
        )
    return _decision(
        "roll_untested", why,
        f"Roll the {untested} side to {int(new_short)} ({tested_d:.2f}Δ, matching the tested side), wing {int(new_wing)}: {trigger}.",
        metrics, orders=tuple(priced), roll_credit_points=credit,
    )


def _tranche(
    state: CampaignState,
    market: MarketSnapshot,
    model: GreeksModel,
    settings: CondorSettings,
    check_kind: CheckKind,
    dte: int,
    metrics: Metrics,
) -> Optional[Decision]:
    if check_kind != "on_demand" and check_kind != settings.entry_check:
        return None
    if state.tranches_entered >= settings.tranches or dte < settings.tranche_cutoff_dte:
        return None
    if dte > tranche_due_dte(settings, state.tranches_entered):
        return None
    strikes = entry_strikes(model, market.strikes, settings)
    if strikes is None:
        return _decision("no_action", "tranche_unpriced", "Tranche due but its strikes cannot be priced.", metrics)
    n = state.tranches_entered + 1
    return _decision(
        "enter_tranche", "tranche_due",
        f"Tranche {n} of {settings.tranches} due at {dte} DTE: "
        f"{int(strikes['long_put'])}/{int(strikes['short_put'])} PE · {int(strikes['short_call'])}/{int(strikes['long_call'])} CE.",
        metrics, tranche_strikes=strikes,
    )


def entry_strikes(model: GreeksModel, strikes: list[float], settings: CondorSettings) -> Optional[dict[str, float]]:
    """Shorts at the short delta, wings at the wing delta snapped outward, beyond the shorts."""
    short_call = strike_for_delta(model, strikes, "Call", settings.short_delta)
    short_put = strike_for_delta(model, strikes, "Put", settings.short_delta)
    if short_call is None or short_put is None:
        return None
    long_call = strike_for_delta(model, [s for s in strikes if s > short_call], "Call", settings.wing_delta, outward=True)
    long_put = strike_for_delta(model, [s for s in strikes if s < short_put], "Put", settings.wing_delta, outward=True)
    if long_call is None or long_put is None:
        return None
    return {"short_call": short_call, "long_call": long_call, "short_put": short_put, "long_put": long_put}


def entry_orders(strikes: dict[str, float], quantity: int, market: MarketSnapshot) -> Optional[list[OrderLeg]]:
    """A sized tranche as sequenced, priced orders (wings first). None if a leg has no price."""
    target = {
        (strikes["long_call"], "Call"): quantity,
        (strikes["long_put"], "Put"): quantity,
        (strikes["short_call"], "Call"): -quantity,
        (strikes["short_put"], "Put"): -quantity,
    }
    orders = order_ops.safe_sequence(order_ops.diff_orders({}, target), market.spot)
    return _price_orders(orders, market)
