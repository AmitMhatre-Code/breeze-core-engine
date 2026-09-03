"""Bot 1 — Holdings Option Writer scan (services.bots.holdings_writer).

The scan's whole job is deciding *how many lots*, and calls and puts are capped by
different things. These tests pin that boundary.
"""
from __future__ import annotations

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.domain.bots import HoldingsWriterConfig, ScripPref
from icici_breeze_backend.app.services.bots import holdings_writer as hw

FUTURE_EXPIRY = "29-Sep-2099"
LATER_EXPIRY = "27-Oct-2099"


class FakeProcessor:
    """Only the surface `scan` actually uses."""

    def __init__(self, holdings, universe, lot_sizes, positions=None, margin=25000.0):
        self._holdings = holdings
        self._universe = universe
        self._lot_sizes = lot_sizes
        self._positions = positions or {"Status": 200, "Success": []}
        self._margin = margin
        self.margin_calls = 0

    def get_holdings(self, user_id):
        return {"Status": 200, "Success": self._holdings, "Error": None}

    def fetch_stock_codes(self, exchange_code=cfg.NFO):
        return [
            {"stock_code": code, "long_name": code, "expiry_dates": expiries}
            for code, expiries in self._universe.items()
        ]

    def fetch_lot_size(self, stock_code, expiry_date, exchange_code=cfg.NFO):
        return self._lot_sizes.get(stock_code)

    def get_positions(self, user_id):
        return self._positions

    def _resolve_leg_margin_with_source(self, **kw):
        self.margin_calls += 1
        if self._margin is None:
            return {"Status": 400, "Error": "no baseline"}, []
        return (
            {"Status": 200, "Success": {"span_margin_required": self._margin}, "Error": ""},
            [],
        )


def holding(code, qty, *, pledged=None, cmp_=1000.0):
    return {
        "stock_code": code,
        "exchange_code": "NSE",
        "quantity": qty,
        "pledged_quantity": pledged,
        "average_price": 900.0,
        "current_market_price": cmp_,
    }


def chain_rows(spot, strikes, *, bid=5.0, ltp=9.99):
    return [
        {
            "strike_price": s,
            "spot_price": spot,
            "best_bid_price": bid,
            "best_offer_price": bid + 0.5,
            "ltp": ltp,
            "total_buy_qty": 100000,
        }
        for s in strikes
    ]


@pytest.fixture
def patch_chain(monkeypatch):
    calls = []

    def _install(rows_by_right):
        def fake(proc, user_id, stock_code, exchange_code, expiry, right):
            calls.append((stock_code, right))
            rows = rows_by_right.get(right)
            if rows is None:
                return {"Status": 500, "Error": "no chain", "Success": None}
            return {"Status": 200, "Error": None, "Success": rows}

        monkeypatch.setattr(hw, "fetch_chain_side_icici_response", fake)
        return calls

    return _install


def run_scan(proc, *, config=None, prefs=None):
    return hw.scan(
        proc,
        "u1",
        config=config or HoldingsWriterConfig(),
        prefs=prefs or {},
        margin_source="breeze_api",
    )


# --- CE: the hard, holdings-backed cap -----------------------------------------------


def test_ce_lots_are_floor_of_holding_over_lot_size(patch_chain):
    patch_chain({cfg.CALL: chain_rows(1000.0, [1000, 1050, 1100, 1150])})
    # 5700 held, lot 1500 -> 3 lots, 1200 remainder ignored.
    proc = FakeProcessor([holding("NTPC", 5700)], {"NTPC": [FUTURE_EXPIRY]}, {"NTPC": 1500})
    result = run_scan(proc)
    assert len(result.legs) == 1
    leg = result.legs[0]
    assert leg.lots == 3
    assert leg.quantity == 4500
    assert leg.held_quantity == 5700


def test_existing_short_calls_reduce_the_cap(patch_chain):
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050, 1100])})
    positions = {
        "Status": 200,
        "Success": [
            {
                "stock_code": "CIPLA",
                "action": "Sell",
                "right": "Call",
                "quantity": "425",
                "expiry_date": FUTURE_EXPIRY,
            }
        ],
    }
    # 1275 held / 425 = 3 coverable lots, 1 already written -> 2 left.
    proc = FakeProcessor(
        [holding("CIPLA", 1275)], {"CIPLA": [FUTURE_EXPIRY]}, {"CIPLA": 425}, positions
    )
    result = run_scan(proc)
    assert result.legs[0].lots == 2
    assert result.legs[0].existing_short_lots == 1


