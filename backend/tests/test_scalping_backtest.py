"""Bot 3's backtest (services.bots.scalping.backtest, .backtest_store, .spreads).

The bot trades a cell of the signal grid; these replays feed it that series' readings
(`backtest_common.series_readings`) -- real momentum readings where the mechanism matters, a
scripted call where a rule of the bot's (one trade per call, exit at the call's end) does.

What matters here is that the harness is honest about its own limits and consistent with
paper mode: the same trade must not look better in a backtest than it does on the live-price
path, or the two cannot be believed together.
"""
from __future__ import annotations

import datetime

import pytest

from icici_breeze_backend.app.db.bots_migrate import ensure_bots_tables
from icici_breeze_backend.app.domain.bots import MomentumLongScalperConfig
from icici_breeze_backend.app.services.bots.scalping import backtest_store, spreads
from icici_breeze_backend.app.services.bots.scalping.backtest import (
    DEFAULT_EXPIRY_WEEKDAY_MAP,
    atm_strike_for,
    expiry_weekday_for,
    next_expiry,
    run_backtest,
    theoretical_price,
    years_to_expiry,
)
from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.bots.scalping.backtest_common import (
    SessionVwap,
    series_readings,
    split_session,
)
from icici_breeze_backend.app.services.index_signal.mechanisms import SeriesKey
from icici_breeze_backend.app.services.bots.scalping.backtest_store import HistCandle
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.bots.scalping.spreads import SpreadStats

CHARGES = ChargesModel()
SPREAD = SpreadStats(source="default", samples=0, median_spread_pct=0.5)


# --- expiry calendar ------------------------------------------------------------------


def test_expiry_weekday_follows_the_date_ranged_map():
    """NIFTY weekly expiry moved off Thursday; a single weekday would misprice one era."""
    assert expiry_weekday_for(datetime.date(2025, 6, 2), DEFAULT_EXPIRY_WEEKDAY_MAP) == 3
    assert expiry_weekday_for(datetime.date(2026, 3, 2), DEFAULT_EXPIRY_WEEKDAY_MAP) == 1


def test_next_expiry_finds_the_coming_target_weekday():
    # 2026-09-07 is a Monday; Tuesday expiry is the next day.
    assert next_expiry(datetime.date(2026, 9, 7), DEFAULT_EXPIRY_WEEKDAY_MAP) == datetime.date(2026, 9, 8)
    # On expiry day itself the expiry is today, not next week.
    assert next_expiry(datetime.date(2026, 9, 8), DEFAULT_EXPIRY_WEEKDAY_MAP) == datetime.date(2026, 9, 8)


def test_a_holiday_expiry_shifts_back_not_forward():
    """The exchange brings an expiry forward to the previous trading day."""
    holidays = {datetime.date(2026, 9, 8)}
    assert next_expiry(datetime.date(2026, 9, 7), DEFAULT_EXPIRY_WEEKDAY_MAP, holidays) == datetime.date(
        2026, 9, 7
    )


def test_time_to_expiry_is_never_zero_or_negative():
    """A zero would make Black-Scholes return pure intrinsic and price every ATM at nothing."""
    t = years_to_expiry(datetime.datetime(2026, 9, 8, 15, 25), datetime.date(2026, 9, 8))
    assert t > 0


# --- pricing --------------------------------------------------------------------------


def test_atm_strike_rounds_to_the_50_point_ladder():
    assert atm_strike_for(24_010.0) == 24_000.0
    assert atm_strike_for(24_030.0) == 24_050.0


def test_an_atm_option_prices_above_the_tick_floor():
    price = theoretical_price(24_000.0, 24_000.0, "call", 3 / 365.0, 0.13)
    assert price > 1.0


def test_calls_and_puts_move_in_opposite_directions_with_spot():
    t, sigma = 3 / 365.0, 0.13
    call_lo = theoretical_price(23_900.0, 24_000.0, "call", t, sigma)
    call_hi = theoretical_price(24_100.0, 24_000.0, "call", t, sigma)
    put_lo = theoretical_price(23_900.0, 24_000.0, "put", t, sigma)
    put_hi = theoretical_price(24_100.0, 24_000.0, "put", t, sigma)
    assert call_hi > call_lo and put_hi < put_lo


def test_higher_volatility_prices_an_option_higher():
    t = 3 / 365.0
    assert theoretical_price(24_000.0, 24_000.0, "call", t, 0.25) > theoretical_price(
        24_000.0, 24_000.0, "call", t, 0.10
    )


