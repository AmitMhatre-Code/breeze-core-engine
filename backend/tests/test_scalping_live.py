"""Live order dispatch (services.bots.scalping.live, .guards reconciliation).

Every test here is about a state that only exists once real orders are involved: a limit
resting at the exchange, a cancel that fails, a crash between placing and recording. Paper
mode has none of them, so none of this is covered anywhere else.
"""
from __future__ import annotations

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.bots_migrate import (
    BOT_MOMENTUM_LONG_SCALPER,
    ensure_bots_tables,
)
from icici_breeze_backend.app.domain.bots import ReasonCode
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots.scalping import guards, live

USER = "u1"
LEG = live.LegOrder(
    stock_code="NIFTY", exchange_code=cfg.NFO, right="call", strike_price=24_000.0,
    expiry_display="10-Sep-2026", action=cfg.BUY, quantity=75,
)


class FakeBroker:
    """A broker that answers exactly as scripted, and records what it was asked.

    `fills[order_id]` is one order-detail row, or a list answered in turn (the last one
    repeats). `book` is the day's order list; None means the order book cannot be read.
    """

    def __init__(self, *, place=None, fills=None, cancels=None, book=None):
        self.placed: list[dict] = []
        self.cancelled: list[str] = []
        self.book_reads = 0
        self._place = list(place or [{"ok": True, "order_id": "OID1"}])
        self._fills = dict(fills or {})
        self._cancels = list(cancels or [])
        self._book = book

    def place_order(self, user_id, product, stock, action, strike, right, price, expiry, qty, **kw):
        self.placed.append({"action": action, "price": float(price), "qty": qty, **kw})
        spec = self._place[min(len(self.placed) - 1, len(self._place) - 1)]
        if spec.get("raise"):
            raise RuntimeError(spec["raise"])
        if spec.get("no_id"):
            return {"Status": 200, "Success": {}}
        if not spec.get("ok"):
            return {"Status": 500, "Error": spec.get("error", "rejected")}
        return {"Status": 200, "Success": {"order_id": spec["order_id"]}}

    def cancel_order_single(self, user_id, order_ref):
        self.cancelled.append(str(order_ref))
        ok = self._cancels[min(len(self.cancelled) - 1, len(self._cancels) - 1)] if self._cancels else True
        return {"success": ok}

    def get_session_breeze(self, user_id):
        return self

    def get_order_detail(self, exchange_code="", order_id=""):
        state = self._fills.get(str(order_id))
        if isinstance(state, list):
            state = state.pop(0) if len(state) > 1 else state[0]
        if state is None:
            return {"Status": 200, "Success": []}
        return {"Status": 200, "Success": [state]}

    def get_orders(self, user_id, start, end, *, exchange_codes=None):
        self.book_reads += 1
        if self._book is None:
            return {"Status": 500, "Error": "order book down"}
        return {"Status": 200, "Success": [dict(r) for r in self._book] or None}


@pytest.fixture(autouse=True)
def clean():
    live.reset_state_for_tests()
    yield
    live.reset_state_for_tests()


def _no_sleep(_seconds):
    return None


def _clock():
    """A monotonic clock that always reports the deadline as passed."""
    ticks = iter([0.0] + [999.0] * 200)
    return lambda: next(ticks)


# --- the gate --------------------------------------------------------------------------


def test_there_is_no_module_level_bypass_of_the_evidence_gate():
    """The gate lives in `evidence.py` and the PATCH path, not in a constant here.

    A regression guard with a specific failure in mind: re-introducing a module-level
    boolean that enables live dispatch would move the decision back out of the user's hands
    and past the paper-evidence requirement, which is the thing that stopped being a comment
    in a document. If a flag like this is ever wanted again it should fail this test first
    and be argued for deliberately.
    """
    suspicious = [
        name
        for name in dir(live)
        if name.isupper() and "LIVE" in name and isinstance(getattr(live, name), bool)
    ]
    assert suspicious == []


# --- placing ---------------------------------------------------------------------------


def test_a_filled_order_reports_its_fill():
    broker = FakeBroker(fills={"OID1": {"quantity_executed": 75, "status": "Executed",
                                        "average_price": 101.5}})
    result = live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=lambda a: 101.5,
        timeout_seconds=0, now=_clock(), sleep=_no_sleep,
    )
    assert result.ok and result.filled_quantity == 75
    assert result.average_price == pytest.approx(101.5)
    assert len(broker.placed) == 1 and broker.cancelled == []


def test_an_unfilled_limit_is_cancelled_then_repriced_once_then_abandoned():
    """The whole point of the live entry path: never walk away from a resting order."""
    broker = FakeBroker(fills={})  # nothing ever fills
    result = live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=lambda a: 101.0 + a,
        timeout_seconds=0, attempts=2, now=_clock(), sleep=_no_sleep,
    )
    assert not result.ok
    assert len(broker.placed) == 2, "one re-price, then stop"
    assert len(broker.cancelled) == 2, "every resting order is cancelled"
    assert result.cancelled is True
    assert "cancelled" in (result.error or "")


def test_a_reprice_uses_a_fresh_price():
    broker = FakeBroker(fills={})
    live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=lambda a: 101.0 + a,
        timeout_seconds=0, attempts=2, now=_clock(), sleep=_no_sleep,
    )
    assert [p["price"] for p in broker.placed] == [101.0, 102.0]


def test_a_failed_cancel_stops_everything_immediately():
    """An order believed dead that is not will fill into a position nothing manages.

    So: no second order, and the caller is told to stand the bot down.
    """
    broker = FakeBroker(fills={}, cancels=[False])
    result = live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=lambda a: 101.0,
        timeout_seconds=0, attempts=3, now=_clock(), sleep=_no_sleep,
    )
    assert result.cancel_failed is True
    assert len(broker.placed) == 1, "must not place again while an order is unaccounted for"
    assert "standing down" in (result.error or "")


def test_a_rejected_order_is_not_cancelled_but_is_retried():
    """Nothing rests after a rejection, so a re-price is safe and a cancel is pointless."""
    broker = FakeBroker(
        place=[{"ok": False, "error": "margin shortfall"}],
    )
    result = live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=lambda a: 101.0,
        timeout_seconds=0, attempts=2, now=_clock(), sleep=_no_sleep,
    )
    assert not result.ok and broker.cancelled == []
    assert "margin shortfall" in (result.error or "")


