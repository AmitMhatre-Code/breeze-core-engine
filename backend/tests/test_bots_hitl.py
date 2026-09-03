"""Semi-autonomous bots: the Telegram approval loop (app.services.bots.hitl).

The assertions here are mostly about what does NOT happen. A proposal must not count as
the bot having traded, a stale or replayed tap must not place anything, and a bot that
cannot reach its user must fail loudly rather than look like a quiet day.
"""
from __future__ import annotations

import datetime

import pytest

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


@pytest.mark.parametrize("status", ["completed", "failed", "skipped"])
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
    _propose_row()
    pending = repo.get_pending_proposal("u1", BOT_HOLDINGS_WRITER)
    repo.resolve_proposal("u1", pending.id, status="expired")
    with repo._connect() as conn:
        conn.execute(
            "UPDATE bot_proposals SET created_at = ? WHERE id = ?",
            ((datetime.datetime.now() - datetime.timedelta(minutes=40)).strftime(
                "%Y-%m-%d %H:%M:%S"), pending.id),
        )
        conn.commit()

    config = _config(nag_interval_minutes=15)
    assert hitl.next_action("u1", BOT_HOLDINGS_WRITER, config, now=datetime.datetime.now()) == "propose"


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


def test_nothing_to_adopt_is_not_an_error(db_path, monkeypatch):
    """Bot 2 re-derives its plan without persisting one, so there is simply nothing there."""
    monkeypatch.setattr(hitl, "ask_about", lambda *a, **k: True)

    hitl._ask_again("u1", BOT_EXPIRY_INDEX_WRITER)  # must not raise
