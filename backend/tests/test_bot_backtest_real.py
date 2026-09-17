"""Real-price backtests (docs/bots-scalping-plan.md sections 8.6-8.9).

What these pin: the replays run the live gate stack; real and modelled prices are never mixed;
an uncached contract stops a day (and the day's partial trades are discarded) rather than
being guessed at; the fetcher never stores a refused request as an empty success, never runs
during market hours, and asks only for what a replay actually needs.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from dataclasses import replace

import pytest

from icici_breeze_backend.app.db.bots_migrate import ensure_bots_tables
from icici_breeze_backend.app.domain.bots import (
    ExpiryIndexWriterConfig,
    IronFlyScalperConfig,
    MomentumLongScalperConfig,
    ReasonCode,
    SessionWindow,
)
from icici_breeze_backend.app.services.bots.backtest_expiry import (
    EXIT_EXPIRED,
    EXIT_STOP,
    EXIT_TARGET,
    expiry_days,
    run_expiry_backtest,
)
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime
from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
from icici_breeze_backend.app.services.bots.scalping.backtest import BacktestCycle, run_backtest
from icici_breeze_backend.app.services.bots.scalping.backtest_compare import (
    load_paper_cycles,
    match_cycles,
    render,
)
from icici_breeze_backend.app.services.bots.scalping.backtest_fetch import (
    BudgetExhausted,
    Fetcher,
    market_hours_refusal,
    parse_response,
)
from icici_breeze_backend.app.services.bots.scalping.backtest_fly import run_fly_backtest
from icici_breeze_backend.app.services.bots.scalping.backtest_options import (
    NO_DATA,
    OK,
    OptionBook,
    RealPricer,
)
from icici_breeze_backend.app.services.bots.scalping.backtest_store import HistCandle, Need, OptionKey
from icici_breeze_backend.app.services.bots.scalping.spreads import SpreadStats

D, DT = datetime.date, datetime.datetime
MIN = datetime.timedelta(minutes=1)
CHARGES = ChargesModel()
SPREAD = SpreadStats(source="default", samples=0, median_spread_pct=0.5)
_TS = "%Y-%m-%d %H:%M:%S"

# Monday 2026-03-09; NIFTY's weekly expires the next day.
MON = D(2026, 3, 9)
KEY = OptionKey("NIFTY", D(2026, 3, 10), 24_000.0, "call")


@pytest.fixture
def cache(tmp_path):
    path = str(tmp_path / "backtest.sqlite3")
    store.ensure_tables(path)
    return path


def _bars(day, closes, volumes, start=datetime.time(9, 15)):
    base = DT.combine(day, start)
    return [
        HistCandle(base + i * MIN, c, c, c, c, v) for i, (c, v) in enumerate(zip(closes, volumes))
    ]


def _trending(day=MON):
    """Flat, then a high-volume breakout that keeps running -- a signal and a winner."""
    closes = [24_000.0] * 25 + [24_000.0 + 12 * i for i in range(1, 40)]
    volumes = [1_000] * 25 + [90_000] * 39
    return _bars(day, closes, volumes)


def _quiet(day=MON, *, drift_from=None, drift_per_min=0.0, until=datetime.time(14, 0)):
    n = int((DT.combine(day, until) - DT.combine(day, datetime.time(9, 15))) / MIN)
    closes = []
    for m in range(n):
        at = (DT.combine(day, datetime.time(9, 15)) + m * MIN).time()
        moved = drift_from is not None and at >= drift_from
        start_m = (
            int((DT.combine(day, drift_from) - DT.combine(day, datetime.time(9, 15))) / MIN) if moved else m
        )
        closes.append(24_000.0 + (drift_per_min * (m - start_m) if moved else 0.0))
    return _bars(day, closes, [50_000] * n)


class FakeBook:
    """Traded bars for any contract, priced off the underlying: 40 at the money, delta 0.5."""

    def __init__(self, underlying, *, minute_status=OK, volume=100, seconds=False):
        self.needs: set = set()
        self.spot = {b.ts: b.close for b in underlying}
        self.minute_status = minute_status
        self.volume = volume
        self.seconds = seconds

    @staticmethod
    def price(key, spot):
        intrinsic = spot - key.strike if key.right == "call" else key.strike - spot
        return round(max(0.05, 40.0 + 0.5 * intrinsic), 2)

    def _spot_at(self, at):
        known = [ts for ts in self.spot if ts <= at]
        return self.spot[max(known)] if known else next(iter(self.spot.values()))

    def minute_bars(self, key, day):
        if self.minute_status != OK:
            return self.minute_status, {}
        out = {}
        for ts, s in self.spot.items():
            p = self.price(key, s)
            out[ts] = HistCandle(ts, p, p, p, p, self.volume)
        return OK, out

    def second_bars(self, key, start, end):
        if not self.seconds:
            return NO_DATA, []
        bars, t = [], start
        while t <= end:
            p = self.price(key, self._spot_at(t.replace(second=0)))
            bars.append(HistCandle(t, p, p, p, p, 10))
            t += datetime.timedelta(seconds=1)
        return OK, bars


def _momentum(underlying, pricer=None, config=None, spot_bars=None):
    return run_backtest(
        underlying,
        config=config or MomentumLongScalperConfig(),
        charges=CHARGES,
        spread=SPREAD,
        vix_by_day={},
        pricer=pricer,
        spot_bars=underlying if spot_bars is None else spot_bars,
    )


# --- the regime -------------------------------------------------------------------------


def test_every_replayed_day_uses_todays_lot_size():
    """#36: a backtest asks what the bot as configured today would have done -- so a day when
    NIFTY was 75 a lot is still sized at today's 65, deliberately."""
    assert regime.lot_size_for("NIFTY", D(2026, 3, 2)) == 65
    assert regime.lot_size_for("BSESEN", D(2026, 3, 2)) == 20
    assert regime.lot_size_for("NIFTY", D(2025, 12, 31)) == 65


