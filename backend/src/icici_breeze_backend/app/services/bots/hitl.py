"""Semi-autonomous bots: ask on Telegram, place only on an explicit tap.

`approval_mode="telegram"` sits between the two modes that already existed. The bot still
runs on its own schedule and still sizes the trade itself, but instead of placing it, it
persists the priced legs as a proposal and sends them to the user's linked chat with an
Approve/Reject keyboard. Silence places nothing, ever.

Three properties are load-bearing and easy to lose:

* **The portal routes, this module decides.** A callback reaches us because the portal
  matched a token to this deployment, but only `consume_approval_token` decides whether that
  token still authorises anything -- the same split as account linking, for the stronger
  reason that this one can place orders.
* **A tap is not a placement.** Approval runs the same re-price-and-refuse rules as the app's
  review screen (`services/bots/proposals`), so an approved proposal whose prices have moved
  places nothing and says so. The outcome message must never read as "done" when it isn't.
* **`require_trading_not_revoked` is not in this path.** It is an HTTP dependency, and this
  path has no request, so read-only mode is checked explicitly here.
"""
from __future__ import annotations

import datetime
import logging
import re
from typing import Any, Optional

from icici_breeze_backend.app.db.bots_migrate import (
    BOT_EXPIRY_INDEX_WRITER,
    BOT_HOLDINGS_WRITER,
)
from icici_breeze_backend.app.domain.bots import (
    TRANSIENT_REASON_CODES,
    TRANSIENT_RETRY_MINUTES,
    ApproveProposalRequest,
    ExpiryIndexWriterConfig,
    HoldingsWriterConfig,
    ProposalLeg,
    ReasonCode,
)
from icici_breeze_backend.app.repositories import bots as repo

_logger = logging.getLogger(__name__)

_CONFIG_MODEL = {
    BOT_HOLDINGS_WRITER: HoldingsWriterConfig,
    BOT_EXPIRY_INDEX_WRITER: ExpiryIndexWriterConfig,
}


def is_telegram_mode(config: Any) -> bool:
    return str(getattr(config, "approval_mode", "auto")) == "telegram"


def _reachable(user_id: str) -> Optional[str]:
    """The chat we can ask on, or None. A bot in `telegram` mode with no chat cannot run."""
    from icici_breeze_backend.app.repositories.user_telegram import get_status

    try:
        status = get_status(user_id)
    except Exception:  # noqa: BLE001
        _logger.warning("hitl: telegram status lookup failed", exc_info=True)
        return None
    if not status.get("alerts_enabled") or not status.get("telegram_chat_id"):
        return None
    return str(status["telegram_chat_id"])


def trading_allowed() -> bool:
    """Read-only mode, checked without a request.

    `require_trading_not_revoked` is a FastAPI dependency and there is no request on this
    path, so the same question is asked of the same source directly. Fails closed on an
    unreadable licence state -- an approval places orders, and "we could not tell" is not
    permission.
    """
    from icici_breeze_backend.app.services.deployment_license_status import (
        trading_mutations_allowed,
    )

    try:
        return bool(trading_mutations_allowed())
    except Exception:  # noqa: BLE001
        _logger.warning("hitl: could not read licence status", exc_info=True)
        return False


# --------------------------------------------------------------------------------------
# Asking
# --------------------------------------------------------------------------------------


def ask_about(
    user_id: str,
    bot_type: str,
    proposal: Any,
    *,
    ttl_minutes: int,
    chat_id: Optional[str] = None,
) -> bool:
    """Mint a token for an existing proposal and send it with its buttons.

    Split out of `propose` so the drift path can reuse it. When an approval is refused
    because the market moved, the re-price has *already* left a fresh proposal at current
    prices -- exactly the thing the user should be looking at -- and the useful move is to
    ask about that one rather than strand it and make them wait for the next scheduler tick.
    """
    from icici_breeze_backend.app.services import telegram_alerts
    from icici_breeze_backend.app.services.telegram_link_portal import (
        register_approval_token,
    )

    chat = chat_id or _reachable(user_id)
    if not chat:
        return False

    # The previous ask's token is about to be burned, and its message would otherwise keep
    # live-looking buttons right above the new one.
    retire_open_asks(
        user_id,
        bot_type,
        "⌛ *Superseded* — a newer proposal follows below. Nothing was placed from this one.",
    )
    token = repo.issue_approval_token(
        user_id=user_id,
        bot_type=bot_type,
        proposal_id=proposal.id,
        chat_id=chat,
        ttl_minutes=ttl_minutes,
    )
    # Registered before the message goes out: a button whose token the portal cannot route
    # is a dead control, and the user would tap it on an expiry morning and get nothing.
    register_approval_token(token, ttl_minutes * 60)

    deadline = (proposal.expires_at or "")[11:16] or f"+{ttl_minutes}m"
    return telegram_alerts.notify_bot_proposal(
        user_id,
        bot_type=bot_type,
        proposal=proposal,
        deadline=f"{deadline} IST",
        token=token,
        record_message=lambda message_id, text: repo.set_approval_message(
            token, message_id, text
        ),
    )