def test_a_partial_fill_is_reported_rather_than_retried():
    """A cancelled partial is a real, small position. The caller decides, not the dispatcher."""
    broker = FakeBroker(fills={"OID1": {"quantity_executed": 25, "status": "Ordered"}})
    result = live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=lambda a: 101.0,
        timeout_seconds=0, attempts=3, now=_clock(), sleep=_no_sleep,
    )
    assert result.filled_quantity == 25 and not result.ok
    assert len(broker.placed) == 1, "a partial is not something to place more on top of"


def test_the_rest_backstop_catches_a_fill_the_feed_missed():
    """The lesson Strategy Groups learned: a completion path that only listens gets stuck."""
    broker = FakeBroker(fills={"OID1": {"quantity_executed": 75, "status": "Executed",
                                        "average_price": 100.0}})
    result = live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=lambda a: 101.0,
        timeout_seconds=0, now=_clock(), sleep=_no_sleep,
    )
    # No WS notification was ever delivered; only the REST check knew.
    assert result.ok and result.filled_quantity == 75


def test_a_broker_exception_does_not_escape():
    class Boom(FakeBroker):
        def place_order(self, *a, **k):
            raise RuntimeError("socket died")

    result = live.place_and_confirm(
        Boom(), USER, LEG, price_for_attempt=lambda a: 101.0,
        timeout_seconds=0, now=_clock(), sleep=_no_sleep,
    )
    assert not result.ok and "socket died" in (result.error or "")


# --- price ladders ---------------------------------------------------------------------


def test_an_entry_does_not_chase_further_on_retry():
    """The re-price is against a fresh touch, not a progressively worse price."""
    ladder = live.entry_price_ladder(100.0, tolerance_pct=1.0)
    assert ladder(0) == pytest.approx(101.0)
    assert ladder(1) == pytest.approx(101.0)


def test_an_exit_widens_progressively():
    """An exit must complete -- there is a live position with no stop behind it."""
    ladder = live.exit_price_ladder(100.0, band_pct=1.0)
    assert ladder(0) == pytest.approx(99.0)
    assert ladder(1) < ladder(0)
    assert ladder(2) < ladder(1)


def test_an_exit_limit_never_goes_to_zero():
    ladder = live.exit_price_ladder(0.10, band_pct=20.0)
    assert all(ladder(i) >= 0.05 for i in range(6))


def test_limits_are_sent_on_the_exchange_tick():
    """ICICI rejects any price off the 0.05 grid ("Price should be in multiples of: 0.05").

    The CAS Bingo case that hit it: buy at an ask of 103.75 with a 1% tolerance = 104.7875.
    """
    broker = FakeBroker(fills={"OID1": {"quantity_executed": 75, "status": "Executed"}})
    live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=live.entry_price_ladder(103.75, 1.0),
        timeout_seconds=0, now=_clock(), sleep=_no_sleep,
    )
    assert broker.placed[0]["price"] == pytest.approx(104.75)


def test_tick_snapping_never_crosses_the_ladder_price():
    # Buys round down (still >= the on-grid ask), sells round up (still <= the on-grid bid).
    assert live.limit_on_tick(104.7875, cfg.BUY) == pytest.approx(104.75)
    assert live.limit_on_tick(98.0125, cfg.SELL) == pytest.approx(98.05)
    # Already on the grid: unchanged despite float noise.
    assert live.limit_on_tick(101.0, cfg.BUY) == pytest.approx(101.0)
    assert live.limit_on_tick(0.9, cfg.SELL) == pytest.approx(0.9)
    # Never a zero-price order.
    assert live.limit_on_tick(0.01, cfg.BUY) == pytest.approx(0.05)


# --- B-21: an answer that says nothing is not a refusal -------------------------------

import datetime as _dt

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.bots.scalping import order_intents

NOW = _dt.datetime(2026, 9, 29, 10, 0, 0, tzinfo=IST).timestamp()


def _book_row(order_id, *, at=NOW, action="Buy", right="Call", strike=24_000.0, qty=75,
              expiry="10-Sep-2026", stock="NIFTY"):
    """One order-list row, in the shape `get_orders` serves."""
    stamp = _dt.datetime.fromtimestamp(at, IST).strftime("%d-%b-%Y %H:%M:%S")
    return {
        "order_id": order_id, "stock_code": stock, "exchange_code": "NFO",
        "expiry_date": expiry, "strike_price": strike, "right": right, "action": action,
        "quantity": qty, "order_datetime": stamp, "status": "Executed",
    }


def _place(broker, leg=LEG, **kw):
    return live.place_and_confirm(
        broker, USER, leg, price_for_attempt=lambda a: 101.0, timeout_seconds=0,
        now=_clock(), sleep=_no_sleep, wall=lambda: NOW, **kw,
    )


def test_a_lost_answer_whose_order_is_in_the_book_is_followed_to_its_fill():
    """The request reached ICICI and was accepted; only the answer was lost. Reading that as
    "nothing placed" left a live position with no stop."""
    broker = FakeBroker(
        place=[{"raise": "socket died"}],
        book=[_book_row("OID9", at=NOW + 1)],
        fills={"OID9": {"quantity_executed": 75, "status": "Executed", "average_price": 100.5}},
    )
    result = _place(broker)
    assert result.ok and result.order_id == "OID9"
    assert result.average_price == pytest.approx(100.5)
    assert len(broker.placed) == 1, "never re-sent"


class _TaggingBroker(FakeBroker):
    """Puts rows in the book while the placement is "lost", as ICICI would: `ours` decides
    whether the order that carries this send's tag is among them."""

    def __init__(self, *, ours, **kw):
        super().__init__(place=[{"raise": "socket died"}], book=[], **kw)
        self._ours = ours

    def place_order(self, *a, **kw):
        self._book.append({**_book_row("MANUAL", at=NOW + 1), "user_remark": ""})
        if self._ours:
            self._book.append({**_book_row("OURS", at=NOW + 1), "user_remark": kw["user_remark"]})
        return super().place_order(*a, **kw)