def test_existing_shorts_net_across_expiries_not_per_expiry(patch_chain):
    """A September short and an October short both consume today's coverage."""
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050])})
    positions = {
        "Status": 200,
        "Success": [
            {"stock_code": "CIPLA", "action": "Sell", "right": "Call",
             "quantity": "425", "expiry_date": FUTURE_EXPIRY},
            {"stock_code": "CIPLA", "action": "Sell", "right": "Call",
             "quantity": "425", "expiry_date": LATER_EXPIRY},
        ],
    }
    proc = FakeProcessor(
        [holding("CIPLA", 1275)], {"CIPLA": [FUTURE_EXPIRY]}, {"CIPLA": 425}, positions
    )
    assert run_scan(proc).legs[0].lots == 1


def test_fully_written_coverage_is_skipped_with_a_reason(patch_chain):
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050])})
    positions = {
        "Status": 200,
        "Success": [
            {"stock_code": "CIPLA", "action": "Sell", "right": "Call", "quantity": "850"}
        ],
    }
    proc = FakeProcessor(
        [holding("CIPLA", 850)], {"CIPLA": [FUTURE_EXPIRY]}, {"CIPLA": 425}, positions
    )
    result = run_scan(proc)
    assert result.legs == []
    assert result.skipped[0].reason_code == "coverage_exhausted"


def test_long_options_do_not_reduce_coverage(patch_chain):
    """Only *short* calls consume coverage; a bought call does not."""
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050])})
    positions = {
        "Status": 200,
        "Success": [
            {"stock_code": "CIPLA", "action": "Buy", "right": "Call", "quantity": "425"}
        ],
    }
    proc = FakeProcessor(
        [holding("CIPLA", 850)], {"CIPLA": [FUTURE_EXPIRY]}, {"CIPLA": 425}, positions
    )
    assert run_scan(proc).legs[0].lots == 2


# --- eligibility gates ---------------------------------------------------------------


def test_holding_below_one_lot_is_skipped(patch_chain):
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050])})
    # HINPET: 1170 held against a 2025 lot.
    proc = FakeProcessor([holding("HINPET", 1170)], {"HINPET": [FUTURE_EXPIRY]}, {"HINPET": 2025})
    result = run_scan(proc)
    assert result.legs == []
    assert result.skipped[0].reason_code == "below_one_lot"
    assert "under one lot" in result.skipped[0].reason


def test_scrip_without_fno_contracts_is_skipped(patch_chain):
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050])})
    proc = FakeProcessor([holding("IRCTC", 500)], {}, {})
    result = run_scan(proc)
    assert result.skipped[0].reason_code == "not_fno_eligible"


def test_past_expiries_are_never_proposed(patch_chain):
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050])})
    proc = FakeProcessor(
        [holding("NTPC", 5700)], {"NTPC": ["25-Jan-2020"]}, {"NTPC": 1500}
    )
    assert run_scan(proc).skipped[0].reason_code == "not_fno_eligible"


def test_unparseable_expiry_drops_that_row_not_the_scan(patch_chain):
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050])})
    proc = FakeProcessor(
        [holding("NTPC", 5700)], {"NTPC": ["garbage", FUTURE_EXPIRY]}, {"NTPC": 1500}
    )
    assert run_scan(proc).legs[0].lots == 3


def test_next_month_preference_selects_the_second_expiry(patch_chain):
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050])})
    proc = FakeProcessor(
        [holding("NTPC", 5700)], {"NTPC": [FUTURE_EXPIRY, LATER_EXPIRY]}, {"NTPC": 1500}
    )
    result = run_scan(proc, config=HoldingsWriterConfig(expiry_preference="next"))
    assert result.legs[0].expiry_display == LATER_EXPIRY


# --- strike selection and pricing ----------------------------------------------------


def test_call_strike_rounds_away_from_spot(patch_chain):
    """5% above 1000 is 1050; with no 1050 on the grid the safer 1060 must win, not 1040."""
    patch_chain({cfg.CALL: chain_rows(1000.0, [1020, 1040, 1060, 1080])})
    proc = FakeProcessor([holding("NTPC", 3000)], {"NTPC": [FUTURE_EXPIRY]}, {"NTPC": 1500})
    assert run_scan(proc).legs[0].strike_price == 1060


def test_put_strike_rounds_away_from_spot(patch_chain):
    patch_chain({cfg.PUT: chain_rows(1000.0, [920, 940, 960, 980])})
    proc = FakeProcessor([holding("NTPC", 3000)], {"NTPC": [FUTURE_EXPIRY]}, {"NTPC": 1500})
    result = run_scan(
        proc, prefs={"NTPC": ScripPref(stock_code="NTPC", ce_enabled=False, pe_enabled=True)}
    )
    assert result.legs[0].strike_price == 940


