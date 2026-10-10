"""The premium gate in the bot backtests (docs/premium-gate-plan.md section 4)."""
from __future__ import annotations

import datetime
import math

from icici_breeze_backend.app.domain.bots import (
    ExpiryIndexWriterConfig,
    IndexWriterLeg,
    MomentumLongScalperConfig,
    PremiumGateConfig,
)
from icici_breeze_backend.app.services.bots import backtest_combos
from icici_breeze_backend.app.services.bots.backtest_expiry import run_expiry_backtest
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.bots.scalping.backtest_options import OK
from icici_breeze_backend.app.services.bots.scalping.backtest_store import HistCandle
from icici_breeze_backend.app.services.bots.scalping.spreads import SpreadStats
from icici_breeze_backend.app.services.premium_gate import reading as pg
from icici_breeze_backend.app.services.premium_gate.replay import ReplayPremium

DAY = datetime.date(2026, 3, 10)  # a NIFTY expiry (Tuesday)
SPREAD = SpreadStats(source="default", samples=0, median_spread_pct=0.5)


class _Pricer:
    real = True
    source = "scripted"

    def __init__(self):
        self.asked = []

    def bar(self, key, minute, *, spot=0.0, sigma=0.0):
        self.asked.append((key.strike, key.right))
        return OK, HistCandle(minute, 10.0, 10.0, 10.0, 10.0, 10)


class _Premium:
    """A stand-in reading: `ratio` None is no reading."""

    def __init__(self, ratio, implied=1e-4):
        self.ratio, self.implied = ratio, implied

    def reading(self, now, expiry, spot, strike, call, put):
        if self.ratio is None:
            return pg.Reading(None, None, None, pg.REASON_NO_HISTORY), None
        return pg.Reading(self.ratio, 1.0, 1.0 / self.ratio), self.implied


def _spot_day(spot=24_000.0):
    t0 = datetime.datetime.combine(DAY, datetime.time(9, 15))
    return [HistCandle(t0 + datetime.timedelta(minutes=m), spot, spot, spot, spot, 0) for m in range(375)]


def _writer(premium, *, gate=True, leg=None):
    config = ExpiryIndexWriterConfig(
        indices={"NIFTY": leg or IndexWriterLeg(enabled=True)},
        premium_gate=PremiumGateConfig(enabled=gate, threshold=1.0),
    )
    pricer = _Pricer()
    result = run_expiry_backtest(
        index="NIFTY", days=[DAY], spot_bars=_spot_day(), config=config, charges=ChargesModel(),
        spread=SPREAD,
        strategies=("naked_pe",), pricer=pricer, premium=premium,
    )
    return result, pricer


def test_bot2s_replay_sells_only_rich_premium():
    rich, pricer = _writer(_Premium(1.4))
    assert len(rich.trades) == 1 and rich.skipped_premium == 0
    # The ATM pair is read before the strikes are placed, as live.
    assert pricer.asked[:2] == [(24_000.0, "call"), (24_000.0, "put")]
    thin, _ = _writer(_Premium(0.7))
    assert thin.trades == [] and thin.skipped_premium == 1
    assert thin.summary()["skipped_premium"] == 1


def test_no_reading_in_a_replay_is_no_trade():
    for premium in (None, _Premium(None)):
        result, _ = _writer(premium)
        assert result.trades == [] and result.skipped_premium == 1


def test_a_replay_places_strikes_by_implied_move():
    leg = IndexWriterLeg(enabled=True, distance_basis="implied_move", implied_multiple_pe=2.5)
    result, _ = _writer(_Premium(1.1, implied=0.01 ** 2), gate=False, leg=leg)
    # 24000 x e^-0.025 = 23407.4, rounded away from spot.
    assert result.trades[0].legs[0].strike == 23_400.0


def test_an_off_gate_reads_no_atm_pair():
    result, pricer = _writer(None, gate=False)
    assert len(result.trades) == 1
    assert (24_000.0, "call") not in pricer.asked


def test_the_replay_reading_is_the_live_one():
    """ReplayPremium is the live formula on replayed bars: a pair priced at 1.5x the forecast's
    volatility reads 1.5x."""
    from icici_breeze_backend.app.services.condor.pricing import bs_price, years_to_expiry_close

    bars, level, k = [], 25_000.0, 0
    day = datetime.date(2026, 2, 2)
    while len({b.ts.date() for b in bars}) < 22:
        if day.weekday() < 5:
            t0 = datetime.datetime.combine(day, datetime.time(9, 15))
            for m in range(375):
                k += 1
                prev = level
                level *= math.exp(0.0004 * (1 if k % 2 else -1))
                bars.append(HistCandle(t0 + datetime.timedelta(minutes=m), prev, prev, prev, level, 0))
        day += datetime.timedelta(days=1)
    while day.weekday() >= 5:
        day += datetime.timedelta(days=1)
    replay = ReplayPremium(bars)
    now = datetime.datetime.combine(day, datetime.time(9, 30))
    fc = pg.forecast(replay.sessions, now, 0)
    years = years_to_expiry_close(day, now)
    sigma = 1.5 * math.sqrt(fc.variance / years)
    call = bs_price("Call", 25_000, 25_000, years, sigma, pg.RISK_FREE, pg.RISK_FREE)
    put = bs_price("Put", 25_000, 25_000, years, sigma, pg.RISK_FREE, pg.RISK_FREE)
    reading, _ = replay.reading(now, day, 25_000, 25_000, call, put)
    assert abs(reading.ratio - 1.5) < 0.05