# --- replay ---------------------------------------------------------------------------


def _bars(day: datetime.date, closes: list[float], volumes: list[int]) -> list[HistCandle]:
    base = datetime.datetime.combine(day, datetime.time(9, 15))
    return [
        HistCandle(base + datetime.timedelta(minutes=i), c, c, c, c, v)
        for i, (c, v) in enumerate(zip(closes, volumes))
    ]


MOMENTUM_1M = {"mechanism": "momentum", "duration": 1, "direction": "follow"}


def _config(**kw) -> MomentumLongScalperConfig:
    return MomentumLongScalperConfig(signal=kw.pop("signal", MOMENTUM_1M), **kw)


def _replay(bars, config=None, **kw):
    """Bot 3 on the momentum 1-minute series, read from these same bars."""
    config = config or _config()
    readings = series_readings(bars, SeriesKey(config.signal.mechanism, config.signal.duration, "nifty"))
    return run_backtest(bars, config=config, readings=readings, charges=kw.pop("charges", CHARGES),
                        spread=SPREAD, **kw)


def _scripted_call(bars, first: int, last: int, side: str = "bullish") -> dict:
    """A reading per bar: one call standing from bar `first` through bar `last`, quiet around it."""
    out = {}
    started = bars[first].ts.replace(tzinfo=IST).timestamp() + 60
    for k, b in enumerate(bars):
        close = b.ts.replace(tzinfo=IST).timestamp() + 60
        if first <= k <= last:
            out[b.ts] = {"state": side, "call_started_at": started, "held_until": close + 60,
                         "signal": 0.9, "reason": None}
        else:
            out[b.ts] = {"state": "neutral", "reason": "no_expansion", "signal": 0.1}
    return out


def _bar_at(day: datetime.date, hour: int, minute: int, o, h, l, c, v) -> HistCandle:
    return HistCandle(datetime.datetime.combine(day, datetime.time(hour, minute)), o, h, l, c, v)


def test_only_the_live_feeds_minutes_become_candles():
    """ICICI returns pre-open bars from 09:00 and post-close bars to 15:39; live has neither."""
    day = datetime.date(2026, 9, 15)
    bars = [_bar_at(day, h, m, 1.0, 1.0, 1.0, 1.0, 1) for h, m in ((9, 0), (9, 8), (9, 15), (15, 29), (15, 30), (15, 39))]
    pre_open, session = split_session(bars)
    assert [b.ts.time() for b in pre_open] == [datetime.time(9, 0), datetime.time(9, 8)]
    assert [b.ts.time() for b in session] == [datetime.time(9, 15), datetime.time(15, 29)]


def test_session_vwap_prices_bars_at_their_ohlc_average_and_counts_the_pre_open():
    day = datetime.date(2026, 9, 15)
    pre_open = [
        _bar_at(day, 9, 0, 100.0, 100.0, 100.0, 100.0, 10),
        # ICICI served a -650 volume bar in the 2026-09-15 pre-open; it must not count.
        _bar_at(day, 9, 5, 500.0, 500.0, 500.0, 500.0, -650),
    ]
    vwap = SessionVwap(pre_open)
    # OHLC average (100 + 110 + 90 + 104) / 4 = 101.
    assert vwap.add(_bar_at(day, 9, 15, 100.0, 110.0, 90.0, 104.0, 10)) == pytest.approx(100.5)


# Monday 2026-03-09; the next Tuesday expiry is the 10th, so options are 1 day out. Chosen
# deliberately: at 6 DTE a single ATM lot costs more than the default 10,000 outlay and the
# bot correctly refuses to trade at all (see `test_an_expensive_atm_option_is_unaffordable`).
_NEAR_EXPIRY = datetime.date(2026, 3, 9)


def _trending_day(day=_NEAR_EXPIRY):
    """Flat, then a high-volume breakout that keeps running -- a signal and a winner."""
    closes = [24_000.0] * 25 + [24_000.0 + 12 * i for i in range(1, 40)]
    volumes = [1_000] * 25 + [90_000] * 39
    return _bars(day, closes, volumes)


