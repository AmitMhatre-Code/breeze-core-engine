"""Volume-confirmed price expansion with OI quadrants (#34): the pure engine."""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.services.index_signal import expansion as ex
from icici_breeze_backend.app.services.index_signal.engine import (
    REASON_MARKET_CLOSED,
    REASON_WARMING_UP,
)

B = 1_800_000_000.0  # bar 0's start; every bar below is a minute apart


def flat(n: int, *, close: float = 100.0, volume: float = 100.0, oi: float = 1_000_000.0) -> list[ex.Bar]:
    return [ex.Bar(ts=B + 60 * i, close=close, volume=volume, oi=oi) for i in range(n)]


def params(**kw) -> ex.ExpansionParams:
    base = dict(window_minutes=15, baseline_bars=120, min_baseline_bars=60)
    base.update(kw)
    return ex.ExpansionParams(**base)


# -- the pieces --------------------------------------------------------------------------


def test_percentile_rank_counts_strictly_below():
    # A dead market where every bar is identical must rank at 0 and therefore never fire:
    # the top tail has to be a real tail, not a tie.
    assert ex.percentile_rank([5.0] * 10, 5.0) == 0.0
    assert ex.percentile_rank([1.0, 2.0, 3.0, 4.0], 3.5) == 0.75
    assert ex.percentile_rank([], 1.0) is None


def test_quadrants_map_to_sides_and_unwinds_are_not_reversals():
    assert ex.quadrant(10.0, 500.0) == "new_longs"
    assert ex.quadrant(10.0, -500.0) == "short_covering"
    assert ex.quadrant(-10.0, 500.0) == "new_shorts"
    assert ex.quadrant(-10.0, -500.0) == "long_liquidation"
    assert ex.QUADRANT_SIDE["new_longs"] == "bullish"
    assert ex.QUADRANT_SIDE["new_shorts"] == "bearish"
    # An unwind refuses to call; it never calls the other way.
    assert ex.QUADRANT_SIDE["short_covering"] is None
    assert ex.QUADRANT_SIDE["long_liquidation"] is None


def test_a_window_under_fifteen_minutes_is_refused():
    # Median 1-minute OI change is under 0.01% of outstanding -- there is nothing to read.
    with pytest.raises(ValueError, match="at least 15"):
        params(window_minutes=5)


def test_thresholds_must_be_percentiles_and_the_baseline_must_outlast_the_window():
    with pytest.raises(ValueError, match="percentile"):
        params(price_percentile=1.5)
    with pytest.raises(ValueError, match="percentile"):
        params(volume_percentile=0.0)
    with pytest.raises(ValueError, match="baseline must be longer"):
        params(window_minutes=60, min_baseline_bars=60, baseline_bars=120)


def test_a_window_with_one_unknown_volume_reports_no_volume_at_all():
    bars = flat(20)
    bars[10] = ex.Bar(ts=bars[10].ts, close=100.0, volume=None, oi=1_000_000.0)
    reading = ex._window_reading(bars, 19, 15)
    assert reading is not None
    # Summing the rest would understate the window and suppress the filter exactly when the
    # feed has just come up.
    assert reading[1] is None


# -- evaluate ----------------------------------------------------------------------------


def test_too_few_bars_is_warming_up_not_a_reading():
    side, strength, _c, reason = ex.evaluate(flat(40), params())
    assert side is None and strength is None
    assert reason == REASON_WARMING_UP


def test_a_quiet_market_is_a_reading_of_no_expansion():
    side, strength, components, reason = ex.evaluate(flat(100), params())
    assert side is None
    assert reason == ex.REASON_NO_EXPANSION
    # Still reports a number: the shadow log scores a continuous value, not three states.
    assert strength == 0.0
    assert components["price_rank"] == 0.0


def _burst(*, up: bool, oi_rises: bool, n: int = 100) -> list[ex.Bar]:
    """A flat baseline then one bar that jumps on heavy volume, with OI moving either way."""
    bars = flat(n)
    last = bars[-1]
    bars[-1] = ex.Bar(
        ts=last.ts,
        close=100.0 + (2.0 if up else -2.0),
        volume=10_000.0,
        oi=1_000_000.0 + (50_000.0 if oi_rises else -50_000.0),
    )
    return bars


def test_price_up_on_volume_with_rising_oi_is_bullish():
    side, strength, components, reason = ex.evaluate(_burst(up=True, oi_rises=True), params())
    assert side == "bullish"
    assert reason is None
    assert components["quadrant"] == "new_longs"
    assert strength == pytest.approx(1.0)


