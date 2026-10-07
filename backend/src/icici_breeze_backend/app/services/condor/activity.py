"""The Dynamic Iron Condor bot in the Activity table (#73).

Every other bot shows its work as rows in `bot_runs`; the condor bot's lived only on its card, as
a link to the campaign. A campaign runs for days, so it is **one row** (`trigger = "campaign"`):
opened when the bot takes a campaign on, `running` while the campaign is open, its reason
restated after every scheduled check, and finished with the outcome when the campaign closes or
is handed back to the user.

The row follows the campaign; it never decides anything. `sync` reconciles rows against
campaigns on every scheduler tick, so whatever path opened, closed or handed over a campaign (the
bot, a ticket, the user's own close, a settings change), its row catches up within a tick
without each of those paths having to remember it. The stale-run reaper never touches these rows
(`repositories/bots.REAPABLE_RUNS`): a restart does not end a campaign.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Optional

from icici_breeze_backend.app.db.bots_migrate import BOT_DYNAMIC_CONDOR
from icici_breeze_backend.app.repositories import condor as repo

_logger = logging.getLogger(__name__)

BOT = BOT_DYNAMIC_CONDOR

_CLOSE_LABEL = {
    "exit_dte": "its exit DTE was reached",
    "beyond_breakeven": "spot went beyond a break-even",
    "max_loss": "the max loss was hit",
    "close_all": "the bot closed everything",
    "suggested_close": "a suggested close was filled",
    "time_roll": "it rolled out and ended flat",
    "closed_by_user": "you closed it",
    "bot_switched_off": "the bot was switched off",
    "settings_changed": "the bot's settings changed",
    "promoted_to_live": "the bot moved from Simulation to live",
    "moved_to_paper": "the bot moved to Simulation",
}


def _bots_repo():
    from icici_breeze_backend.app.repositories import bots as bots_repo

    return bots_repo


def _mode(campaign: repo.Campaign) -> str:
    return "Simulation" if campaign.mode == "paper" else "Live"


def _expiry(campaign: repo.Campaign) -> str:
    if campaign.cycle:
        return campaign.cycle.expiry
    return campaign.cycles[-1].expiry if campaign.cycles else "—"


def _inr(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}₹{abs(value):,.0f}"


def _detail(campaign: repo.Campaign, **extra: Any) -> dict[str, Any]:
    cycle = campaign.cycle
    return {
        "campaign_id": campaign.id,
        "mode": campaign.mode,
        "expiry": _expiry(campaign),
        "tranches_entered": cycle.tranches_entered if cycle else None,
        "tranches": campaign.settings.tranches,
        **{k: v for k, v in extra.items() if v is not None},
    }


def _still_held(campaign: repo.Campaign, fills: list[repo.Fill]) -> bool:
    from icici_breeze_backend.app.services.condor.campaigns import ledger_position

    expiries = {y.expiry for y in campaign.cycles} or {f.expiry for f in fills}
    return any(units for e in expiries for units, _avg in ledger_position(fills, e).values())


def _ending(campaign: Optional[repo.Campaign]) -> tuple[str, str, Optional[float]]:
    """(reason_code, reason_text, net P&L when flat) for a row whose campaign the bot no longer runs."""
    if campaign is None:
        return "campaign_missing", "Its campaign no longer exists.", None
    head = f"NIFTY {_expiry(campaign)} · {_mode(campaign)}"
    if campaign.status == "active":
        # Still open, but no longer the bot's: handed back to the user (`bot.release`).
        return ("handed_to_you",
                f"{head} · handed to you with its legs open. Manage it from its card on Portfolio.", None)
    code = campaign.close_reason or "closed"
    label = _CLOSE_LABEL.get(code, code.replace("_", " "))
    fills = repo.list_fills(campaign.id)
    if _still_held(campaign, fills):
        if campaign.mode == "paper":
            return code, f"{head} · ended because {label}, with legs still open in the simulation.", None
        return code, f"{head} · closed because {label}, with legs still in its ledger.", None
    net = round(sum(f.cash for f in fills), 2)
    if not fills:
        return code, f"{head} · ended because {label}, before any tranche was entered.", None
    return code, f"{head} · closed because {label}. Net {_inr(net)} after charges.", net


def _opening_text(campaign: repo.Campaign) -> str:
    s = campaign.settings
    return (f"Took on NIFTY {_expiry(campaign)} in {_mode(campaign)}. "
            f"Checks at {s.sod_check_ist} and {s.eod_check_ist} IST.")


def sync() -> None:
    """Open a row for every bot campaign without one; finish every row whose campaign the bot
    no longer runs. Idempotent and cheap (database only), so it runs on every tick, trading day
    or not: a bot switched off on a Saturday must not leave its row `running` until Monday."""
    bots_repo = _bots_repo()
    try:
        rows = bots_repo.running_campaign_runs(BOT)
    except Exception:  # noqa: BLE001 -- no bots table yet (tests, first boot)
        return
    followed: set[str] = set()
    for row in rows:
        cid = row.get("campaign_id")
        campaign = repo.get_campaign(cid, row["user_id"]) if cid else None
        if campaign is not None and campaign.status == "active" and campaign.origin == "bot":
            if cid not in followed:
                followed.add(cid)
                continue
            # Two rows on one campaign (a race between two writers): keep the first.
            bots_repo.finish_run(row["id"], status="completed", reason_code="duplicate_row",
                                 reason_text="A duplicate row for this campaign; the other row follows it.",
                                 detail=row["detail"])
            continue
        code, text, net = _ending(campaign)
        detail = {**row["detail"], **(_detail(campaign) if campaign else {})}
        if net is not None:
            detail["net_pnl"] = net
        bots_repo.finish_run(row["id"], status="completed" if campaign else "failed",
                             reason_code=code, reason_text=text, detail=detail)
    for campaign in repo.list_active_all():
        if campaign.origin != "bot" or campaign.id in followed:
            continue
        bots_repo.open_campaign_run(campaign.user_id, BOT, reason_code="campaign_opened",
                                    reason_text=_opening_text(campaign), detail=_detail(campaign))


def _row_for(campaign: repo.Campaign) -> Optional[str]:
    bots_repo = _bots_repo()
    for row in bots_repo.running_campaign_runs(BOT):
        if row["user_id"] == campaign.user_id and row.get("campaign_id") == campaign.id:
            return row["id"]
    sync()
    for row in bots_repo.running_campaign_runs(BOT):
        if row["user_id"] == campaign.user_id and row.get("campaign_id") == campaign.id:
            return row["id"]
    return None


def note(campaign: repo.Campaign, *, reason_code: str, reason_text: str, **extra: Any) -> None:
    """Restate what the bot's campaign is doing on its row. Never raises: the Activity row is a
    record of the campaign, and failing to write it must not undo what the bot did."""
    try:
        # Re-read: the check that called this may have just filled a tranche or closed the cycle.
        campaign = repo.get_campaign(campaign.id, campaign.user_id) or campaign
        if campaign.origin != "bot":
            return
        run_id = _row_for(campaign)
        if run_id:
            _bots_repo().update_run_reason(run_id, reason_code=reason_code, reason_text=reason_text,
                                           detail=_detail(campaign, **extra))
    except Exception:  # noqa: BLE001
        _logger.exception("condor: could not update the Activity row for %s", campaign.id)


def _outcome(result: Optional[str]) -> str:
    """What the bot did with a check's decision (`bot.act`'s return), in the user's words."""
    if not result:
        return ""
    fixed = {
        "paused": "Not acted on: the bot is paused.",
        "paper_completed": "Filled in Simulation at live prices; nothing was placed.",
        "paper_stopped": "The simulated fill stopped part-way: a leg had no two-sided quote.",
        "read_only": "Not placed: read-only mode allows closing only.",
        "awaiting_approval": "Asked you on Telegram.",
        "approval_unreachable": "Could not ask you: no Telegram chat is linked.",
        "approval_undelivered": "The Telegram ask did not go through.",
        "executing": "Placing it now, wings first.",
    }
    if result in fixed:
        return fixed[result]
    for prefix, lead in (("skipped: ", "Skipped: "), ("refused: ", "Not placed: "),
                         ("failed: ", "The bot could not act on it: ")):
        if result.startswith(prefix):
            return lead + result[len(prefix):]
    return result


def note_check(campaign: repo.Campaign, kind: str, at: datetime.datetime, out: dict[str, Any]) -> None:
    """A scheduled check's verdict on the campaign's row: when, what it decided, what the bot did."""
    d = out.get("decision") or {}
    label = "Start-of-day" if kind == "sod" else "End-of-day"
    text = d.get("text") or ""
    if d.get("action") == "unavailable":
        text = f"Could not decide: {text}"
    tail = _outcome(out.get("bot"))
    pnl = (d.get("metrics") or {}).get("campaign_pnl_inr")
    note(
        campaign,
        reason_code=str(d.get("action") or "checked"),
        reason_text=f"{at:%d %b %H:%M} {label} check · {text}{(' ' + tail) if tail else ''}",
        campaign_pnl_inr=round(pnl, 2) if isinstance(pnl, (int, float)) else None,
        check_kind=kind,
    )
