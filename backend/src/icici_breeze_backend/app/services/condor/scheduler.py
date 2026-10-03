"""Scheduled campaign checks (docs/dynamic-iron-condor-plan.md section 6).

A daemon thread in the API process, like the bots' scheduler: it needs the broker session
cache and the WS feed, which live here. Thin on purpose -- every judgement is the engine's.

For each active campaign, on a trading day:

* **Warm** the cycle expiry's chain a few minutes before each check, so the quotes the check
  reads are live rather than a cold chain's stand-ins (#50, #19).
* **Check** once at the start-of-day time and once at the end-of-day time (the campaign's own
  settings). A check missed by more than `CHECK_WINDOW` -- the app was down -- is skipped,
  never run late: a 15:31 decision taken at 16:10 would be about a closed market.
* **Record** the decision, and **alert** on Telegram when it asks for an action or could not
  decide. A manual campaign is never traded from here; the user acts on the card.
"""
from __future__ import annotations

import dataclasses
import datetime
import logging
import threading
from typing import Any, Optional

from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.repositories import condor as repo
from icici_breeze_backend.app.services.condor import campaigns, live

_logger = logging.getLogger(__name__)

TICK_SECONDS = 30
WARM_AHEAD = datetime.timedelta(minutes=5)
CHECK_WINDOW = datetime.timedelta(minutes=20)
ALERT_KIND = "condor"
_ACTIONABLE = {"roll_untested", "enter_tranche", "exit_or_roll", "close_all"}

_stop = threading.Event()
_thread: Optional[threading.Thread] = None
_warmed: set[tuple[str, str, datetime.date]] = set()


def _at(day: datetime.date, hhmm: str, tz) -> datetime.datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.datetime.combine(day, datetime.time(h, m), tzinfo=tz)


def _already_checked(campaign_id: str, kind: str, day: datetime.date) -> bool:
    for d in repo.list_decisions(campaign_id, limit=20):
        if d["check_kind"] == kind and str(d["at"]).startswith(day.isoformat()):
            return True
    return False


def _label(kind: str) -> str:
    return "start-of-day" if kind == "sod" else "end-of-day"


def alert_text(campaign: repo.Campaign, kind: str, decision: dict[str, Any]) -> str:
    expiry = campaign.cycle.expiry if campaign.cycle else "—"
    head = f"Iron Condor · NIFTY {expiry} · {_label(kind)} check"
    if decision["action"] == "unavailable":
        return f"{head}\nCould not decide: {decision['text']}"
    pnl = (decision.get("metrics") or {}).get("campaign_pnl_inr")
    tail = f"\nCampaign P&L ₹{pnl:,.0f}." if isinstance(pnl, (int, float)) else ""
    return f"{head}\n{decision['text']}{tail}\nReview it on the Portfolio page."


def _notify(user_id: str, text: str) -> None:
    from icici_breeze_backend.app.services.telegram_alerts import _notify as notify

    try:
        notify(user_id, text, kind=ALERT_KIND)
    except Exception:  # noqa: BLE001 -- an alert failure never undoes a recorded check
        _logger.exception("condor: alert failed for user=%s", user_id)


