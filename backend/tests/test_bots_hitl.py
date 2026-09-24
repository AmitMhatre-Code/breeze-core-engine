"""Semi-autonomous bots: the Telegram approval loop (app.services.bots.hitl).

The assertions here are mostly about what does NOT happen. A proposal must not count as
the bot having traded, a stale or replayed tap must not place anything, and a bot that
cannot reach its user must fail loudly rather than look like a quiet day.
"""
from __future__ import annotations

import datetime

import pytest

from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.db.bots_migrate import (
    BOT_EXPIRY_INDEX_WRITER,
    BOT_HOLDINGS_WRITER,
    ensure_bots_tables,
)
from icici_breeze_backend.app.domain.bots import (
    HoldingsWriterConfig,
    ProposalLeg,
    ReasonCode,
)
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots import hitl


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_bots_tables(path)
    ensure_bots_tables(path)  # migration must be idempotent
    return path


def _leg(**kw):
    base = dict(
        stock_code="ITC",
        right="call",
        expiry_display="24-Sep-2026",
        strike_price=280.0,
        lots=1,
        lot_size=1725,
        quantity=1725,
        premium_per_share=4.25,
        premium_total=4.25 * 1725,
    )
    base.update(kw)
    return ProposalLeg(**base)


def _propose_row(user_id="u1", bot_type=BOT_HOLDINGS_WRITER):
    """A run + proposal in the shape `hitl.propose` leaves behind."""
    run_id = repo.start_run(user_id, bot_type, "schedule")
    proposal = repo.create_proposal(
        run_id=run_id,
        user_id=user_id,
        bot_type=bot_type,
        legs=[_leg()],
        totals={"premium_total": 7331.25},
        ttl_minutes=15,
    )
    repo.finish_run(
        run_id,
        status="proposed",
        reason_code=ReasonCode.AWAITING_APPROVAL,
        reason_text="1 leg(s) sent to Telegram for approval.",
    )
    return run_id, proposal


# --- the day-gate ----------------------------------------------------------------------


def test_a_proposal_is_not_a_committed_run(db_path):
    """The whole re-proposal loop rests on this: asking is not acting.

    `has_terminal_run_today` deliberately still counts it, because it guards the fully
    autonomous path where any run row really does resolve the day.
    """
    _propose_row()

    assert repo.has_committed_run_today("u1", BOT_HOLDINGS_WRITER) is False
    assert repo.has_terminal_run_today("u1", BOT_HOLDINGS_WRITER) is True


@pytest.mark.parametrize("status", ["completed", "partial", "failed", "skipped"])
def test_a_resolved_run_is_committed(db_path, status):
    run_id = repo.start_run("u1", BOT_HOLDINGS_WRITER, "schedule")
    repo.finish_run(
        run_id, status=status, reason_code=ReasonCode.ORDERS_PLACED, reason_text="done"
    )

    assert repo.has_committed_run_today("u1", BOT_HOLDINGS_WRITER) is True


def test_a_run_in_flight_is_committed(db_path):
    """`running` must count, or a tick could start a second run alongside the first."""
    repo.start_run("u1", BOT_HOLDINGS_WRITER, "schedule")

    assert repo.has_committed_run_today("u1", BOT_HOLDINGS_WRITER) is True


def test_committed_runs_are_scoped_per_bot(db_path):
    run_id = repo.start_run("u1", BOT_HOLDINGS_WRITER, "schedule")
    repo.finish_run(
        run_id, status="completed", reason_code=ReasonCode.ORDERS_PLACED, reason_text="done"
    )

    assert repo.has_committed_run_today("u1", BOT_EXPIRY_INDEX_WRITER) is False


# --- approval tokens -------------------------------------------------------------------


def _issue(user_id="u1", bot_type=BOT_HOLDINGS_WRITER, chat_id="900", ttl=15):
    _, proposal = _propose_row(user_id, bot_type)
    token = repo.issue_approval_token(
        user_id=user_id,
        bot_type=bot_type,
        proposal_id=proposal.id,
        chat_id=chat_id,
        ttl_minutes=ttl,
    )
    return token, proposal


def test_an_approval_token_is_single_use(db_path):
    token, proposal = _issue()

    first = repo.consume_approval_token(token)
    assert first is not None
    assert first["proposal_id"] == proposal.id
    assert repo.consume_approval_token(token) is None


