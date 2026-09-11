"""Tests for the index signal's shadow log -- the evidence that gates bot consumption."""
from __future__ import annotations

import csv
import io

import pytest

from icici_breeze_backend.app.services.index_signal import publisher, shadow_log

# 2027-01-15 13:30:00 IST -- every row below lands on the same IST trading day.
B = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _reset():
    shadow_log.reset_state_for_tests()
    yield
    shadow_log.reset_state_for_tests()


def _payload(state: str, reason: str | None = None) -> dict:
    return {"state": state, "reason": reason, "signal": 0.1, "raw_wobi": 0.2, "coverage": 0.9}


def _s(at: float, state: str, spot: float | None) -> dict:
    return {"kind": "sample", "ts": B + at, "state": state, "spot": spot}


def _t(at: float, state: str, spot: float | None) -> dict:
    return {"kind": "transition", "ts": B + at, "state": state, "spot": spot}


def test_samples_once_a_minute_and_logs_every_transition(tmp_path):
    db = str(tmp_path / "users_test.sqlite3")

    def rec(state, at, reason=None):
        return shadow_log.record("nifty", _payload(state, reason), spot=24000.0, now=B + at, db_path=db)

    assert rec("neutral", 0) == ["sample"]  # the first publish has nothing to transition from
    assert rec("neutral", 10) == []
    assert rec("bullish", 20) == ["transition"]
    assert rec("bullish", 61) == ["sample"]
    assert rec("unavailable", 130, reason="market_closed") == ["transition"]  # no closed-market sample

    rows = shadow_log.load_rows("nifty", 0.0, db)
    assert [(r["kind"], r["state"]) for r in rows] == [
        ("sample", "neutral"),
        ("transition", "bullish"),
        ("sample", "bullish"),
        ("transition", "unavailable"),
    ]
    assert rows[0]["spot"] == 24000.0


def test_moves_under_the_minimum_are_flat_not_hits():
    rows = [_s(0, "bullish", 100.0), _s(60, "bullish", 100.03), _s(120, "bullish", 100.2), _s(180, "bullish", 100.1)]

    cell = shadow_log.score(rows, horizons=(60,), min_move_bps=5.0)["forward_returns"][60]["bullish"]
    assert (cell["n"], cell["ups"], cell["downs"], cell["flats"]) == (3, 1, 1, 1)  # +3 bps is flat
    assert cell["hit_rate"] == 0.5

    any_move = shadow_log.score(rows, horizons=(60,), min_move_bps=0.0)["forward_returns"][60]["bullish"]
    assert any_move["hit_rate"] == pytest.approx(2 / 3, abs=1e-4)


def test_range_comes_from_readings_a_horizon_apart():
    # Sixteen bullish minutes in a steady rise: eleven +5 min outcomes, but they overlap -- only
    # three are a whole horizon apart, so the 95% range must be as wide as three readings allow.
    rows = [_s(60 * k, "bullish", 100.0 * 1.001**k) for k in range(16)]
    out = shadow_log.score(rows, horizons=(300,), min_move_bps=0.0)
    cell = out["forward_returns"][300]["bullish"]
    assert cell["n"] == 11
    assert cell["n_independent"] == 3
    assert cell["hit_rate"] == 1.0
    assert cell["hit_rate_low"] < 0.5
    assert cell["hit_rate_high"] == 1.0
    # A market that only rose makes bullish look perfect -- and no better than reading nothing.
    assert out["baseline"][300]["up_share"] == 1.0
    assert cell["edge_hit"] == 0.0
    assert cell["edge_bps"] == 0.0
    assert cell["verdict"] == "unclear"
    assert out["excluded"][300] == {"no_level": 0, "day_end": 5, "gap": 0}


