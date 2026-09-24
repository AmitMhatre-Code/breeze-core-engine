"""A run that traded and then fell short is `partial`, never `failed`.

The case that forced this: Bot 2's approved legs were placed, and only the stop was
declined because the group already held another open position. Both the bot card and the
Activity log said FAILED -- the one reading that is definitely wrong, because a live short
was open and the whole point of the message is to send the user to set PB/SL by hand.

`failed` now means nothing reached the exchange. `partial` means something did, and the
reason code says which half is missing.
"""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.db.bots_migrate import (
    BOT_EXPIRY_INDEX_WRITER,
    BOT_HOLDINGS_WRITER,
    ensure_bots_tables,
)
from icici_breeze_backend.app.domain.bots import (
    ApproveProposalRequest,
    ProposalLeg,
    ReasonCode,
)
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots import exit_arming
from icici_breeze_backend.app.services.bots import expiry_index_writer as bot2
from icici_breeze_backend.app.services.bots import proposals as svc


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_bots_tables(path)
    return path


class FakeProc:
    def get_strategy_builder_margin_source(self, user_id):
        return "span"


def _index_leg(**kw):
    base = dict(
        stock_code="BSESEN",
        right="put",
        expiry_display="24-Sep-2026",
        strike_price=80000.0,
        lots=1,
        lot_size=20,
        quantity=20,
        premium_per_share=90.0,
        premium_total=1800.0,
    )
    base.update(kw)
    return ProposalLeg(**base)


def _pending_index_proposal(user_id="u1"):
    run_id = repo.start_run(user_id, BOT_EXPIRY_INDEX_WRITER, "schedule")
    proposal = repo.create_proposal(
        run_id=run_id,
        user_id=user_id,
        bot_type=BOT_EXPIRY_INDEX_WRITER,
        legs=[_index_leg()],
        totals={"premium_total": 1800.0},
        ttl_minutes=15,
    )
    repo.finish_run(
        run_id,
        status="proposed",
        reason_code=ReasonCode.AWAITING_APPROVAL,
        reason_text="1 leg(s) sent to Telegram for approval.",
    )
    return proposal


def _plan():
    return bot2.FireResult(
        index_code="BSESEN",
        exchange_code="BFO",
        expiry_display="24-Sep-2026",
        right="put",
        strike_price=80000.0,
        lots=1,
        quantity=20,
        legs=[
            {
                "right": "put",
                "strike_price": 80000.0,
                "quantity": 20,
                "bid": 90.0,
                "premium_total": 1800.0,
            }
        ],
        premium_total=1800.0,
    )


def _arrange(monkeypatch, executed: bot2.FireResult):
    """Everything between the approval and the run row, stubbed out."""
    import icici_breeze_backend.app.services.processor as processor_mod

    monkeypatch.setattr(processor_mod, "processor", lambda: FakeProc())
    monkeypatch.setattr(bot2, "_available_margin", lambda proc, user_id: 5_000_000.0)
    monkeypatch.setattr(bot2, "plan_index", lambda *a, **k: _plan())
    monkeypatch.setattr(bot2, "execute_plan", lambda *a, **k: executed)
    monkeypatch.setattr(
        exit_arming, "fill_state", lambda ids: {"heard": False, "executed": 0, "terminal": False}
    )
    monkeypatch.setattr(svc.repo, "supersede_other_pending", lambda *a, **k: None)


def _executed(**kw):
    base = dict(
        index_code="BSESEN",
        exchange_code="BFO",
        expiry_display="24-Sep-2026",
        right="put",
        strike_price=80000.0,
        lots=1,
        quantity=20,
        legs=[{"right": "put", "strike_price": 80000.0, "quantity": 20, "bid": 90.0}],
        order_ids=["OID1"],
    )
    base.update(kw)
    return bot2.FireResult(**base)