def run_check(
    proc: Any, campaign: repo.Campaign, kind: str, now: Optional[datetime.datetime] = None
) -> dict[str, Any]:
    """One scheduled check: evaluate, record, alert. Returns the evaluation."""
    now = now or now_ist()
    if proc.get_session_breeze(campaign.user_id) is None:
        out = {"decision": {"action": "unavailable", "reason": "no_session",
                            "text": "No broker session: log in to ICICI so the checks can read prices.",
                            "metrics": {}, "orders": []}}
    else:
        out = campaigns.evaluate(proc, campaign, kind)
    d = out["decision"]
    decision_id = repo.add_decision(
        campaign.id, check_kind=kind, action=d["action"], reason=d["reason"], text=d["text"],
        snapshot={"metrics": d.get("metrics"), "orders": d.get("orders"),
                  "roll_credit_points": d.get("roll_credit_points"),
                  "tranche_strikes": d.get("tranche_strikes"),
                  "spot": out.get("spot"), "stand_ins": out.get("stand_ins")},
        outcome="suggested" if d["action"] in _ACTIONABLE else None,
        at=now.strftime("%Y-%m-%d %H:%M:%S"),
    )
    out["decision_id"] = decision_id
    if campaign.origin == "bot" and d["action"] in _ACTIONABLE:
        # The bot says what it did with the suggestion; a second "review it" alert would
        # tell the user to act on something already handled.
        from icici_breeze_backend.app.services.condor import bot

        try:
            out["bot"] = bot.act(proc, campaign, kind, out, decision_id)
        except Exception as exc:  # noqa: BLE001 -- recorded and alerted, never silent
            _logger.exception("condor: bot failed to act on %s", campaign.id)
            _notify(campaign.user_id, f"{alert_text(campaign, kind, d)}\nThe bot could not act on it: {exc}")
        return out
    if d["action"] in _ACTIONABLE or d["action"] == "unavailable":
        _notify(campaign.user_id, alert_text(campaign, kind, d))
    return out


def tick(proc: Any, now: Optional[datetime.datetime] = None) -> None:
    from icici_breeze_backend.app.services.market_calendar import is_trading_day

    now = now or now_ist()
    if not is_trading_day(now):
        return
    day = now.date()
    _ensure_bot_campaigns(proc, day)
    for campaign in repo.list_active_all():
        cycle = campaign.cycle
        if cycle is None:
            continue
        s = campaign.settings
        for kind, hhmm in (("sod", s.sod_check_ist), ("eod", s.eod_check_ist)):
            due = _at(day, hhmm, now.tzinfo)
            warm_key = (campaign.id, kind, day)
            if due - WARM_AHEAD <= now < due and warm_key not in _warmed:
                _warmed.add(warm_key)
                try:
                    live.snapshot(proc, campaign.user_id, cycle.expiry, now=now)
                except Exception:  # noqa: BLE001
                    _logger.warning("condor: warm-up failed for %s", campaign.id, exc_info=True)
            if due <= now < due + CHECK_WINDOW and not _already_checked(campaign.id, kind, day):
                try:
                    run_check(proc, campaign, kind, now)
                except Exception:  # noqa: BLE001 -- one campaign's failure never stops the rest
                    _logger.exception("condor: %s check failed for %s", kind, campaign.id)
    stale = {k for k in _warmed if k[2] < day}
    _warmed.difference_update(stale)


def _ensure_bot_campaigns(proc: Any, day: datetime.date) -> None:
    """An enabled, unpaused condor bot always has a campaign for its checks to run on."""
    from icici_breeze_backend.app.db.bots_migrate import BOT_DYNAMIC_CONDOR
    from icici_breeze_backend.app.repositories import bots as bots_repo
    from icici_breeze_backend.app.services.condor import bot

    try:
        records = bots_repo.list_enabled_bots(BOT_DYNAMIC_CONDOR)
    except Exception:  # noqa: BLE001 -- no bots table yet (tests, first boot)
        return
    for record in records:
        user_id = bots_repo.bot_owner(record.id)
        if not user_id:
            continue
        try:
            bot.ensure_campaign(proc, user_id, record, day)
        except Exception:  # noqa: BLE001
            _logger.exception("condor: could not open the bot's campaign for %s", user_id)


def _loop() -> None:
    while not _stop.is_set():
        try:
            from icici_breeze_backend.app.services.processor import processor

            tick(processor())
        except Exception:  # noqa: BLE001 -- one bad tick must never kill the scheduler
            _logger.exception("condor scheduler tick failed")
        _stop.wait(TICK_SECONDS)


def start_condor_scheduler() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="condor-scheduler", daemon=True)
    _thread.start()
    _logger.info("Condor scheduler started.")


def stop_condor_scheduler() -> None:
    global _thread
    _stop.set()
    _thread = None