def test_scan_leg_carries_the_spot_it_was_priced_against(patch_chain):
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050, 1100])})
    proc = FakeProcessor([holding("NTPC", 3000)], {"NTPC": [FUTURE_EXPIRY]}, {"NTPC": 1500})
    assert run_scan(proc).legs[0].spot == 1000.0


def test_price_contract_distance_pct_repicks_the_strike_against_current_spot(patch_chain):
    """A user typing 10% must land on the same strike an autonomous run at this spot would:
    1100, snapped away from spot, not the 1050 the scan first proposed at 5%."""
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050, 1100, 1150])})
    proc = FakeProcessor([holding("NTPC", 3000)], {"NTPC": [FUTURE_EXPIRY]}, {"NTPC": 1500})
    leg = hw.price_contract(
        proc, "u1", stock_code="NTPC", right="call", expiry_display=FUTURE_EXPIRY,
        strike_price=1050.0, lots=2, lot_size=1500, margin_source="breeze_api",
        distance_pct=10.0,
    )
    assert leg is not None
    assert leg.strike_price == 1100
    assert leg.spot == 1000.0
    assert leg.quantity == 3000


def test_premium_uses_the_bid_not_the_ltp(patch_chain):
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050], bid=4.25, ltp=99.0)})
    proc = FakeProcessor([holding("NTPC", 3000)], {"NTPC": [FUTURE_EXPIRY]}, {"NTPC": 1500})
    leg = run_scan(proc).legs[0]
    assert leg.premium_per_share == 4.25
    assert leg.premium_total == pytest.approx(4.25 * 3000)


def test_a_real_bid_is_marked_as_such(patch_chain):
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050], bid=4.25, ltp=99.0)})
    proc = FakeProcessor([holding("NTPC", 3000)], {"NTPC": [FUTURE_EXPIRY]}, {"NTPC": 1500})
    assert run_scan(proc).legs[0].premium_basis == "bid"


def test_absent_bid_falls_back_to_an_indicative_ltp(patch_chain):
    """The bhavcopy has no order book at all, so off-market there is never a bid. Pricing
    off the last trade keeps the bot usable for planning; `approve` refuses to place it."""
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050], bid=0.0, ltp=12.0)})
    proc = FakeProcessor([holding("NTPC", 3000)], {"NTPC": [FUTURE_EXPIRY]}, {"NTPC": 1500})
    leg = run_scan(proc).legs[0]
    assert leg.premium_basis == "ltp_indicative"
    assert leg.premium_per_share == 12.0


def test_neither_bid_nor_ltp_is_skipped(patch_chain):
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050], bid=0.0, ltp=0.0)})
    proc = FakeProcessor([holding("NTPC", 3000)], {"NTPC": [FUTURE_EXPIRY]}, {"NTPC": 1500})
    result = run_scan(proc)
    assert result.legs == []
    assert result.skipped[0].reason_code == "no_price"


def test_no_strike_far_enough_out_is_skipped(patch_chain):
    patch_chain({cfg.CALL: chain_rows(1000.0, [1010, 1020])})
    proc = FakeProcessor([holding("NTPC", 3000)], {"NTPC": [FUTURE_EXPIRY]}, {"NTPC": 1500})
    assert run_scan(proc).skipped[0].reason_code == "no_strike"


def test_per_scrip_safety_override_beats_the_default(patch_chain):
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050, 1100, 1150, 1200])})
    proc = FakeProcessor([holding("NTPC", 3000)], {"NTPC": [FUTURE_EXPIRY]}, {"NTPC": 1500})
    result = run_scan(
        proc, prefs={"NTPC": ScripPref(stock_code="NTPC", safety_pct_ce=15.0)}
    )
    assert result.legs[0].strike_price == 1150


def test_holdings_cmp_is_used_when_the_chain_has_no_spot(patch_chain):
    rows = chain_rows(0.0, [1050, 1100])
    patch_chain({cfg.CALL: rows})
    proc = FakeProcessor(
        [holding("NTPC", 3000, cmp_=1000.0)], {"NTPC": [FUTURE_EXPIRY]}, {"NTPC": 1500}
    )
    assert run_scan(proc).legs[0].strike_price == 1050


def test_chain_failure_skips_the_scrip_with_a_reason(patch_chain):
    patch_chain({})  # no chain for either side
    proc = FakeProcessor([holding("NTPC", 3000)], {"NTPC": [FUTURE_EXPIRY]}, {"NTPC": 1500})
    result = run_scan(proc)
    assert result.skipped[0].reason_code == "chain_unavailable"


# --- PE: opt-in, and capped by cash rather than stock --------------------------------