def test_the_gate_is_compared_off_and_at_three_thresholds_beside_the_saved_row():
    off = backtest_combos.combos_for("expiry", ExpiryIndexWriterConfig())
    assert [c.id for c in off] == ["as-configured", "premium-0.90", "premium-1.00", "premium-1.20"]
    on = backtest_combos.combos_for(
        "expiry", ExpiryIndexWriterConfig(premium_gate=PremiumGateConfig(enabled=True, threshold=1.0)))
    assert [c.id for c in on] == ["as-configured", "premium-off", "premium-0.90", "premium-1.20"]
    assert on[1].config.premium_gate.enabled is False and on[2].config.premium_gate.threshold == 0.9


def test_the_scalpers_gate_rows_follow_its_saved_signal_row():
    combos = backtest_combos.combos_for("momentum", MomentumLongScalperConfig())
    saved = next(i for i, c in enumerate(combos) if c.is_saved)
    rows = combos[saved + 2:saved + 5]  # after the saved follow and its fade
    assert [c.id for c in rows] == ["premium-0.90", "premium-1.00", "premium-1.20"]
    assert all(c.config.signal == combos[saved].config.signal for c in rows)
    assert "buy at or below 0.90x" in rows[0].label
    assert len(combos) == 27


class _FarCheaper(_Pricer):
    """Shorts at 10, anything further than 3% from spot (the wings) at 4."""

    def bar(self, key, minute, *, spot=0.0, sigma=0.0):
        self.asked.append((key.strike, key.right))
        price = 4.0 if abs(key.strike - 24_000.0) > 720 else 10.0
        return OK, HistCandle(minute, price, price, price, price, 10)


def _hedged(premium, strategies, *, leg=None):
    config = ExpiryIndexWriterConfig(
        indices={"NIFTY": leg or IndexWriterLeg(enabled=True, safety_pct_pe=2.0, safety_pct_ce=2.0,
                                                 wing_multiple=1.0)},
        premium_gate=PremiumGateConfig(enabled=False),
    )
    return run_expiry_backtest(
        index="NIFTY", days=[DAY], spot_bars=_spot_day(), config=config, charges=ChargesModel(),
        spread=SPREAD, strategies=strategies, pricer=_FarCheaper(), premium=premium,
    )


def test_a_hedged_replay_buys_the_wing_beyond_the_short_and_sells_for_net_premium():
    # implied 0.02^2: the wing sits one 2% move beyond the 23,520 short.
    result = _hedged(_Premium(1.1, implied=0.02 ** 2), ("bull_put_spread",))
    (trade,) = result.trades
    wing, short = trade.legs
    assert (wing.action, short.action) == ("buy", "sell")
    assert short.strike == 23_500.0
    # 23500 x e^-0.02 = 23034.6, rounded away from the money.
    assert wing.strike == 23_000.0 and wing.right == short.right == "put"
    # Net premium: the short's bid less the wing's ask, and the stop is a multiple of it.
    assert trade.premium_inr == round((short.touch_bid - wing.touch_bid) * trade.quantity, 2)
    assert 0 < trade.premium_inr < short.touch_bid * trade.quantity
    # Spot never moves: both expire worthless, the wing's cost comes off the short's credit.
    assert trade.gross_pnl == round((short.entry_price - wing.entry_price) * trade.quantity, 2)


def test_an_iron_condor_trades_beside_the_naked_shapes():
    result = _hedged(_Premium(1.1, implied=0.02 ** 2), ("short_strangle", "iron_condor"))
    by = {t.strategy: t for t in result.trades}
    assert set(by) == {"short_strangle", "iron_condor"}
    assert [leg.action for leg in by["iron_condor"].legs] == ["buy", "buy", "sell", "sell"]
    assert by["iron_condor"].premium_inr < by["short_strangle"].premium_inr


def test_with_no_implied_move_the_hedged_shapes_drop_out_and_the_naked_still_trade():
    result = _hedged(None, ("naked_pe", "bull_put_spread"))
    assert [t.strategy for t in result.trades] == ["naked_pe"]
    assert result.skipped_no_data == 1


def test_the_hedged_shapes_price_their_wing_through_the_monitor_signs():
    from icici_breeze_backend.app.services.bots.backtest_expiry import EXIT_STOP, _monitor

    t0 = datetime.datetime.combine(DAY, datetime.time(10, 0))
    from icici_breeze_backend.app.services.bots.scalping.backtest_store import OptionKey

    keys = [OptionKey("NIFTY", DAY, 23_000.0, "put"), OptionKey("NIFTY", DAY, 23_500.0, "put")]

    class _Rising:
        def bar(self, key, minute, *, spot=0.0, sigma=0.0):
            # The short doubles; the wing gains half of that back.
            price = 20.0 if key.strike == 23_500.0 else 9.0
            return OK, HistCandle(minute, price, price, price, price, 10)

    spots = {t0 + datetime.timedelta(minutes=1): HistCandle(t0, 1, 1, 1, 1, 0)}
    # Net credit 6 a unit; short +10, wing +5 = a 5-a-unit loss, inside a 2x stop of 12...
    reason, _, _ = _monitor(keys, [4.0, 10.0], t0, spots, 1, 12.0, None, _Rising(), 0.1, False,
                            signs=[-1, 1])
    assert reason != EXIT_STOP
    # ...but past it if the wing were a short too.
    reason, _, _ = _monitor(keys, [4.0, 10.0], t0, spots, 1, 12.0, None, _Rising(), 0.1, False)
    assert reason == EXIT_STOP