def propose(
    user_id: str,
    bot_type: str,
    *,
    run_id: str,
    legs: list[ProposalLeg],
    totals: dict[str, Any],
    ttl_minutes: int,
    detail: Optional[dict[str, Any]] = None,
) -> bool:
    """Persist the proposal, mint its token, and send it. False if the user was not reached.

    The run is finished as `proposed` immediately rather than left `running`: a run awaiting
    a human is not in flight, and leaving it running would put it in front of the stale-run
    reaper, which would kill a proposal the user was about to approve.
    """
    chat_id = _reachable(user_id)
    if not chat_id:
        repo.finish_run(
            run_id,
            status="skipped",
            reason_code=ReasonCode.APPROVAL_UNREACHABLE,
            reason_text=(
                "This bot asks for approval on Telegram, but no chat is linked (or alerts "
                "are switched off), so it could not ask and placed nothing."
            ),
            detail=detail,
        )
        return False

    proposal = repo.create_proposal(
        run_id=run_id,
        user_id=user_id,
        bot_type=bot_type,
        legs=legs,
        totals=totals,
        ttl_minutes=ttl_minutes,
    )
    sent = ask_about(user_id, bot_type, proposal, ttl_minutes=ttl_minutes, chat_id=chat_id)
    if not sent:
        repo.resolve_proposal(
            user_id,
            proposal.id,
            status="expired",
            note="The approval request could not be delivered to Telegram.",
        )
        repo.finish_run(
            run_id,
            status="skipped",
            reason_code=ReasonCode.APPROVAL_UNREACHABLE,
            reason_text="Telegram would not accept the approval request; nothing was placed.",
            detail=detail,
        )
        return False

    repo.finish_run(
        run_id,
        status="proposed",
        reason_code=ReasonCode.AWAITING_APPROVAL,
        reason_text=f"{len(legs)} leg(s) sent to Telegram for approval.",
        detail={**(detail or {}), "proposal_id": proposal.id},
    )
    return True


def next_action(user_id: str, bot_type: str, config: Any, *, now: datetime.datetime) -> str:
    """`"propose"` or `"wait"` — what the scheduler should do on this tick.

    The gates, in the order they can be answered cheaply:

    1. **Already committed today** — placed, failed, or definitively stood down. This uses
       `has_committed_run_today`, *not* `has_terminal_run_today`: a `proposed` run is an ask,
       not an act, and treating it as terminal would end the day at the first proposal.
       Transient pricing misses are excused on the same principle — a chain that had not
       warmed decided nothing either — so the retryable set is passed through here as well.
       It has to be: `decide()` and this function are two gates in series, and a retry the
       scheduler allows would be silently cancelled if this one still called the day over.
    2. **Past the cutoff** — the window has shut. Logged once, here, so the run log's last
       word is not `awaiting_approval` hours after the chance was gone.
    3. **A proposal is still outstanding.** Reading it retires it first if it has expired,
       which is also what turns a stale ask into the next re-ask.
    4. **The last ask is younger than the nag interval** — the user is asked on the cadence
       they configured for nags, not once every thirty-second tick.
    """
    if repo.has_committed_run_today(
        user_id,
        bot_type,
        retryable_reason_codes=TRANSIENT_REASON_CODES,
        retry_after_minutes=TRANSIENT_RETRY_MINUTES,
    ):
        return "wait"
    if now.time() >= _hhmm(config.cutoff_ist):
        _note_timeout(user_id, bot_type, config, now=now)
        return "wait"
    if repo.get_pending_proposal(user_id, bot_type) is not None:
        return "wait"
    last = repo.last_proposal_at(user_id, bot_type)
    if last is not None and last.date() == now.date():
        if (now - last).total_seconds() / 60.0 < float(config.nag_interval_minutes):
            return "wait"
    return "propose"