def test_price_down_on_volume_with_rising_oi_is_bearish():
    side, _s, components, reason = ex.evaluate(_burst(up=False, oi_rises=True), params())
    assert side == "bearish"
    assert reason is None
    assert components["quadrant"] == "new_shorts"


def test_the_same_rally_on_falling_oi_is_an_unwind_and_calls_nothing():
    side, strength, components, reason = ex.evaluate(_burst(up=True, oi_rises=False), params())
    assert side is None
    assert reason == ex.REASON_UNWIND
    assert components["quadrant"] == "short_covering"
    # The price and volume were extreme; it is the confirmation that is missing, not the move.
    assert strength == pytest.approx(1.0)


def test_without_oi_required_the_same_bars_call_on_price_and_volume_alone():
    """SENSEX: ICICI serves no OI for BSE, so the quadrant half cannot run there."""
    bars = _burst(up=True, oi_rises=False)
    side, _s, components, reason = ex.evaluate(bars, params(require_oi=False))
    assert side == "bullish"  # the unwind that neutralised NIFTY cannot be seen here
    assert reason is None
    assert components["quadrant"] is None


def test_missing_oi_when_it_is_required_is_no_reading_not_a_neutral_one():
    bars = _burst(up=True, oi_rises=True)
    bars = [ex.Bar(ts=b.ts, close=b.close, volume=b.volume, oi=None) for b in bars]
    side, _s, _c, reason = ex.evaluate(bars, params())
    assert side is None
    assert reason == ex.REASON_NO_OI


def test_a_zero_oi_bar_is_treated_as_absent_not_as_a_reading_of_zero():
    """ICICI serves OI 0 on pre-open bars and on *every* BSE bar, so a zero reaching the
    engine is likely, not hypothetical. Anchoring a window on one would turn the next real
    value into the largest OI rise ever recorded, and call it `new_longs`."""
    bars = _burst(up=True, oi_rises=True)
    leaked = list(bars)
    anchor = leaked[-16]  # the window's opening bar
    leaked[-16] = ex.Bar(ts=anchor.ts, close=anchor.close, volume=anchor.volume, oi=0.0)
    side, strength, components, reason = ex.evaluate(leaked, params())
    assert components["oi_delta"] is None
    assert side is None
    assert reason == ex.REASON_NO_OI
    # The price and volume halves still read normally -- only the confirmation is missing.
    assert strength == pytest.approx(1.0)


# -- the engine --------------------------------------------------------------------------


def _engine(**kw) -> ex.ExpansionEngine:
    return ex.ExpansionEngine("nifty", params(**kw))


def test_a_call_stands_while_the_feed_keeps_arriving_then_lapses_to_neutral():
    eng = _engine()
    eng.seed(_burst(up=True, oi_rises=True))
    fired_at = B + 60 * 99
    assert eng.snapshot(fired_at, session_open=True)["state"] == "bullish"

    # Keep the feed alive at the new price level. The step stays inside the 15-bar window for
    # 15 more bars, so the call legitimately re-fires; past that the window flattens.
    ts = fired_at
    for i in range(1, 60):
        ts = fired_at + 60 * i
        eng.on_bar(ex.Bar(ts=ts, close=102.0, volume=100.0, oi=1_050_000.0))
    assert eng.snapshot(ts, session_open=True)["state"] == "neutral"


def test_a_held_call_goes_unavailable_when_the_feed_stops_rather_than_standing():
    eng = _engine()
    eng.seed(_burst(up=True, oi_rises=True))
    fired_at = B + 60 * 99
    assert eng.snapshot(fired_at, session_open=True)["state"] == "bullish"
    # Nothing has arrived for longer than stale_seconds: fail closed, do not keep asserting.
    snap = eng.snapshot(fired_at + 400, session_open=True)
    assert snap["state"] == "unavailable"
    assert snap["reason"] == ex.REASON_STALE


def test_a_closed_market_and_an_excluded_session_are_both_unavailable():
    eng = _engine()
    eng.seed(_burst(up=True, oi_rises=True))
    at = B + 60 * 99
    assert eng.snapshot(at, session_open=False)["reason"] == REASON_MARKET_CLOSED
    # Expiry day and rollover week: OI moves because contracts die, not because anyone's view
    # changed. The engine owns no calendar, so the caller says so.
    excluded = eng.snapshot(at, session_open=True, excluded=True)
    assert excluded["state"] == "unavailable"
    assert excluded["reason"] == ex.REASON_EXCLUDED_SESSION


