"""Bot 2 — Expiry-Day Index Writer (services.bots.expiry_index_writer).

`decide()` is where every awkward case lives: the session that arrives at 11:47, the cutoff
that passes with nobody logged in, an app that boots after the nag was supposed to start.
It is pure, so all of that is testable without a broker, a market, or the real clock.
"""
from __future__ import annotations

import datetime

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.domain.bots import (
    ExpiryIndexWriterConfig,
    IndexWriterLeg,
    LegEdit,
    ProposalLeg,
    ReasonCode,
)
from icici_breeze_backend.app.services.bots import exit_arming
from icici_breeze_backend.app.services.bots import expiry_index_writer as bot2

TODAY = datetime.date(2026, 9, 3)
EXPIRY = "03-Sep-2026"
BOOT = datetime.datetime(2026, 9, 3, 7, 45)


def at(hour, minute=0):
    return datetime.datetime(2026, 9, 3, hour, minute)


def config(**kw):
    indices = kw.pop("indices", {"NIFTY": IndexWriterLeg(enabled=True, priority=1)})
    return ExpiryIndexWriterConfig(indices=indices, **kw)


def ctx(**kw):
    base = dict(
        now=at(9, 30),
        app_started_at=BOOT,
        config=config(),
        expiring_today={"NIFTY": EXPIRY},
        has_session=True,
        ran_today=False,
        last_nag_at=None,
    )
    base.update(kw)
    return bot2.TickContext(**base)


# --- not a trading opportunity -------------------------------------------------------


def test_no_expiry_today_is_a_clean_skip():
    d = bot2.decide(ctx(expiring_today={}))
    assert d.action == "skip"
    assert d.reason_code == ReasonCode.NOT_AN_EXPIRY_DAY


def test_an_expiry_the_user_did_not_enable_is_a_distinct_skip():
    """Different from "no expiry today" — the user should be able to tell them apart."""
    d = bot2.decide(
        ctx(
            expiring_today={"BSESEN": EXPIRY},
            config=config(indices={"NIFTY": IndexWriterLeg(enabled=True)}),
        )
    )
    assert d.action == "skip"
    assert d.reason_code == ReasonCode.NOTHING_ELIGIBLE


def test_a_disabled_index_is_not_traded():
    d = bot2.decide(config=None) if False else bot2.decide(
        ctx(config=config(indices={"NIFTY": IndexWriterLeg(enabled=False)}))
    )
    assert d.action == "skip"


def test_once_the_day_is_resolved_every_later_tick_is_idle():
    """The scheduler ticks every minute; a resolved day must not re-log or re-fire."""
    assert bot2.decide(ctx(ran_today=True)).action == "idle"
    assert bot2.decide(ctx(ran_today=True, has_session=False, now=at(11))).action == "idle"


# --- the session problem -------------------------------------------------------------


def test_before_the_nag_window_it_stays_quiet():
    assert bot2.decide(ctx(now=at(7, 50), has_session=False)).action == "idle"


def test_nags_once_the_window_opens():
    d = bot2.decide(ctx(now=at(8, 0), has_session=False))
    assert d.action == "nag"
    assert d.reason_code == ReasonCode.NO_BROKER_SESSION
    assert "NIFTY" in d.reason_text and "12:00" in d.reason_text


def test_the_nag_cannot_start_before_the_app_is_up_to_send_it():
    """A deployment powered on at 09:10 starts nagging then, not pretending it did at 08:00."""
    late_boot = datetime.datetime(2026, 9, 3, 9, 10)
    assert bot2.decide(
        ctx(now=at(9, 5), has_session=False, app_started_at=late_boot)
    ).action == "idle"
    assert bot2.decide(
        ctx(now=at(9, 11), has_session=False, app_started_at=late_boot)
    ).action == "nag"


def test_nags_are_spaced_by_the_configured_interval():
    last = at(9, 0)
    assert bot2.decide(ctx(now=at(9, 10), has_session=False, last_nag_at=last)).action == "idle"
    assert bot2.decide(ctx(now=at(9, 15), has_session=False, last_nag_at=last)).action == "nag"


