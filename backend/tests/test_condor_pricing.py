"""The condor engine's delta matches the Portfolio leg-delta column.

`fixtures/condor_delta_parity.json` holds two chains and the deltas the frontend model
(`frontend/src/lib/strategy-builder/greeks.ts`) gives for every contract in them: one with a
full smile and some untrusted quotes, one whose call side has no trusted quote and falls back
to each strike's LTP, then to ATM. `greeks.parity.test.ts` checks the frontend against the
same numbers.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from icici_breeze_backend.app.services.condor.pricing import (
    ChainRow,
    Quote,
    bs_price,
    build_greeks_model,
    implied_volatility,
    years_to_expiry_close,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "condor_delta_parity.json").read_text())
EXPIRY = datetime.datetime.strptime(FIXTURE["expiry_display"], "%d-%b-%Y").date()


def _quote(cell):
    if not cell:
        return None
    return Quote(
        bid=cell["best_bid_price"],
        ask=cell["best_offer_price"],
        ltp=cell["ltp"],
        bid_qty=cell["total_buy_qty"],
        ask_qty=cell["total_sell_qty"],
    )


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda c: c["name"])
def test_delta_matches_the_frontend_model(case):
    rows = [ChainRow(float(r["strike_price"]), _quote(r["call"]), _quote(r["put"])) for r in case["rows"]]
    now = datetime.datetime.fromtimestamp(case["nowMs"] / 1000, tz=datetime.timezone.utc)
    model = build_greeks_model(rows, case["spot"], EXPIRY, now)
    expected = case["expected"]
    assert model.forward_source == expected["forwardSource"]
    assert model.q == pytest.approx(expected["q"], abs=1e-12)
    for strike, right, delta, _source in expected["deltas"]:
        got = model.delta(right, float(strike))
        assert got == pytest.approx(delta, abs=1e-9), (strike, right)


def test_time_runs_to_1530_ist_and_floors_at_a_minute():
    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    day = datetime.date(2026, 9, 29)
    one_day_before = datetime.datetime(2026, 9, 28, 15, 30, tzinfo=ist)
    assert years_to_expiry_close(day, one_day_before) == pytest.approx(1 / 365)
    after_close = datetime.datetime(2026, 9, 29, 15, 31, tzinfo=ist)
    assert years_to_expiry_close(day, after_close) == pytest.approx(60 / (365 * 24 * 3600))


def test_implied_volatility_round_trips():
    for right, k in (("Call", 25000.0), ("Put", 23000.0)):
        price = bs_price(right, 24200.0, k, 0.1, 0.14, 0.07, 0.0)
        assert implied_volatility(right, price, 24200.0, k, 0.1) == pytest.approx(0.14, abs=1e-5)


def test_price_below_intrinsic_has_no_volatility():
    assert implied_volatility("Call", 100.0, 24200.0, 24000.0, 0.1) is None


def test_no_spot_no_model():
    assert build_greeks_model([], None, EXPIRY, datetime.datetime.now(datetime.timezone.utc)) is None