def test_puts_are_not_proposed_unless_opted_in(patch_chain):
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050]), cfg.PUT: chain_rows(1000.0, [950])})
    proc = FakeProcessor([holding("NTPC", 5700)], {"NTPC": [FUTURE_EXPIRY]}, {"NTPC": 1500})
    rights = {leg.right for leg in run_scan(proc).legs}
    assert rights == {"call"}


def test_opted_in_put_carries_delivery_exposure_and_is_unselected(patch_chain):
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050]), cfg.PUT: chain_rows(1000.0, [950])})
    proc = FakeProcessor([holding("NTPC", 5700)], {"NTPC": [FUTURE_EXPIRY]}, {"NTPC": 1500})
    result = run_scan(
        proc, prefs={"NTPC": ScripPref(stock_code="NTPC", pe_enabled=True)}
    )
    put = next(leg for leg in result.legs if leg.right == "put")
    # One lot, not the holdings-derived 3 -- stock does not cover a short put.
    assert put.lots == hw.PE_LOTS_PER_SCRIP == 1
    assert put.delivery_exposure == pytest.approx(950 * 1500)
    # Unselected by default: the user allocates the delivery-cash budget.
    assert put.selected is False

    call = next(leg for leg in result.legs if leg.right == "call")
    assert call.delivery_exposure is None
    assert call.selected is True


def test_ce_cap_does_not_constrain_the_pe_leg(patch_chain):
    """A holding with every covered call already written can still write a put."""
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050]), cfg.PUT: chain_rows(1000.0, [950])})
    positions = {
        "Status": 200,
        "Success": [
            {"stock_code": "NTPC", "action": "Sell", "right": "Call", "quantity": "4500"}
        ],
    }
    proc = FakeProcessor(
        [holding("NTPC", 4500)], {"NTPC": [FUTURE_EXPIRY]}, {"NTPC": 1500}, positions
    )
    result = run_scan(
        proc, prefs={"NTPC": ScripPref(stock_code="NTPC", pe_enabled=True)}
    )
    assert [leg.right for leg in result.legs] == ["put"]


# --- margin, pledging, totals --------------------------------------------------------


def test_margin_failure_keeps_the_row_with_span_unknown(patch_chain):
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050])})
    proc = FakeProcessor(
        [holding("NTPC", 3000)], {"NTPC": [FUTURE_EXPIRY]}, {"NTPC": 1500}, margin=None
    )
    leg = run_scan(proc).legs[0]
    assert leg.span_margin is None
    assert leg.elm_margin > 0, "ELM is computed locally, so it survives a margin outage"


def test_pledged_holding_is_still_coverage_but_is_flagged(patch_chain):
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050])})
    proc = FakeProcessor(
        [holding("SAIL", 14100, pledged=9400)], {"SAIL": [FUTURE_EXPIRY]}, {"SAIL": 4700}
    )
    leg = run_scan(proc).legs[0]
    assert leg.lots == 3, "pledged stock still counts as coverage"
    assert leg.pledged_quantity == 9400
    assert leg.note and "2 of 3 lots are pledged" in leg.note


def test_unpledged_holding_has_no_note(patch_chain):
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050])})
    proc = FakeProcessor(
        [holding("NTPC", 3000, pledged=0)], {"NTPC": [FUTURE_EXPIRY]}, {"NTPC": 1500}
    )
    assert run_scan(proc).legs[0].note is None


def test_totals_count_only_selected_legs_and_track_budget(patch_chain):
    patch_chain({cfg.CALL: chain_rows(1000.0, [1050]), cfg.PUT: chain_rows(1000.0, [950])})
    proc = FakeProcessor([holding("NTPC", 3000)], {"NTPC": [FUTURE_EXPIRY]}, {"NTPC": 1500})
    result = run_scan(
        proc,
        config=HoldingsWriterConfig(delivery_cash_budget=2_000_000.0),
        prefs={"NTPC": ScripPref(stock_code="NTPC", pe_enabled=True)},
    )
    totals = result.totals
    assert totals["leg_count"] == 2
    assert totals["selected_count"] == 1, "the put starts unselected"
    assert totals["delivery_exposure_total"] == 0.0
    assert totals["delivery_headroom"] == 2_000_000.0


def test_empty_holdings_yields_an_empty_scan_not_an_error(patch_chain):
    patch_chain({})
    proc = FakeProcessor([], {}, {})
    result = run_scan(proc)
    assert result.legs == [] and result.skipped == []


def test_broker_failure_raises_rather_than_reporting_nothing_eligible(patch_chain):
    patch_chain({})

    class Failing(FakeProcessor):
        def get_holdings(self, user_id):
            return {"Status": 500, "Error": "Broker down", "Success": None}

    with pytest.raises(hw.BotScanError, match="Broker down"):
        run_scan(Failing([], {}, {}))