def test_edge_over_all_readings_in_the_called_direction():
    # Bullish readings are always followed by a rise and bearish by a fall, in a flat market.
    rows = [
        _s(60 * k, "bullish" if k % 2 == 0 else "bearish", 100.0 if k % 2 == 0 else 100.1)
        for k in range(200)
    ]
    out = shadow_log.score(rows, horizons=(60,), min_move_bps=5.0)
    assert out["baseline"][60]["up_share"] == pytest.approx(0.5, abs=0.01)
    for state in ("bullish", "bearish"):
        cell = out["forward_returns"][60][state]
        assert cell["hit_rate"] == 1.0
        assert cell["verdict"] == "better"
        assert cell["edge_hit"] == pytest.approx(0.5, abs=0.01)
        assert cell["edge_bps"] > 9.0  # positive = helped, for both directions


def test_flips_are_scored_from_the_flip_level_and_wake_ups_are_skipped():
    rows = [
        _s(0, "unavailable", 100.0),
        _t(30, "bullish", 100.0),  # the signal waking up, not changing its mind
        _s(60, "bullish", 100.5),
        _t(90, "neutral", 100.5),
        _t(120, "bearish", 101.0),
        _s(120, "bearish", 101.0),
        _s(180, "bearish", 100.0),
    ]
    out = shadow_log.score(rows, horizons=(60,), min_move_bps=5.0)
    assert out["flips"] == 1
    assert set(out["flip_returns"][60]) == {"bearish"}
    flip = out["flip_returns"][60]["bearish"]
    assert (flip["n"], flip["hit_rate"]) == (1, 1.0)
    assert flip["mean_return_bps"] == pytest.approx(-99.01, abs=0.01)


def test_unscored_readings_are_counted_by_reason():
    rows = [
        _s(0, "neutral", 100.0),
        _s(60, "neutral", None),  # index feed not ticking
        _s(120, "neutral", 100.0),  # next level is 8 minutes on: a gap, not an outcome
        _s(600, "neutral", 100.0),  # last reading of the day
    ]
    out = shadow_log.score(rows, horizons=(60,))
    assert out["excluded"][60] == {"no_level": 1, "day_end": 1, "gap": 1}
    assert out["forward_returns"][60]["neutral"]["n"] == 1


def test_readings_csv_blanks_only_the_outcomes_the_session_ran_out_for(tmp_path):
    db = str(tmp_path / "users_test.sqlite3")
    for k in range(11):  # the last eleven minutes of a session
        shadow_log.record("nifty", _payload("bullish"), spot=24000.0 + k, now=B + 60 * k + 1, db_path=db)

    table = list(csv.reader(io.StringIO(shadow_log.readings_csv("nifty", days=1, db_path=db, now=B + 700))))
    assert table[0] == [
        "time_ist", "state", "reason", "signal", "raw_wobi", "coverage", "nifty_level",
        "nifty_after_1m", "move_1m_bps", "nifty_after_5m", "move_5m_bps", "nifty_after_15m", "move_15m_bps",
    ]
    first = table[1]
    assert first[:7] == ["2027-01-15 13:30:01", "bullish", "", "0.1000", "0.2000", "0.9000", "24000.00"]
    assert first[7:11] == ["24001.00", "0.42", "24005.00", "2.08"]
    assert first[11:] == ["", ""]  # fewer than 15 minutes of session left
    assert table[7][7] == "24007.00" and table[7][9] == ""  # 5 minutes from the end: +1 only
    assert table[11][7:] == [""] * 6  # the last reading has no outcome at all


def test_index_spot_ignores_a_cached_level_that_is_not_a_live_tick(monkeypatch):
    cached = {"ltp": 25000.0, "updated_at": 1_000.0}
    monkeypatch.setattr(publisher, "cache_get_json", lambda key: cached)
    assert publisher._index_spot("nifty", 1_010.0) == 25000.0
    assert publisher._index_spot("nifty", 1_020.0) is None  # e.g. yesterday's close at the open
    cached.pop("updated_at")
    assert publisher._index_spot("nifty", 1_010.0) is None
