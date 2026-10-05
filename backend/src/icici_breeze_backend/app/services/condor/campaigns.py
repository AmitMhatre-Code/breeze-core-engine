"""Campaigns: the ledger, its agreement with the broker, and live evaluation (plan section 2).

**The ledger is the campaign's legs.** Its net position per contract is what the campaign
holds; its cash is the plan's total net credit. The broker is the truth about what the account
holds (#58), so every evaluation compares the two for the cycle's expiry:

* broker == ledger: nothing to say.
* the broker holds something the ledger does not (a trade placed outside the campaign): it is
  flagged, and the user either **assigns** it -- booked at a price they confirm -- or **leaves
  it out**, after which it stays visible as outside the ledger and the engine ignores it.
* the ledger holds something the broker no longer does (closed outside the campaign): it can
  only be assigned, because a campaign cannot decide about legs that no longer exist.

Until every difference is settled one way or the other, the engine is not asked: the answer
is `unavailable` / `ledger_mismatch`, never a decision about a position the ledger has wrong.
"""
from __future__ import annotations

import dataclasses
import datetime
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.domain.condor import CondorSettings
from icici_breeze_backend.app.repositories import condor as repo
from icici_breeze_backend.app.services.bots.charges import ChargesModel, load_charges
from icici_breeze_backend.app.services.condor import live
from icici_breeze_backend.app.services.condor.engine import decide
from icici_breeze_backend.app.services.condor.model import CampaignState, CheckKind, Decision, Leg, Metrics
from icici_breeze_backend.app.services.condor.strikes import cycle_expiry

_logger = logging.getLogger(__name__)

UNDERLYING = "NIFTY"
EXCHANGE = "NFO"
_FMT = "%d-%b-%Y"


def parse_expiry(text: str) -> Optional[datetime.date]:
    try:
        return datetime.datetime.strptime(str(text).strip(), _FMT).date()
    except (TypeError, ValueError):
        return None


def _right(raw: Any) -> str:
    return "Call" if str(raw or "").strip().lower() in ("call", "ce", "c") else "Put"


# --------------------------------------------------------------------------------------
# The ledger
# --------------------------------------------------------------------------------------


def ledger_cash(fills: list[repo.Fill]) -> float:
    return sum(f.cash for f in fills)


def ledger_position(fills: list[repo.Fill], expiry: str) -> dict[tuple[float, str], tuple[int, float]]:
    """(strike, right) -> (signed units, average price of what is held) for one expiry."""
    units: dict[tuple[float, str], int] = defaultdict(int)
    avg: dict[tuple[float, str], float] = {}
    want = parse_expiry(expiry)
    for f in fills:
        if parse_expiry(f.expiry) != want:
            continue
        key = (float(f.strike), f.right)
        signed = f.quantity if f.side == "Buy" else -f.quantity
        before = units[key]
        after = before + signed
        if before == 0 or (before > 0) == (signed > 0):
            # Adding to (or opening) a position: the held average moves.
            held = abs(before)
            avg[key] = (held * avg.get(key, 0.0) + f.quantity * f.price) / (held + f.quantity)
        elif after != 0 and (after > 0) != (before > 0):
            # Crossed through zero: what is held now was opened at this fill.
            avg[key] = f.price
        units[key] = after
    return {k: (u, avg.get(k, 0.0)) for k, u in units.items() if u}


def legs_from(position: dict[tuple[float, str], tuple[int, float]]) -> tuple[Leg, ...]:
    return tuple(
        Leg(strike, right, "Buy" if units > 0 else "Sell", abs(units), price)
        for (strike, right), (units, price) in sorted(position.items())
    )


# --------------------------------------------------------------------------------------
# The broker
# --------------------------------------------------------------------------------------


def broker_position(proc: Any, user_id: str, expiry: str) -> Optional[dict[tuple[float, str], tuple[int, float]]]:
    """What the account holds on NIFTY at `expiry`, or None when positions cannot be read."""
    from icici_breeze_backend.app.services.portfolio_margin_netting import positions_for_underlying

    positions = positions_for_underlying(proc, user_id, UNDERLYING, EXCHANGE)
    if not positions.available:
        return None
    want = parse_expiry(expiry)
    out: dict[tuple[float, str], tuple[int, float]] = {}
    for row in positions.rows:
        if parse_expiry(str(row.get("expiry_date") or "")) != want:
            continue
        try:
            qty = abs(int(float(row.get("quantity") or 0)))
            strike = float(row.get("strike_price") or 0)
            price = float(row.get("average_price") or 0)
        except (TypeError, ValueError):
            continue
        if not qty or not strike:
            continue
        signed = qty if str(row.get("action") or "").strip().lower() == "buy" else -qty
        key = (strike, _right(row.get("right")))
        units, _ = out.get(key, (0, 0.0))
        out[key] = (units + signed, price)
    return {k: v for k, v in out.items() if v[0]}


