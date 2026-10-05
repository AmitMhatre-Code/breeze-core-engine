"""The Dynamic Iron Condor bot (docs/dynamic-iron-condor-plan.md section 7).

The bot is a campaign with `origin = "bot"` that acts on its own scheduled checks instead of
only suggesting. Everything it decides is the engine's (#63); everything it places goes through
the campaign executor (#65); this module only decides *whether and how* to act.

The card names the modes as every bot does (#70): `paper` is **Simulation** and `telegram` is
**Semi-auto**. The stored values keep their old names, as the scalpers keep `paper`.

**Modes unlock in order, enforced here and asked by the route before every save:**

* Simulation is open from day 1. A completed backtest of the saved settings is shown beside
  the evidence but gates nothing, as with every other bot (#70);
* `telegram` (Semi-auto) needs one finished Simulation cycle on those settings;
* `auto` needs at least one ticket **approved on Telegram** and executed, on those settings.

**Simulation** fills each action at the live touch plus the simulation slippage (`charges`),
books it to a paper campaign's ledger, and places nothing. A paper campaign holds nothing at
the broker, so it owns no Portfolio group. **Semi-auto** sends the ticket on Telegram with
Approve/Reject on the bots' approval tokens; a tap executes it through the executor, or refuses
if it went stale. **Auto** executes.

**The user always wins.** A manual ticket on the bot's campaign pauses the bot until resumed.
Switching the bot off, changing its settings, or moving between paper and live hands its live
campaign to the user (legs stay open) or closes a paper one.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from icici_breeze_backend.app.db.bots_migrate import BOT_DYNAMIC_CONDOR
from icici_breeze_backend.app.domain.condor import DynamicCondorBotConfig
from icici_breeze_backend.app.repositories import condor as repo
from icici_breeze_backend.app.services.bots.charges import load_charges
from icici_breeze_backend.app.services.condor import campaigns, executor, live, tickets
from icici_breeze_backend.app.services.condor.strikes import cycle_expiry

_logger = logging.getLogger(__name__)

BOT = BOT_DYNAMIC_CONDOR
APPROVED_NOTE = "telegram-approved"
AUTO_NOTE = "bot-auto"
ACTIONABLE = {"roll_untested", "enter_tranche", "exit_or_roll", "close_all"}
_PAPER_CYCLE_ENDS = {"exit_dte", "beyond_breakeven", "max_loss", "time_roll", "close_all", "suggested_close"}
_FMT = "%d-%b-%Y"
# What the user reads for each stored mode (#70): the same words as every other bot's card.
MODE_LABEL = {"paper": "Simulation", "telegram": "Semi-auto", "auto": "Auto"}


def campaign_mode_label(mode: str) -> str:
    return "simulation" if mode == "paper" else mode


class Refused(ValueError):
    pass


def _bots_repo():
    from icici_breeze_backend.app.repositories import bots as bots_repo

    return bots_repo


def config_of(raw: Any) -> DynamicCondorBotConfig:
    return DynamicCondorBotConfig(**(raw if isinstance(raw, dict) else {}))


def settings_hash(settings: dict[str, Any], exit_action: str, engine_version: int) -> str:
    """The identity every piece of evidence is matched on: the campaign settings, the exit
    action, and the rules' version. Same settings under the same rules match at any age (the
    user's choice, 2026-10-03); a rules change matches nothing earned before it."""
    blob = json.dumps(
        {"settings": settings, "exit_action": exit_action, "engine_version": int(engine_version or 0)},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def hash_of(cfg: DynamicCondorBotConfig) -> str:
    from icici_breeze_backend.app.services.condor.engine import ENGINE_VERSION

    return settings_hash(cfg.campaign.model_dump(mode="json"), cfg.exit_action, ENGINE_VERSION)


def _notify(user_id: str, text: str) -> None:
    from icici_breeze_backend.app.services.condor.scheduler import _notify as notify

    notify(user_id, f"Iron Condor bot\n{text}")


# --------------------------------------------------------------------------------------
# Eligibility
# --------------------------------------------------------------------------------------


@dataclass
class Eligibility:
    settings_hash: str
    backtest: Optional[dict[str, Any]]
    paper_cycles: int
    approved_executions: int

    def as_dict(self) -> dict[str, Any]:
        # `backtest` travels for the card to show; it unlocks nothing (#70).
        return {
            **dataclasses.asdict(self),
            "may_telegram": self.paper_cycles >= 1,
            "may_auto": self.paper_cycles >= 1 and self.approved_executions >= 1,
        }


def eligibility(user_id: str, cfg: DynamicCondorBotConfig) -> Eligibility:
    from icici_breeze_backend.app.services.condor import backtest_combos, backtest_job

    h = hash_of(cfg)
    found = None
    try:
        for run in backtest_job.list_runs(user_id, limit=None):
            params = run.get("params") or {}
            # A run compares settings combinations (#71), each evidence for its own settings; a
            # run from before that holds one, its own. A run from before versions were recorded
            # counts as version 0, so it never matches.
            summary = run.get("summary") or {}
            saved = params.get("settings") or {}
            members = [
                {**row, "settings": backtest_combos.settings_of(saved, row),
                 "max_drawdown": abs(float(row.get("max_drawdown") or 0))}
                for row in summary.get("comparison") or []
            ] or [{
                "settings": saved, "exit_action": params.get("exit_action"), "status": run.get("status"),
                "closed_pnl": summary.get("closed_pnl"), "open_campaign_cash": summary.get("open_campaign_cash"),
                "max_drawdown": summary.get("max_drawdown"), "net_pnl": summary.get("net_pnl"), "label": None,
            }]
            for m in members:
                matches = settings_hash(m["settings"], m.get("exit_action") or "",
                                        params.get("engine_version") or 0) == h
                if m.get("status") == "completed" and matches:
                    found = {
                        "run_id": run["id"], "created_at": run["created_at"], "from": params.get("from"),
                        "to": params.get("to"), "closed_pnl": m.get("closed_pnl"),
                        "open_campaign_cash": m.get("open_campaign_cash"), "max_drawdown": m.get("max_drawdown"),
                        "net_pnl": m.get("net_pnl"), "combination": m.get("label"),
                    }
                    break
            if found:
                break
    except Exception:  # noqa: BLE001 -- no backtest cache means no evidence, never a crash
        _logger.debug("condor bot: backtest runs unreadable", exc_info=True)
    # Paper cycles and approvals count only when earned on these settings under these rules.
    # A live campaign the bot handed back keeps its fingerprint, so its approvals still count
    # if the same settings return.
    mine = repo.campaigns_with_note(user_id, f"bot:{h}")
    paper = sum(
        1 for c in mine if c.mode == "paper"
        for y in c.cycles if y.closed_at and y.tranches_entered and y.close_reason in _PAPER_CYCLE_ENDS
    )
    approved = repo.count_executions([c.id for c in mine if c.mode == "live"], note=APPROVED_NOTE)
    return Eligibility(h, found, paper, approved)


def guard(user_id: str, *, after_enabled: bool, after_config: dict[str, Any]) -> None:
    """Refuse a save that would arm a mode the evidence does not yet allow."""
    if not after_enabled:
        return
    cfg = config_of(after_config)
    if cfg.mode == "paper":
        return
    e = eligibility(user_id, cfg)
    if cfg.mode == "telegram" and e.paper_cycles < 1:
        raise Refused("Semi-auto unlocks after one full Simulation cycle on these settings.")
    if cfg.mode == "auto" and (e.paper_cycles < 1 or e.approved_executions < 1):
        raise Refused(
            "Auto unlocks after a Simulation cycle and then a ticket approved in Semi-auto, both on these settings."
        )


# --------------------------------------------------------------------------------------
# The bot's campaign
# --------------------------------------------------------------------------------------


def release(campaign: repo.Campaign, reason: str) -> None:
    """Take the campaign away from the bot: a paper one ends; a live one becomes the user's."""
    if campaign.mode == "paper":
        repo.close_campaign(campaign.id, campaign.user_id, reason)
        return
    repo.set_origin(campaign.id, "manual")
    expiry = campaign.cycle.expiry if campaign.cycle else "—"
    _notify(campaign.user_id, f"The bot has handed its NIFTY {expiry} campaign to you ({reason.replace('_', ' ')}). "
            "Its legs are open; manage them from its card on Portfolio.")


def on_config_change(user_id: str, *, before: dict[str, Any], after: dict[str, Any], after_enabled: bool) -> None:
    camp = repo.bot_campaign(user_id)
    if camp is None:
        return
    b, a = config_of(before), config_of(after)
    if not after_enabled:
        release(camp, "bot_switched_off")
    elif (b.mode == "paper") != (a.mode == "paper"):
        release(camp, "promoted_to_live" if a.mode != "paper" else "moved_to_paper")
    elif hash_of(b) != hash_of(a):
        release(camp, "settings_changed")


# --------------------------------------------------------------------------------------
# Handing a manual campaign to the bot (#68)
# --------------------------------------------------------------------------------------


def _settings_diff(before: Any, after: Any) -> list[dict[str, Any]]:
    a, b = before.model_dump(mode="json"), after.model_dump(mode="json")
    return [{"field": k, "campaign": a.get(k), "bot": b.get(k)} for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]


def handover_preview(proc: Any, campaign: repo.Campaign) -> dict[str, Any]:
    """What handing this campaign to the bot would change, and why it cannot happen yet.

    Every refusal is listed, not just the first, so the user sees everything to fix at once.
    The decision is the engine's answer under the **bot's** settings now, because those are
    what it will run on: it can differ from the card's (an immediate roll, a tranche due)."""
    record = _bots_repo().get_or_create_bot(campaign.user_id, BOT)
    cfg = config_of(record.config)
    blockers: list[str] = []
    if campaign.status != "active" or campaign.origin != "manual" or campaign.mode != "live":
        blockers.append("Only an active campaign of your own, on real positions, can be handed to the bot.")
    if not record.enabled or cfg.mode == "paper":
        blockers.append(
            "Switch the bot on in Semi-auto or Auto first. In Simulation it places nothing, so it cannot "
            "manage real positions."
        )
    elif cfg.paused:
        blockers.append(f"The bot is paused ({cfg.paused_reason or 'by you'}). Resume it on its card first.")
    own = repo.bot_campaign(campaign.user_id)
    if own is not None and own.id != campaign.id:
        expiry = own.cycle.expiry if own.cycle else "—"
        blockers.append(f"The bot already runs a campaign (NIFTY {expiry}, {campaign_mode_label(own.mode)}); it runs one at a time.")
    if repo.running_execution(campaign.id):
        blockers.append("A ticket is executing on this campaign. Wait for it to finish.")
    as_bot = dataclasses.replace(campaign, settings=cfg.campaign)
    ev = campaigns.evaluate(proc, as_bot, "on_demand")
    if any(not d.get("left_out") for d in ev.get("differences") or []):
        blockers.append("The broker's position differs from the campaign's ledger. Assign or leave out each difference first.")
    cycle = campaign.cycle
    entered = cycle.tranches_entered if cycle else 0
    remaining = max(0, cfg.campaign.tranches - entered)
    sizing = (
        f"{cfg.lots_per_tranche} lot(s) each, the bot's lots per tranche" if cfg.lots_per_tranche
        else "each sized from that day's margin: the ceiling over the tranches"
    )
    return {
        "allowed": not blockers,
        "blockers": blockers,
        "mode": cfg.mode if record.enabled else "off",
        "settings_changes": _settings_diff(campaign.settings, cfg.campaign),
        "tranches_entered": entered,
        "tranches_remaining": remaining,
        "sizing": f"{remaining} more tranche(s), {sizing}." if remaining else "No tranches left to enter.",
        "decision": ev.get("decision"),
        "indicative": ev.get("indicative", False),
    }


def hand_over(proc: Any, campaign: repo.Campaign) -> repo.Campaign:
    """Make a manual campaign the bot's, re-checking everything the preview checked."""
    preview = handover_preview(proc, campaign)
    if not preview["allowed"]:
        raise Refused(" ".join(preview["blockers"]))
    cfg = config_of(_bots_repo().get_or_create_bot(campaign.user_id, BOT).config)
    if not repo.hand_to_bot(campaign.id, campaign.user_id, cfg.campaign, f"bot:{hash_of(cfg)}"):
        raise Refused("The campaign changed while it was being handed over. Reload and try again.")
    expiry = campaign.cycle.expiry if campaign.cycle else "—"
    _notify(campaign.user_id, f"You handed the NIFTY {expiry} campaign to the bot. It manages it from the next check, in {MODE_LABEL[cfg.mode]}.")
    return repo.get_campaign(campaign.id, campaign.user_id)


def pause(user_id: str, reason: str) -> None:
    bots_repo = _bots_repo()
    record = bots_repo.get_or_create_bot(user_id, BOT)
    if config_of(record.config).paused:
        return
    bots_repo.update_bot(user_id, BOT, config={"paused": True, "paused_reason": reason})
    _notify(user_id, f"Paused: {reason} It decides nothing until you resume it on its card.")


def waiting_reason(proc: Any, user_id: str, cfg: DynamicCondorBotConfig, today: datetime.date) -> tuple[Optional[str], Optional[str]]:
    """(the expiry a new bot campaign would use, why it cannot open there yet). A live bot never
    shares an expiry with another campaign (#67): it waits, and says so, rather than skip."""
    expiry = cycle_expiry(campaigns.listed_expiries(proc), today, cfg.campaign)
    if expiry is None:
        return None, "No listed NIFTY expiry is at or beyond the tranche cut-off yet."
    display = expiry.strftime(_FMT)
    if cfg.mode == "paper":
        return display, None
    if repo.active_owner(user_id, "NIFTY", display):
        return display, f"NIFTY {display} is managed by another campaign; the bot opens its own once that one closes."
    if campaigns._sg_armed(user_id, display):  # noqa: SLF001
        return display, f"A PB/SL rule is armed on NIFTY {display}; the bot opens its campaign once it is disarmed."
    return display, None


# (user, expiry, reason) already told on Telegram, so a check every minute does not repeat it.
_waiting_told: set[tuple[str, str, str]] = set()


def ensure_campaign(proc: Any, user_id: str, record: Any, today: datetime.date) -> Optional[repo.Campaign]:
    """The enabled bot's campaign, opened empty when it has none (its tranches then come due)."""
    cfg = config_of(record.config)
    if not record.enabled or cfg.paused:
        return None
    camp = repo.bot_campaign(user_id)
    if camp is not None:
        return camp
    display, why = waiting_reason(proc, user_id, cfg, today)
    if display is None:
        return None
    if why:
        key = (user_id, display, why)
        if key not in _waiting_told:
            _waiting_told.add(key)
            _notify(user_id, f"Waiting: {why}")
        return None
    mode = "paper" if cfg.mode == "paper" else "live"
    try:
        return repo.create_campaign(user_id, cfg.campaign, expiry=display, origin="bot", mode=mode, note=f"bot:{hash_of(cfg)}")
    except repo.GroupTaken:
        return None


# --------------------------------------------------------------------------------------
# Acting on a scheduled check
# --------------------------------------------------------------------------------------


def _lots(user_id: str, cfg: DynamicCondorBotConfig) -> tuple[Optional[int], str]:
    if cfg.lots_per_tranche:
        return cfg.lots_per_tranche, ""
    from icici_breeze_backend.app.services.condor.backtest_job import SizingError, size_from_margin

    try:
        return size_from_margin(cfg.campaign, user_id)["lots"], ""
    except SizingError as e:
        return None, str(e)
    except Exception as e:  # noqa: BLE001
        return None, f"Today's margin could not size a tranche ({e})."


def _fit_to_book(ctx: tickets.Ctx, orders: list[dict[str, Any]], lots: int) -> int:
    """The largest lot count, at the same strikes, every opening order's book can absorb (#62).
    Unknown (no book seen) is not a fail."""
    from icici_breeze_backend.app.services.liquidity.check import check_contract

    opening = [o for o in orders if o.get("opening")]
    if not opening:
        return lots
    per_lot = {id(o): o["quantity"] // lots for o in opening}
    for n in range(lots, 0, -1):
        ok = True
        for o in opening:
            try:
                v = check_contract("NFO", "NIFTY", o.get("expiry") or ctx.expiry_display, o["strike"], o["right"],
                                   o["action"], per_lot[id(o)] * n, lot_size=ctx.lot_size)
            except Exception:  # noqa: BLE001
                continue
            if v.ok is False:
                ok = False
                break
        if ok:
            return n
    return 0


def _ticket(proc: Any, campaign: repo.Campaign, decision: dict[str, Any], cfg: DynamicCondorBotConfig) -> tuple[Optional[dict[str, Any]], str]:
    """(ticket, why-not). The ticket is built from the same templates a user would use."""
    action = decision["action"]
    template_kind, params = "suggested", {}
    sized = action == "enter_tranche" or (action == "exit_or_roll" and cfg.exit_action == "time_roll")
    if action == "exit_or_roll" and cfg.exit_action == "time_roll":
        template_kind = "time_roll"
    lots = None
    if sized:
        lots, why = _lots(campaign.user_id, cfg)
        if not lots:
            return None, why or "No lot size for a tranche."
        params = {"lots": lots}
    try:
        t = tickets.template(proc, campaign, template_kind, params)
    except tickets.Refused as e:
        return None, str(e)
    if not t["orders"]:
        return None, t.get("note") or "The suggestion has no orders."
    if sized and lots:
        ctx = tickets.context(proc, campaign)
        fit = _fit_to_book(ctx, t["orders"], lots)
        if fit == 0:
            return None, "The order book cannot take one lot at these strikes (liquidity_thin)."
        if fit < lots:
            t = tickets.template(proc, campaign, template_kind, {"lots": fit})
            t["note"] = f"Shrunk from {lots} to {fit} lot(s) to fit the order book."
    t["kind"] = template_kind
    return t, ""


def _ticket_kind(template_kind: str, orders: list[dict[str, Any]]) -> str:
    if template_kind == "time_roll":
        return "time_roll"
    if all(not o.get("opening") for o in orders):
        return "suggested_close"
    if all(o.get("opening") for o in orders):
        return "suggested_entry"
    return "suggested_roll"


def act(proc: Any, campaign: repo.Campaign, check_kind: str, out: dict[str, Any], decision_id: int) -> Optional[str]:
    """Called by the scheduler after a bot campaign's check is recorded. Returns what it did."""
    d = out["decision"]
    if d["action"] not in ACTIONABLE:
        return None
    record = _bots_repo().get_or_create_bot(campaign.user_id, BOT)
    cfg = config_of(record.config)
    if not record.enabled:
        return None
    if cfg.paused:
        _notify(campaign.user_id, f"{d['text']}\nNot acted on: the bot is paused ({cfg.paused_reason or 'by you'}).")
        return "paused"
    ticket, why = _ticket(proc, campaign, d, cfg)
    if ticket is None:
        repo.update_decision(decision_id, outcome="skipped")
        _notify(campaign.user_id, f"{d['text']}\nSkipped: {why}")
        return f"skipped: {why}"
    kind = _ticket_kind(ticket["kind"], ticket["orders"])
    if campaign.mode == "paper":
        return simulate(proc, campaign, ticket["orders"], kind, decision_id)
    opens = any(o.get("opening") for o in ticket["orders"])
    from icici_breeze_backend.app.services.bots import hitl

    if opens and not hitl.trading_allowed():
        repo.update_decision(decision_id, outcome="read_only")
        _notify(campaign.user_id, f"{d['text']}\nNot placed: read-only mode allows closing only.")
        return "read_only"
    if cfg.mode == "telegram":
        return propose(campaign, decision_id, d, ticket, kind, cfg)
    try:
        executor.start(proc, campaign, ticket["orders"], kind=kind, note=AUTO_NOTE)
    except (executor.Refused, tickets.Refused) as e:
        _notify(campaign.user_id, f"{d['text']}\nNot placed: {e}")
        return f"refused: {e}"
    repo.update_decision(decision_id, outcome="executing")
    _notify(campaign.user_id, f"{d['text']}\nPlacing it now ({len(ticket['orders'])} order(s), wings first).")
    return "executing"


# --------------------------------------------------------------------------------------
# Paper
# --------------------------------------------------------------------------------------


def simulate(proc: Any, campaign: repo.Campaign, raw: list[dict[str, Any]], kind: str, decision_id: Optional[int] = None) -> str:
    """Fill a ticket on paper: the live touch plus the paper slippage, booked to the ledger."""
    sequenced, ctx = executor.plan(proc, campaign, raw)
    charges = load_charges()
    frac = float(charges.slippage_spread_fraction)
    markets = {None: ctx.market}
    for exp in {o.expiry for o in sequenced if o.expiry}:
        markets[exp] = live.snapshot(proc, campaign.user_id, exp.strftime(_FMT)).market
    cycle = campaign.cycle
    cycle_id, cycle_expiry = cycle.id, cycle.expiry
    filled = 0
    for o in sequenced:
        exp_display = o.expiry.strftime(_FMT) if o.expiry else ctx.expiry_display
        if o.expiry is not None and exp_display != cycle_expiry:
            repo.close_cycle(cycle_id, "time_roll")
            cycle_id, cycle_expiry = repo.open_cycle(campaign.id, exp_display), exp_display
        q = markets[o.expiry].quote(o.strike, o.right)
        bid = q.bid if q and q.bid and q.bid > 0 else None
        ask = q.ask if q and q.ask and q.ask > 0 else None
        if bid is None or ask is None:
            msg = f"Simulation: fill stopped at step {filled + 1}: no two-sided quote for {int(o.strike)} {o.right}."
            repo.add_decision(campaign.id, check_kind="manual", action=kind, reason="stopped", text=msg, outcome="paper")
            _notify(campaign.user_id, msg)
            break
        slip = frac * (ask - bid)
        price = ask + slip if o.action == "Buy" else max(0.0, bid - slip)
        repo.add_fills(campaign.id, cycle_id, [{
            "expiry": exp_display, "strike": o.strike, "right": o.right, "side": o.action,
            "quantity": o.quantity, "price": round(price, 2),
            "charges": charges.leg_charges(price, o.quantity, is_buy=o.action == "Buy"),
            "kind": executor._fill_kind(kind, o), "note": "paper",  # noqa: SLF001
        }])
        filled += 1
    status = "completed" if filled == len(sequenced) else "stopped"
    executor._settle(campaign, cycle_id, cycle_expiry, kind, status)  # noqa: SLF001
    if decision_id is not None:
        repo.update_decision(decision_id, outcome=f"paper_{status}")
    if status == "completed":
        _notify(campaign.user_id, f"Simulation: {kind.replace('_', ' ')} filled at live prices, {filled} order(s). Nothing was placed.")
    return f"paper_{status}"


# --------------------------------------------------------------------------------------
# Telegram approval
# --------------------------------------------------------------------------------------


def _proposal_text(campaign: repo.Campaign, decision: dict[str, Any], ticket: dict[str, Any], ttl: int) -> str:
    from icici_breeze_backend.app.services.bots.hitl import _md

    lines = []
    for i, o in enumerate(ticket["orders"], 1):
        verb = ("Buy" if o["opening"] else "Buy back") if o["action"] == "Buy" else ("Sell" if o["opening"] else "Sell to close")
        exp = f" ({o['expiry']})" if o.get("expiry") else ""
        lines.append(f"{i}. {verb} {o['quantity']} × {int(o['strike'])} {'CE' if o['right'] == 'Call' else 'PE'}{exp}")
    note = f"\n_{_md(ticket['note'])}_" if ticket.get("note") else ""
    expiry = campaign.cycle.expiry if campaign.cycle else ""
    return (
        f"🦅 *Iron Condor bot* · NIFTY {expiry}\n{_md(decision['text'])}\n\n" + "\n".join(lines) + note +
        f"\n\nOrders go out one at a time, wings first. Approve within {ttl} min."
    )


def propose(campaign: repo.Campaign, decision_id: int, decision: dict[str, Any], ticket: dict[str, Any], kind: str,
            cfg: DynamicCondorBotConfig) -> str:
    from icici_breeze_backend.app.services import telegram_alerts
    from icici_breeze_backend.app.services.bots import hitl
    from icici_breeze_backend.app.services.telegram_client import send_message_get_id
    from icici_breeze_backend.app.services.telegram_link_portal import register_approval_token

    bots_repo = _bots_repo()
    chat = hitl._reachable(campaign.user_id)  # noqa: SLF001
    if not chat:
        repo.update_decision(decision_id, outcome="approval_unreachable")
        return "approval_unreachable"
    ttl = cfg.proposal_ttl_minutes
    expires = (datetime.datetime.now(live.IST) + datetime.timedelta(minutes=ttl)).isoformat(timespec="seconds")
    snap = (repo.get_decision(decision_id) or {}).get("snapshot") or {}
    repo.update_decision(decision_id, outcome="awaiting_approval",
                         snapshot={**snap, "ticket": {"kind": kind, "orders": ticket["orders"], "note": ticket.get("note")},
                                   "expires_at": expires})
    hitl.retire_open_asks(campaign.user_id, BOT, "⌛ *Superseded* — a newer check follows. Nothing was placed from this one.")
    token = bots_repo.issue_approval_token(user_id=campaign.user_id, bot_type=BOT, proposal_id=str(decision_id),
                                           chat_id=chat, ttl_minutes=ttl)
    register_approval_token(token, ttl * 60)
    text = _proposal_text(campaign, decision, ticket, ttl)
    mid = send_message_get_id(chat, text, reply_markup=telegram_alerts._approval_keyboard(token, telegram_alerts._bots_app_url()))  # noqa: SLF001
    if mid is None:
        repo.update_decision(decision_id, outcome="approval_undelivered")
        return "approval_undelivered"
    bots_repo.set_approval_message(token, mid, text)
    return "awaiting_approval"


def handle_approval(user_id: str, proposal_id: str, action: str, ask: Optional[dict[str, Any]], stamp: str) -> None:
    """A Telegram tap on a condor proposal, routed here by `hitl.handle_callback`."""
    from icici_breeze_backend.app.services.bots import hitl
    from icici_breeze_backend.app.services.processor import processor

    try:
        decision = repo.get_decision(int(proposal_id))
    except (TypeError, ValueError):
        decision = None
    if action == "r":
        hitl._retire(ask, f"❌ *Rejected at {stamp}* — nothing was placed.")  # noqa: SLF001
        if decision:
            repo.update_decision(decision["id"], outcome="rejected")
        _notify(user_id, "Rejected — nothing was placed. The next check will decide again.")
        return
    if action != "a":
        return
    snap = (decision or {}).get("snapshot") or {}
    ticket = snap.get("ticket")
    expires = snap.get("expires_at")
    stale = (
        decision is None or decision.get("outcome") != "awaiting_approval" or not ticket
        or (expires and datetime.datetime.fromisoformat(expires) < datetime.datetime.now(live.IST))
    )
    campaign = repo.get_campaign(decision["campaign_id"], user_id) if decision else None
    if stale or campaign is None or campaign.status != "active" or campaign.origin != "bot":
        hitl._retire(ask, "⌛ *No longer valid* — nothing was placed.")  # noqa: SLF001
        _notify(user_id, "That approval is no longer valid (it expired, or the campaign changed). Nothing was placed.")
        return
    opens = any(o.get("opening") for o in ticket["orders"])
    if opens and not hitl.trading_allowed():
        hitl._retire(ask, "🔒 *Not placed* — read-only mode.")  # noqa: SLF001
        _notify(user_id, "Read-only mode — your licence does not currently allow opening positions. Nothing was placed.")
        return
    hitl._retire(ask, f"⏳ *Approved at {stamp} — placing orders now…*")  # noqa: SLF001
    try:
        ex = executor.start(processor(), campaign, ticket["orders"], kind=ticket["kind"], note=APPROVED_NOTE, run=lambda fn: fn())
    except (executor.Refused, tickets.Refused) as e:
        repo.update_decision(decision["id"], outcome="refused")
        _notify(user_id, f"Nothing was placed: {e}")
        return
    final = repo.get_execution(ex["id"], campaign.id) or ex
    repo.update_decision(decision["id"], outcome=f"approved_{final.get('status')}")
    hitl._edit_ask(ask, f"☑️ *Approved at {stamp}* — the result is in the message below.")  # noqa: SLF001
    _notify(user_id, final.get("message") or f"Ticket {final.get('status')}.")