def _note_timeout(user_id: str, bot_type: str, config: Any, *, now: datetime.datetime) -> None:
    """Close out a day that was asked about but never answered.

    Only fires when this bot actually proposed today — a bot that never got as far as
    asking had no approval to time out, and logging one would invent an event. Writing a
    `skipped` run makes `has_committed_run_today` true, so this logs exactly once however
    many ticks follow the cutoff.
    """
    last = repo.last_proposal_at(user_id, bot_type)
    if last is None or last.date() != now.date():
        return
    repo.expire_stale_proposals(user_id)
    retire_open_asks(
        user_id,
        bot_type,
        f"⌛ *Expired* — no approval before the {config.cutoff_ist} cutoff. Nothing was placed.",
    )
    run_id = repo.start_run(user_id, bot_type, "schedule")
    repo.finish_run(
        run_id,
        status="skipped",
        reason_code=ReasonCode.APPROVAL_TIMEOUT,
        reason_text=(
            f"No approval arrived before the {config.cutoff_ist} cutoff, so nothing was "
            "placed today."
        ),
    )


def _hhmm(value: str) -> datetime.time:
    hh, mm = str(value).split(":")
    return datetime.time(int(hh), int(mm))


# --------------------------------------------------------------------------------------
# The proposal message itself
# --------------------------------------------------------------------------------------


def _edit_ask(row: dict[str, Any], footer: str) -> None:
    """Rewrite a proposal message as answered: its text, a status line, no Approve/Reject."""
    from icici_breeze_backend.app.services.telegram_alerts import answered_keyboard
    from icici_breeze_backend.app.services.telegram_client import edit_message_text_sync

    try:
        edit_message_text_sync(
            str(row["chat_id"]),
            int(row["message_id"]),
            f"{row.get('message_text') or ''}\n\n{footer}",
            reply_markup=answered_keyboard(),
        )
    except Exception:  # noqa: BLE001 -- a failed edit costs the buttons, never the trade
        _logger.warning("hitl: could not edit the proposal message", exc_info=True)


def _retire(row: Optional[dict[str, Any]], footer: str) -> bool:
    """Retire one proposal message's buttons, once. False if already retired (or none)."""
    if not row or not repo.close_approval_message(str(row["token"])):
        return False
    _edit_ask(row, footer)
    return True


def retire_open_asks(user_id: str, bot_type: str, footer: str) -> None:
    """Retire every proposal message still showing buttons for this bot.

    Buttons on an ask that can no longer be answered are the worst kind of control: a tap
    on one reaches nothing, and on a phone the only reply is a toast that fades unread.
    Never raises -- this runs beside proposals and approvals, not instead of them.
    """
    try:
        rows = repo.open_approval_messages(user_id, bot_type)
    except Exception:  # noqa: BLE001
        _logger.warning("hitl: could not list open proposal messages", exc_info=True)
        return
    for row in rows:
        _retire(row, footer)


# --------------------------------------------------------------------------------------
# Answering
# --------------------------------------------------------------------------------------