@dataclass(frozen=True)
class Difference:
    strike: float
    right: str
    ledger_units: int
    broker_units: int
    left_out: bool
    broker_avg_price: Optional[float]

    @property
    def units(self) -> int:
        """What the broker holds beyond the ledger (signed)."""
        return self.broker_units - self.ledger_units

    @property
    def can_leave_out(self) -> bool:
        """Only exposure *added* outside the campaign may be left out: the ledger's own legs
        must all still be there."""
        if self.ledger_units == 0:
            return True
        same_side = (self.broker_units > 0) == (self.ledger_units > 0)
        return same_side and abs(self.broker_units) > abs(self.ledger_units)

    def as_dict(self) -> dict[str, Any]:
        return {**dataclasses.asdict(self), "units": self.units, "can_leave_out": self.can_leave_out}


def reconcile(
    ledger: dict[tuple[float, str], tuple[int, float]],
    broker: dict[tuple[float, str], tuple[int, float]],
    left_out: dict[tuple[float, str], int],
) -> list[Difference]:
    out: list[Difference] = []
    for key in sorted(set(ledger) | set(broker)):
        l_units = ledger.get(key, (0, 0.0))[0]
        b_units, b_price = broker.get(key, (0, None))
        if l_units == b_units:
            continue
        diff = b_units - l_units
        out.append(Difference(
            strike=key[0], right=key[1], ledger_units=l_units, broker_units=b_units,
            left_out=left_out.get(key) == diff, broker_avg_price=b_price,
        ))
    return out


# --------------------------------------------------------------------------------------
# Creating
# --------------------------------------------------------------------------------------


def listed_expiries(proc: Any) -> list[datetime.date]:
    from icici_breeze_backend.app.services.reference_data.scrip_master_sql import _expiry_api_to_display

    out: set[datetime.date] = set()
    for entry in proc.fetch_stock_codes(cfg.NFO) or []:
        if str(entry.get("stock_code") or "").strip().upper() != UNDERLYING:
            continue
        for raw in entry.get("expiry_dates") or []:
            d = parse_expiry(_expiry_api_to_display(str(raw)))
            if d:
                out.add(d)
    return sorted(out)


def _sg_armed(user_id: str, expiry: str) -> bool:
    from icici_breeze_backend.app.repositories.squareoff_rules import get_active_rule_for_group

    return get_active_rule_for_group(user_id, UNDERLYING, expiry) is not None


class Refused(ValueError):
    pass


def create(
    proc: Any,
    user_id: str,
    settings: CondorSettings,
    *,
    expiry: Optional[str] = None,
    adopt: bool = False,
    today: Optional[datetime.date] = None,
    charges: Optional[ChargesModel] = None,
) -> repo.Campaign:
    """A new campaign. `adopt` books the group's current legs as its opening fills, at the
    broker's average prices; without it the campaign starts empty and the engine's tranche
    schedule enters it."""
    today = today or datetime.datetime.now(live.IST).date()
    if expiry is None:
        if adopt:
            raise Refused("Adopting needs the expiry of the group to adopt.")
        chosen = cycle_expiry(listed_expiries(proc), today, settings)
        if chosen is None:
            raise Refused("No listed NIFTY expiry is at or beyond the tranche cut-off.")
        expiry = chosen.strftime(_FMT)
    elif parse_expiry(expiry) is None:
        raise Refused(f"'{expiry}' is not a DD-Mon-YYYY expiry.")
    if _sg_armed(user_id, expiry):
        raise Refused(
            f"A PB/SL rule is armed on NIFTY {expiry}. A campaign has its own max-loss; "
            "disarm the rule first."
        )
    held = broker_position(proc, user_id, expiry) if adopt else None
    if adopt and held is None:
        raise Refused("Positions could not be read from the broker, so there is nothing to adopt yet.")
    if adopt and not held:
        raise Refused(f"No open NIFTY {expiry} legs to adopt.")
    try:
        campaign = repo.create_campaign(user_id, settings, expiry=expiry)
    except repo.GroupTaken as e:
        raise Refused(str(e)) from e
    if adopt and held:
        model = charges or load_charges()
        repo.add_fills(campaign.id, campaign.cycle.id, [
            {
                "expiry": expiry, "strike": strike, "right": right,
                "side": "Buy" if units > 0 else "Sell", "quantity": abs(units), "price": price,
                "charges": model.leg_charges(price, abs(units), is_buy=units > 0),
                "kind": "adopted",
                "note": "Adopted at the broker's average price; charges estimated.",
            }
            for (strike, right), (units, price) in held.items()
        ])
        repo.bump_tranches(campaign.cycle.id)
    return repo.get_campaign(campaign.id, user_id)


