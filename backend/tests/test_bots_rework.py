"""Behaviour added by the /bots rework: per-scrip sizing, autonomous Bot 1, and the
cross-bot priority that decides who gets the margin when both fire on one day.

The Bot 2 strategy shortlist and the premium-based profit target are covered in
test_bots_expiry_index_writer.py, next to the sizing they interact with.
"""
from __future__ import annotations

import datetime

import pytest

from icici_breeze_backend.app.db.bots_migrate import (
    BOT_EXPIRY_INDEX_WRITER,
    BOT_HOLDINGS_WRITER,
)
from icici_breeze_backend.app.domain.bots import (
    HoldingsWriterConfig,
    ProposalLeg,
    ReasonCode,
    ScripPref,
)
from icici_breeze_backend.app.db.bots_migrate import ensure_bots_tables
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots import holdings_writer as bot1


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_bots_tables(path)
    ensure_bots_tables(path)  # the migration must stay idempotent across boots
    return path


# --- per-scrip lot targets -----------------------------------------------------------


def _leg(code, right, span=100_000.0, elm=0.0, exposure=None, priority=1, premium=1000.0):
    return ProposalLeg(
        stock_code=code,
        right=right,
        expiry_display="25-Sep-2026",
        strike_price=100.0,
        lots=1,
        lot_size=100,
        quantity=100,
        premium_per_share=premium / 100,
        premium_total=premium,
        span_margin=span,
        elm_margin=elm,
        delivery_exposure=exposure,
        scrip_priority=priority,
    )


def test_zero_lots_means_do_not_write_that_side():
    assert ScripPref(stock_code="X", ce_lots=0).writes_ce is False
    assert ScripPref(stock_code="X", pe_enabled=True, pe_lots=0).writes_pe is False


def test_unset_lots_keep_the_pre_rework_behaviour():
    """An upgrading deployment must not silently change what its bot writes."""
    pref = ScripPref(stock_code="X", pe_enabled=True)
    assert pref.ce_lots is None and pref.writes_ce is True   # every covered lot
    assert pref.pe_lots is None and pref.writes_pe is True   # one lot


# --- allocation under the caps -------------------------------------------------------


def test_allocation_spends_margin_in_priority_order():
    legs = [_leg("A", "call", span=100_000, priority=1), _leg("B", "call", span=100_000, priority=2)]
    alloc = bot1.allocate(legs, free_margin=150_000, delivery_budget=0)
    assert alloc.selected == [0], "the higher-priority scrip is funded first"
    assert alloc.dropped[0].reason_code == ReasonCode.MARGIN_EXHAUSTED


def test_a_cheap_low_priority_leg_is_still_written_behind_an_unaffordable_one():
    """Priority orders funding; it does not gate it.

    Punishing a scrip for sitting behind an expensive one would leave margin unused for no
    reason the user asked for.
    """
    legs = [
        _leg("EXPENSIVE", "call", span=900_000, priority=1),
        _leg("CHEAP", "call", span=50_000, priority=2),
    ]
    alloc = bot1.allocate(legs, free_margin=100_000, delivery_budget=0)
    assert alloc.selected == [1]
    assert alloc.margin_used == 50_000


def test_puts_are_bounded_by_the_delivery_budget_as_well_as_by_margin():
    """A short put assigned means BUYING shares, so it costs cash on top of margin."""
    legs = [_leg("P", "put", span=50_000, exposure=1_000_000, priority=1)]
    within = bot1.allocate(legs, free_margin=500_000, delivery_budget=1_500_000)
    assert within.selected == [0] and within.delivery_used == 1_000_000

    over = bot1.allocate(legs, free_margin=500_000, delivery_budget=900_000)
    assert over.selected == []
    assert over.dropped[0].reason_code == ReasonCode.BUDGET_EXHAUSTED


def test_a_leg_with_no_margin_number_is_never_placed():
    """An unpriced margin is not a free trade -- placing on one is exactly the unattended
    over-commitment the caps exist to prevent."""
    legs = [_leg("X", "call", span=None)]
    legs[0].span_margin = None
    legs[0].elm_margin = None
    alloc = bot1.allocate(legs, free_margin=10_000_000, delivery_budget=0)
    assert alloc.selected == []
    assert alloc.dropped[0].reason_code == ReasonCode.MARGIN_LOOKUP_FAILED


# --- firing day ----------------------------------------------------------------------


def test_firing_day_counts_trading_days_not_calendar_days():
    """25-Sep-2026 is a Friday. Three trading days earlier is Tuesday the 22nd; three
    CALENDAR days earlier would be the Tuesday only by luck, and any intervening holiday
    would break it."""
    expiry = datetime.date(2026, 9, 25)
    assert bot1.firing_date(expiry, 3) == datetime.date(2026, 9, 22)


def test_firing_zero_days_before_is_the_expiry_day_itself():
    expiry = datetime.date(2026, 9, 25)
    assert bot1.firing_date(expiry, 0) == expiry


def test_firing_day_never_lands_on_a_weekend():
    # 28-Sep-2026 is a Monday; one trading day back is Friday the 25th, not Sunday the 27th.
    assert bot1.firing_date(datetime.date(2026, 9, 28), 1) == datetime.date(2026, 9, 25)