def test_an_expired_approval_token_is_refused(db_path):
    _, proposal = _propose_row()
    token = repo.issue_approval_token(
        user_id="u1",
        bot_type=BOT_HOLDINGS_WRITER,
        proposal_id=proposal.id,
        chat_id="900",
        ttl_minutes=1,
    )
    # Reach past the clock rather than sleeping: the TTL is minutes.
    with repo._connect() as conn:
        conn.execute(
            "UPDATE bot_approval_tokens SET expires_at = '2000-01-01 00:00:00' WHERE token = ?",
            (token,),
        )
        conn.commit()

    assert repo.consume_approval_token(token) is None


def test_a_new_proposal_burns_the_previous_token(db_path):
    """A re-proposal supersedes the prices, so the old tap must stop authorising anything —
    otherwise a user scrolling back could approve a snapshot the bot has already replaced."""
    stale, _ = _issue()
    fresh, _ = _issue()

    assert repo.consume_approval_token(stale) is None
    assert repo.consume_approval_token(fresh) is not None


def test_outstanding_tokens_drive_the_claim_loop(db_path):
    assert repo.has_outstanding_approval_token() is False
    token, _ = _issue()
    assert repo.has_outstanding_approval_token() is True
    repo.consume_approval_token(token)
    assert repo.has_outstanding_approval_token() is False


# --- next_action -----------------------------------------------------------------------


def _config(**kw):
    return HoldingsWriterConfig(approval_mode="telegram", **kw)


def _at(hh, mm):
    return datetime.datetime.combine(datetime.date.today(), datetime.time(hh, mm))


def test_proposes_when_nothing_has_happened_yet(db_path):
    assert (
        hitl.next_action("u1", BOT_HOLDINGS_WRITER, _config(), now=_at(9, 30)) == "propose"
    )


def test_waits_while_a_proposal_is_outstanding(db_path):
    _propose_row()

    assert hitl.next_action("u1", BOT_HOLDINGS_WRITER, _config(), now=_at(9, 30)) == "wait"


def test_waits_until_the_nag_interval_has_passed(db_path):
    """An expired proposal is not licence to ask again immediately — the user is asked on
    the cadence they configured, not once every thirty-second tick."""
    _propose_row()
    repo.resolve_proposal("u1", repo.get_pending_proposal("u1", BOT_HOLDINGS_WRITER).id,
                          status="expired")

    config = _config(nag_interval_minutes=15)
    assert hitl.next_action("u1", BOT_HOLDINGS_WRITER, config, now=_at(9, 30)) == "wait"


def test_reproposes_once_the_interval_has_passed(db_path):
    # Both times are fixed and inside the window. This used to backdate against
    # `datetime.datetime.now()` and pass it as `now`, so the test failed outright whenever
    # the suite ran after the config's 12:00 cutoff -- and, off IST, compared a local
    # timestamp against the IST one the repo writes.
    _propose_row()
    pending = repo.get_pending_proposal("u1", BOT_HOLDINGS_WRITER)
    repo.resolve_proposal("u1", pending.id, status="expired")
    with repo._connect() as conn:
        conn.execute(
            "UPDATE bot_proposals SET created_at = ? WHERE id = ?",
            (_at(9, 0).strftime("%Y-%m-%d %H:%M:%S"), pending.id),
        )
        conn.commit()

    config = _config(nag_interval_minutes=15)
    assert hitl.next_action("u1", BOT_HOLDINGS_WRITER, config, now=_at(9, 40)) == "propose"


def _finished_run(bot_type, reason_code, *, status="skipped", minutes_ago=0.0, user_id="u1"):
    """A closed run, optionally backdated, in the shape the scheduler leaves behind."""
    run_id = repo.start_run(user_id, bot_type, "schedule")
    repo.finish_run(run_id, status=status, reason_code=reason_code, reason_text="t")
    if minutes_ago:
        # Backdated against the same IST clock `finish_run` stamps with, not the local one:
        # under any other TZ those differ by hours and the interval under test would be
        # whatever the offset happened to be.
        stamp = (
            now_ist().replace(tzinfo=None) - datetime.timedelta(minutes=minutes_ago)
        ).strftime("%Y-%m-%d %H:%M:%S")
        with repo._connect() as conn:
            conn.execute("UPDATE bot_runs SET finished_at = ? WHERE id = ?", (stamp, run_id))
            conn.commit()
    return run_id