def entry_proposal(proc: Any, user_id: str, settings: CondorSettings, *, today: Optional[datetime.date] = None) -> dict[str, Any]:
    """A first tranche at these settings, for Basket Orders to pre-fill: the expiry a new cycle
    would use, and the four strikes the engine's own helpers pick on today's chain.

    Priced on stand-ins outside market hours, like an on-demand evaluation: it is a starting
    point the user edits and the executor re-prices, never an order."""
    from icici_breeze_backend.app.services.condor.engine import entry_strikes, narrowed_wings_note, wing_width_points
    from icici_breeze_backend.app.services.condor.pricing import build_greeks_model

    today = today or datetime.datetime.now(live.IST).date()
    expiry = cycle_expiry(listed_expiries(proc), today, settings)
    if expiry is None:
        raise Refused("No listed NIFTY expiry is at or beyond the tranche cut-off.")
    display = expiry.strftime(_FMT)
    snap = live.snapshot(proc, user_id, display)
    market = snap.market if snap.live else live.with_ltp_stand_ins(snap.market)
    model = build_greeks_model(market.chain, market.spot, expiry, market.now) if market.spot else None
    if model is None:
        raise Refused(f"The NIFTY {display} chain has no spot or prices yet. Try again shortly.")
    strikes = entry_strikes(model, market.strikes, settings)
    if strikes is None:
        raise Refused(_why_no_entry(model, market.strikes, settings, display))
    lot = int(proc.fetch_lot_size(UNDERLYING, display, exchange_code=cfg.NFO) or 0) or None
    return {
        "underlying": UNDERLYING,
        "exchange_code": cfg.NFO,
        "expiry": display,
        "lot_size": lot,
        "legs": [
            {"strike": strikes["long_put"], "right": "Put", "side": "Buy"},
            {"strike": strikes["short_put"], "right": "Put", "side": "Sell"},
            {"strike": strikes["short_call"], "right": "Call", "side": "Sell"},
            {"strike": strikes["long_call"], "right": "Call", "side": "Buy"},
        ],
        "tranches": settings.tranches,
        "indicative": not snap.live,
        "wing_note": narrowed_wings_note(strikes, wing_width_points(settings, model.spot)).strip() or None,
    }


def _why_no_entry(model: Any, strikes: list[float], settings: CondorSettings, display: str) -> str:
    """Which strike the chain cannot supply. A wing past the list falls back to the furthest
    listed strike (#69), so what is left is a short the chain cannot price, or a short with
    nothing listed beyond it."""
    from icici_breeze_backend.app.services.condor.strikes import strike_for_delta

    for right in ("Put", "Call"):
        short = strike_for_delta(model, strikes, right, settings.short_delta)
        if short is None:
            return f"The NIFTY {display} chain cannot price a {settings.short_delta:g} Δ {right.lower()} short yet. Try again shortly."
        if not [k for k in strikes if (k < short if right == "Put" else k > short)]:
            return f"No NIFTY {display} {right.lower()} is listed beyond the {int(short)} short, so it cannot have a wing."
    return f"The NIFTY {display} chain cannot place the entry strikes right now. Try again shortly."


def assign(proc: Any, campaign: repo.Campaign, strike: float, right: str, price: float) -> int:
    """Book the broker/ledger difference on one contract into the ledger at `price`."""
    cycle = campaign.cycle
    if cycle is None:
        raise Refused("The campaign has no open cycle.")
    broker = broker_position(proc, campaign.user_id, cycle.expiry)
    if broker is None:
        raise Refused("Positions could not be read from the broker; try again.")
    ledger = ledger_position(repo.list_fills(campaign.id), cycle.expiry)
    key = (float(strike), right)
    diff = broker.get(key, (0, 0.0))[0] - ledger.get(key, (0, 0.0))[0]
    if diff == 0:
        raise Refused("Nothing to assign on that contract: the ledger already matches the broker.")
    if not price > 0:
        raise Refused("Enter the price the difference traded at.")
    qty = abs(diff)
    added = repo.add_fills(campaign.id, cycle.id, [{
        "expiry": cycle.expiry, "strike": float(strike), "right": right,
        "side": "Buy" if diff > 0 else "Sell", "quantity": qty, "price": float(price),
        "charges": load_charges().leg_charges(float(price), qty, is_buy=diff > 0),
        "kind": "assigned", "note": "Assigned by the user; charges estimated.",
    }])
    repo.clear_left_out(campaign.id, cycle.expiry, float(strike), right)
    return added