def test_each_index_expires_on_its_own_weekday():
    assert regime.next_expiry(D(2026, 9, 14), regime.EXPIRY_WEEKDAY_MAP["NIFTY"]) == D(2026, 9, 15)
    assert regime.next_expiry(D(2026, 9, 14), regime.EXPIRY_WEEKDAY_MAP["BSESEN"]) == D(2026, 9, 17)


def test_futures_roll_to_next_month_after_the_last_tuesday():
    assert regime.near_month_futures_expiry(D(2026, 9, 29), "NIFTY") == D(2026, 9, 29)
    assert regime.near_month_futures_expiry(D(2026, 9, 30), "NIFTY") == D(2026, 10, 27)


def test_a_holiday_month_end_expiry_moves_earlier():
    assert regime.monthly_expiry(2026, 9, 1, {D(2026, 9, 29)}) == D(2026, 9, 28)


def test_atm_ties_go_to_the_lower_strike_like_the_live_bot():
    assert regime.atm_strike(24_025.0, "NIFTY") == 24_000.0
    assert regime.atm_strike(24_026.0, "NIFTY") == 24_050.0
    assert regime.atm_strike(81_050.0, "BSESEN") == 81_000.0


def test_bot2_strikes_round_away_from_the_money():
    assert regime.strike_beyond(24_480.0, "NIFTY", up=True) == 24_500.0
    assert regime.strike_beyond(23_520.0, "NIFTY", up=False) == 23_500.0


# --- the cache --------------------------------------------------------------------------


def test_an_old_cache_gains_the_futures_contract_column(tmp_path):
    path = str(tmp_path / "old.sqlite3")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE futures_candles (stock_code TEXT NOT NULL, ts TEXT NOT NULL, open REAL, "
            "high REAL, low REAL, close REAL, volume INTEGER, PRIMARY KEY (stock_code, ts))"
        )
    store.ensure_tables(path)
    rows = [{"datetime": "2026-03-04 09:15:00", "close": 1.0, "volume": 1}]
    assert store.store_candles(rows, path=path, expiry=D(2026, 3, 31)) == 1


def test_option_bars_round_trip_by_contract_and_interval(cache):
    rows = [{"datetime": "2026-03-09 10:00:00", "open": 50, "high": 55, "low": 49, "close": 52,
             "volume": 10, "open_interest": 1000}]
    assert store.store_option_candles(rows, KEY, store.INTERVAL_MINUTE, path=cache) == 1
    day = (DT(2026, 3, 9, 9, 15), DT(2026, 3, 9, 15, 30))
    assert [b.close for b in store.load_option_bars(KEY, store.INTERVAL_MINUTE, *day, path=cache)] == [52.0]
    assert store.load_option_bars(KEY, store.INTERVAL_SECOND, *day, path=cache) == []
    assert store.load_option_bars(replace(KEY, right="put"), store.INTERVAL_MINUTE, *day, path=cache) == []