def test_a_pricing_miss_does_not_end_the_day(db_path):
    """The bug this guards: one "No spot price available" at 09:30 used to be terminal.

    A chain that had not warmed decided nothing about the market, so once the short retry
    interval has passed the bot must get the rest of its window back.
    """
    _finished_run(BOT_HOLDINGS_WRITER, ReasonCode.QUOTE_UNAVAILABLE, minutes_ago=5)

    assert (
        hitl.next_action("u1", BOT_HOLDINGS_WRITER, _config(), now=_at(9, 40)) == "propose"
    )


def test_a_pricing_miss_still_waits_out_its_retry_interval(db_path):
    """Retrying is not the same as retrying every thirty-second tick."""
    _finished_run(BOT_HOLDINGS_WRITER, ReasonCode.QUOTE_UNAVAILABLE, minutes_ago=0)

    assert hitl.next_action("u1", BOT_HOLDINGS_WRITER, _config(), now=_at(9, 40)) == "wait"


def test_a_real_answer_still_ends_the_day(db_path):
    """Only pricing misses are excused. "Nothing eligible" is a finding, not a hiccup —
    re-asking a question the bot already answered would be its own kind of broken."""
    _finished_run(BOT_HOLDINGS_WRITER, ReasonCode.NOTHING_ELIGIBLE, minutes_ago=90)

    assert hitl.next_action("u1", BOT_HOLDINGS_WRITER, _config(), now=_at(9, 40)) == "wait"


def test_a_run_still_in_flight_is_never_retried(db_path):
    """However it is coded, something is working on it right now — starting a second
    attempt alongside it is exactly the double-fire the day-gate exists to prevent."""
    repo.start_run("u1", BOT_HOLDINGS_WRITER, "schedule")

    assert hitl.next_action("u1", BOT_HOLDINGS_WRITER, _config(), now=_at(9, 40)) == "wait"


def test_a_pricing_miss_does_not_excuse_a_later_real_run(db_path):
    """A retryable row plus a committed one is still a committed day."""
    _finished_run(BOT_HOLDINGS_WRITER, ReasonCode.QUOTE_UNAVAILABLE, minutes_ago=30)
    _finished_run(
        BOT_HOLDINGS_WRITER, ReasonCode.ORDERS_PLACED, status="completed", minutes_ago=10
    )

    assert hitl.next_action("u1", BOT_HOLDINGS_WRITER, _config(), now=_at(9, 40)) == "wait"


def test_stands_down_after_a_committed_run(db_path):
    run_id = repo.start_run("u1", BOT_HOLDINGS_WRITER, "schedule")
    repo.finish_run(
        run_id, status="completed", reason_code=ReasonCode.ORDERS_PLACED, reason_text="done"
    )

    assert hitl.next_action("u1", BOT_HOLDINGS_WRITER, _config(), now=_at(9, 30)) == "wait"


def test_the_cutoff_closes_the_day_and_is_logged_once(db_path):
    """Without this the run log's last word on an unanswered day would read
    `awaiting_approval` long after the window shut."""
    _propose_row()
    config = _config(cutoff_ist="12:00")

    assert hitl.next_action("u1", BOT_HOLDINGS_WRITER, config, now=_at(12, 1)) == "wait"
    runs = repo.list_runs("u1", bot_type=BOT_HOLDINGS_WRITER)
    timeouts = [r for r in runs if r.reason_code == ReasonCode.APPROVAL_TIMEOUT]
    assert len(timeouts) == 1

    # Every later tick must find the day already closed rather than logging again.
    assert hitl.next_action("u1", BOT_HOLDINGS_WRITER, config, now=_at(12, 30)) == "wait"
    runs = repo.list_runs("u1", bot_type=BOT_HOLDINGS_WRITER)
    assert len([r for r in runs if r.reason_code == ReasonCode.APPROVAL_TIMEOUT]) == 1