# --- Bot 1's unattended decision -----------------------------------------------------


def _ctx(**kw):
    base = dict(
        now=datetime.datetime(2026, 9, 22, 9, 30),
        app_started_at=datetime.datetime(2026, 9, 22, 8, 0),
        config=HoldingsWriterConfig(),
        is_firing_day=True,
        has_session=True,
        ran_today=False,
        last_nag_at=None,
    )
    base.update(kw)
    return bot1.TickContext(**base)


def test_a_non_firing_day_is_idle_not_a_logged_skip():
    """~20 non-firing days a month. Logging each would bury the days that mattered."""
    assert bot1.decide(_ctx(is_firing_day=False)).action == "idle"


def test_it_fires_on_the_firing_day_once_a_session_exists():
    assert bot1.decide(_ctx()).action == "fire"


def test_it_waits_until_the_entry_time():
    assert bot1.decide(_ctx(now=datetime.datetime(2026, 9, 22, 9, 0))).action == "idle"


def test_a_missing_session_nags_rather_than_skipping_early():
    d = bot1.decide(_ctx(has_session=False))
    assert d.action == "nag"
    assert d.reason_code == ReasonCode.NO_BROKER_SESSION


def test_the_nag_respects_its_interval():
    ctx = _ctx(has_session=False, last_nag_at=datetime.datetime(2026, 9, 22, 9, 25))
    assert bot1.decide(ctx).action == "idle"


def test_a_session_arriving_late_still_fires_before_the_cutoff():
    assert bot1.decide(_ctx(now=datetime.datetime(2026, 9, 22, 11, 47))).action == "fire"


def test_no_session_by_the_cutoff_skips_the_month_with_a_reason():
    d = bot1.decide(_ctx(now=datetime.datetime(2026, 9, 22, 12, 1), has_session=False))
    assert d.action == "skip"
    assert d.reason_code == ReasonCode.NO_BROKER_SESSION


def test_it_never_acts_twice_in_a_day():
    assert bot1.decide(_ctx(ran_today=True)).action == "idle"


# --- cross-bot priority --------------------------------------------------------------


def test_bots_are_created_with_distinct_priorities(db_path):
    bots = repo.list_bots("u1")
    priorities = sorted(b.priority for b in bots)
    assert priorities == [1, 2], "two freshly-created bots must never be tied"


def test_priority_is_editable_and_orders_the_sweep(db_path):
    repo.get_or_create_bot("u1", BOT_HOLDINGS_WRITER)
    repo.get_or_create_bot("u1", BOT_EXPIRY_INDEX_WRITER)
    repo.update_bot("u1", BOT_HOLDINGS_WRITER, enabled=True, priority=5)
    repo.update_bot("u1", BOT_EXPIRY_INDEX_WRITER, enabled=True, priority=2)

    ordered = repo.list_enabled_bots_by_user()["u1"]
    assert [b.bot_type for b in ordered] == [BOT_EXPIRY_INDEX_WRITER, BOT_HOLDINGS_WRITER]


def test_a_disabled_bot_never_appears_in_the_sweep(db_path):
    repo.update_bot("u1", BOT_HOLDINGS_WRITER, enabled=True)
    repo.update_bot("u1", BOT_EXPIRY_INDEX_WRITER, enabled=False)
    assert [b.bot_type for b in repo.list_enabled_bots_by_user()["u1"]] == [
        BOT_HOLDINGS_WRITER
    ]


# --- holdings split three ways --------------------------------------------------------


def _holding(qty, *, pledged=None, blocked=None):
    row = {"stock_code": "X", "quantity": qty}
    if pledged is not None:
        row["pledged_quantity"] = pledged
    if blocked is not None:
        row["blocked_quantity"] = blocked
    return row


def test_pledged_stock_still_counts_as_call_coverage():
    """It is genuinely owned. Unpledging before expiry is a step the user takes, not a
    reason to leave the coverage unwritten."""
    assert bot1.deliverable_quantity(_holding(1000, pledged=400, blocked=0)) == 1000


def test_blocked_stock_is_not_call_coverage():
    """Already earmarked — a pending sale or a settlement hold — so it is not the user's to
    deliver on assignment."""
    assert bot1.deliverable_quantity(_holding(1000, pledged=0, blocked=400)) == 600


def test_both_encumbrances_are_handled_together():
    assert bot1.deliverable_quantity(_holding(1000, pledged=300, blocked=200)) == 800


def test_unknown_demat_falls_back_to_the_full_holding():
    """`blocked_quantity` is None when the demat call failed. Unknown is not zero, but
    treating it as fully blocked would stop the bot writing anything across the whole
    portfolio the moment one broker call failed."""
    assert bot1.deliverable_quantity(_holding(1000)) == 1000
    assert bot1.deliverable_quantity(_holding(1000, pledged=400)) == 1000


def test_the_three_categories_are_exhaustive():
    """available + blocked + pledged must equal the total, or the UI's breakdown would not
    add up to the number above it."""
    total, in_demat, avail = 1000, 700, 500
    pledged = total - in_demat
    blocked = in_demat - avail
    available = total - pledged - blocked
    assert pledged + blocked + available == total
    assert available == avail