def test_a_trending_day_produces_cycles(and_config=None):
    result = _replay(_trending_day(), vix_by_day={_NEAR_EXPIRY: 13.0})
    assert result.days == 1
    assert len(result.cycles) >= 1
    c = result.cycles[0]
    assert c.right == "call" and c.quantity > 0
    assert c.net_pnl == pytest.approx(c.gross_pnl - c.friction, abs=0.01)


def test_a_flat_day_produces_no_cycles():
    day = datetime.date(2026, 3, 5)
    bars = _bars(day, [24_000.0] * 60, [1_000] * 60)
    result = _replay(bars, vix_by_day={day: 13.0})
    assert result.cycles == []
    assert result.skipped_no_signal > 0


def test_every_cycle_carries_friction():
    """Friction is the binding constraint; a cycle without it is not a cycle."""
    result = _replay(_trending_day(), vix_by_day={_NEAR_EXPIRY: 13.0})
    assert all(c.friction > 0 for c in result.cycles)
    assert result.summary()["friction"] > 0


def test_slippage_is_adverse_on_both_legs_as_in_paper_mode():
    """An earlier draft applied it only on entry, which made backtests flatter paper mode."""
    day = _NEAR_EXPIRY
    generous = _replay(_trending_day(day), charges=ChargesModel(slippage_spread_fraction=0.0),
                       vix_by_day={day: 13.0})
    penalised = _replay(_trending_day(day), charges=ChargesModel(slippage_spread_fraction=0.5),
                        vix_by_day={day: 13.0})
    assert penalised.cycles and generous.cycles
    assert penalised.cycles[0].entry_price > generous.cycles[0].entry_price
    assert penalised.cycles[0].exit_price < generous.cycles[0].exit_price


def test_only_one_position_is_held_at_a_time():
    result = _replay(_trending_day(), vix_by_day={_NEAR_EXPIRY: 13.0})
    for a, b in zip(result.cycles, result.cycles[1:]):
        assert a.exited_at <= b.entered_at


def test_one_call_buys_once_in_the_backtest_too():
    """A call stopped out while it still stands is not re-bought on the same call -- the rule
    the live runtime applies, or the backtest would describe a different strategy."""
    day = _NEAR_EXPIRY
    # The call stands from 09:40; spot falls through it, so the long call is stopped out early.
    closes = [24_000.0] * 25 + [24_000.0 - 15 * i for i in range(1, 30)]
    bars = _bars(day, closes, [1_000] * len(closes))
    readings = _scripted_call(bars, 25, len(bars) - 2)
    result = run_backtest(bars, config=_config(), readings=readings, charges=CHARGES, spread=SPREAD,
                          vix_by_day={day: 13.0})
    assert len(result.cycles) == 1
    assert result.cycles[0].exit_reason == "stop_loss"
    assert result.skipped_same_signal > 0
    assert result.summary()["skipped_same_signal"] == result.skipped_same_signal


def test_a_trade_is_closed_when_its_call_ends():
    """The duration is the horizon the call is about: the trade is held until the call lapses,
    then closed, however the option has moved (stop and ladder permitting)."""
    day = _NEAR_EXPIRY
    closes = [24_000.0] * 25 + [24_000.0 + 1 * i for i in range(1, 30)]
    bars = _bars(day, closes, [1_000] * len(closes))
    readings = _scripted_call(bars, 25, 30)  # stands six minutes, then goes quiet
    result = run_backtest(bars, config=_config(), readings=readings, charges=CHARGES, spread=SPREAD,
                          vix_by_day={day: 13.0})
    assert len(result.cycles) == 1
    c = result.cycles[0]
    assert c.exit_reason == "signal_window_ended"
    # Seen at the close of the first quiet bar (index 31, 09:46), i.e. 09:47.
    assert c.exited_at == datetime.datetime.combine(day, datetime.time(9, 47))


def test_fade_buys_the_put_on_a_bullish_call():
    day = _NEAR_EXPIRY
    closes = [24_000.0] * 40
    bars = _bars(day, closes, [1_000] * 40)
    readings = _scripted_call(bars, 25, 30)
    faded = _config(signal={**MOMENTUM_1M, "direction": "fade"})
    result = run_backtest(bars, config=faded, readings=readings, charges=CHARGES, spread=SPREAD,
                          vix_by_day={day: 13.0})
    assert result.cycles and result.cycles[0].right == "put"