def leave_out(proc: Any, campaign: repo.Campaign, strike: float, right: str) -> None:
    cycle = campaign.cycle
    if cycle is None:
        raise Refused("The campaign has no open cycle.")
    broker = broker_position(proc, campaign.user_id, cycle.expiry)
    if broker is None:
        raise Refused("Positions could not be read from the broker; try again.")
    ledger = ledger_position(repo.list_fills(campaign.id), cycle.expiry)
    diffs = reconcile(ledger, broker, {})
    match = next((d for d in diffs if d.strike == float(strike) and d.right == right), None)
    if match is None:
        raise Refused("Nothing outside the ledger on that contract.")
    if not match.can_leave_out:
        raise Refused(
            "The broker no longer holds legs the ledger has; those cannot be left out. "
            "Assign the price they were closed at."
        )
    repo.leave_out(campaign.id, cycle.expiry, float(strike), right, match.units)


# --------------------------------------------------------------------------------------
# Evaluating
# --------------------------------------------------------------------------------------


def decision_dict(d: Decision) -> dict[str, Any]:
    return {
        "action": d.action,
        "reason": d.reason,
        "text": d.text,
        "roll_credit_points": d.roll_credit_points,
        "tranche_strikes": d.tranche_strikes,
        "orders": [dataclasses.asdict(o) for o in d.orders],
        "metrics": dataclasses.asdict(d.metrics),
    }


def evaluate(
    proc: Any,
    campaign: repo.Campaign,
    check_kind: CheckKind = "on_demand",
    *,
    now: Optional[datetime.datetime] = None,
    charges: Optional[ChargesModel] = None,
) -> dict[str, Any]:
    cycle = campaign.cycle
    settings = campaign.settings
    out: dict[str, Any] = {"check_kind": check_kind, "indicative": False, "stand_ins": [], "differences": []}
    if cycle is None:
        out["decision"] = decision_dict(Decision("unavailable", "no_cycle", "The campaign has no open cycle.", metrics=Metrics()))
        return out
    expiry = parse_expiry(cycle.expiry)
    fills = repo.list_fills(campaign.id)
    cash = ledger_cash(fills)
    ledger = ledger_position(fills, cycle.expiry)
    legs = legs_from(ledger)
    out["ledger"] = {
        "cash_inr": round(cash, 2),
        "charges_inr": round(sum(f.charges for f in fills), 2),
        "fills": len(fills),
        "legs": [dataclasses.asdict(l) for l in legs],
    }

    if campaign.mode == "paper":
        # A paper campaign holds nothing at the broker: its ledger is its whole position.
        broker = ledger
    else:
        broker = broker_position(proc, campaign.user_id, cycle.expiry)
    if broker is None:
        out["decision"] = decision_dict(Decision(
            "unavailable", "positions_unreadable", "Positions could not be read from the broker.", metrics=Metrics(),
        ))
        return out
    diffs = reconcile(ledger, broker, repo.left_out(campaign.id, cycle.expiry))
    out["differences"] = [d.as_dict() for d in diffs]
    blocking = [d for d in diffs if not d.left_out]

    snap = live.snapshot(proc, campaign.user_id, cycle.expiry, [(l.strike, l.right) for l in legs], now=now)
    out["stand_ins"] = list(snap.stand_ins)
    out["spot"] = snap.market.spot
    out["spot_source"] = snap.spot_source
    lot_size = int(proc.fetch_lot_size(UNDERLYING, cycle.expiry, exchange_code=cfg.NFO) or 0) or 65
    state = CampaignState(expiry, lot_size, legs, cash, cycle.tranches_entered)

    if blocking:
        names = ", ".join(f"{int(d.strike)} {'CE' if d.right == 'Call' else 'PE'}" for d in blocking)
        decision = Decision(
            "unavailable", "ledger_mismatch",
            f"The broker's position differs from the campaign's ledger on {names}. Assign or leave out each difference first.",
            metrics=Metrics(),
        )
    elif not snap.chain_ready:
        decision = Decision("unavailable", "chain_not_ready", f"The NIFTY {cycle.expiry} chain is not loaded yet.", metrics=Metrics())
    else:
        market = snap.market
        if not snap.live:
            if check_kind == "on_demand":
                # Numbers for the card on stand-in prices -- labelled, and never acted on.
                market = live.with_ltp_stand_ins(dataclasses.replace(market, spot_live=market.spot is not None))
                out["indicative"] = True
            else:
                market = dataclasses.replace(market, feeds_ok=False)
        decision = decide(state, market, settings, check_kind, charges or load_charges())
        if decision.action == "unavailable" and decision.reason == "feed_down" and snap.stand_ins:
            decision = dataclasses.replace(
                decision, text=f"Not live: {', '.join(snap.stand_ins)} on stand-in prices."
            )
    out["decision"] = decision_dict(decision)
    return out