def test_a_fetched_window_clears_its_need_and_covers_sub_windows(cache):
    need = Need(KEY, store.INTERVAL_MINUTE, DT(2026, 3, 9, 9, 15), DT(2026, 3, 9, 15, 30))
    assert store.add_needs([need, need], path=cache) == 1
    assert store.pending_needs(path=cache) == [need]
    store.record_fetch(need, 0, path=cache)  # an empty answer is still an answer
    assert store.pending_needs(path=cache) == []
    assert store.fetched(Need(KEY, store.INTERVAL_MINUTE, DT(2026, 3, 9, 10), DT(2026, 3, 9, 11)), path=cache)
    assert not store.fetched(
        Need(KEY, store.INTERVAL_SECOND, DT(2026, 3, 9, 10), DT(2026, 3, 9, 10, 15)), path=cache
    )


def test_coverage_lists_short_days_and_pending_needs(cache):
    store.store_candles([{"datetime": "2026-03-04 09:15:00", "close": 1, "volume": 1}], path=cache)
    store.add_needs([Need(KEY, store.INTERVAL_MINUTE, DT(2026, 3, 9, 9, 15), DT(2026, 3, 9, 15, 30))], path=cache)
    cov = store.coverage(path=cache)
    assert cov["short_futures_days"] == {"2026-03-04": 1}
    assert cov["option_needs_pending"] == 1


# --- the fetcher ------------------------------------------------------------------------


class FakeSdk:
    def __init__(self, respond=None):
        self.calls = []
        self.respond = respond or (lambda p: {"Status": 200, "Success": [], "Error": None})

    def get_historical_data_v2(self, **params):
        self.calls.append(params)
        return self.respond(params)


def _fetcher(sdk, cache, **kw):
    logs = []
    return Fetcher(sdk, path=cache, log=logs.append, sleep=lambda s: None, **kw), logs


def test_fetching_is_refused_during_market_hours_on_trading_days():
    assert market_hours_refusal(DT(2026, 9, 14, 10, 0), trading_day=True)
    assert market_hours_refusal(DT(2026, 9, 14, 9, 0), trading_day=True)
    assert market_hours_refusal(DT(2026, 9, 14, 8, 59), trading_day=True) is None
    assert market_hours_refusal(DT(2026, 9, 14, 15, 45), trading_day=True) is None
    assert market_hours_refusal(DT(2026, 9, 13, 10, 0), trading_day=False) is None


def test_an_sdk_refusal_is_an_error_not_an_empty_success():
    """The first fetcher stored '0 bars' for every chunk: the SDK refuses an NFO request with
    no expiry before it reaches ICICI, and nothing read the refusal."""
    rows, error = parse_response({"Success": "", "Status": 500, "Error": "Expiry-Date cannot be empty"})
    assert rows == [] and "Expiry-Date" in error
    rows, error = parse_response({"Status": 200, "Success": [{"close": 1}], "Error": None})
    assert rows == [{"close": 1}] and error is None


def test_futures_fetch_passes_an_expiry_and_rolls_contracts(cache):
    sdk = FakeSdk()
    fetcher, _ = _fetcher(sdk, cache)
    fetcher.fetch_futures("NIFTY", D(2026, 9, 28), D(2026, 10, 1))
    assert [c["expiry_date"] for c in sdk.calls] == ["2026-09-29T06:00:00.000Z", "2026-10-27T06:00:00.000Z"]
    assert all(c["product_type"] == "futures" and c["exchange_code"] == "NFO" for c in sdk.calls)


def test_complete_days_are_not_fetched_again(cache):
    full = [
        {"datetime": (DT(2026, 9, 28, 9, 15) + m * MIN).strftime(_TS), "close": 1, "volume": 1}
        for m in range(store.SESSION_BARS)
    ]
    store.store_candles(full, path=cache)
    sdk = FakeSdk()
    fetcher, _ = _fetcher(sdk, cache)
    fetcher.fetch_futures("NIFTY", D(2026, 9, 28), D(2026, 9, 29))
    assert len(sdk.calls) == 1 and sdk.calls[0]["from_date"].startswith("2026-09-29")