def test_the_nag_stops_the_moment_a_session_appears():
    """A session before the entry time silences the nag and waits — it does not fire early."""
    early = bot2.decide(ctx(now=at(9, 15), has_session=True, last_nag_at=at(9, 0)))
    assert early.action == "idle"
    at_entry = bot2.decide(ctx(now=at(9, 30), has_session=True, last_nag_at=at(9, 0)))
    assert at_entry.action == "fire"


def test_no_session_by_the_cutoff_ends_the_day_with_a_reason():
    d = bot2.decide(ctx(now=at(12, 0), has_session=False))
    assert d.action == "skip"
    assert d.reason_code == ReasonCode.NO_BROKER_SESSION
    assert "12:00 cut-off" in d.reason_text


def test_nothing_is_traded_after_the_cutoff_even_with_a_session():
    d = bot2.decide(ctx(now=at(12, 1), has_session=True))
    assert d.action == "skip"
    assert d.reason_code == ReasonCode.CUTOFF_PASSED


# --- entry timing --------------------------------------------------------------------


def test_waits_for_the_entry_time():
    assert bot2.decide(ctx(now=at(9, 15))).action == "idle"


def test_fires_at_the_entry_time():
    assert bot2.decide(ctx(now=at(9, 30))).action == "fire"


def test_a_late_session_fires_immediately_rather_than_waiting():
    """The scheduled time has already passed; waiting for it again would skip the day."""
    d = bot2.decide(ctx(now=at(11, 47), has_session=True))
    assert d.action == "fire"
    assert d.indices == ("NIFTY",)


# --- multi-index ordering ------------------------------------------------------------


def test_both_indices_fire_in_priority_order():
    d = bot2.decide(
        ctx(
            expiring_today={"NIFTY": EXPIRY, "BSESEN": EXPIRY},
            config=config(
                indices={
                    "NIFTY": IndexWriterLeg(enabled=True, priority=5),
                    "BSESEN": IndexWriterLeg(enabled=True, priority=2),
                }
            ),
        )
    )
    assert d.indices == ("BSESEN", "NIFTY")


def test_only_indices_expiring_today_are_traded():
    d = bot2.decide(
        ctx(
            expiring_today={"NIFTY": EXPIRY},
            config=config(
                indices={
                    "NIFTY": IndexWriterLeg(enabled=True, priority=1),
                    "BSESEN": IndexWriterLeg(enabled=True, priority=2),
                }
            ),
        )
    )
    assert d.indices == ("NIFTY",)


# --- sizing and execution ------------------------------------------------------------