def test_no_timeout_is_logged_for_a_day_that_never_asked(db_path):
    """A bot that never proposed had no approval to time out; logging one would invent an
    event the user never saw."""
    config = _config(cutoff_ist="12:00")

    assert hitl.next_action("u1", BOT_HOLDINGS_WRITER, config, now=_at(12, 1)) == "wait"
    assert repo.list_runs("u1", bot_type=BOT_HOLDINGS_WRITER) == []


# --- propose ---------------------------------------------------------------------------


def test_an_unreachable_user_is_a_logged_skip_not_a_silent_day(db_path, monkeypatch):
    monkeypatch.setattr(hitl, "_reachable", lambda user_id: None)
    run_id = repo.start_run("u1", BOT_HOLDINGS_WRITER, "schedule")

    sent = hitl.propose(
        "u1", BOT_HOLDINGS_WRITER, run_id=run_id, legs=[_leg()], totals={}, ttl_minutes=15
    )

    assert sent is False
    run = repo.list_runs("u1", bot_type=BOT_HOLDINGS_WRITER)[0]
    assert run.status == "skipped"
    assert run.reason_code == ReasonCode.APPROVAL_UNREACHABLE
    assert repo.get_pending_proposal("u1", BOT_HOLDINGS_WRITER) is None


def test_a_proposal_that_cannot_be_delivered_leaves_nothing_pending(db_path, monkeypatch):
    """A pending proposal the user was never shown would sit there blocking the re-ask
    while they had no way to answer it."""
    monkeypatch.setattr(hitl, "_reachable", lambda user_id: "900")
    monkeypatch.setattr(hitl, "register_approval_token", lambda *a, **k: True, raising=False)
    import icici_breeze_backend.app.services.telegram_link_portal as portal
    import icici_breeze_backend.app.services.telegram_alerts as alerts

    monkeypatch.setattr(portal, "register_approval_token", lambda *a, **k: True)
    monkeypatch.setattr(alerts, "notify_bot_proposal", lambda *a, **k: False)
    run_id = repo.start_run("u1", BOT_HOLDINGS_WRITER, "schedule")

    sent = hitl.propose(
        "u1", BOT_HOLDINGS_WRITER, run_id=run_id, legs=[_leg()], totals={}, ttl_minutes=15
    )

    assert sent is False
    assert repo.get_pending_proposal("u1", BOT_HOLDINGS_WRITER) is None
    run = repo.list_runs("u1", bot_type=BOT_HOLDINGS_WRITER)[0]
    assert run.reason_code == ReasonCode.APPROVAL_UNREACHABLE


# --- handling a tap --------------------------------------------------------------------


def test_a_stale_tap_places_nothing(db_path, monkeypatch):
    placed = []
    monkeypatch.setattr(hitl, "_approve_and_report", lambda *a: placed.append(a))
    sent = []
    import icici_breeze_backend.app.services.telegram_client as client

    monkeypatch.setattr(client, "send_message_sync", lambda *a, **k: sent.append(a) or True)

    hitl.handle_callback({"token": "nope", "chat_id": "900", "action": "a"})

    assert placed == []
    assert sent, "the user must be told the tap did nothing"


def test_a_tap_from_another_chat_is_refused(db_path, monkeypatch):
    """The portal routes by token, so a chat mismatch means the tap did not come from the
    chat the proposal was sent to."""
    token, _ = _issue(chat_id="900")
    placed = []
    monkeypatch.setattr(hitl, "_approve_and_report", lambda *a: placed.append(a))

    hitl.handle_callback({"token": token, "chat_id": "111", "action": "a"})

    assert placed == []


def test_reject_resolves_the_proposal_and_ends_the_day(db_path, monkeypatch):
    token, proposal = _issue()
    messages = []
    import icici_breeze_backend.app.services.telegram_alerts as alerts

    monkeypatch.setattr(
        alerts, "notify_bot_approval_outcome", lambda uid, text: messages.append(text)
    )

    hitl.handle_callback({"token": token, "chat_id": "900", "action": "r"})

    assert repo.get_proposal("u1", proposal.id).status == "rejected"
    run = repo.list_runs("u1", bot_type=BOT_HOLDINGS_WRITER)[0]
    assert run.reason_code == ReasonCode.APPROVAL_REJECTED
    assert repo.has_committed_run_today("u1", BOT_HOLDINGS_WRITER) is True
    assert messages and "Rejected" in messages[0]