def test_every_bot_order_goes_out_with_its_own_tag():
    broker = FakeBroker(
        place=[{"ok": True, "order_id": "A"}, {"ok": True, "order_id": "B"}],
        fills={"A": {"quantity_executed": 0, "status": "Cancelled"},
               "B": {"quantity_executed": 75, "status": "Executed"}},
    )
    _place(broker, attempts=2)
    tags = [o["user_remark"] for o in broker.placed]
    assert len(tags) == 2 and tags[0] != tags[1]
    assert all(len(t) == 8 and t.isalpha() and t.islower() for t in tags)


def test_a_lost_answer_is_found_by_its_tag_not_by_an_identical_manual_order():
    """Identical contract, side, quantity and time: before the tag this was "unknown" and
    the bot stood down."""
    broker = _TaggingBroker(
        ours=True, fills={"OURS": {"quantity_executed": 75, "status": "Executed"}}
    )
    result = _place(broker)
    assert result.ok and result.order_id == "OURS"


def test_an_identical_manual_order_is_not_adopted_as_the_bots_own():
    """The bot's order never went in. Matching on contract and time alone would have
    adopted the user's hand-placed order as the bot's position."""
    result = _place(_TaggingBroker(ours=False))
    assert not result.ok and not result.outcome_unknown
    assert result.order_id in (None, "")


def test_a_lost_answer_with_nothing_in_the_book_is_a_refusal():
    broker = FakeBroker(place=[{"raise": "socket died"}], book=[])
    result = _place(broker)
    assert not result.ok and not result.outcome_unknown
    assert "not placed" in (result.error or "")


def test_a_200_without_an_order_id_is_looked_up_too():
    broker = FakeBroker(
        place=[{"no_id": True}], book=[_book_row("OID9")],
        fills={"OID9": {"quantity_executed": 75, "status": "Executed"}},
    )
    assert _place(broker).order_id == "OID9"


@pytest.mark.parametrize("book", [
    None,                                                            # unreadable
    [_book_row("A", at=NOW), _book_row("B", at=NOW + 5)],           # two identical orders
    [{**_book_row("A"), "order_datetime": "sometime"}],               # unreadable time
])
def test_a_lost_answer_the_book_cannot_settle_stands_down(book):
    """Never guessed: an order that may exist is not treated as absent, and nothing more is
    placed until the question is answered."""
    broker = FakeBroker(place=[{"raise": "socket died"}], book=book)
    result = live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=lambda a: 101.0, timeout_seconds=0, attempts=3,
        now=_clock(), sleep=_no_sleep, wall=lambda: NOW,
    )
    assert result.outcome_unknown and result.unaccounted
    assert len(broker.placed) == 1
    assert "socket died" in (result.error or "")


def test_the_book_match_ignores_orders_outside_the_window_and_other_sides():
    broker = FakeBroker(
        place=[{"raise": "socket died"}],
        book=[
            _book_row("OLD", at=NOW - 3600),        # an hour earlier: someone else's
            _book_row("SELL", action="Sell"),       # other side
            _book_row("BIG", qty=150),              # other size
        ],
    )
    result = _place(broker)
    assert not result.outcome_unknown and result.order_id is None


def test_a_clean_rejection_never_reads_the_book():
    """ICICI answered, and the answer was no. The book costs a call and adds nothing."""
    broker = FakeBroker(place=[{"ok": False, "error": "margin shortfall"}], book=[])
    _place(broker)
    assert broker.book_reads == 0


# --- B-10: a cancel goes to the exchange the order is on ---------------------------------


def test_a_bfo_order_is_cancelled_on_bfo():
    sensex = live.LegOrder(
        stock_code="BSESEN", exchange_code=cfg.BFO, right="call", strike_price=82_000.0,
        expiry_display="10-Sep-2026", action=cfg.BUY, quantity=20,
    )
    broker = FakeBroker(fills={})
    _place(broker, leg=sensex, attempts=1)
    assert broker.cancelled == ["OID1|BFO"]


def test_an_nfo_cancel_keeps_the_bare_id():
    broker = FakeBroker(fills={})
    _place(broker, attempts=1)
    assert broker.cancelled == ["OID1"]


# --- crash reconciliation (B-02) --------------------------------------------------------


@pytest.fixture
def db(tmp_path, monkeypatch):
    from icici_breeze_backend.app.services.bots import charges as charges_mod

    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    monkeypatch.setattr(charges_mod, "_db_path", lambda: path)
    ensure_bots_tables(path)
    order_intents.reset_state_for_tests()
    return path


@pytest.fixture
def alerts(monkeypatch):
    sent: list = []
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.telegram_alerts._notify",
        lambda user_id, text, *, kind: sent.append((kind, text)),
    )
    return sent


def _long_leg(qty=75):
    return {
        "stock_code": "NIFTY", "exchange_code": "NFO", "right": "call",
        "strike_price": 24_000.0, "expiry_display": "10-Sep-2026", "action": cfg.BUY,
        "lots": qty // 75, "lot_size": 75, "quantity": qty,
    }


def _intent_of(leg, order_id=None, *, action=None, at=NOW):
    return {
        **{k: leg[k] for k in ("stock_code", "exchange_code", "right", "strike_price",
                               "expiry_display", "quantity")},
        "action": action or leg["action"], "price": 101.0, "sent_at": at, "order_id": order_id,
    }


def _pending(run_id, legs, intents, bot=BOT_MOMENTUM_LONG_SCALPER, **extra):
    return repo.open_cycle(
        USER, bot, run_id, structure="long_ce" if bot == BOT_MOMENTUM_LONG_SCALPER else "iron_fly",
        legs=legs, lots=1, paper=False,
        detail={"pending": True, "order_ids": [], "intents": intents, **extra},
    )


def _resolve(broker, bot=BOT_MOMENTUM_LONG_SCALPER, **kw):
    from icici_breeze_backend.app.domain.bots import (
        IronFlyScalperConfig,
        MomentumLongScalperConfig,
    )
    from icici_breeze_backend.app.services.bots.charges import ChargesModel
    from icici_breeze_backend.app.services.bots.scalping import iron_fly_bot, momentum_bot

    if bot == BOT_MOMENTUM_LONG_SCALPER:
        adopt = momentum_bot.adopt_recovered(MomentumLongScalperConfig(), ChargesModel())
    else:
        adopt = iron_fly_bot.adopt_recovered(IronFlyScalperConfig(), ChargesModel())
    kw.setdefault("now", NOW + 600)
    return order_intents.resolve_pending(
        broker, USER, bot, adopt=adopt, charges=ChargesModel(), **kw
    )