class FakeProc:
    def __init__(self, *, span_per_lot=120000.0, lot=75, bid=42.0, spot=24000.0,
                 verified=None, place_ok=True, strangle_margin_multiple=1.6,
                 bid_by_right=None, orders=(), reject_rights=(), feed="filled"):
        self.span_per_lot = span_per_lot
        self.lot = lot
        self.bid = bid
        self.spot = spot
        self.verified = verified
        self.place_ok = place_ok
        # A strangle's two sides net at the exchange, so it costs less than twice a naked
        # leg -- which is exactly what gives it a fair shot in the yield ranking.
        self.strangle_margin_multiple = strangle_margin_multiple
        self.bid_by_right = bid_by_right or {}
        self.orders = list(orders)
        # Rights the broker refuses, for a strangle that fills on one side only.
        self.reject_rights = set(reject_rights)
        # What the WS order feed reports for each accepted order: "filled" (fills at once),
        # "working" (acknowledged and resting) or "silent" (nothing arrives -- a deaf feed).
        self.feed = feed
        self.order_book_reads = 0
        self.placed = []

    def bid_for(self, right):
        return self.bid_by_right.get(right, self.bid)

    def fetch_lot_size(self, stock_code, expiry_date, exchange_code=cfg.NFO):
        return self.lot

    def fetch_qty_limits(self, stock_code, exchange_code=cfg.NFO):
        return 1800

    def _resolve_leg_margin_with_source(self, *, quantity, **kw):
        lots = quantity // self.lot
        if lots > 1 and self.verified is not None:
            return {"Status": 200, "Success": {"span_margin_required": self.verified}}, []
        return (
            {"Status": 200, "Success": {"span_margin_required": self.span_per_lot * lots}},
            [],
        )

    def margin_calculator(self, payload, exchange_code=cfg.NFO):
        """Stands in for the broker's netted multi-leg call.

        Both sides of a strangle arrive in ONE call, so the fake has to net them the way
        the exchange does -- pricing them additively here would hide the very bias the
        yield ranking exists to correct.
        """
        lots = max(int(row["quantity"]) for row in payload) // self.lot
        if lots > 1 and self.verified is not None:
            return {"Status": 200, "Success": {"span_margin_required": self.verified}}
        span = self.span_per_lot * lots
        if len({row["right"] for row in payload}) > 1:
            span *= self.strangle_margin_multiple
        return {"Status": 200, "Success": {"span_margin_required": span}}

    def place_order(self, user_id, product_type, stock_code, action, strike_price, right,
                    price, expiry_date, quantity, exchange_code=cfg.NFO, aggressive_limit=False):
        self.placed.append({"quantity": quantity, "price": price, "right": right})
        if not self.place_ok or right in self.reject_rights:
            return {"Status": 400, "Error": "Rejected"}
        order_id = f"OID{len(self.placed)}"
        if self.feed != "silent":
            filled = self.feed == "filled"
            exit_arming.record_order_state(
                order_id,
                status="executed" if filled else "ordered",
                executed=quantity if filled else 0,
                total=quantity,
                stock_code=stock_code,
                expiry_display=expiry_date,
            )
        return {"Status": 200, "Success": {"order_id": order_id}}

    def get_orders(self, user_id, start, end, *, exchange_codes=None):
        """The Processor's order-book shape, NOT BreezeConnect's.

        The arm guard reads the order book through this method, and the bot must hand it
        the Processor to reach it -- `BreezeConnect` only has the raw `get_order_list`.
        Modelling it here is what makes that a test failure rather than a live position
        left without a stop.
        """
        self.order_book_reads += 1
        return {"Status": 200, "Error": None, "Success": list(self.orders)}

    def get_session_breeze(self, user_id):
        """A BreezeConnect-shaped object, deliberately NOT the Processor.

        The fake used to return `self` here, which quietly made the two interchangeable and
        hid a real bug: code that reached for a Processor method through the session object
        passed the tests and then raised AttributeError against the live SDK.
        """
        return FakeSession(self)


class FakeSession:
    """The subset of `BreezeConnect` this bot actually touches.

    Anything absent here is absent from the real SDK too -- `get_orders` above all, which
    belongs to the Processor. Reaching for it must fail in a test, not in production.
    """

    def __init__(self, proc):
        self._proc = proc

    def margin_calculator(self, payload, exchange_code=cfg.NFO):
        return self._proc.margin_calculator(payload, exchange_code=exchange_code)


@pytest.fixture
def patch_chain(monkeypatch):
    def _install(proc, strikes=(23500, 23600, 23700, 23800, 24000)):
        def fake(p, user_id, stock_code, exchange_code, expiry, right):
            return {
                "Status": 200,
                "Error": None,
                "Success": [
                    {"strike_price": s, "spot_price": proc.spot,
                     "best_bid_price": proc.bid_for(right), "ltp": proc.bid_for(right) + 1}
                    for s in strikes
                ],
            }

        monkeypatch.setattr(bot2, "fetch_chain_side_icici_response", fake, raising=False)
        monkeypatch.setattr(
            "icici_breeze_backend.app.services.quote_source_router."
            "fetch_chain_side_icici_response",
            fake,
        )

    return _install