def test_read_only_mode_blocks_an_approved_tap(db_path, monkeypatch):
    """`require_trading_not_revoked` is an HTTP dependency and this path has no request, so
    the licence has to be checked here or read-only mode would be bypassed entirely."""
    token, _ = _issue()
    monkeypatch.setattr(hitl, "trading_allowed", lambda: False)
    placed = []
    monkeypatch.setattr(hitl, "_approve_and_report", lambda *a: placed.append(a))
    messages = []
    import icici_breeze_backend.app.services.telegram_alerts as alerts

    monkeypatch.setattr(
        alerts, "notify_bot_approval_outcome", lambda uid, text: messages.append(text)
    )

    hitl.handle_callback({"token": token, "chat_id": "900", "action": "a"})

    assert placed == []
    assert messages and "Read-only" in messages[0]


def test_an_approved_tap_reaches_the_approval_service(db_path, monkeypatch):
    token, proposal = _issue()
    monkeypatch.setattr(hitl, "trading_allowed", lambda: True)
    seen = []
    monkeypatch.setattr(hitl, "_approve_and_report", lambda *a: seen.append(a))

    hitl.handle_callback({"token": token, "chat_id": "900", "action": "a"})

    assert seen == [("u1", BOT_HOLDINGS_WRITER, proposal.id)]


# --- the re-price scan's leftovers -----------------------------------------------------


def test_placing_retires_the_proposal_the_reprice_scan_left_behind(db_path):
    """Approving re-prices by running a real scan, and a scan creates a proposal. Once the
    orders are out that one is debris — left pending it would offer the same trade twice."""
    _, approved = _propose_row()
    # What `holdings_runner.run_scan` leaves behind mid-approval: a second proposal, which
    # supersedes the one being approved.
    run_id = repo.start_run("u1", BOT_HOLDINGS_WRITER, "manual")
    fresh = repo.create_proposal(
        run_id=run_id,
        user_id="u1",
        bot_type=BOT_HOLDINGS_WRITER,
        legs=[_leg()],
        totals={},
        ttl_minutes=15,
    )
    repo.resolve_proposal("u1", approved.id, status="placed", note="2 of 2 leg(s) placed.")

    assert repo.supersede_other_pending("u1", BOT_HOLDINGS_WRITER, approved.id) == 1

    assert repo.get_proposal("u1", fresh.id).status == "superseded"
    assert repo.get_proposal("u1", approved.id).status == "placed"
    assert repo.get_pending_proposal("u1", BOT_HOLDINGS_WRITER) is None


def test_the_approved_proposal_is_never_the_one_retired(db_path):
    _, approved = _propose_row()

    assert repo.supersede_other_pending("u1", BOT_HOLDINGS_WRITER, approved.id) == 0
    assert repo.get_proposal("u1", approved.id).status == "pending"


def test_retiring_leftovers_is_scoped_to_one_bot(db_path):
    _, mine = _propose_row(bot_type=BOT_HOLDINGS_WRITER)
    _, theirs = _propose_row(bot_type=BOT_EXPIRY_INDEX_WRITER)

    repo.supersede_other_pending("u1", BOT_HOLDINGS_WRITER, mine.id)

    assert repo.get_proposal("u1", theirs.id).status == "pending"


def test_a_drifted_approval_asks_again_about_the_repriced_proposal(db_path, monkeypatch):
    """Otherwise the fresh proposal sits there unmentioned: `next_action` reads a pending
    proposal as "already asked", so one tick of drift would cost the TTL *plus* the nag
    interval and the user would never see the prices that replaced theirs."""
    _, fresh = _propose_row()
    asked = []
    monkeypatch.setattr(
        hitl,
        "ask_about",
        lambda uid, bt, proposal, **kw: asked.append(proposal.id) or True,
    )

    hitl._ask_again("u1", BOT_HOLDINGS_WRITER)

    assert asked == [fresh.id]


def test_an_undeliverable_reask_does_not_leave_the_loop_blocked(db_path, monkeypatch):
    """A pending proposal the user was never shown would stall the scheduler's own re-ask
    for the whole of its TTL."""
    _, fresh = _propose_row()
    monkeypatch.setattr(hitl, "ask_about", lambda *a, **k: False)

    hitl._ask_again("u1", BOT_HOLDINGS_WRITER)

    assert repo.get_proposal("u1", fresh.id).status == "expired"
    assert repo.get_pending_proposal("u1", BOT_HOLDINGS_WRITER) is None