def test_an_intent_row_blocks_new_cycles(db):
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    assert guards.has_unresolved_intent(USER, BOT_MOMENTUM_LONG_SCALPER) is False
    _pending(run_id, [_long_leg()], [_intent_of(_long_leg(), "OID1")])
    assert guards.has_unresolved_intent(USER, BOT_MOMENTUM_LONG_SCALPER) is True


def test_the_order_id_is_on_the_row_before_the_fill_wait(db):
    """B-02's root cause: ids were written only after the fill wait and any cancel, so a crash
    in that window left a row nothing could answer."""
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    cycle = repo.open_cycle(USER, BOT_MOMENTUM_LONG_SCALPER, run_id, structure="long_ce",
                            legs=[_long_leg()], paper=False,
                            detail={"pending": True, "order_ids": [], "intents": []})
    seen: list = []

    class Watching(FakeBroker):
        def get_order_detail(self, exchange_code="", order_id=""):
            # Called from inside the fill wait: the row must already carry the id.
            row = repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)[0]
            seen.append([i["order_id"] for i in row.detail["intents"]])
            return super().get_order_detail(exchange_code, order_id)

    broker = Watching(fills={"OID1": {"quantity_executed": 75, "status": "Executed"}})
    _place(broker, journal=order_intents.Journal(cycle))
    assert seen and seen[0] == ["OID1"]


def test_a_filled_order_is_adopted_with_its_real_entry(db, alerts):
    """B-52 as well: the adopted row carries a ladder, an entry price and `entry_value`, so it
    has a stop and the daily total reads it correctly."""
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    _pending(run_id, [_long_leg()], [_intent_of(_long_leg(), "OID1")])
    broker = FakeBroker(fills={"OID1": {"quantity_executed": 75, "status": "Executed",
                                        "average_price": 101.5}})

    assert _resolve(broker) == 1

    assert guards.has_unresolved_intent(USER, BOT_MOMENTUM_LONG_SCALPER) is False
    row = repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)[0]
    assert row.detail["reconciled"] is True and "pending" not in row.detail
    assert row.detail["ladder"] and row.detail["entry"]["price"] == pytest.approx(101.5)
    assert row.entry_value == pytest.approx(101.5 * 75)
    assert row.legs[0]["entry_price"] == pytest.approx(101.5)
    assert [k for k, _ in alerts] == ["scalping_orphan"]
    assert "recovered" in alerts[0][1]


def test_a_partial_long_is_adopted_at_its_filled_size(db, alerts):
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    _pending(run_id, [_long_leg()], [_intent_of(_long_leg(), "OID1")])
    broker = FakeBroker(fills={"OID1": {"quantity_executed": 25, "status": "Cancelled",
                                        "average_price": 100.0}})
    _resolve(broker)
    row = repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)[0]
    assert row.legs[0]["quantity"] == 25 and row.entry_value == pytest.approx(2_500.0)


def test_an_order_with_no_id_is_found_in_the_book(db, alerts):
    """The crash landed between the request going out and its id coming back."""
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    _pending(run_id, [_long_leg()], [_intent_of(_long_leg())])
    broker = FakeBroker(
        book=[_book_row("OID7", at=NOW + 2)],
        fills={"OID7": {"quantity_executed": 75, "status": "Executed", "average_price": 99.0}},
    )
    _resolve(broker)
    row = repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)[0]
    assert row.detail["intents"][0]["order_id"] == "OID7"
    assert row.detail["intents"][0]["located"] is True
    assert row.entry_value == pytest.approx(99.0 * 75)


def test_an_order_another_cycle_owns_is_never_claimed(db, alerts):
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    repo.open_cycle(USER, BOT_MOMENTUM_LONG_SCALPER, run_id, structure="long_ce",
                    legs=[_long_leg()], paper=False, detail={"order_ids": ["OID7"]})
    _pending(run_id, [_long_leg()], [_intent_of(_long_leg())])
    broker = FakeBroker(book=[_book_row("OID7")])
    _resolve(broker)
    cycles = repo.list_cycles(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)
    abandoned = [c for c in cycles if c.exit_reason_code == ReasonCode.ENTRY_UNFILLED]
    assert len(abandoned) == 1, "the only match belonged to another cycle, so nothing was ours"


def test_nothing_in_the_book_is_abandoned_without_counting_as_a_loss(db, alerts):
    """Nothing was traded, so it must not read as a loss or trip the cooldown."""
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    _pending(run_id, [_long_leg()], [_intent_of(_long_leg())])
    _resolve(FakeBroker(book=[]))
    cycles = repo.list_cycles(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)
    assert cycles[0].exit_reason_code == ReasonCode.ENTRY_UNFILLED
    assert cycles[0].is_loss is False
    assert repo.scalper_day_totals(USER, BOT_MOMENTUM_LONG_SCALPER).consecutive_losses == 0
    assert alerts == [], "nothing was ever reported, so there is nothing to clear"


def test_an_order_still_resting_after_a_restart_is_cancelled_then_settled(db, alerts):
    """An entry from a pass that no longer exists: nothing would manage what it fills into."""
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    _pending(run_id, [_long_leg()], [_intent_of(_long_leg(), "OID1")])
    broker = FakeBroker(fills={"OID1": [
        {"quantity_executed": 0, "status": "Ordered"},
        {"quantity_executed": 0, "status": "Cancelled"},
    ]})
    _resolve(broker)
    assert broker.cancelled == ["OID1"]
    assert not repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)