@pytest.fixture(autouse=True)
def isolated_arming(tmp_path, monkeypatch):
    """Keep exit arming off the real database, the WS, Telegram and the real clock's close.

    A stop that cannot arm at placement is persisted and waited on; without this the
    waiting path would write into whatever database the environment points at.
    """
    from icici_breeze_backend.app.db.bots_migrate import ensure_bots_tables
    from icici_breeze_backend.app.repositories import bots as repo

    path = str(tmp_path / "bots.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_bots_tables(path)
    exit_arming.reset_state_for_tests()
    monkeypatch.setattr(exit_arming, "prepare", lambda proc, user_id: None)
    monkeypatch.setattr(exit_arming, "now_ist", lambda: datetime.datetime(2026, 9, 3, 10, 0))
    monkeypatch.setattr(exit_arming, "_notify", lambda user_id, text: None)
    yield
    exit_arming.reset_state_for_tests()


@pytest.fixture
def no_arm(monkeypatch):
    """Exit-arming is covered by its own tests; stub it out when sizing is what matters."""
    monkeypatch.setattr(bot2, "_arm_exit", lambda *a, **k: "rule-1")


def fire(proc, *, available=1_000_000.0, **cfg_kw):
    return bot2.fire_index(
        proc, "u1", "NIFTY",
        expiry_display=EXPIRY,
        config=config(indices={"NIFTY": IndexWriterLeg(enabled=True, safety_pct=2.0,
                                                       margin_pct_cap=30.0)}, **cfg_kw),
        available_margin=available,
        margin_source="breeze_api",
    )


def test_lots_are_sized_against_the_per_index_cap(patch_chain, no_arm):
    proc = FakeProc(span_per_lot=120000.0)
    patch_chain(proc)
    # 30% of 10L = 3L budget; 3L / 1.2L per lot = 2 lots.
    result = fire(proc)
    assert result.lots == 2
    assert result.quantity == 150
    assert result.budget == 300000.0
    assert result.ok


def test_one_lot_over_the_cap_is_skipped_not_shrunk(patch_chain, no_arm):
    proc = FakeProc(span_per_lot=400000.0)
    patch_chain(proc)
    result = fire(proc)
    assert result.lots == 0
    assert result.reason_code == ReasonCode.MARGIN_CAP_TOO_SMALL
    assert proc.placed == [], "nothing may be placed when even one lot is unaffordable"


def test_a_verified_margin_above_the_estimate_reduces_the_size(patch_chain, no_arm):
    """The baseline is an estimate; over-committing an unattended trade is what the cap
    exists to prevent, so the real number wins."""
    proc = FakeProc(span_per_lot=120000.0, verified=500000.0)
    patch_chain(proc)
    result = fire(proc)
    assert result.lots == 1


def test_put_strike_is_chosen_away_from_spot(patch_chain, no_arm):
    proc = FakeProc(spot=24000.0)
    patch_chain(proc)
    # 2% below 24000 = 23520; rounding away from spot gives 23500, not 23600.
    assert fire(proc).strike_price == 23500


def test_reprice_index_legs_moves_the_strike_to_a_new_distance(patch_chain):
    """The one thing a Bot 2 manual run lets you change about the strike: a distance %,
    which re-picks it against the current spot exactly as an autonomous fire would."""
    import types

    from icici_breeze_backend.app.services.bots import proposals

    proc = FakeProc(spot=24000.0, bid=42.0, span_per_lot=120000.0)
    patch_chain(proc)
    leg = ProposalLeg(
        stock_code="NIFTY", exchange_code=cfg.NFO, right="put", expiry_display=EXPIRY,
        strike_price=23700.0, lots=1, lot_size=75, quantity=75,
        premium_per_share=30.0, premium_total=2250.0, premium_basis="bid",
        span_margin=120000.0, strategy="naked_pe", group_key="NIFTY:naked_pe", spot=24000.0,
    )
    pending = types.SimpleNamespace(legs=[leg])

    out = proposals.reprice_index_legs(proc, "u1", pending, {0: LegEdit(distance_pct=2.0)})

    assert out[0].strike_price == 23500  # 2% below 24000, snapped away from spot
    assert out[0].premium_per_share == 42.0  # re-quoted at the fresh bid
    assert out[0].spot == 24000.0


def test_reprice_index_legs_lots_only_edit_leaves_the_strike_alone(patch_chain):
    import types

    from icici_breeze_backend.app.services.bots import proposals

    proc = FakeProc(spot=24000.0, bid=42.0, span_per_lot=120000.0)
    patch_chain(proc)
    leg = ProposalLeg(
        stock_code="NIFTY", exchange_code=cfg.NFO, right="put", expiry_display=EXPIRY,
        strike_price=23700.0, lots=1, lot_size=75, quantity=75,
        premium_per_share=30.0, premium_total=2250.0, premium_basis="bid",
        span_margin=120000.0, strategy="naked_pe", group_key="NIFTY:naked_pe", spot=24000.0,
    )
    pending = types.SimpleNamespace(legs=[leg])

    out = proposals.reprice_index_legs(proc, "u1", pending, {0: LegEdit(lots=3)})

    assert out[0].strike_price == 23700.0
    assert out[0].lots == 3
    assert out[0].quantity == 225


def test_sizes_a_trade_from_a_live_websocket_chain(no_arm, monkeypatch):
    """The regression for the expiry day Bot 2 spent standing down.

    Every other sizing test here stubs `fetch_chain_side_icici_response`, which hides the
    thing that actually broke: the rows a *live* chain produces carry no `spot_price` of
    their own, because real websocket option ticks have no such field -- only the payload
    does. So this one stubs the routed payload instead and lets the real flattener run,
    which is the seam where the spot has to survive. Before the fix this reported
    "No spot price available" with a fully warm chain, wrote a terminal skip, and gave up
    on the whole 09:30-12:00 window.
    """
    proc = FakeProc(spot=23640.3, bid=15.6, lot=65)

    def routed(p, user_id, stock_code, exchange_code, expiry_display, **kw):
        return {
            "spot_price": 23640.3,  # resolved from the "4.1!NIFTY 50" index tick
            "quote_source": "websocket",
            "chain_rows": [
                {
                    "strike_price": strike,
                    # No `spot_price` on either cell -- exactly what the WS feed delivers.
                    "call": {"strike_price": strike, "ltp": 15.6, "best_bid_price": 15.6,
                             "total_buy_qty": 1, "total_sell_qty": 1},
                    "put": {"strike_price": strike, "ltp": 15.6, "best_bid_price": 15.6,
                            "total_buy_qty": 1, "total_sell_qty": 1},
                }
                # A real NIFTY expiry ladder: 50-point steps either side of spot.
                for strike in range(22400, 25000, 50)
            ],
        }

    monkeypatch.setattr(
        "icici_breeze_backend.app.services.quote_source_router.fetch_chain_payload_routed",
        routed,
    )

    result = fire(proc)

    assert result.error is None, result.error
    assert result.spot == 23640.3
    assert result.lots >= 1
    assert proc.placed, "a live chain must produce a placeable trade"


def test_no_bid_refuses_to_trade(patch_chain, no_arm):
    """Unlike Bot 1 there is no indicative fallback — this bot only runs in market hours,
    so an empty book is real."""
    proc = FakeProc(bid=0.0)
    patch_chain(proc)
    result = fire(proc)
    assert result.reason_code == ReasonCode.QUOTE_UNAVAILABLE
    assert proc.placed == []


def test_a_rejected_order_reports_the_rejection(patch_chain, no_arm):
    proc = FakeProc(place_ok=False)
    patch_chain(proc)
    result = fire(proc)
    assert result.ok is False
    assert result.reason_code == ReasonCode.ORDER_REJECTED


def test_a_failed_arm_is_reported_as_an_open_unprotected_position(patch_chain, monkeypatch):
    """The worst state this bot can leave behind, so it must never read as a clean fire."""
    proc = FakeProc()
    patch_chain(proc)

    def boom(*a, **k):
        raise RuntimeError("engine down")

    monkeypatch.setattr(bot2, "_arm_exit", boom)
    with pytest.raises(RuntimeError):
        fire(proc)


def test_exit_uses_a_price_target_and_a_premium_multiple(patch_chain, monkeypatch):
    captured = {}

    _stub_arming(monkeypatch, captured)
    proc = FakeProc(span_per_lot=120000.0, bid=42.0)
    patch_chain(proc)
    result = fire(proc, loss_limit_premium_multiple=1.5, profit_book_premium_pct=60.0)

    premium = 42.0 * result.quantity
    assert captured["loss_limit_pnl"] == pytest.approx(1.5 * premium)
    # Profit booking is a share of the premium, expressed as a per-leg PRICE target --
    # never converted into a rupee P&L, because the engine computes P&L from the broker's
    # average_price, which need not equal the price the bot sold at.
    assert captured["target_option_price"] == pytest.approx(42.0 * 0.40)
    # And the rupee profit target is pushed out of reach so it cannot front-run it.
    assert captured["profit_target_pnl"] > premium * 10


def test_booking_the_whole_premium_arms_no_profit_target(monkeypatch, patch_chain):
    """100% means "let it expire worthless", and the only honest way to express that is to
    arm no profit exit at all -- a limit order at zero does not exist, and the tick floor
    makes anything near it degenerate."""
    captured = {}
    _stub_arming(monkeypatch, captured)
    proc = FakeProc(span_per_lot=120000.0, bid=42.0)
    patch_chain(proc)
    fire(proc, profit_book_premium_pct=100.0)

    assert captured["target_option_price"] is None
    # The stop-loss is still live -- expiring worthless is not the same as unprotected.
    assert captured["loss_limit_pnl"] > 0


def test_the_arm_guard_is_reached_through_the_processor(patch_chain, monkeypatch):
    """Regression: the guard used to be handed `proc.get_session_breeze(...)`.

    `BreezeConnect` has no `get_orders`, so every Full-Auto fire raised AttributeError
    inside the arm and left a live short position with no stop behind it -- reported as a
    fired trade plus an error line, which is the worst possible pairing. The other arming
    tests stubbed the guard out, so nothing caught it.
    """
    captured = {}
    _stub_arming(monkeypatch, captured)
    proc = FakeProc(span_per_lot=120000.0, bid=42.0)
    patch_chain(proc)

    result = fire(proc)

    assert result.rule_id == "rule-1"
    assert result.error is None
    assert captured, "the guard must let a clean order book through to the arm"


def test_orders_still_working_leave_the_stop_waiting_and_the_last_fill_arms_it(
    patch_chain, monkeypatch
):
    """The 15-Sep-2026 incident. Freshly placed limit orders are almost never all filled when
    placement returns, and arming only then -- once -- failed and left a 14,885-qty strangle
    with no stop. Now the stop waits on the order feed and arms on the fill that completes
    the position, with no broker call until then."""
    captured = {}
    _stub_arming(monkeypatch, captured)
    proc = FakeProc(span_per_lot=120000.0, bid=42.0, feed="working")
    patch_chain(proc)

    result = fire(proc)

    assert result.rule_id is None
    assert captured == {}, "nothing may be armed while an order is still working"
    assert result.arm_pending is True
    assert result.reason_code == ReasonCode.EXIT_ARM_PENDING
    assert result.error is None, "a stop waiting on its fills is not an error"
    assert proc.order_book_reads == 0, "the feed said 'working'; no REST read is owed"

    for oid in result.order_ids:
        exit_arming.record_order_state(
            oid, status="executed", executed=result.quantity, total=result.quantity,
            stock_code="NIFTY", expiry_display=EXPIRY,
        )
    exit_arming.evaluate(proc)

    assert captured, "armed on the fill that completed the position"
    assert exit_arming.current_status(result.pending_exit_id) == "armed"
    assert proc.order_book_reads == 1, "one read: the arm guard's own"


def test_an_unrelated_working_order_holds_the_stop_until_it_ends(patch_chain, monkeypatch):
    """The guard refuses on ANY live order for the expiry. That refusal used to be reported
    as a failed arm and never retried; now the stop waits, and the blocking order's own end
    on the feed is what re-triggers it."""
    captured = {}
    _stub_arming(monkeypatch, captured)
    proc = FakeProc(
        span_per_lot=120000.0,
        bid=42.0,
        orders=[{
            "order_id": "MANUAL1",
            "stock_code": "NIFTY",
            "expiry_date": EXPIRY,
            "strike_price": 23500.0,
            "right": "Call",
            "status": "Ordered",
        }],
    )
    patch_chain(proc)

    result = fire(proc)

    assert result.rule_id is None and captured == {}
    assert result.arm_pending is True
    assert result.order_ids, "the legs are filled; only the stop is waiting"

    proc.orders = []
    exit_arming.record_order_state(
        "MANUAL1", status="cancelled", executed=0, total=75,
        stock_code="NIFTY", expiry_display=EXPIRY,
    )
    exit_arming.evaluate(proc)

    assert captured, "armed once nothing on the expiry was still working"


def test_a_stop_that_errors_is_reported_apart_from_the_legs(patch_chain, monkeypatch):
    """A real arm failure is loud -- but it is the STOP that failed. Copying it onto the
    legs is what once reported two placed legs as "0 of 2 placed"."""
    _stub_arming(monkeypatch, {})

    def engine_down(*a, **k):
        raise RuntimeError("engine down")

    monkeypatch.setattr(
        "icici_breeze_backend.app.services.portfolio_pnl_engine.set_group_rule", engine_down
    )
    proc = FakeProc(span_per_lot=120000.0, bid=42.0)
    patch_chain(proc)

    result = fire(proc)

    assert result.reason_code == ReasonCode.EXIT_ARM_FAILED
    assert result.arm_error == "engine down"
    assert all(leg["error"] is None and leg["order_ids"] for leg in result.legs)
    assert result.pending_exit_id, "and it keeps retrying rather than giving up"


def _stub_arming(monkeypatch, captured):
    def fake_arm(user_id, **kw):
        captured.update(kw)

        class R:
            id = "rule-1"
            profit_target_pnl = kw["profit_target_pnl"]
            loss_limit_pnl = kw["loss_limit_pnl"]
            target_premium_pct = kw["target_premium_pct"]
            stop_loss_premium_pct = kw["stop_loss_premium_pct"]
            target_option_price = kw["target_option_price"]

        return R()

    monkeypatch.setattr(
        "icici_breeze_backend.app.repositories.squareoff_rules.arm_rule", fake_arm
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.portfolio_pnl_engine.set_group_rule",
        lambda *a, **k: None,
    )


# --- strategy shortlist ---------------------------------------------------------------


# Spot is 24000 and the safety distance is 2%, so a shortlist that includes a call needs
# strikes above 24480 as well as below 23520 -- the default ladder is one-sided.
BOTH_SIDES = (23000, 23500, 24000, 24500, 25000)


def _fire_with(proc, strategies, **cfg_kw):
    return bot2.fire_index(
        proc, "u1", "NIFTY",
        expiry_display=EXPIRY,
        config=config(
            indices={
                "NIFTY": IndexWriterLeg(
                    enabled=True, strategies=strategies, safety_pct_ce=2.0,
                    safety_pct_pe=2.0, margin_pct_cap=30.0,
                )
            },
            **cfg_kw,
        ),
        available_margin=1_000_000.0,
        margin_source="breeze_api",
    )


def test_a_single_shortlisted_strategy_is_simply_traded(patch_chain, no_arm):
    proc = FakeProc(span_per_lot=120000.0)
    patch_chain(proc, strikes=BOTH_SIDES)
    result = _fire_with(proc, ["naked_ce"])
    assert result.strategy == "naked_ce"
    assert [leg["right"] for leg in result.legs] == ["call"]


def test_the_better_paying_side_wins_when_both_nakeds_are_shortlisted(patch_chain, no_arm):
    """Same margin either side, so the richer book decides."""
    proc = FakeProc(span_per_lot=120000.0, bid_by_right={cfg.CALL: 20.0, cfg.PUT: 55.0})
    patch_chain(proc, strikes=BOTH_SIDES)
    result = _fire_with(proc, ["naked_ce", "naked_pe"])
    assert result.strategy == "naked_pe"


def test_strategies_are_ranked_by_yield_not_by_absolute_premium(patch_chain, no_arm):
    """The load-bearing test for the whole shortlist feature.

    A strangle collects both premiums, so on ABSOLUTE premium it wins every time it is
    shortlisted -- which would silently retire the other two options the moment a user
    ticked all three. Here the strangle collects 75 against the put's 55, and still loses,
    because the extra 20 does not pay for the extra margin it ties up.
    """
    proc = FakeProc(
        span_per_lot=120000.0,
        bid_by_right={cfg.CALL: 20.0, cfg.PUT: 55.0},
        strangle_margin_multiple=1.9,
    )
    patch_chain(proc, strikes=BOTH_SIDES)
    result = _fire_with(proc, ["naked_ce", "naked_pe", "short_strangle"])

    considered = {c["strategy"]: c for c in result.considered}
    assert considered["short_strangle"]["premium_per_lot"] > considered["naked_pe"]["premium_per_lot"]
    assert result.strategy == "naked_pe"


def test_a_strangle_wins_when_netting_makes_it_pay(patch_chain, no_arm):
    proc = FakeProc(
        span_per_lot=120000.0,
        bid_by_right={cfg.CALL: 40.0, cfg.PUT: 55.0},
        strangle_margin_multiple=1.15,
    )
    patch_chain(proc, strikes=BOTH_SIDES)
    result = _fire_with(proc, ["naked_ce", "naked_pe", "short_strangle"])
    assert result.strategy == "short_strangle"
    assert [leg["right"] for leg in result.legs] == ["call", "put"]
    assert len(proc.placed) == 2, "both sides of a strangle must be placed"


def test_a_strangle_books_only_when_both_legs_are_cheap(monkeypatch, patch_chain):
    """One price target for the group, taken from the cheaper leg.

    Booking the group because one side collapsed would leave the other side naked, which is
    strictly worse than holding both.
    """
    captured = {}
    _stub_arming(monkeypatch, captured)
    proc = FakeProc(
        span_per_lot=120000.0,
        bid_by_right={cfg.CALL: 40.0, cfg.PUT: 60.0},
        strangle_margin_multiple=1.1,
    )
    patch_chain(proc, strikes=BOTH_SIDES)
    result = _fire_with(proc, ["short_strangle"], profit_book_premium_pct=50.0)

    assert result.strategy == "short_strangle"
    assert captured["target_option_price"] == pytest.approx(20.0)  # min(40, 60) x 50%


def test_a_one_sided_strangle_names_the_rejected_leg_and_waits_for_its_stop(
    monkeypatch, patch_chain
):
    """One leg on, one refused, and a stop still to arm: the user needs all three facts,
    and each belongs to its own leg -- not smeared across both."""
    captured = {}
    _stub_arming(monkeypatch, captured)
    proc = FakeProc(
        span_per_lot=120000.0,
        bid_by_right={cfg.CALL: 40.0, cfg.PUT: 60.0},
        strangle_margin_multiple=1.1,
        reject_rights={cfg.CALL},
        orders=[{
            "stock_code": "NIFTY",
            "expiry_date": EXPIRY,
            "strike_price": 23500.0,
            "right": "Put",
            "status": "Ordered",
        }],
    )
    patch_chain(proc, strikes=BOTH_SIDES)

    result = _fire_with(proc, ["short_strangle"])

    assert result.strategy == "short_strangle"
    call, put = result.legs
    assert call["error"] == "Rejected" and call["order_ids"] == []
    assert put["error"] is None and put["order_ids"], "the put side filled"
    assert result.rule_id is None
    # The rejection is the run's headline; the stop is waiting, not failed.
    assert result.reason_code == ReasonCode.ORDER_REJECTED
    assert result.arm_pending is True