# --- the proposal message and the reply ------------------------------------------------


def _capture_edits(monkeypatch):
    import icici_breeze_backend.app.services.telegram_client as client

    edits = []
    monkeypatch.setattr(
        client,
        "edit_message_text_sync",
        lambda chat_id, message_id, text, reply_markup=None: (
            edits.append((message_id, text, reply_markup)) or True
        ),
    )
    return edits


def test_an_approve_tap_retires_the_buttons_before_placing_anything(db_path, monkeypatch):
    """Placement takes a while; a tap with no visible reaction reads as a tap that did not
    register. The message changes first, and its Approve button is gone for good."""
    token, _ = _issue()
    repo.set_approval_message(token, 77, "the proposal")
    monkeypatch.setattr(hitl, "trading_allowed", lambda: True)
    events = []
    edits = _capture_edits(monkeypatch)
    monkeypatch.setattr(
        hitl, "_approve_and_report", lambda *a: events.append(("placed", len(edits)))
    )

    hitl.handle_callback({"token": token, "chat_id": "900", "action": "a"})

    assert events == [("placed", 1)], "the message was edited before anything was placed"
    first_id, first_text, keyboard = edits[0]
    assert first_id == 77 and "placing orders now" in first_text
    assert "callback_data" not in str(keyboard), "no Approve/Reject may survive the tap"
    assert "the result is in the message below" in edits[-1][1]
    assert repo.open_approval_messages("u1", BOT_HOLDINGS_WRITER) == []


def test_a_reject_tap_says_so_on_the_proposal_itself(db_path, monkeypatch):
    token, _ = _issue()
    repo.set_approval_message(token, 78, "the proposal")
    edits = _capture_edits(monkeypatch)
    import icici_breeze_backend.app.services.telegram_alerts as alerts

    monkeypatch.setattr(alerts, "notify_bot_approval_outcome", lambda uid, text: None)

    hitl.handle_callback({"token": token, "chat_id": "900", "action": "r"})

    assert len(edits) == 1 and "Rejected at" in edits[0][1]


def test_a_new_ask_retires_the_previous_messages_buttons(db_path, monkeypatch):
    import icici_breeze_backend.app.services.telegram_alerts as alerts
    import icici_breeze_backend.app.services.telegram_link_portal as portal

    monkeypatch.setattr(portal, "register_approval_token", lambda *a, **k: True)
    message_ids = iter([101, 102])

    def fake_notify(user_id, *, record_message=None, **kw):
        record_message(next(message_ids), "an ask")
        return True

    monkeypatch.setattr(alerts, "notify_bot_proposal", fake_notify)
    edits = _capture_edits(monkeypatch)
    _, proposal = _propose_row()

    hitl.ask_about("u1", BOT_HOLDINGS_WRITER, proposal, ttl_minutes=15, chat_id="900")
    hitl.ask_about("u1", BOT_HOLDINGS_WRITER, proposal, ttl_minutes=15, chat_id="900")

    assert [(mid, "Superseded" in text) for mid, text, _ in edits] == [(101, True)]
    assert [r["message_id"] for r in repo.open_approval_messages("u1", BOT_HOLDINGS_WRITER)] == [102]


def _placed(right, strike, qty, filled, **kw):
    from icici_breeze_backend.app.domain.bots import PlacedLegResult

    base = dict(
        stock_code="NIFTY", right=right, strike_price=strike, expiry_display="15-Sep-2026",
        quantity=qty, limit_price=0.65, order_ids=["OID"], filled_quantity=filled,
    )
    base.update(kw)
    return PlacedLegResult(**base)