def test_a_position_never_carries_across_days():
    """A candle history is not a position; each session starts flat."""
    d1, d2 = _NEAR_EXPIRY, datetime.date(2026, 3, 10)
    result = _replay(_trending_day(d1) + _trending_day(d2), vix_by_day={d1: 13.0, d2: 13.0})
    assert result.days == 2
    for c in result.cycles:
        assert c.entered_at.date() == c.exited_at.date()


def test_the_result_states_its_iv_and_spread_sources():
    """A run priced off a fallback must never be mistaken for a calibrated one."""
    day = _NEAR_EXPIRY
    with_vix = _replay(_trending_day(day), vix_by_day={day: 13.0})
    assert with_vix.summary()["iv_source"] == "daily India VIX"
    assert "no calibration yet" in with_vix.summary()["spread_source"]

    without = _replay(_trending_day(day), vix_by_day={})
    assert without.summary()["iv_source"].startswith("constant")


# --- store ----------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    path = str(tmp_path / "backtest.sqlite3")
    backtest_store.ensure_tables(path)
    backtest_store.ensure_tables(path)  # idempotent
    return path


def test_candles_round_trip_and_refetching_is_idempotent(store):
    rows = [
        {"datetime": "2026-03-04 09:15:00", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100},
        {"datetime": "2026-03-04 09:16:00", "open": 1.5, "high": 2, "low": 1, "close": 1.8, "volume": 200},
    ]
    assert backtest_store.store_candles(rows, path=store) == 2
    assert backtest_store.store_candles(rows, path=store) == 2  # re-fetch overlapping range
    assert len(backtest_store.load_candles(path=store)) == 2  # but no duplicates


def test_unparseable_rows_are_skipped_not_fatal(store):
    rows = [{"datetime": "nonsense", "close": 1}, {"datetime": "2026-03-04 09:15:00", "close": 1.5}]
    assert backtest_store.store_candles(rows, path=store) == 1


def test_coverage_reports_what_is_cached(store):
    backtest_store.store_candles(
        [{"datetime": "2026-03-04 09:15:00", "close": 1.5, "volume": 10}], path=store
    )
    backtest_store.store_vix([{"date": "2026-03-04", "value": 13.2}], path=store)
    cov = backtest_store.coverage(path=store)
    assert cov["candles"] == 1 and cov["vix_days"] == 1


# --- spread calibration ---------------------------------------------------------------