def test_a_replayed_or_out_of_order_bar_is_ignored():
    eng = _engine()
    eng.seed(flat(100))
    before = len(eng._bars)
    eng.on_bar(ex.Bar(ts=B, close=100.0, volume=100.0, oi=1_000_000.0))  # ancient
    eng.on_bar(ex.Bar(ts=B + 60 * 99, close=100.0, volume=100.0, oi=1_000_000.0))  # duplicate
    assert len(eng._bars) == before


def test_changing_the_window_discards_the_baseline_it_was_ranked_against():
    eng = _engine()
    eng.seed(flat(100))
    assert len(eng._bars) > 0
    eng.set_params(params(window_minutes=30))
    assert len(eng._bars) == 0
    # A threshold change keeps the bars: the distribution is still the right one.
    eng.seed(flat(100))
    eng.set_params(params(window_minutes=30, price_percentile=0.9))
    assert len(eng._bars) > 0


def test_the_payload_says_when_only_half_the_mechanism_is_running():
    full = _engine().snapshot(B, session_open=True)
    half = ex.ExpansionEngine("sensex", params(require_oi=False)).snapshot(B, session_open=True)
    assert full["requires_oi"] is True and full["coverage"] == 1.0
    assert half["requires_oi"] is False and half["coverage"] == 0.5


def test_a_window_spanning_the_overnight_break_is_not_a_reading():
    """Without this, the first bars of every session measure the gap move as a 15-minute
    expansion on 15 minutes' volume, and the signal opens screaming."""
    yesterday = flat(80)
    overnight = 17 * 3600
    today = [
        ex.Bar(ts=yesterday[-1].ts + overnight + 60 * i, close=103.0, volume=100.0, oi=1_000_000.0)
        for i in range(5)
    ]
    reading = ex._window_reading(yesterday + today, 84, 15)
    assert reading is None


def test_seeding_across_sessions_keeps_the_baseline_and_drops_only_the_straddling_windows():
    """`seed` exists so the first live call does not wait an hour for a distribution. Yesterday's
    windows are good samples of a typical window; only the ones crossing the break are not."""
    eng = _engine()
    eng.seed(flat(100))
    overnight = 17 * 3600
    base_ts = B + 60 * 99 + overnight
    for i in range(20):
        eng.on_bar(ex.Bar(ts=base_ts + 60 * i, close=100.0, volume=100.0, oi=1_000_000.0))
    # 20 bars into the new session is far short of min_baseline_bars on its own, yet the
    # engine already has a reading rather than warming up.
    snap = eng.snapshot(base_ts + 60 * 19, session_open=True)
    assert snap["reason"] == ex.REASON_NO_EXPANSION
    assert snap["state"] == "neutral"


def test_a_small_feed_hole_is_tolerated_and_a_large_one_invalidates_the_window():
    """The tolerance is deliberate. Invalidating a window for one missing bar would make the
    signal fragile on a feed that drops ticks by design; the cost is that the volume sum is
    one bar short, which biases *against* firing and so fails safe."""
    bars = flat(100)
    one_missing = [b for i, b in enumerate(bars) if i != 90]
    assert ex._window_reading(one_missing, len(one_missing) - 1, 15) is not None

    # Six consecutive bars gone is a seven-minute hole, past max_gap_seconds.
    many_missing = [b for i, b in enumerate(bars) if not 88 <= i <= 93]
    assert ex._window_reading(many_missing, len(many_missing) - 1, 15) is None


# -- replay over stored history (#34) ------------------------------------------------------


def _store_bars(cache: str, n: int, *, with_oi: bool = True) -> None:
    import datetime as dtm

    from icici_breeze_backend.app.services.bots.scalping import backtest_store as store

    store.ensure_tables(cache)
    start = dtm.datetime(2026, 9, 14, 9, 15)
    rows = []
    for i in range(n):
        ts = start + dtm.timedelta(minutes=i)
        if ts.time() > dtm.time(15, 15):  # roll to the next day's open
            start = dtm.datetime(ts.year, ts.month, ts.day, 9, 15) + dtm.timedelta(days=1)
            ts = start
        row = {
            "datetime": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 100,
        }
        if with_oi:
            row["open_interest"] = 1_000_000 + i
        rows.append(row)
    store.store_candles(rows, stock_code="NIFTY", path=cache)