def test_an_unanswerable_row_is_alerted_once_retried_and_finally_switched_off(db, alerts):
    """Assuming 'no fill' would strand a live position with no stop behind it. The row stays
    pending (so it can never be traded) and is re-asked on the back-off; the bot is switched
    off only once the retry budget is spent."""
    repo.get_or_create_bot(USER, BOT_MOMENTUM_LONG_SCALPER)
    repo.update_bot(USER, BOT_MOMENTUM_LONG_SCALPER, enabled=True)
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    _pending(run_id, [_long_leg()], [_intent_of(_long_leg())])
    broker = FakeBroker(book=None)

    t = NOW + 600
    assert _resolve(broker, now=t) == 0
    assert _resolve(broker, now=t + 1) == 0
    assert broker.book_reads == 1, "not due again until the back-off has passed"
    for wait in order_intents.RESOLVE_BACKOFF_SECONDS:
        t += wait
        _resolve(broker, now=t)
    row = repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)[0]
    assert row.detail["pending"] is True
    assert row.detail["resolve_attempts"] == order_intents.MAX_RESOLVE_ATTEMPTS + 1
    assert row.detail["resolve_disarmed"] is True
    assert repo.get_or_create_bot(USER, BOT_MOMENTUM_LONG_SCALPER).enabled is False
    texts = [t for _, t in alerts]
    assert len(texts) == 2 and "needs checking" in texts[0] and "switched off" in texts[1]
    assert guards.has_unresolved_intent(USER, BOT_MOMENTUM_LONG_SCALPER) is True


def test_a_restart_asks_again_at_once(db, alerts):
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    _pending(run_id, [_long_leg()], [_intent_of(_long_leg())],
             resolve_next_at=NOW + 10_000, resolve_attempts=1, resolve_alerted=True)
    broker = FakeBroker(book=[])
    assert _resolve(broker) == 0
    assert _resolve(broker, force=True) == 1
    assert "nothing was traded" in alerts[-1][1]


def test_a_row_being_placed_right_now_is_left_alone(db, alerts):
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    cycle = _pending(run_id, [_long_leg()], [_intent_of(_long_leg())])
    broker = FakeBroker(book=[])
    with order_intents.placing(cycle.id):
        assert _resolve(broker, force=True) == 0
    assert broker.book_reads == 0


def test_a_legacy_row_is_searched_from_the_time_it_was_opened(db, alerts):
    """Rows written before journaling have no intents and no ids at all."""
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    cycle = repo.open_cycle(USER, BOT_MOMENTUM_LONG_SCALPER, run_id, structure="long_ce",
                            legs=[_long_leg()], paper=False,
                            detail={"pending": True, "order_ids": []})
    opened = order_intents._opened_epoch(cycle.opened_at)
    broker = FakeBroker(
        book=[_book_row("OLD1", at=opened + 30)],
        fills={"OLD1": {"quantity_executed": 75, "status": "Executed", "average_price": 98.0}},
    )
    _resolve(broker, now=opened + 900)
    assert repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)[0].entry_value == pytest.approx(98.0 * 75)


def test_a_pending_long_is_invisible_to_the_exit_path(db):
    """B-02's second half: a pending row was managed as the planned position, so a square-off
    sold 75 units that were never bought."""
    from icici_breeze_backend.app.domain.bots import MomentumLongScalperConfig
    from icici_breeze_backend.app.services.bots.scalping import momentum_bot

    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    _pending(run_id, [_long_leg()], [_intent_of(_long_leg())])
    assert momentum_bot.inspect_position(
        FakeBroker(), USER, MomentumLongScalperConfig(), BOT_MOMENTUM_LONG_SCALPER
    ) is None


# --- the fly --------------------------------------------------------------------------


def _fly_legs(q=75):
    base = {"stock_code": "NIFTY", "exchange_code": "NFO", "expiry_display": "10-Sep-2026",
            "lots": 1, "lot_size": 75, "quantity": q}
    return [
        {**base, "right": "call", "strike_price": 24_150.0, "action": cfg.BUY},
        {**base, "right": "put", "strike_price": 23_850.0, "action": cfg.BUY},
        {**base, "right": "call", "strike_price": 24_000.0, "action": cfg.SELL},
        {**base, "right": "put", "strike_price": 24_000.0, "action": cfg.SELL},
    ]


def _fly_row(ids, fills):
    from icici_breeze_backend.app.db.bots_migrate import BOT_IRON_FLY_SCALPER

    legs = _fly_legs()
    run_id = repo.open_session_run(USER, BOT_IRON_FLY_SCALPER)
    _pending(run_id, legs, [_intent_of(l, i) for l, i in zip(legs, ids)],
             bot=BOT_IRON_FLY_SCALPER, atm_strike=24_000.0)
    return FakeBroker(fills={
        i: {"quantity_executed": 75, "status": "Executed", "average_price": p}
        for i, p in zip(ids, fills)
    })


def test_a_fully_filled_fly_is_adopted_as_a_fly(db, alerts):
    from icici_breeze_backend.app.db.bots_migrate import BOT_IRON_FLY_SCALPER

    broker = _fly_row(["W1", "W2", "S1", "S2"], [10.0, 12.0, 100.0, 105.0])
    _resolve(broker, bot=BOT_IRON_FLY_SCALPER)
    row = repo.open_cycles(USER, BOT_IRON_FLY_SCALPER)[0]
    assert "pending" not in row.detail and not row.detail.get("unwinding")
    assert row.detail["net_credit_per_unit"] == pytest.approx(183.0)
    assert row.entry_value == pytest.approx(183.0 * 75)


def test_a_partly_filled_fly_keeps_only_what_filled_and_unwinds_it(db, alerts):
    """Two wings and one short is not a fly. It goes to B-01's close machinery holding
    exactly those three legs -- never the plan."""
    from icici_breeze_backend.app.db.bots_migrate import BOT_IRON_FLY_SCALPER

    legs = _fly_legs()
    run_id = repo.open_session_run(USER, BOT_IRON_FLY_SCALPER)
    _pending(run_id, legs, [_intent_of(l, i) for l, i in zip(legs[:3], ["W1", "W2", "S1"])],
             bot=BOT_IRON_FLY_SCALPER)
    broker = FakeBroker(fills={
        "W1": {"quantity_executed": 75, "status": "Executed", "average_price": 10.0},
        "W2": {"quantity_executed": 75, "status": "Executed", "average_price": 12.0},
        "S1": {"quantity_executed": 0, "status": "Cancelled"},
    })
    _resolve(broker, bot=BOT_IRON_FLY_SCALPER)
    row = repo.open_cycles(USER, BOT_IRON_FLY_SCALPER)[0]
    assert row.detail["unwinding"] is True and row.detail["unwind_from_recovery"] is True
    assert "pending" not in row.detail
    assert sorted((l["right"], l["action"], l["quantity"]) for l in row.legs) == [
        ("call", cfg.BUY, 75), ("put", cfg.BUY, 75),
    ]
    assert row.detail["exit_decision"]["code"] == ReasonCode.ENTRY_PARTIAL_UNWOUND
    assert "being closed" in alerts[-1][1]