def handle_callback(event: dict[str, Any]) -> None:
    """Act on one Approve/Reject tap routed to us by the portal.

    Runs off the event loop (SQLite writes and broker calls both block). Never raises: one
    bad callback must not take down the claim loop that delivers the rest.

    The proposal message is edited FIRST, before any broker call: its buttons go and a
    "placing orders now" line appears. Placement takes a while (a plan rebuild and a dozen
    serialized freeze-sliced orders), and a tap with no visible reaction for that long reads
    as a tap that did not register -- so the user taps again.
    """
    from icici_breeze_backend.app.core.timezone import now_ist
    from icici_breeze_backend.app.services import telegram_alerts
    from icici_breeze_backend.app.services.telegram_client import send_message_sync

    token = str(event.get("token") or "")
    chat_id = str(event.get("chat_id") or "")
    action = str(event.get("action") or "")
    if not token or not chat_id:
        return

    claim = repo.consume_approval_token(token)
    if claim is None:
        # Expired, already tapped, or superseded by a newer proposal. Say which is not
        # possible -- the token is gone -- so say the one thing that is always true.
        _retire(
            repo.open_approval_message(token),
            "⌛ *No longer valid* — nothing was placed from this proposal.",
        )
        send_message_sync(
            chat_id,
            "This approval is no longer valid — it was already answered, or the prices it "
            "was based on went stale. Nothing was placed. Open the app to see the latest.",
        )
        return

    if str(claim["chat_id"]) != chat_id:
        # The portal routed by token, so a mismatch here means the tap did not come from the
        # chat the proposal was sent to. Refuse rather than place on someone else's say-so.
        _logger.warning("hitl: approval token used from an unexpected chat; refusing")
        return

    user_id = str(claim["user_id"])
    bot_type = str(claim["bot_type"])
    ask = repo.open_approval_message(token)
    stamp = now_ist().strftime("%H:%M")

    if action == "r":
        _retire(ask, f"❌ *Rejected at {stamp}* — nothing was placed.")
        repo.resolve_proposal(
            user_id, str(claim["proposal_id"]), status="rejected", note="Rejected on Telegram."
        )
        run_id = repo.start_run(user_id, bot_type, "schedule")
        repo.finish_run(
            run_id,
            status="skipped",
            reason_code=ReasonCode.APPROVAL_REJECTED,
            reason_text="You rejected the proposal on Telegram; nothing was placed.",
        )
        telegram_alerts.notify_bot_approval_outcome(
            user_id,
            "❌ *Rejected* — nothing was placed, and this bot will not ask again today.",
        )
        return

    if action != "a":
        return

    if not trading_allowed():
        _retire(ask, "🔒 *Not placed* — read-only mode.")
        telegram_alerts.notify_bot_approval_outcome(
            user_id,
            "🔒 *Read-only mode* — your licence does not currently allow trading, so "
            "nothing was placed. Nothing about your positions has changed.",
        )
        return

    retired = _retire(
        ask,
        f"⏳ *Approved at {stamp} — placing orders now…*\n"
        "_The result follows in a new message._",
    )
    _approve_and_report(user_id, bot_type, str(claim["proposal_id"]))
    if retired:
        # "Placing orders now" would read as still in flight for ever after the result.
        _edit_ask(ask, f"☑️ *Approved at {stamp}* — the result is in the message below.")


def _ask_again(user_id: str, bot_type: str) -> None:
    """Follow a "the market moved" refusal with the proposal that reflects where it moved to.

    Bot 1 re-prices by running a real scan, and a scan leaves a fresh proposal behind at
    current prices -- which is precisely what the user should now be looking at. Without
    this it would sit there unmentioned: `next_action` treats a pending proposal as "already
    asked" and would wait for it to expire *and then* for the nag interval, so a single tick
    of drift could cost half an hour of the window and the user would never have been shown
    the prices that replaced theirs.

    Bot 2 re-derives its plan without persisting one, so there is nothing to adopt and the
    scheduler's own cadence takes over. That asymmetry is fine: the point is never to strand
    a proposal, not to guarantee an immediate re-ask.
    """
    fresh = repo.get_pending_proposal(user_id, bot_type)
    if fresh is None:
        return
    bot = repo.get_or_create_bot(user_id, bot_type)
    config = _CONFIG_MODEL[bot_type](**bot.config)
    ttl = int(
        getattr(config, "proposal_ttl_minutes", None) or config.nag_interval_minutes
    )
    if not ask_about(user_id, bot_type, fresh, ttl_minutes=ttl):
        # Could not deliver it, so do not leave it pending and silently blocking the
        # scheduler's re-ask -- retire it and let the normal cadence try again.
        repo.resolve_proposal(
            user_id,
            fresh.id,
            status="expired",
            note="The re-priced proposal could not be delivered to Telegram.",
        )


def _approve_and_report(user_id: str, bot_type: str, proposal_id: str) -> None:
    from icici_breeze_backend.app.services import telegram_alerts
    from icici_breeze_backend.app.services.bots import proposals as svc

    pending = repo.get_pending_proposal(user_id, bot_type)
    if pending is None or pending.id != proposal_id:
        telegram_alerts.notify_bot_approval_outcome(
            user_id,
            "⌛ *Too late* — those prices expired before the approval arrived, so nothing "
            "was placed. A fresh proposal will follow if the window is still open.",
        )
        return

    payload = ApproveProposalRequest(leg_indexes=list(range(len(pending.legs))))
    try:
        result = svc.approve(user_id, bot_type, payload, trigger="telegram")
    except svc.ApprovalRefused as e:
        telegram_alerts.notify_bot_approval_outcome(
            user_id, f"⚠️ *Nothing was placed.*\n\n{e.message}"
        )
        if e.repriceable:
            _ask_again(user_id, bot_type)
        else:
            _logger.info("hitl: approval refused and not repriceable: %s", e.reason_code)
        return
    except Exception:  # noqa: BLE001
        _logger.exception("hitl: approval failed for user=%s bot=%s", user_id, bot_type)
        telegram_alerts.notify_bot_approval_outcome(
            user_id,
            "⚠️ *The approval failed unexpectedly.* Some orders may have gone out — check "
            "the Order Book before placing anything yourself.",
        )
        return

    # The stop may have armed while the result was being assembled -- the order feed does
    # not wait for this message -- so report where it stands now, not where it stood.
    from icici_breeze_backend.app.services.bots import exit_arming

    for stop in result.stops:
        if stop.status == "pending" and exit_arming.current_status(stop.pending_exit_id) == "armed":
            stop.status = "armed"
    telegram_alerts.notify_bot_approval_outcome(user_id, format_outcome(result))