def test_a_refused_request_is_reported_and_nothing_is_stored(cache):
    sdk = FakeSdk(lambda p: {"Success": "", "Status": 500, "Error": "Expiry-Date cannot be empty"})
    fetcher, logs = _fetcher(sdk, cache)
    assert fetcher.fetch_futures("NIFTY", D(2026, 9, 28), D(2026, 9, 29)) == 0
    assert any("ERROR" in line for line in logs)


def test_a_response_at_the_cap_is_flagged_as_truncated(cache):
    store.set_meta("max_bars_per_call", "3", path=cache)
    rows = [{"datetime": f"2026-09-28 09:1{m}:00", "close": 1, "volume": 1} for m in range(5, 8)]
    fetcher, logs = _fetcher(FakeSdk(lambda p: {"Status": 200, "Success": rows}), cache)
    fetcher.fetch_futures("NIFTY", D(2026, 9, 28), D(2026, 9, 28))
    assert any("truncated" in line for line in logs)


def test_needed_windows_are_fetched_recorded_and_cleared(cache):
    need = Need(KEY, store.INTERVAL_MINUTE, DT(2026, 3, 9, 9, 15), DT(2026, 3, 9, 15, 30))
    store.add_needs([need], path=cache)
    rows = [{"datetime": "2026-03-09 10:00:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 5}]
    sdk = FakeSdk(lambda p: {"Status": 200, "Success": rows})
    fetcher, _ = _fetcher(sdk, cache)
    assert fetcher.fetch_needs(store.pending_needs(path=cache)) == {
        "fetched": 1, "bars": 1, "empty": 0, "errors": 0,
    }
    call = sdk.calls[0]
    assert call["product_type"] == "options" and call["right"] == "call"
    assert call["strike_price"] == "24000" and call["exchange_code"] == "NFO"
    assert call["expiry_date"] == "2026-03-10T06:00:00.000Z"
    assert store.pending_needs(path=cache) == [] and store.fetched(need, path=cache)


def test_a_throttle_leaves_the_need_pending_but_no_data_is_an_answer(cache):
    day = (DT(2026, 3, 9, 9, 15), DT(2026, 3, 9, 15, 30))
    call, put = Need(KEY, store.INTERVAL_MINUTE, *day), Need(replace(KEY, right="put"), store.INTERVAL_MINUTE, *day)
    store.add_needs([call, put], path=cache)

    def respond(p):
        if p["right"] == "call":
            return {"Status": 429, "Error": "Too Many Requests"}
        return {"Status": 500, "Error": "No Data Found"}

    fetcher, _ = _fetcher(FakeSdk(respond), cache)
    stats = fetcher.fetch_needs(store.pending_needs(path=cache))
    assert stats["errors"] == 1 and stats["empty"] == 1
    assert store.pending_needs(path=cache) == [call]


def test_the_call_budget_stops_a_run(cache):
    fetcher, _ = _fetcher(FakeSdk(), cache, max_calls=1)
    fetcher.call(interval="1minute")
    with pytest.raises(BudgetExhausted):
        fetcher.call(interval="1minute")


def test_one_second_windows_follow_the_measured_request_clock(cache):
    fetcher, _ = _fetcher(FakeSdk(), cache)
    at = DT(2026, 3, 9, 10, 0)
    assert fetcher.stamp(at) == "2026-03-09T10:00:00.000Z"
    store.set_meta("request_clock", "utc", path=cache)
    assert fetcher.stamp(at) == "2026-03-09T04:30:00.000Z"


# --- Bot 3 on real prices ---------------------------------------------------------------


def test_real_prices_replay_the_live_signal_at_the_current_lot():
    underlying = _trending()
    result = _momentum(underlying, RealPricer(FakeBook(underlying)))
    assert result.cycles
    c = result.cycles[0]
    assert c.quantity % 65 == 0 and c.resolution == "1m"
    assert c.entered_at.second == 0  # the next minute's open
    assert result.summary()["price_source"].startswith("real")


def test_one_second_bars_drive_the_trade_when_icici_has_them():
    underlying = _trending()
    result = _momentum(underlying, RealPricer(FakeBook(underlying, seconds=True)))
    assert result.cycles and result.cycles[0].resolution == "1s"
    assert result.cycles[0].entered_at.second >= 2  # after the runtime's reaction delay


def test_an_uncached_contract_stops_the_day_and_becomes_a_need(cache):
    underlying = _trending()
    book = OptionBook(cache)
    result = _momentum(underlying, RealPricer(book))
    assert result.days_awaiting_data == 1 and result.cycles == []
    (need,) = book.needs
    assert need.interval == store.INTERVAL_MINUTE and need.key.expiry == D(2026, 3, 10)


def _fill_need_from(fake, need, cache):
    if need.interval == store.INTERVAL_SECOND:
        store.record_fetch(need, 0, path=cache)  # as if ICICI had no 1-second bars
        return
    _, bars = fake.minute_bars(need.key, need.start.date())
    rows = [
        {"datetime": ts.strftime(_TS), "open": b.open, "high": b.high, "low": b.low,
         "close": b.close, "volume": b.volume}
        for ts, b in bars.items()
    ]
    store.store_option_candles(rows, need.key, need.interval, path=cache)
    store.record_fetch(need, len(rows), path=cache)


def test_backfill_rounds_converge_on_the_answer_full_data_gives(cache):
    """Replay, fetch what it lacked, repeat: the loop `backfill` runs. A day that is still
    waiting must contribute nothing -- its partial trades would otherwise be counted twice."""
    underlying = _trending()
    fake = FakeBook(underlying)
    for _ in range(10):
        book = OptionBook(cache)
        result = _momentum(underlying, RealPricer(book))
        if not book.needs:
            break
        assert result.cycles == []  # a waiting day keeps nothing
        for need in book.needs:
            _fill_need_from(fake, need, cache)
    else:
        pytest.fail("backfill did not converge")
    direct = _momentum(underlying, RealPricer(FakeBook(underlying)))
    assert result.days_awaiting_data == 0
    assert [c.net_pnl for c in result.cycles] == [c.net_pnl for c in direct.cycles]


def test_a_contract_icici_had_nothing_for_is_skipped_never_modelled():
    underlying = _trending()
    result = _momentum(underlying, RealPricer(FakeBook(underlying, minute_status=NO_DATA)))
    assert result.cycles == [] and result.skipped_no_data > 0


def test_a_minute_with_no_trade_is_no_fill():
    underlying = _trending()
    result = _momentum(underlying, RealPricer(FakeBook(underlying, volume=0)))
    assert result.cycles == [] and result.skipped_no_fill > 0


def test_real_pricing_needs_the_cash_index_for_its_strike():
    underlying = _trending()
    result = _momentum(underlying, RealPricer(FakeBook(underlying)), spot_bars=[])
    assert result.days_without_spot == 1 and result.cycles == []


def test_session_windows_come_from_the_live_gate_stack():
    config = MomentumLongScalperConfig(sessions=[SessionWindow(start="10:30", end="11:30")])
    result = _momentum(_trending(), config=config)
    assert result.cycles == []
    assert result.idle.get(ReasonCode.OUTSIDE_SESSION_WINDOW, 0) > 0


def test_expiry_day_is_not_traded_by_default():
    result = _momentum(_trending(D(2026, 3, 10)))
    assert result.cycles == []
    assert result.idle.get(ReasonCode.NOT_A_FIRING_DAY, 0) > 0


def test_days_before_the_history_start_are_replayed_like_any_other():
    early = _momentum(_trending(D(2025, 12, 15)))
    later = _momentum(_trending(MON))
    assert early.days_outside_history == 0
    assert len(early.cycles) == len(later.cycles)


# --- Bot 4 ------------------------------------------------------------------------------


def _fly(underlying, pricer=None, lots=3):
    return run_fly_backtest(
        underlying,
        config=IronFlyScalperConfig(),
        charges=CHARGES,
        spread=SPREAD,
        vix_by_day={MON: 13.0},
        spot_bars=underlying,
        pricer=pricer,
        lots=lots,
    )


def test_a_quiet_day_opens_a_fly_inside_its_window_and_flattens_by_its_end():
    result = _fly(_quiet())
    assert result.cycles
    for c in result.cycles:
        assert DT.combine(MON, datetime.time(11, 30)) <= c.entered_at
        assert c.exited_at <= DT.combine(MON, datetime.time(13, 30))
        assert c.quantity == 3 * 65
        assert c.net_pnl == pytest.approx(c.gross_pnl - c.friction, abs=0.01)
    assert result.cycles[-1].exit_reason in {ReasonCode.SQUARE_OFF, ReasonCode.CREDIT_DECAY_TARGET}


def test_a_drift_past_the_limit_closes_the_fly():
    result = _fly(_quiet(drift_from=datetime.time(11, 45), drift_per_min=10.0))
    assert result.cycles and result.cycles[0].exit_reason == ReasonCode.DRIFT_STOP


def test_real_fly_entries_queue_all_four_legs_in_one_pass(cache):
    book = OptionBook(cache)
    result = _fly(_quiet(), RealPricer(book))
    assert result.days_awaiting_data == 1 and result.cycles == []
    assert {(n.key.strike, n.key.right) for n in book.needs} == {
        (24_150.0, "call"), (23_850.0, "put"), (24_000.0, "call"), (24_000.0, "put"),
    }


# --- Bot 2 ------------------------------------------------------------------------------


class ScriptedPricer:
    real = True
    source = "scripted"

    def __init__(self, ohlc):
        self.ohlc = ohlc

    def bar(self, key, minute, *, spot=0.0, sigma=0.0):
        o, h, l, c = self.ohlc(key, minute)
        return OK, HistCandle(minute, o, h, l, c, 10)


def _flat(price):
    return lambda key, minute: (price, price, price, price)


EXPIRY = D(2026, 3, 10)


def _writer(pricer, strategies=("naked_pe",), *, index="NIFTY", day=EXPIRY, spot=24_000.0, close=None):
    closes = [spot] * 375
    if close is not None:
        closes[-1] = close
    return run_expiry_backtest(
        index=index,
        days=[day],
        spot_bars=_bars(day, closes, [0] * 375),
        config=ExpiryIndexWriterConfig(),
        charges=CHARGES,
        spread=SPREAD,
        strategies=strategies,
        pricer=pricer,
    )


def test_expiry_days_follow_each_indexs_weekday_and_holidays():
    assert expiry_days("NIFTY", D(2026, 9, 1), D(2026, 9, 30)) == [
        D(2026, 9, 1), D(2026, 9, 8), D(2026, 9, 15), D(2026, 9, 22), D(2026, 9, 29),
    ]
    assert expiry_days("NIFTY", D(2026, 9, 14), D(2026, 9, 16), {D(2026, 9, 15)}) == [D(2026, 9, 14)]
    assert expiry_days("BSESEN", D(2026, 9, 14), D(2026, 9, 18)) == [D(2026, 9, 17)]


def test_an_otm_short_left_alone_expires_worthless_and_keeps_its_premium():
    (trade,) = _writer(ScriptedPricer(_flat(10.0))).trades
    assert trade.exit_reason == EXIT_EXPIRED and trade.legs[0].strike == 23_500.0
    assert trade.gross_pnl == pytest.approx(trade.legs[0].entry_price * 65)
    assert trade.net_pnl < trade.gross_pnl


def test_the_loss_stop_is_n_times_the_premium_collected():
    def ohlc(key, m):
        return (10.0,) * 4 if m.hour < 11 else (30.0,) * 4

    trade = _writer(ScriptedPricer(ohlc)).trades[0]
    assert trade.exit_reason == EXIT_STOP and trade.exited_at.hour == 11
    assert trade.net_pnl < -0.9 * trade.premium_inr


def test_profit_booking_fires_once_half_the_premium_is_captured():
    def ohlc(key, m):
        return (10.0,) * 4 if m.hour < 11 else (4.0,) * 4

    trade = _writer(ScriptedPricer(ohlc)).trades[0]
    assert trade.exit_reason == EXIT_TARGET and trade.exited_at.hour == 11
    assert trade.gross_pnl > 0


def test_a_single_leg_minute_is_judged_adverse_extreme_first():
    def ohlc(key, m):
        return (10.0, 30.0, 4.0, 10.0) if m.hour == 11 and m.minute == 0 else (10.0,) * 4

    trade = _writer(ScriptedPricer(ohlc)).trades[0]
    assert trade.exit_reason == EXIT_STOP


def test_a_strangle_books_only_when_both_legs_are_cheap():
    def ohlc(key, m):
        if m.hour >= 12 or (m.hour == 11 and key.right == "call"):
            return (4.0,) * 4
        return (10.0,) * 4

    trade = _writer(ScriptedPricer(ohlc), ("short_strangle",)).trades[0]
    assert trade.exit_reason == EXIT_TARGET and trade.exited_at.hour == 12


def test_strategies_are_reported_side_by_side_not_ranked():
    result = _writer(ScriptedPricer(_flat(10.0)), ("naked_ce", "naked_pe", "short_strangle"))
    assert [t.strategy for t in result.trades] == ["naked_ce", "naked_pe", "short_strangle"]
    assert set(result.summary()["by_strategy"]) == {
        "NIFTY naked_ce", "NIFTY naked_pe", "NIFTY short_strangle",
    }


def test_an_itm_short_at_expiry_pays_its_intrinsic_value():
    trade = _writer(ScriptedPricer(_flat(10.0)), close=23_400.0).trades[0]
    assert trade.exit_reason == EXIT_EXPIRED
    assert trade.legs[0].exit_price == 100.0 and trade.gross_pnl < 0


def test_sensex_trades_its_own_lot_at_bse_rates():
    trade = _writer(ScriptedPricer(_flat(10.0)), index="BSESEN", day=D(2026, 3, 12), spot=80_000.0).trades[0]
    assert trade.quantity == 20 and trade.legs[0].strike == 78_400.0
    assert trade.friction == pytest.approx(
        CHARGES.leg_charges(trade.legs[0].entry_price, 20, is_buy=False, exchange_code="BFO"), abs=0.01
    )


def test_uncached_legs_stop_the_day_with_every_leg_queued(cache):
    book = OptionBook(cache)
    result = _writer(RealPricer(book), ("short_strangle",))
    assert result.days_awaiting_data == 1 and result.trades == [] and len(book.needs) == 2


# --- paper against backtest -------------------------------------------------------------


def test_paper_cycles_are_read_and_matched_to_backtest_trades(tmp_path):
    db = str(tmp_path / "users.sqlite3")
    ensure_bots_tables(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO bot_cycles (id, run_id, user_id, bot_type, cycle_no, structure, legs, lots, "
            "opened_at, closed_at, gross_pnl, friction, net_pnl, exit_reason_code, detail, paper) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            ("c1", "r1", "u1", "momentum_long_scalper", 1, "long_ce",
             json.dumps([{"strike_price": 24000, "right": "call"}]), 5,
             "2026-09-11 13:48:00", "2026-09-11 13:54:20", 3000.0, 150.0, 2850.0,
             "trailing_stop", json.dumps({"entry": {"price": 110.5}})),
        )
    paper = load_paper_cycles("momentum_long_scalper", D(2026, 9, 11), db=db)
    assert len(paper) == 1 and paper[0].entry == 110.5 and paper[0].strike == 24_000.0

    near = BacktestCycle(
        entered_at=DT(2026, 9, 11, 13, 49), exited_at=DT(2026, 9, 11, 13, 55), right="call",
        strike=24_000.0, lots=5, quantity=325, entry_price=111.0, exit_price=115.0,
        gross_pnl=1300.0, friction=150.0, net_pnl=1150.0, exit_reason="trailing_stop",
        spot_entry=0.0, spot_exit=0.0, iv=0.13, resolution="1s",
    )
    far = replace(near, entered_at=DT(2026, 9, 11, 14, 30))
    pairs, lonely, extra = match_cycles(paper, [far, near])
    assert len(pairs) == 1 and pairs[0].backtest is near and pairs[0].entry_diff == 0.5
    assert lonely == [] and extra == [far]

    lines = render(
        D(2026, 9, 11), paper, [near, far],
        config_hash_now="new", config_hashes_then={"old"}, price_source="real",
    )
    assert any("WARNING" in line for line in lines)
    assert any("median |entry difference|: 0.50" in line for line in lines)