def test_an_entry_unwind_that_finished_nets_to_nothing(db, alerts):
    """Unwind orders are journaled too, so a crash after the unwind nets out correctly
    instead of reading the wing as still held."""
    from icici_breeze_backend.app.db.bots_migrate import BOT_IRON_FLY_SCALPER

    legs = _fly_legs()
    run_id = repo.open_session_run(USER, BOT_IRON_FLY_SCALPER)
    _pending(run_id, legs, [
        _intent_of(legs[0], "W1"),
        _intent_of(legs[1], "W2"),                      # failed
        _intent_of(legs[0], "U1", action=cfg.SELL),     # the unwind sold W1 back
    ], bot=BOT_IRON_FLY_SCALPER)
    broker = FakeBroker(fills={
        "W1": {"quantity_executed": 75, "status": "Executed", "average_price": 10.0},
        "W2": {"quantity_executed": 0, "status": "Cancelled"},
        "U1": {"quantity_executed": 75, "status": "Executed", "average_price": 9.0},
    })
    _resolve(broker, bot=BOT_IRON_FLY_SCALPER)
    assert not repo.open_cycles(USER, BOT_IRON_FLY_SCALPER)


def test_reconciliation_is_a_no_op_when_nothing_is_pending(db):
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    repo.open_cycle(USER, BOT_MOMENTUM_LONG_SCALPER, run_id, structure="long_ce",
                    legs=[], lots=1, paper=True)
    assert _resolve(FakeBroker()) == 0


def test_older_rows_get_their_entry_value_backfilled(db):
    """B-52: open live rows an older build left with `entry_value` NULL."""
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    repo.open_cycle(USER, BOT_MOMENTUM_LONG_SCALPER, run_id, structure="long_ce",
                    legs=[_long_leg()], paper=False, detail={"entry": {"price": 100.0}})
    assert repo.backfill_live_entry_values() == 1
    assert repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)[0].entry_value == pytest.approx(7_500.0)
    assert repo.backfill_live_entry_values() == 0


# --- the Long Scalper's exit sells only what is held --------------------------------------
#
# A partial exit (50 of 75), or one whose answer was lost after it filled, used to leave the
# row at 75. The next pass sold 75 again -- 50 of them short.

from icici_breeze_backend.app.services import portfolio_margin_netting as pmn


class _Exit:
    reason_code = ReasonCode.SQUARE_OFF
    reason_text = "Hard square-off."


def _open_long(db):
    from icici_breeze_backend.app.domain.bots import MomentumLongScalperConfig
    from icici_breeze_backend.app.services.bots.charges import ChargesModel
    from icici_breeze_backend.app.services.bots.scalping import momentum_bot

    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    cycle = _pending(run_id, [_long_leg()], [_intent_of(_long_leg(), "OID1")])
    momentum_bot._record_live_entry(
        cycle, fill_price=100.0, filled_qty=75, order_ids=["OID1"],
        config=MomentumLongScalperConfig(), charges=ChargesModel(),
    )
    return repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)[0]


def _sell(monkeypatch, outcomes):
    calls: list = []

    def place(proc, user_id, leg, **kw):
        spec = outcomes[len(calls)] if len(calls) < len(outcomes) else {}
        calls.append(leg.quantity)
        filled = spec.get("filled", leg.quantity)
        return live.FillResult(
            order_id=f"X{len(calls)}", requested_quantity=leg.quantity, filled_quantity=filled,
            average_price=spec.get("price", 110.0),
            error=spec.get("error") if filled < leg.quantity else None,
            outcome_unknown=spec.get("outcome_unknown", False),
        )

    monkeypatch.setattr(live, "place_and_confirm", place)
    return calls


def _close(cycle, *, bid=110.0):
    from icici_breeze_backend.app.domain.bots import MomentumLongScalperConfig
    from icici_breeze_backend.app.services.bots.charges import ChargesModel
    from icici_breeze_backend.app.services.bots.scalping import momentum_bot
    from icici_breeze_backend.app.services.bots.scalping.momentum_bot import Quote

    context = momentum_bot.PositionContext(
        cycle=cycle, state=None, stop_moved=False, verdict=None,
        quote=Quote(bid=bid, ask=bid + 1, ltp=bid, source="websocket"),
    )
    momentum_bot._close_live(
        FakeBroker(), USER, MomentumLongScalperConfig(), context, _Exit(), ChargesModel(),
    )


def _holding(monkeypatch, qty):
    monkeypatch.setattr(
        pmn, "positions_for_underlying",
        lambda proc, uid, stock, exchange: pmn.PositionSet(
            rows=[{"stock_code": "NIFTY", "exchange_code": "NFO", "action": "Buy",
                   "quantity": str(qty), "right": "Call", "strike_price": "24000",
                   "expiry_date": "10-Sep-2026"}] if qty else [],
            available=True, error=None,
        ),
    )


def test_a_partial_exit_leaves_only_the_remainder(db, alerts, monkeypatch):
    cycle = _open_long(db)
    _sell(monkeypatch, [{"filled": 50, "error": "part filled"}])
    _close(cycle)
    row = repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)[0]
    assert row.legs[0]["quantity"] == 25
    assert row.detail["unwinding"] is True
    assert row.detail["realized_gross"] == pytest.approx((110.0 - 100.0) * 50)
    assert len(alerts) == 1 and "could not exit" in alerts[0][1]


def test_the_retry_waits_then_sells_only_what_the_broker_still_shows(db, alerts, monkeypatch):
    cycle = _open_long(db)
    _sell(monkeypatch, [{"filled": 50, "error": "part filled"}])
    _close(cycle)

    calls = _sell(monkeypatch, [{"price": 112.0}])
    _holding(monkeypatch, 25)
    _close(repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)[0])
    assert calls == [], "not before the back-off"

    row = repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)[0]
    repo.update_cycle_detail(row.id, {**row.detail, "unwind_next_at": 0})
    _close(repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)[0])
    assert calls == [25]
    closed = repo.list_cycles(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)[0]
    assert closed.closed_at is not None and closed.exit_reason_code == ReasonCode.SQUARE_OFF
    assert closed.gross_pnl == pytest.approx(10.0 * 50 + 12.0 * 25)
    assert "can trade again" in alerts[-1][1]