def test_a_placed_strangle_waiting_on_its_stop_never_reads_as_a_failure():
    """The 15-Sep-2026 reply: both legs out, one still filling, stop waiting. It read
    "Partly placed ... 0 of 2 leg(s) placed" -- an invitation to place the trade again."""
    from icici_breeze_backend.app.domain.bots import ApprovalResult, ExitStopResult

    result = ApprovalResult(
        proposal_id="p",
        all_succeeded=True,
        placed=[
            _placed("call", 24200, 14885, 14885),
            _placed("put", 22750, 14885, 4500),
        ],
        stops=[ExitStopResult(stock_code="NIFTY", expiry_display="15-Sep-2026", status="pending")],
    )

    text = hitl.format_outcome(result)

    assert text.startswith("✅ *Orders placed*")
    assert "2 of 2 leg(s) placed." in text
    assert "❌" not in text
    assert "24200 CE ×14,885 @ ₹0.65 — filled" in text
    assert "working, 4,500 of 14,885 filled" in text
    assert "stop not armed yet" in text
    assert "Only the legs marked" not in text


def test_a_rejected_leg_and_a_failed_stop_are_two_separate_lines():
    from icici_breeze_backend.app.domain.bots import ApprovalResult, ExitStopResult

    result = ApprovalResult(
        proposal_id="p",
        all_succeeded=False,
        placed=[
            _placed("call", 24200, 14885, None, order_ids=[], error="bad_price"),
            _placed("put", 22750, 14885, None),
        ],
        stops=[ExitStopResult(
            stock_code="NIFTY", expiry_display="15-Sep-2026", status="failed",
            detail="engine_down",
        )],
    )

    text = hitl.format_outcome(result)

    assert text.startswith("⚠️ *Only part of the trade was placed*")
    assert "❌ NIFTY 24200 CE — not placed: bad\\_price" in text, "broker text is escaped"
    assert "✅ SELL NIFTY 22750 PE" in text
    assert "stop NOT armed:* engine\\_down" in text
    assert "1 of 2 leg(s) placed." in text


def test_a_telegram_approval_is_logged_as_telegram_not_manual(db_path, monkeypatch):
    """The Activity log read "Manual" for an approval tapped on a phone: both doors into
    `proposals.approve` started their run with the same trigger."""
    from icici_breeze_backend.app.domain.bots import ApprovalResult
    from icici_breeze_backend.app.services.bots import proposals as svc
    import icici_breeze_backend.app.services.telegram_alerts as alerts

    _, proposal = _propose_row()
    seen = {}

    def fake_approve(user_id, bot_type, payload, **kw):
        seen.update(kw)
        return ApprovalResult(proposal_id=proposal.id, all_succeeded=True)

    monkeypatch.setattr(svc, "approve", fake_approve)
    monkeypatch.setattr(alerts, "notify_bot_approval_outcome", lambda uid, text: None)

    hitl._approve_and_report("u1", BOT_HOLDINGS_WRITER, proposal.id)

    assert seen == {"trigger": "telegram"}


def test_nothing_to_adopt_is_not_an_error(db_path, monkeypatch):
    """Bot 2 re-derives its plan without persisting one, so there is simply nothing there."""
    monkeypatch.setattr(hitl, "ask_about", lambda *a, **k: True)

    hitl._ask_again("u1", BOT_EXPIRY_INDEX_WRITER)  # must not raise



# -- backtest runs share the Activity table but never satisfy a live guard (#35) -----------


class TestBacktestRunsAreNotLiveRuns:
    def _row(self, user="u1", bot="momentum_long_scalper", trigger="backtest"):
        import uuid

        from icici_breeze_backend.app.repositories.bots import _connect
        from icici_breeze_backend.app.core.timezone import ist_timestamp

        with _connect() as conn:
            conn.execute(
                "INSERT INTO bot_runs (id, user_id, bot_type, trigger, status, started_at) "
                "VALUES (?, ?, ?, ?, 'completed', ?)",
                (str(uuid.uuid4()), user, bot, trigger, ist_timestamp()),
            )
            conn.commit()

    def test_a_backtest_row_does_not_stand_down_a_real_session(self, db_path):
        self._row(trigger="backtest")
        # The whole hazard: a replay must never make the scheduler think the bot already ran.
        assert repo.has_terminal_run_today("u1", "momentum_long_scalper") is False
        assert repo.has_committed_run_today("u1", "momentum_long_scalper") is False

    def test_a_real_run_still_stands_the_day_down(self, db_path):
        self._row(trigger="schedule")
        assert repo.has_terminal_run_today("u1", "momentum_long_scalper") is True
        assert repo.has_committed_run_today("u1", "momentum_long_scalper") is True
