"""The condor replay: cycles, tranches, rolls and exits on a scripted NIFTY path, and the
demand-driven fetch loop -- a replay that stops for data must resume to the same result as
one that had everything cached from the start."""
from __future__ import annotations

import datetime
import math

import pytest

from icici_breeze_backend.app.domain.condor import CondorSettings
from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime
from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
from icici_breeze_backend.app.services.bots.scalping.backtest_store import Need, OptionKey
from icici_breeze_backend.app.services.bots.scalping.spreads import SpreadStats
from icici_breeze_backend.app.services.condor.backtest import CondorReplay, price_at
from icici_breeze_backend.app.services.condor.orders import unhedged_rights
from icici_breeze_backend.app.services.condor.pricing import bs_price, years_to_expiry_close
from tests.fixtures.condor_chain import CHARGES, smile_sigma

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
SPREAD = SpreadStats(source="default", samples=0, median_spread_pct=0.5)
START = datetime.date(2026, 3, 2)
END = datetime.date(2026, 6, 30)


class PathSource:
    """Spot follows `path(day)`; options are Black-Scholes on the test smile. Contracts are
    'missing' until fetched, unless `everything_cached`."""

    def __init__(self, path, *, everything_cached: bool = False):
        self.path = path
        self.everything = everything_cached
        self.cached: set = set()
        self.fetch_calls = 0

    def spot(self, at):
        return self.path(at.date())

    def option(self, contract, at):
        if not self.everything and contract not in self.cached:
            return "missing", None
        expiry, strike, right = contract
        spot = self.path(at.date())
        t = years_to_expiry_close(expiry, at.replace(tzinfo=IST))
        return "ok", max(0.05, bs_price(right, spot, strike, t, smile_sigma(strike, spot)))

    def needs(self, contract, at, until):
        expiry, strike, right = contract
        key = OptionKey("NIFTY", expiry, strike, "call" if right == "Call" else "put")
        return [Need(key, store.INTERVAL_5MIN, datetime.datetime.combine(at.date(), datetime.time()), datetime.datetime.combine(expiry, datetime.time()))]

    def fetch(self, needs):
        for n in needs:
            self.fetch_calls += 1
            self.cached.add((n.key.expiry, n.key.strike, "Call" if n.key.right == "call" else "Put"))

    def refresh(self):
        pass


def flat(day):
    return 24200.0


def trend_up(day):
    # +0.25% a session from March: about +20% by the end of June.
    return 24200.0 * (1.0025 ** max(0, (day - START).days * 5 / 7))


def whipsaw(day):
    return 24200.0 * (1 + 0.04 * math.sin((day - START).days / 6.0))


def _replay(path, *, settings=None, exit_action="time_roll", everything=True, **kw):
    settings = settings or CondorSettings(margin_ceiling_inr=60_00_000)
    source = PathSource(path, everything_cached=everything)
    replay = CondorReplay(
        settings, START, END, lots_per_tranche=2, exit_action=exit_action, source=source,
        charges=CHARGES, spread=SPREAD, holidays=set(), **kw,
    )
    return replay, source


def _run_to_end(replay, source, *, max_rounds=5000):
    rounds = 0
    while True:
        needs = replay.run()
        if not needs:
            return rounds
        source.fetch(needs)
        rounds += 1
        assert rounds < max_rounds


def test_flat_market_enters_three_tranches_and_rolls_cycles_at_21_dte():
    replay, source = _replay(flat)
    _run_to_end(replay, source)
    s = replay.summary()
    assert s["complete"]
    cycles = [cy for c in s["campaigns"] for cy in c["cycles"]]
    assert len(cycles) >= 3
    closed = [cy for cy in cycles if cy["closed"]]
    assert closed and all(cy["close_reason"] == "exit_dte" for cy in closed)
    assert all(cy["tranches"] == 3 for cy in closed)
    # A market that never moves pays the theta: the campaign makes money.
    assert s["campaigns"][0]["pnl"] > 0


def test_time_roll_keeps_one_campaign_and_close_starts_fresh_ones():
    roll, src = _replay(flat, exit_action="time_roll")
    _run_to_end(roll, src)
    close, src2 = _replay(flat, exit_action="close")
    _run_to_end(close, src2)
    assert len(roll.summary()["campaigns"]) == 1
    assert len(close.summary()["campaigns"]) > 1


def test_trend_forces_rolls_and_never_leaves_a_naked_short():
    replay, source = _replay(trend_up)
    original_fill = replay._fill

    def checked_fill(cycle, orders):
        out = original_fill(cycle, orders)
        position = {k: int(v[0]) for k, v in cycle.legs.items()}
        assert not unhedged_rights(position), position
        return out

    replay._fill = checked_fill
    _run_to_end(replay, source)
    s = replay.summary()
    assert s["rolls"] > 0
    roll_events = [e for e in replay.events if e["action"] == "roll"]
    assert all({o["right"] for o in e["orders"]} == {"Put"} for e in roll_events)


def test_max_loss_ends_the_campaign():
    tight = CondorSettings(margin_ceiling_inr=60_00_000, max_loss_inr=5_000, max_loss_pct_of_ceiling=None)
    replay, source = _replay(trend_up, settings=tight)
    _run_to_end(replay, source)
    reasons = [c["end_reason"] for c in replay.summary()["campaigns"]]
    assert "max_loss" in reasons