def test_a_remainder_sold_by_hand_is_not_sold_again(db, alerts, monkeypatch):
    cycle = _open_long(db)
    _sell(monkeypatch, [{"filled": 50, "error": "part filled"}])
    _close(cycle)
    row = repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)[0]
    repo.update_cycle_detail(row.id, {**row.detail, "unwind_next_at": 0})
    calls = _sell(monkeypatch, [])
    _holding(monkeypatch, 0)
    _close(repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)[0])
    assert calls == []
    closed = repo.list_cycles(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)[0]
    assert closed.exit_reason_code == ReasonCode.CLOSED_OUTSIDE_BOT


def test_a_lost_exit_answer_stops_orders_for_the_position(db, alerts, monkeypatch):
    """The sell may have filled. Another sell on top of it is how the account goes short."""
    cycle = _open_long(db)
    _sell(monkeypatch, [{"filled": 0, "error": "answer lost", "outcome_unknown": True}])
    _close(cycle)
    row = repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)[0]
    assert row.detail["unwind_orders_halted"] == "outcome_unknown"
    assert "stopped sending orders" in alerts[-1][1]


def test_a_pending_long_is_never_sold(db, alerts, monkeypatch):
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    cycle = _pending(run_id, [_long_leg()], [_intent_of(_long_leg())])
    calls = _sell(monkeypatch, [])
    _close(cycle)
    assert calls == []


# --- B-53 / B-54: an order that partly fills and then dies --------------------------------


@pytest.mark.parametrize("status", [
    "Partially Executed And Cancelled",  # ICICI's own strings for a partial that died
    "Partially Executed And Expired",
    "Cancelled",  # and a plain terminal status carrying executed units, never assumed away
])
def test_a_partial_that_dies_is_reported_not_resent(status):
    """Re-sending the full size on top of the units that traded bought more than planned on
    an entry and sold more than was held on an exit. And the ICICI strings used to be unknown
    to the bots, so the order waited out the timeout and a cancel of a dead order read as a
    failed cancel."""
    broker = FakeBroker(fills={"OID1": {"quantity_executed": 25, "status": status}})
    result = live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=lambda a: 101.0, timeout_seconds=0, attempts=3,
        now=_clock(), sleep=_no_sleep,
    )
    assert len(broker.placed) == 1, "never placed again on top of a partial"
    assert broker.cancelled == [], "a dead order is not cancelled"
    assert result.filled_quantity == 25 and result.partial
    assert not result.cancel_failed
    assert result.average_price == pytest.approx(101.0)


def test_a_dead_order_with_nothing_traded_is_still_repriced():
    broker = FakeBroker(fills={"OID1": {"quantity_executed": 0,
                                        "status": "Partially Executed And Cancelled"}})
    live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=lambda a: 101.0 + a, timeout_seconds=0,
        attempts=2, now=_clock(), sleep=_no_sleep,
    )
    assert [p["price"] for p in broker.placed] == [101.0, 102.0]


def test_a_partial_carries_the_order_price_when_known():
    broker = FakeBroker(fills={"OID1": {"quantity_executed": 25, "status": "Ordered",
                                        "average_price": "100.55"}})
    result = _place(broker)
    assert result.partial and result.average_price == pytest.approx(100.55)


def test_a_resolver_reads_a_partial_that_died_as_settled(db, alerts):
    """The same status set decides when the resolver may stop asking. Missing ICICI's
    partial strings meant an order it could never settle, and the bot switched off."""
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    _pending(run_id, [_long_leg()], [_intent_of(_long_leg(), "OID1")])
    broker = FakeBroker(fills={"OID1": {"quantity_executed": 25,
                                        "status": "Partially Executed And Cancelled",
                                        "average_price": 99.0}})
    assert _resolve(broker) == 1
    assert broker.cancelled == []
    row = repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)[0]
    assert row.legs[0]["quantity"] == 25 and row.entry_value == pytest.approx(99.0 * 25)


def test_an_exit_that_dies_partway_keeps_the_remainder_at_the_price_it_sold(
    db, alerts, monkeypatch,
):
    """End to end through the real `place_and_confirm`: 50 of 75 sell, the rest is cancelled
    outside the bot. The row keeps 25 -- not 75 re-sold, not 0 -- and the 50 are booked at
    the price they sold at, not at ₹0."""
    from icici_breeze_backend.app.domain.bots import MomentumLongScalperConfig

    cycle = _open_long(db)
    real = live.place_and_confirm
    monkeypatch.setattr(
        live, "place_and_confirm",
        lambda *a, **k: real(*a, **{**k, "now": _clock(), "sleep": _no_sleep}),
    )
    broker = FakeBroker(fills={"OID1": {"quantity_executed": 50,
                                        "status": "Partially Executed And Cancelled"}})
    from icici_breeze_backend.app.services.bots.charges import ChargesModel
    from icici_breeze_backend.app.services.bots.scalping import momentum_bot
    from icici_breeze_backend.app.services.bots.scalping.momentum_bot import Quote

    context = momentum_bot.PositionContext(
        cycle=cycle, state=None, stop_moved=False, verdict=None,
        quote=Quote(bid=110.0, ask=111.0, ltp=110.0, source="websocket"),
    )
    config = MomentumLongScalperConfig()
    momentum_bot._close_live(broker, USER, config, context, _Exit(), ChargesModel())

    assert len(broker.placed) == 1
    sold_at = live.limit_on_tick(
        live.exit_price_ladder(110.0, config.execution.exit_limit_band_pct)(0), cfg.SELL
    )
    row = repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)[0]
    assert row.legs[0]["quantity"] == 25
    assert row.detail["realized_gross"] == pytest.approx((sold_at - 100.0) * 50)
    assert not row.detail.get("unwind_orders_halted"), "a dead order is not a failed cancel"


# --- B-22: fills are booked at the traded price ------------------------------------------