def test_an_approved_trade_whose_stop_was_skipped_is_partial(db, monkeypatch):
    """The screenshot: "2 of 2 leg(s) placed ... SENSEX stop not armed", badged FAILED."""
    _pending_index_proposal()
    detail = "1 other open leg(s) already exist for BSESEN 24-Sep-2026"
    _arrange(
        monkeypatch,
        _executed(
            rule_id=None,
            reason_code=ReasonCode.EXIT_ARM_SKIPPED_EXISTING_POSITION,
            arm_error=detail,
            error=f"Position is OPEN but no stop was armed: {detail}",
        ),
    )

    svc.approve(
        "u1", BOT_EXPIRY_INDEX_WRITER, ApproveProposalRequest(leg_indexes=[0]),
        trigger="telegram",
    )

    run = repo.list_runs("u1")[0]
    assert run.status == "partial"
    assert run.reason_code == ReasonCode.EXIT_ARM_SKIPPED_EXISTING_POSITION
    assert "1 of 1 leg(s) placed" in run.reason_text
    assert "stop not armed" in run.reason_text
    # The leg itself is clean: the stop's problem must never be copied onto it, or the
    # report reads "0 of 1 placed" while the position is open.
    assert run.detail["legs"][0]["error"] is None
    assert run.detail["stops"][0]["status"] == "skipped"


def test_an_approved_trade_whose_stop_failed_is_partial(db, monkeypatch):
    """Same shape, different cause: `exit_arming` will retry this one, but the position is
    open either way and the run must say so."""
    _pending_index_proposal()
    _arrange(
        monkeypatch,
        _executed(
            rule_id=None,
            reason_code=ReasonCode.EXIT_ARM_FAILED,
            arm_error="engine down",
            error="Position is OPEN but its stop could not be armed: engine down",
        ),
    )

    svc.approve("u1", BOT_EXPIRY_INDEX_WRITER, ApproveProposalRequest(leg_indexes=[0]))

    run = repo.list_runs("u1")[0]
    assert run.status == "partial"
    assert run.reason_code == ReasonCode.EXIT_ARM_FAILED


def test_an_approved_trade_that_armed_cleanly_is_still_completed(db, monkeypatch):
    """The guard on the change: a clean run must not drift into the new middle state."""
    _pending_index_proposal()
    _arrange(monkeypatch, _executed(rule_id="rule-1"))

    svc.approve("u1", BOT_EXPIRY_INDEX_WRITER, ApproveProposalRequest(leg_indexes=[0]))

    run = repo.list_runs("u1")[0]
    assert run.status == "completed"
    assert run.reason_code == ReasonCode.ORDERS_PLACED


def test_a_run_that_placed_nothing_is_still_failed(db, monkeypatch):
    """`partial` must not swallow the real failure: no order ids means no position, and
    the user has nothing to go and protect."""
    _pending_index_proposal()
    _arrange(
        monkeypatch,
        _executed(order_ids=[], legs=[
            {
                "right": "put", "strike_price": 80000.0, "quantity": 20, "bid": 90.0,
                "order_ids": [], "error": "Rejected: insufficient funds",
            }
        ]),
    )

    svc.approve("u1", BOT_EXPIRY_INDEX_WRITER, ApproveProposalRequest(leg_indexes=[0]))

    run = repo.list_runs("u1")[0]
    assert run.status == "failed"
    assert run.reason_code == ReasonCode.ORDER_REJECTED


def test_a_partial_run_still_counts_as_the_bot_having_acted_today(db):
    """The day-gate reads the run row, not the status word. A bot that opened a position
    and could not protect it must not come back and open a second one."""
    run_id = repo.start_run("u1", BOT_HOLDINGS_WRITER, "schedule")
    repo.finish_run(
        run_id,
        status="partial",
        reason_code=ReasonCode.EXIT_ARM_SKIPPED_EXISTING_POSITION,
        reason_text="1 of 2 leg(s) placed",
    )

    assert repo.has_committed_run_today("u1", BOT_HOLDINGS_WRITER) is True
    assert repo.has_terminal_run_today("u1", BOT_HOLDINGS_WRITER) is True