def _md(text: Any) -> str:
    """Escape Telegram legacy-Markdown metacharacters in text we did not write.

    A broker error carrying one stray `_` makes Telegram refuse the whole message -- and the
    message it refuses is the one saying what happened to the user's orders.
    """
    return re.sub(r"([_*`\[])", r"\\\1", str(text))


def _label(stock_code: str) -> str:
    from icici_breeze_backend.app.services.bots.expiry_index_writer import INDEX_LABEL

    return INDEX_LABEL.get(stock_code, stock_code)


def _fill_note(p: Any) -> str:
    if p.error or p.filled_quantity is None:
        return ""
    if p.filled_quantity >= p.quantity:
        return " — filled"
    if p.filled_quantity <= 0:
        return " — working, nothing filled yet"
    return f" — working, {p.filled_quantity:,} of {p.quantity:,} filled"


def _stop_line(stop: Any) -> str:
    label = _label(stop.stock_code)
    if stop.status == "armed":
        return f"🛡 *{label} stop armed.* Automatic exit is live."
    if stop.status == "pending":
        return (
            f"⏳ *{label} stop not armed yet.* It arms automatically the moment every order "
            "has filled, and you will get a message here when it does. Until then this "
            "position has *no automatic exit*. To protect it sooner, cancel the unfilled "
            "rest in the Order Book (the stop then arms on what filled) or set PB/SL "
            "yourself."
        )
    if stop.status == "skipped":
        # Final, not a retry -- unlike the generic "failed" branch below, nothing will arm
        # this later, so the message must not promise a retry that is not coming.
        return (
            f"⚠️ *{label} stop NOT armed:* {_md(stop.detail or 'existing open position in this group')}\n"
            "This position has no automatic exit, and none will be armed automatically -- "
            "set PB/SL yourself in Portfolio if you want it protected."
        )
    return (
        f"🚨 *{label} stop NOT armed:* {_md(stop.detail or 'unknown error')}\n"
        "This position has no automatic exit. Retrying every 2 minutes; set PB/SL "
        "yourself in Portfolio if you would rather not wait."
    )


def format_outcome(result: Any) -> str:
    """The reply to an approval: what reached the exchange, and separately, the stop.

    Placement and protection are reported as two facts because they are two facts. The old
    single error field carried the stop's failure onto every leg, so a fully placed strangle
    read "Partly placed ... 0 of 2 leg(s) placed" -- which invites the user to place the
    trade again by hand on top of the one already open.
    """
    ok = [p for p in result.placed if not p.error]
    went_out = [p for p in result.placed if p.order_ids]
    if not went_out:
        head = "❌ *Nothing was placed*"
    elif result.all_succeeded:
        head = "✅ *Orders placed*"
    else:
        head = "⚠️ *Only part of the trade was placed*"
    lines = [head, ""]
    for p in result.placed:
        side = "CE" if str(p.right).lower().startswith("c") else "PE"
        contract = f"{p.stock_code} {p.strike_price:g} {side}"
        if p.error and p.order_ids:
            lines.append(f"⚠️ {contract} — partly placed: {_md(p.error)}")
        elif p.error:
            lines.append(f"❌ {contract} — not placed: {_md(p.error)}")
        else:
            lines.append(
                f"✅ SELL {contract} ×{p.quantity:,} @ ₹{p.limit_price:g}{_fill_note(p)}"
            )
    for stop in result.stops:
        lines += ["", _stop_line(stop)]
    if went_out and not result.all_succeeded:
        lines += [
            "",
            "_Only the legs marked ✅ (and any partly placed) went out. Review the position "
            "before placing anything yourself._",
        ]
    lines += ["", f"{len(ok)} of {len(result.placed)} leg(s) placed."]
    return "\n".join(lines)


__all__ = [
    "ask_about",
    "format_outcome",
    "handle_callback",
    "is_telegram_mode",
    "next_action",
    "propose",
    "retire_open_asks",
    "trading_allowed",
]