def _feed_fill(order_id, executed=75):
    from types import SimpleNamespace

    live._tracker.watch(order_id)
    live._tracker._on_notification(
        SimpleNamespace(order_id=order_id, executed_quantity=executed, status="executed")
    )


def test_a_fill_confirmed_on_the_feed_is_booked_at_icicis_average_not_the_limit():
    """The feed carries no usable average, so a feed-confirmed fill was booked at its limit."""
    broker = FakeBroker(fills={"OID1": {"quantity_executed": 75, "status": "Executed",
                                        "average_price": 100.35}})
    _feed_fill("OID1")
    result = live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=lambda i: 101.0, timeout_seconds=5,
        now=_clock(), sleep=_no_sleep,
    )
    assert result.filled_quantity == 75
    assert result.average_price == pytest.approx(100.35)


def test_an_average_past_the_limit_is_not_trusted():
    broker = FakeBroker(fills={"OID1": {"quantity_executed": 75, "status": "Executed",
                                        "average_price": 155.0}})
    _feed_fill("OID1")
    result = live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=lambda i: 101.0, timeout_seconds=5,
        now=_clock(), sleep=_no_sleep,
    )
    assert result.average_price == pytest.approx(101.0)


def test_with_no_average_anywhere_the_limit_is_booked():
    broker = FakeBroker(fills={})
    _feed_fill("OID1")
    result = live.place_and_confirm(
        broker, USER, LEG, price_for_attempt=lambda i: 101.0, timeout_seconds=5,
        now=_clock(), sleep=_no_sleep,
    )
    assert result.average_price == pytest.approx(101.0)


# --- B-23 / B-44: an exit is priced from a live touch or one ICICI quote -----------------


class _QuoteBroker(FakeBroker):
    def __init__(self, rows=None, **kw):
        super().__init__(**kw)
        self.quote_calls = []
        self._rows = rows

    def get_quotes(self, **kw):
        self.quote_calls.append(kw)
        if self._rows is None:
            return {"Status": 500, "Error": "down"}
        return {"Status": 200, "Success": self._rows}


def _touch(broker, quote=None, is_buy=False, now=lambda: 0.0):
    return live.exit_touch(
        broker, USER, stock_code="NIFTY", exchange_code=cfg.NFO, expiry_display="10-Sep-2026",
        strike_price=24_000.0, right="call", is_buy=is_buy, quote=quote, now=now,
    )


def test_a_live_quote_prices_the_exit_without_a_broker_call():
    from icici_breeze_backend.app.services.bots.scalping.momentum_bot import Quote

    broker = _QuoteBroker(rows=[])
    assert _touch(broker, Quote(99.0, 101.0, 100.0, "websocket")) == 99.0
    assert _touch(broker, Quote(99.0, 101.0, 100.0, "websocket"), is_buy=True) == 101.0
    assert broker.quote_calls == []


def test_a_stand_in_or_missing_quote_asks_icici_once():
    from icici_breeze_backend.app.services.bots.scalping.momentum_bot import Quote

    broker = _QuoteBroker(rows=[{"exchange_code": "NFO", "best_bid_price": "98.5", "best_offer_price": "99.5"}])
    assert _touch(broker, Quote(120.0, 121.0, 120.0, "snapshot")) == 98.5
    assert broker.quote_calls[0]["product_type"] == "options"
    assert broker.quote_calls[0]["right"] == "call"


def test_rest_exit_quotes_are_spaced_per_contract():
    broker = _QuoteBroker(rows=None)
    clock = {"t": 0.0}
    assert _touch(broker, now=lambda: clock["t"]) is None
    clock["t"] = 5.0
    assert _touch(broker, now=lambda: clock["t"]) is None
    assert len(broker.quote_calls) == 1, "a pass two seconds later must not ask again"
    clock["t"] = live.EXIT_QUOTE_SPACING_SECONDS + 1
    _touch(broker, now=lambda: clock["t"])
    assert len(broker.quote_calls) == 2


def test_an_unpriceable_leg_is_recorded_as_nothing_sent():
    result = live.no_price_result(75)
    assert result.filled_quantity == 0 and not result.ok and not result.unaccounted
    assert "nothing was sent" in result.error


# --- B-33: orders are sliced under the freeze quantity -----------------------------------


class _FreezeBroker(FakeBroker):
    def fetch_qty_limits(self, stock_code, exchange_code=cfg.NFO):
        return 1800

    def fetch_lot_size(self, stock_code, expiry_display, exchange_code=cfg.NFO):
        return 75


def test_a_leg_above_the_freeze_quantity_goes_out_in_slices():
    big = live.LegOrder(**{**LEG.__dict__, "quantity": 4500})
    broker = _FreezeBroker(
        place=[{"ok": True, "order_id": "S1"}, {"ok": True, "order_id": "S2"}, {"ok": True, "order_id": "S3"}],
        fills={
            "S1": {"quantity_executed": 1800, "status": "Executed", "average_price": 100.0},
            "S2": {"quantity_executed": 1800, "status": "Executed", "average_price": 101.0},
            "S3": {"quantity_executed": 900, "status": "Executed", "average_price": 102.0},
        },
    )
    result = live.place_and_confirm(
        broker, USER, big, price_for_attempt=lambda a: 102.0, timeout_seconds=0,
        now=_clock(), sleep=_no_sleep,
    )
    assert [p["qty"] for p in broker.placed] == [1800, 1800, 900]
    assert result.ok and result.filled_quantity == 4500
    assert result.average_price == pytest.approx((1800 * 100 + 1800 * 101 + 900 * 102) / 4500)


def test_a_slice_that_does_not_fill_stops_the_rest():
    big = live.LegOrder(**{**LEG.__dict__, "quantity": 4500})
    broker = _FreezeBroker(
        place=[{"ok": True, "order_id": "S1"}, {"ok": False, "error": "RMS: margin"}],
        fills={"S1": {"quantity_executed": 1800, "status": "Executed", "average_price": 100.0}},
    )
    result = live.place_and_confirm(
        broker, USER, big, price_for_attempt=lambda a: 101.0, timeout_seconds=0,
        now=_clock(), sleep=_no_sleep,
    )
    assert len(broker.placed) == 2, "the third slice is never sent"
    assert result.partial and result.filled_quantity == 1800
    assert "RMS" in result.error
