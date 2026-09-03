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
    ReasonCode,
)
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
                 bid_by_right=None):
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
        if not self.place_ok:
            return {"Status": 400, "Error": "Rejected"}
        return {"Status": 200, "Success": {"order_id": f"OID{len(self.placed)}"}}

    def get_session_breeze(self, user_id):
        return self


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
        "icici_breeze_backend.app.services.strategy_group_arm_guard.assert_can_arm",
        lambda *a, **k: None,
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