@pytest.fixture
def spread_db(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(spreads, "_db_path", lambda: path)
    ensure_bots_tables(path)
    spreads.reset_throttle_for_tests()
    return path


def test_uncalibrated_spread_reports_itself_as_a_default(spread_db):
    stats = spreads.spread_stats()
    assert stats.source == "default"
    assert "no calibration yet" in stats.describe()


def test_sampling_is_throttled_per_contract(spread_db):
    """A quote is read every pass; storing all of them would be tens of thousands of rows."""
    args = ("NIFTY", "10-Sep-2026", 24_000.0, "call", 100.0, 101.0)
    assert spreads.record_spread_sample(*args) is True
    assert spreads.record_spread_sample(*args) is False  # same contract, same minute


def test_a_one_sided_or_crossed_quote_is_not_a_sample(spread_db):
    assert spreads.record_spread_sample("NIFTY", "E", 1.0, "call", None, 101.0) is False
    assert spreads.record_spread_sample("NIFTY", "E", 2.0, "call", 0.0, 101.0) is False
    assert spreads.record_spread_sample("NIFTY", "E", 3.0, "call", 102.0, 101.0) is False  # crossed


def test_enough_samples_switch_the_source_to_observed(spread_db):
    for i in range(spreads.MIN_SAMPLES_FOR_CALIBRATION):
        spreads.reset_throttle_for_tests()
        spreads.record_spread_sample("NIFTY", "E", float(i), "call", 99.5, 100.5)
    stats = spreads.spread_stats()
    assert stats.source == "observed"
    assert stats.samples >= spreads.MIN_SAMPLES_FOR_CALIBRATION
    # 1.00 spread on a 100 mid is 1% of premium.
    assert stats.median_spread_pct == pytest.approx(1.0, abs=0.01)


def test_spread_scales_with_premium_and_is_floored_at_a_tick(spread_db):
    stats = SpreadStats(source="observed", samples=999, median_spread_pct=0.5)
    assert stats.spread_for(200.0) == pytest.approx(1.0)
    assert stats.spread_for(1.0) == pytest.approx(0.05)  # floored


def test_the_raised_outlay_is_what_makes_a_full_week_tradeable():
    """Why the default moved from 10,000 to 25,000 (plan section 8.5).

    With NIFTY's 65 lot size, one ATM lot costs roughly 4,400 at 1 day to expiry and 11,300
    at 6 days (VIX 13). A 10,000 outlay therefore cannot buy a lot for half of most weeks and
    the bot stands down, correctly and quietly. The bot refuses rather than partially funding.
    """
    far = datetime.date(2026, 3, 4)  # Wednesday; expiry the following Tuesday, 6 days out

    at_old_default = _replay(_trending_day(far), config=_config(premium_outlay_inr=10_000.0),
                             vix_by_day={far: 13.0})
    assert at_old_default.cycles == []
    assert at_old_default.skipped_unaffordable > 0

    at_new_default = _replay(_trending_day(far), vix_by_day={far: 13.0})  # 25,000
    assert at_new_default.cycles


def test_position_size_rises_towards_expiry_on_a_fixed_outlay():
    """The accepted cost of keeping the outlay model (plan section 8.5).

    lots = floor(outlay / cost) and an ATM option cheapens as expiry nears, so the same
    outlay buys more lots close to expiry -- putting the largest positions where gamma is
    highest. Pinned here so the behaviour is deliberate rather than discovered later.
    """
    near, far = datetime.date(2026, 3, 9), datetime.date(2026, 3, 4)  # 1 DTE vs 6 DTE
    near_lots = _replay(_trending_day(near), vix_by_day={near: 13.0}).cycles[0].lots
    far_lots = _replay(_trending_day(far), vix_by_day={far: 13.0}).cycles[0].lots
    assert near_lots > far_lots


# -- open interest on the underlying series (#34) -----------------------------------------


def test_futures_bars_round_trip_open_interest_and_treat_zero_as_absent(tmp_path):
    from icici_breeze_backend.app.services.bots.scalping import backtest_store as store

    cache = str(tmp_path / "backtest.sqlite3")
    store.ensure_tables(cache)
    rows = [
        # A pre-open bar: ICICI serves OI 0 there, and on every BSE bar. That is absent, not a
        # reading of zero -- a zero anchor would read as the largest OI rise ever recorded.
        {"datetime": "2026-09-16 09:00:00", "open": 1, "high": 1, "low": 1, "close": 1,
         "volume": 10, "open_interest": 0},
        {"datetime": "2026-09-16 09:15:00", "open": 2, "high": 2, "low": 2, "close": 2,
         "volume": 20, "open_interest": 18_039_125},
        # No key at all, as the cash index series comes back.
        {"datetime": "2026-09-16 09:16:00", "open": 3, "high": 3, "low": 3, "close": 3,
         "volume": 30},
    ]
    assert store.store_candles(rows, stock_code="NIFTY", path=cache) == 3

    bars = store.load_candles(stock_code="NIFTY", path=cache)
    assert [b.oi for b in bars] == [None, 18_039_125, None]
    assert [b.volume for b in bars] == [10, 20, 30]


def test_the_oi_column_is_added_to_a_futures_cache_written_before_it_existed(tmp_path):
    """Upgrading must not lose the bars already cached, or refuse to write new ones."""
    import sqlite3

    from icici_breeze_backend.app.services.bots.scalping import backtest_store as store

    cache = str(tmp_path / "backtest.sqlite3")
    with sqlite3.connect(cache) as conn:  # the pre-#34 shape
        conn.execute(
            "CREATE TABLE futures_candles (stock_code TEXT NOT NULL, ts TEXT NOT NULL, "
            "open REAL, high REAL, low REAL, close REAL, volume INTEGER, "
            "PRIMARY KEY (stock_code, ts))"
        )
        conn.execute(
            "INSERT INTO futures_candles VALUES ('NIFTY','2026-09-15 09:15:00',1,1,1,1,5)"
        )
        conn.commit()

    store.ensure_tables(cache)  # the ALTER that adds `oi` to a cache that predates it
    store.store_candles(
        [{"datetime": "2026-09-16 09:15:00", "open": 2, "high": 2, "low": 2, "close": 2,
          "volume": 7, "open_interest": 999}],
        stock_code="NIFTY",
        path=cache,
    )
    old, new = store.load_candles(stock_code="NIFTY", path=cache)
    assert old.volume == 5 and old.oi is None  # NULL is the truth about a pre-#34 bar
    assert new.oi == 999