def test_resumed_replay_matches_one_with_everything_cached():
    full, src_full = _replay(whipsaw, everything=True)
    _run_to_end(full, src_full)
    lazy, src_lazy = _replay(whipsaw, everything=False)
    rounds = _run_to_end(lazy, src_lazy)
    assert rounds > 0
    a, b = full.summary(), lazy.summary()
    assert a["campaigns"] == b["campaigns"]
    assert a["max_drawdown"] == pytest.approx(b["max_drawdown"])
    # Demand-driven: far fewer contracts than a whole chain over four months.
    assert len(src_lazy.cached) < 400


def test_equity_tracks_a_drawdown_in_a_whipsaw():
    replay, source = _replay(whipsaw)
    _run_to_end(replay, source)
    assert replay.summary()["max_drawdown"] > 0


def test_price_at_reads_the_bar_the_check_falls_in_else_the_last_closed():
    t0 = datetime.datetime(2026, 3, 2, 15, 25)
    bars = [
        store.HistCandle(t0, 10, 11, 9, 10.5, 100),
        store.HistCandle(t0 + datetime.timedelta(minutes=5), 12, 12, 11, 11.5, 50),
    ]
    assert price_at(bars, datetime.datetime(2026, 3, 2, 15, 31)) == ("ok", 12)
    quiet = [bars[0], store.HistCandle(t0 + datetime.timedelta(minutes=5), 10.5, 10.5, 10.5, 10.5, 0)]
    assert price_at(quiet, datetime.datetime(2026, 3, 2, 15, 31)) == ("ok", 10.5)
    assert price_at(bars, datetime.datetime(2026, 3, 2, 17, 0)) == ("stale", None)


def test_schedule_uses_the_configured_check_times():
    s = CondorSettings(margin_ceiling_inr=1_00_000, sod_check_ist="10:45", eod_check_ist="15:33")
    replay, _ = _replay(flat, settings=s)
    times = {ts.strftime("%H:%M") for _, _, ts in replay.checks}
    assert times == {"10:45", "15:33"}


def test_listed_expiries_are_tuesdays_and_include_the_monthly():
    replay, _ = _replay(flat)
    exp = replay.listed_expiries(datetime.date(2026, 3, 2))
    assert all(e.weekday() == 1 for e in exp)
    assert regime.monthly_expiry(2026, 4, 1) in exp


# ---- the store-backed source ------------------------------------------------------------

from icici_breeze_backend.app.services.condor.backtest import BLOCK_SESSIONS, StoreSource  # noqa: E402


def _bars(day, start_hhmm, closes, step_minutes):
    h, m = (int(x) for x in start_hhmm.split(":"))
    t = datetime.datetime.combine(day, datetime.time(h, m))
    return [
        {"datetime": (t + datetime.timedelta(minutes=step_minutes * i)).strftime("%Y-%m-%d %H:%M:%S"),
         "open": c, "high": c, "low": c, "close": c, "volume": 10}
        for i, c in enumerate(closes)
    ]


def test_store_source_reads_spot_options_and_knows_what_it_has_not_fetched(tmp_path):
    path = str(tmp_path / "bt.sqlite3")
    store.ensure_tables(path)
    day = datetime.date(2026, 3, 3)
    expiry = datetime.date(2026, 4, 28)
    store.store_candles(_bars(day, "10:20", [24190, 24195, 24200, 24205, 24210, 24215, 24220, 24225, 24230, 24235, 24240], 1),
                        stock_code="NIFTY", table="spot_candles", path=path)
    src = StoreSource(set(), day, day, data_until=datetime.date(2026, 3, 31), path=path)
    at = datetime.datetime.combine(day, datetime.time(10, 30))
    # Spot: the 10:29 bar, the last to have closed by 10:30.
    assert src.spot(at) == 24235
    assert src.spot(datetime.datetime.combine(day, datetime.time(12, 0))) is None

    traded = (expiry, 24200.0, "Call")
    dead = (expiry, 24250.0, "Call")
    assert src.option(traded, at)[0] == "missing"
    needs = src.needs(traded, at, datetime.date(2026, 3, 31))
    assert needs and all(n.interval == store.INTERVAL_5MIN for n in needs)
    # Blocks of 12 sessions, the last cut at the last completed session.
    first = needs[0]
    assert len(regime.trading_days(first.start.date(), first.end.date())) <= BLOCK_SESSIONS
    assert needs[-1].end.date() <= datetime.date(2026, 3, 31)

    rows = _bars(day, "10:15", [300.0, 305.0, 310.0], 5)
    for need in src.needs(traded, at, datetime.date(2026, 3, 31)):
        store.store_option_candles(rows if need.start.date() <= day <= need.end.date() else [], need.key, need.interval, path=path)
        store.record_fetch(need, len(rows), path=path)
    for need in src.needs(dead, at, datetime.date(2026, 3, 31)):
        store.record_fetch(need, 0, path=path)
    src.refresh()
    # 10:30 falls in the 10:30 bar? No -- bars are 10:15, 10:20, 10:25: the 10:25 bar closed at 10:30.
    assert src.option(traded, at) == ("ok", 310.0)
    assert src.option(dead, at) == ("unlisted", None)


def test_the_near_exit_window_removes_the_flat_market_late_rolls():
    plain, src = _replay(flat)
    _run_to_end(plain, src)
    window = CondorSettings(margin_ceiling_inr=60_00_000, no_roll_within_days_of_exit=3)
    guarded, src2 = _replay(flat, settings=window)
    _run_to_end(guarded, src2)
    assert plain.summary()["rolls"] > 0
    assert guarded.summary()["rolls"] == 0
    assert guarded.summary()["skipped"].get("roll_near_exit", 0) > 0