def test_a_replay_with_no_stored_bars_says_so_instead_of_scoring_nothing(tmp_path):
    from icici_breeze_backend.app.services.index_signal import expansion_backtest as bt

    out = bt.replay(
        "nifty", cache_path=str(tmp_path / "b.sqlite3"), db_path=str(tmp_path / "u.sqlite3")
    )
    assert out["verdict"] == "no_data"
    assert out["readings"] == 0


def test_a_replay_logs_under_its_own_label_and_never_the_live_one(tmp_path):
    from icici_breeze_backend.app.services.index_signal import expansion_backtest as bt
    from icici_breeze_backend.app.services.index_signal import shadow_log

    cache, db = str(tmp_path / "b.sqlite3"), str(tmp_path / "u.sqlite3")
    _store_bars(cache, 200)
    out = bt.replay("nifty", cache_path=cache, db_path=db)

    assert out["label"] == "nifty:expansion:backtest"
    assert shadow_log.load_rows("nifty:expansion:backtest", 0.0, db)
    # The live evidence the readiness gate counts must be untouched by a replay.
    assert shadow_log.load_rows("nifty", 0.0, db) == []
    assert shadow_log.load_rows("nifty:expansion", 0.0, db) == []


def test_replaying_twice_does_not_score_the_first_run_as_well(tmp_path):
    from icici_breeze_backend.app.services.index_signal import expansion_backtest as bt
    from icici_breeze_backend.app.services.index_signal import shadow_log

    cache, db = str(tmp_path / "b.sqlite3"), str(tmp_path / "u.sqlite3")
    _store_bars(cache, 200)
    bt.replay("nifty", cache_path=cache, db_path=db)
    first = len(shadow_log.load_rows("nifty:expansion:backtest", 0.0, db))
    bt.replay("nifty", cache_path=cache, db_path=db)
    assert len(shadow_log.load_rows("nifty:expansion:backtest", 0.0, db)) == first


def test_only_a_replay_label_may_be_purged_wholesale(tmp_path):
    from icici_breeze_backend.app.services.index_signal import shadow_log

    db = str(tmp_path / "u.sqlite3")
    # Live evidence ages out on the retention setting; nothing may clear it in one call, or
    # the readiness gate's "10 sessions" would stop meaning ten real ones.
    with pytest.raises(ValueError, match="refusing to purge"):
        shadow_log.purge_label("nifty", db_path=db)
    with pytest.raises(ValueError, match="refusing to purge"):
        shadow_log.purge_label("nifty:expansion", db_path=db)


def test_a_replayed_call_reaches_the_calls_csv_with_what_fired_it(tmp_path):
    """Engine -> log -> report: the per-call file must carry the inputs the engine fired on."""
    import csv
    import datetime as dtm
    import io

    from icici_breeze_backend.app.core.timezone import IST
    from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
    from icici_breeze_backend.app.services.index_signal import expansion_backtest as bt
    from icici_breeze_backend.app.services.index_signal import shadow_log

    cache, db = str(tmp_path / "b.sqlite3"), str(tmp_path / "u.sqlite3")
    store.ensure_tables(cache)
    start = dtm.datetime(2026, 9, 14, 9, 15)
    rows, close = [], 100.0
    for i in range(240):
        rally = 150 <= i < 170  # a volume-backed rally on rising OI, after a quiet baseline
        close += 0.05 if rally else 0.0
        rows.append({
            "datetime": (start + dtm.timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
            "open": close, "high": close, "low": close, "close": close,
            "volume": 500 if rally else 100,
            "open_interest": 1_000_000 + (50 * i if rally else i),
        })
    store.store_candles(rows, stock_code="NIFTY", path=cache)
    bt.replay("nifty", cache_path=cache, db_path=db)

    now = dtm.datetime(2026, 9, 15, tzinfo=IST).timestamp()
    calls = list(csv.DictReader(io.StringIO(
        shadow_log.calls_csv("nifty:expansion:backtest", days=5, min_move_bps=1.0, db_path=db, now=now)
    )))
    assert calls and calls[0]["turned"] == "bullish"
    first = calls[0]
    assert first["quadrant"] == "new_longs"
    assert float(first["price_rank"]) >= 0.8 and float(first["volume_rank"]) >= 0.8
    assert first["price_threshold"] == "0.80" and first["volume_threshold"] == "0.80"
    assert first["weaker_side"] in ("price", "volume")
    assert float(first["oi_change"]) > 0
    assert first["result_5m"] == "right"  # the rally carried on past the call
