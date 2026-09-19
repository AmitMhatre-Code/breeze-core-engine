"""CAS Bingo's backtest (docs/signals-streamline-plan.md section 8).

The replay reuses the live triggers, strike rule, exits and settlement; these tests pin that it
feeds them what live feeds them.
"""
from __future__ import annotations

import datetime

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.domain.bots import CasBingoConfig
from icici_breeze_backend.app.services.bots.cas_bingo.backtest import run_cas_backtest
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.bots.scalping.backtest_options import OK
from icici_breeze_backend.app.services.bots.scalping.backtest_store import HistCandle
from icici_breeze_backend.app.services.bots.scalping.spreads import SpreadStats

DAY = datetime.date(2026, 9, 15)  # a NIFTY expiry Tuesday
SPREAD = SpreadStats(source="default", samples=0, median_spread_pct=0.5)
CHARGES = ChargesModel()


def _at(hh: int, mm: int) -> datetime.datetime:
    return datetime.datetime.combine(DAY, datetime.time(hh, mm))


def _cash(levels: dict[datetime.time, float], base: float = 24_000.0) -> list[HistCandle]:
    """Cash index bars 09:15-15:39 at `base`, overridden from each given minute onwards."""
    out, level = [], base
    t = _at(9, 15)
    while t <= _at(15, 39):
        level = levels.get(t.time(), level)
        out.append(HistCandle(t, level, level, level, level, 0))
        t += datetime.timedelta(minutes=1)
    return out


class Pricer:
    """Traded bars for any contract, priced off the cash index: intrinsic plus time value that
    falls away with distance from the index."""

    source = "real (test)"
    real = True

    def __init__(self, cash: list[HistCandle]):
        self.spot = {b.ts: b.close for b in cash}

    def bar(self, key, minute, *, spot=0.0, sigma=0.0):
        s = self.spot.get(minute) or self.spot[max(t for t in self.spot if t <= minute)]
        intrinsic = max(0.0, s - key.strike) if key.right == "call" else max(0.0, key.strike - s)
        time_value = max(0.5, 60.0 - 0.2 * abs(key.strike - s))  # cheaper further out
        p = round(intrinsic + time_value, 2)
        return OK, HistCandle(minute, p, p, p, p, 100)


def _run(config, cash, readings=None, futures=None):
    return run_cas_backtest(
        config=config, index="NIFTY", days=[DAY], index_bars=cash,
        futures_bars=futures if futures is not None else cash, readings=readings or {},
        charges=CHARGES, spread=SPREAD, pricer=Pricer(cash), record_decisions=True,
    )


def test_the_strangle_enters_on_its_clock_and_settles_at_the_auction_close():
    cash = _cash({})
    result = _run(CasBingoConfig(strategy="long_strangle"), cash)
    (trade,) = result.cycles
    assert trade.entered_at == _at(15, 15) and trade.structure == "long_strangle"
    assert "BUY 24150 CE" in trade.legs and "BUY 23850 PE" in trade.legs
    # Flat index: both legs expire worthless and the strangle loses its premium.
    assert trade.exit_reason in ("expired_settled", "stop_loss") and trade.net_pnl < 0


def _flip_readings(cash, at: datetime.time, state: str) -> dict:
    out, started = {}, None
    for b in cash:
        if b.ts.time() >= at:
            if started is None:
                started = b.ts.replace(tzinfo=IST).timestamp() + 60
            out[b.ts] = {"state": state, "call_started_at": started, "signal": 0.9}
        else:
            out[b.ts] = {"state": "neutral", "signal": 0.1}
    return out


def test_a_debit_spread_waits_for_the_flip_to_hold_for_its_sustain_minutes():
    cash = _cash({})
    readings = _flip_readings(cash, datetime.time(14, 40), "bullish")
    result = _run(CasBingoConfig(strategy="debit_spread"), cash, readings)
    (trade,) = result.cycles
    # Flipped as the 14:40 bar closed (14:41); held three minutes -> 14:44.
    assert trade.entered_at == _at(14, 44) and trade.structure == "bull_call_debit"


def test_a_faded_debit_goes_the_other_way():
    cash = _cash({})
    readings = _flip_readings(cash, datetime.time(14, 40), "bullish")
    config = CasBingoConfig(strategy="debit_spread", signal={"mechanism": "expansion", "duration": 15,
                                                              "direction": "fade"})
    (trade,) = _run(config, cash, readings).cycles
    assert trade.structure == "bear_put_debit"


def test_the_auction_credit_sells_beyond_the_indicative_index_after_15_20():
    # The index rallies 1% into the auction; the indicative level is carried by the cash bars.
    cash = _cash({datetime.time(15, 15): 24_240.0})
    config = CasBingoConfig(strategy="credit_spread", credit={"auction_min_credit_pct": 1.0})
    result = _run(config, cash)
    (trade,) = result.cycles
    assert trade.structure == "bear_call_credit"
    assert trade.entered_at >= _at(15, 20)
    assert trade.reference == 24_240.0


def test_one_entry_per_index_per_day_and_every_minute_is_recorded():
    cash = _cash({})
    readings = _flip_readings(cash, datetime.time(14, 40), "bullish")
    result = _run(CasBingoConfig(strategy="debit_spread"), cash, readings)
    assert len(result.cycles) == 1
    assert result.decisions and result.decisions[0]["time"] == "14:30"


def test_a_day_with_no_index_history_is_counted_not_guessed():
    result = run_cas_backtest(
        config=CasBingoConfig(strategy="long_strangle"), index="NIFTY", days=[DAY], index_bars=[],
        futures_bars=[], readings={}, charges=CHARGES, spread=SPREAD, pricer=Pricer(_cash({})),
    )
    assert result.cycles == [] and result.days_without_index == 1
