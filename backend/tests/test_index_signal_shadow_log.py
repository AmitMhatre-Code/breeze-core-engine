"""Tests for the index signal's shadow log -- the evidence that gates bot consumption."""
from __future__ import annotations

import csv
import io
import sqlite3
from datetime import date

import pytest

from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.index_signal import breakeven, publisher, shadow_log

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
        "time_ist", "state", "reason", "signal", "raw_wobi", "ofi", "aggressor", "coverage",
        "nifty_level",
        "nifty_after_1m", "move_1m_bps", "nifty_after_5m", "move_5m_bps", "nifty_after_15m", "move_15m_bps",
    ]
    first = table[1]
    # W-OBI has no challenger halves, so both stay blank (#33).
    assert first[:9] == [
        "2027-01-15 13:30:01", "bullish", "", "0.1000", "0.2000", "", "", "0.9000", "24000.00",
    ]
    assert first[9:13] == ["24001.00", "0.42", "24005.00", "2.08"]
    assert first[13:] == ["", ""]  # fewer than 15 minutes of session left
    assert table[7][9] == "24007.00" and table[7][11] == ""  # 5 minutes from the end: +1 only
    assert table[11][9:] == [""] * 6  # the last reading has no outcome at all


def test_index_spot_ignores_a_cached_level_that_is_not_a_live_tick(monkeypatch):
    cached = {"ltp": 25000.0, "updated_at": 1_000.0}
    monkeypatch.setattr(publisher, "cache_get_json", lambda key: cached)
    assert publisher._index_spot("nifty", 1_010.0) == 25000.0
    assert publisher._index_spot("nifty", 1_020.0) is None  # e.g. yesterday's close at the open
    cached.pop("updated_at")
    assert publisher._index_spot("nifty", 1_010.0) is None


def test_breakeven_prices_trading_costs_on_one_atm_lot(monkeypatch):
    monkeypatch.setattr(breakeven, "lot_size", lambda label, today=None: 65)
    monkeypatch.setattr(breakeven, "atm_premium", lambda label, today=None: 100.0)
    monkeypatch.setattr(breakeven.trading_charges, "load_charges", ChargesModel)
    be = breakeven.breakeven("nifty", 25000.0)
    assert be["cost_rupees"] == pytest.approx(62.60, abs=0.01)  # ₹26.52 buy + ₹36.08 sell
    assert be["points"] == pytest.approx(1.93, abs=0.01)  # 62.60 / (65 lots x 0.5 delta)
    assert be["bps"] == pytest.approx(0.770, abs=0.001)
    assert breakeven.breakeven("nifty", None)["bps"] is None  # no index level yet

    # No option price: the flat part alone -- ₹20 brokerage and its 18% GST, both ways.
    monkeypatch.setattr(breakeven, "atm_premium", lambda label, today=None: None)
    assert breakeven.breakeven("nifty", 25000.0)["cost_rupees"] == pytest.approx(47.20, abs=0.01)


def test_atm_premium_is_the_nearest_expiry_straddle_mean_at_the_strike_nearest_spot(tmp_path, monkeypatch):
    db = tmp_path / "scrips.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE fo_bhavcopy (segment TEXT, stock_code TEXT, expiry_date TEXT, "
            "strike_price REAL, right TEXT, ltp REAL, spot_price REAL)"
        )
        conn.executemany(
            "INSERT INTO fo_bhavcopy VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("nfo", "NIFTY", "2026-09-08", 23500, "Call", 5.0, 23477.8),  # already expired
                ("nfo", "NIFTY", "2026-09-15", 23450, "Call", 102.7, 23477.8),
                ("nfo", "NIFTY", "2026-09-15", 23450, "Put", 99.25, 23477.8),
                ("nfo", "NIFTY", "2026-09-15", 23500, "Call", 78.4, 23477.8),  # nearest spot
                ("nfo", "NIFTY", "2026-09-15", 23500, "Put", 125.1, 23477.8),
                ("nfo", "NIFTY", "2026-09-22", 23500, "Call", 186.2, 23477.8),  # a later expiry
            ],
        )
    monkeypatch.setattr(breakeven, "_scrip_db_path", lambda: str(db))
    monkeypatch.setattr(breakeven.symbol_registry, "aliases_for", lambda code, segment=None: ("NIFTY",))
    assert breakeven.atm_premium("nifty", date(2026, 9, 11)) == pytest.approx(101.75)
    assert breakeven.atm_premium("nifty", date(2026, 9, 16)) == pytest.approx(186.2)
    assert breakeven.atm_premium("nifty", date(2026, 9, 30)) is None


def test_lot_size_is_the_nearest_unexpired_expiry(tmp_path, monkeypatch):
    db = tmp_path / "scrips.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE scrip_master (ShortName TEXT, ExpiryDate TEXT, StrikePrice REAL, "
            "OptionType TEXT, LotSize INTEGER, ExchangeCode TEXT, SegmentCode TEXT)"
        )
        conn.executemany(
            "INSERT INTO scrip_master VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("BSESEN", "10-Sep-2026", 80000, "CE", 25, "SENSEX", "BFO"),  # already expired
                ("BSESEN", "17-Sep-2026", 80000, "PE", 20, "SENSEX", "BFO"),
                ("BSESEN", "31-Dec-2026", 80000, "CE", 15, "SENSEX", "BFO"),  # revised size, later
                ("NIFTY", "17-Sep-2026", 25000, "CE", 65, "NIFTY 50", "NFO"),
            ],
        )
    monkeypatch.setattr(breakeven, "_scrip_db_path", lambda: str(db))
    monkeypatch.setattr(breakeven.symbol_registry, "aliases_for", lambda code, segment=None: ("BSESEN", "SENSEX"))
    assert breakeven.lot_size("sensex", date(2026, 9, 11)) == 20
    assert breakeven.lot_size("sensex", date(2026, 10, 1)) == 15

    missing = tmp_path / "missing.sqlite3"
    monkeypatch.setattr(breakeven, "_scrip_db_path", lambda: str(missing))
    assert breakeven.lot_size("sensex", date(2026, 9, 12)) is None
    assert not missing.exists()  # never creates an empty scrip DB


def _flipping_days(days: int, *, against: bool = False) -> list[dict]:
    """Twelve 10-minute cycles a day, alternating bullish and bearish flips. After each flip the
    index moves 4-5 bps a minute for five minutes -- the called way, or `against` it -- then
    holds. Even days rise further than they fall (up days), odd days the other way round."""
    level = 25000.0
    rows = [_s(-60, "neutral", level)]
    for d in range(days):
        up_day = d % 2 == 0
        t = d * 86400.0
        for c in range(12):
            state = "bullish" if c % 2 == 0 else "bearish"
            step = 5.0 if (state == "bullish") == up_day else 4.0
            sign = 1.0 if (state == "bullish") != against else -1.0
            if against:
                step = 9.0 - step  # keep even days up when the moves are inverted
            rows.append(_t(t, state, level))
            for m in range(10):
                rows.append(_s(t + 60 * m, state, level))
                if m < 5:
                    level *= 1 + sign * step / 1e4
            t += 600.0
    return rows


def _store(db: str, rows: list[dict], label: str = "nifty") -> None:
    shadow_log.ensure_log_table(db)
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO index_signal_log (label, ts, kind, state, spot) VALUES (?, ?, ?, ?, ?)",
            [(label, r["ts"], r["kind"], r["state"], r["spot"]) for r in rows],
        )


@pytest.fixture
def lot_65(monkeypatch):
    """One NIFTY lot at a ₹100 ATM premium on the shipped charges: a ₹62.60 round trip."""
    monkeypatch.setattr(breakeven, "lot_size", lambda label, today=None: 65)
    monkeypatch.setattr(breakeven, "atm_premium", lambda label, today=None: 100.0)
    monkeypatch.setattr(breakeven.trading_charges, "load_charges", ChargesModel)


def test_readiness_is_ready_when_flips_beat_the_trend_over_enough_mixed_days(tmp_path, lot_65):
    db = str(tmp_path / "users_test.sqlite3")
    _store(db, _flipping_days(12))
    out = shadow_log.readiness("nifty", db_path=db, now=B + 13 * 86400)

    assert out["status"] == "ready"
    assert out["min_move_bps"] == pytest.approx(0.77, abs=0.02)  # the breakeven, not a fixed 5 bps
    assert (out["sessions"], out["up_days"], out["down_days"]) == (12, 6, 6)
    assert (out["flips"], out["dropped_quickly"]) == (144, 0)
    for state in ("bullish", "bearish"):
        scalp = out["directions"][state]["scalp"]
        assert scalp["status"] == "better"
        assert (scalp["right"], scalp["calls"], scalp["separate_calls"]) == (72, 72, 72)
        assert scalp["trend_share"] == pytest.approx(0.5, abs=0.05)


def test_readiness_is_too_early_on_a_few_days_however_good_they_look(tmp_path, lot_65):
    db = str(tmp_path / "users_test.sqlite3")
    _store(db, _flipping_days(3))
    out = shadow_log.readiness("nifty", db_path=db, now=B + 4 * 86400)
    assert out["status"] == "too_early"
    assert out["directions"]["bullish"]["scalp"]["status"] == "too_early"  # 18 calls, under the floor
    assert out["directions"]["bullish"]["scalp"]["right"] == 18


def test_readiness_is_worse_when_the_index_keeps_going_the_other_way(tmp_path, lot_65):
    db = str(tmp_path / "users_test.sqlite3")
    _store(db, _flipping_days(12, against=True))
    out = shadow_log.readiness("nifty", db_path=db, now=B + 13 * 86400)
    assert out["status"] == "worse"
    assert out["directions"]["bearish"]["scalp"]["status"] == "worse"


def test_readiness_counts_calls_the_signal_gave_up_within_the_scalp_horizon():
    rows = [
        _s(0, "neutral", 100.0),
        _t(30, "bearish", 100.0),
        _t(90, "neutral", 100.0),  # let go a minute later
        _t(400, "bearish", 100.0),
        _t(1000, "neutral", 100.0),  # held ten minutes
    ]
    assert shadow_log._dropped_within(rows, 300) == (2, 1)


def test_a_report_without_a_minimum_move_scores_against_the_breakeven(lot_65, tmp_path):
    db = str(tmp_path / "users_test.sqlite3")
    _store(db, _flipping_days(1))
    out = shadow_log.shadow_report("nifty", days=5, min_move_bps=None, db_path=db, now=B + 86400)
    assert out["min_move_bps"] == out["breakeven"]["bps"] == pytest.approx(0.77, abs=0.02)
    fixed = shadow_log.shadow_report("nifty", days=5, min_move_bps=5.0, db_path=db, now=B + 86400)
    assert fixed["min_move_bps"] == 5.0


# -- the challenger's two halves (#33) ----------------------------------------------------


def _flow_payload(ofi: float | None, aggr: float | None) -> dict:
    """What `flow.FlowEngine.snapshot` publishes for KIND_FUTURES."""
    blend = None if ofi is None or aggr is None else 0.5 * (ofi + aggr)
    return {
        "state": "bullish",
        "reason": None,
        "signal": blend,
        "raw_wobi": None,
        "coverage": 1.0,
        "components": {"order_flow": ofi, "aggressor": aggr},
    }


def test_futures_challenger_halves_are_stored_beside_the_blend(tmp_path):
    db = str(tmp_path / "users_test.sqlite3")
    shadow_log.record("nifty:flow", _flow_payload(0.4, 0.8), spot=24000.0, now=B, db_path=db)

    row = shadow_log.load_rows("nifty:flow", B - 1, db)[0]
    assert row["signal"] == pytest.approx(0.6)
    # The blend alone could not say which half carried the reading -- that is the whole point.
    assert row["ofi"] == pytest.approx(0.4)
    assert row["aggressor"] == pytest.approx(0.8)


def test_labels_without_futures_components_keep_null_halves(tmp_path):
    db = str(tmp_path / "users_test.sqlite3")
    # W-OBI publishes no `components` at all.
    shadow_log.record("nifty", _payload("bullish"), spot=24000.0, now=B, db_path=db)
    # The constituent challenger's map is keyed by ShortName and has no aggressor half.
    shadow_log.record(
        "sensex:flow",
        {**_payload("bearish"), "components": {"HDFBAN": 0.3, "RELIND": -0.1}},
        spot=80000.0,
        now=B,
        db_path=db,
    )
    for label in ("nifty", "sensex:flow"):
        row = shadow_log.load_rows(label, B - 1, db)[0]
        assert row["ofi"] is None and row["aggressor"] is None


def test_a_half_that_is_not_a_finite_number_is_stored_as_null(tmp_path):
    db = str(tmp_path / "users_test.sqlite3")
    shadow_log.record("nifty:flow", _flow_payload(None, 0.5), spot=24000.0, now=B, db_path=db)
    row = shadow_log.load_rows("nifty:flow", B - 1, db)[0]
    assert row["ofi"] is None
    assert row["aggressor"] == pytest.approx(0.5)


def test_the_columns_are_added_to_a_log_written_before_they_existed(tmp_path):
    """Upgrading an instance must not lose the rows it already has, or refuse to write new ones."""
    db = str(tmp_path / "users_test.sqlite3")
    with sqlite3.connect(db) as conn:  # the pre-#33 schema, verbatim
        conn.execute(
            """
            CREATE TABLE index_signal_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL,
                reason TEXT, signal REAL, raw_wobi REAL, coverage REAL, spot REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO index_signal_log (label, ts, kind, state, signal, coverage, spot) "
            "VALUES ('nifty:flow', ?, 'sample', 'bullish', 0.6, 1.0, 24000.0)",
            (B - 60,),
        )
        conn.commit()

    shadow_log.record("nifty:flow", _flow_payload(0.4, 0.8), spot=24010.0, now=B, db_path=db)

    old, new = shadow_log.load_rows("nifty:flow", B - 120, db)
    assert old["signal"] == pytest.approx(0.6)
    assert old["ofi"] is None and old["aggressor"] is None  # NULL is the truth about old rows
    assert new["ofi"] == pytest.approx(0.4) and new["aggressor"] == pytest.approx(0.8)

    # Idempotent: a second ensure must not raise on the columns it just added.
    shadow_log.ensure_log_table(db)


def test_readings_csv_carries_the_halves(tmp_path):
    db = str(tmp_path / "users_test.sqlite3")
    shadow_log.record("nifty:flow", _flow_payload(0.4, 0.8), spot=24000.0, now=B, db_path=db)
    rows = list(csv.DictReader(io.StringIO(
        shadow_log.readings_csv("nifty:flow", days=5, db_path=db, now=B + 60)
    )))
    assert rows[0]["ofi"] == "0.4000"
    assert rows[0]["aggressor"] == "0.8000"


def test_flip_list_is_newest_first_with_outcomes_judged_against_the_bar(tmp_path):
    db = str(tmp_path / "users_test.sqlite3")

    def rec(state, at, spot):
        shadow_log.record("nifty:expansion", _payload(state), spot=spot, now=B + at, db_path=db)

    rec("neutral", 0, 24000.0)
    rec("bullish", 60, 24000.0)      # flip up at 24000
    for k in range(2, 20):
        rec("bullish", 60 * k, 24000.0 + 5 * k)
    rec("bearish", 60 * 20, 24100.0)  # flip down at 24100
    for k in range(21, 40):
        rec("bearish", 60 * k, 24100.0)

    out = shadow_log.flip_list("nifty:expansion", days=1, min_move_bps=1.0, db_path=db, now=B + 3000)
    states = [f["state"] for f in out["flips"]]
    assert states == ["bearish", "bullish"]  # newest first
    up = out["flips"][1]
    assert up["level"] == 24000.0
    assert up["move_5m_bps"] > 0 and up["right_5m"] is True
    down = out["flips"][0]
    assert down["move_5m_bps"] == 0.0 and down["right_5m"] is False  # flat is not right


def _expansion_payload(state: str, *, price_rank: float = 0.9, volume_rank: float = 0.85) -> dict:
    """What `expansion.ExpansionEngine.snapshot` publishes."""
    return {
        "state": state,
        "reason": None,
        "signal": price_rank if state != "bearish" else -min(price_rank, volume_rank),
        "raw_wobi": None,
        "coverage": 1.0,
        "thresholds": {"price_percentile": 0.8, "volume_percentile": 0.8},
        "components": {
            "price_bps": 12.5,
            "volume": 1_234_567.0,
            "oi_delta": 45_000.0,
            "price_rank": price_rank,
            "volume_rank": volume_rank,
            "quadrant": "new_longs",
            "strength": min(price_rank, volume_rank),
        },
    }


def test_expansion_components_and_their_thresholds_are_stored_with_the_reading(tmp_path):
    db = str(tmp_path / "users_test.sqlite3")
    shadow_log.record("nifty:expansion", _expansion_payload("bullish"), spot=24000.0, now=B, db_path=db)
    shadow_log.record("nifty", _payload("bullish"), spot=24000.0, now=B, db_path=db)

    parts = shadow_log._components(shadow_log.load_rows("nifty:expansion", B - 1, db)[0])
    assert parts["volume_rank"] == pytest.approx(0.85)
    assert parts["quadrant"] == "new_longs"
    assert parts["thresholds"] == {"price_percentile": 0.8, "volume_percentile": 0.8}
    # W-OBI publishes no components, and says so with NULL.
    assert shadow_log.load_rows("nifty", B - 1, db)[0]["components"] is None


def test_the_components_column_is_added_to_a_log_written_before_it_existed(tmp_path):
    db = str(tmp_path / "users_test.sqlite3")
    with sqlite3.connect(db) as conn:  # the #33 schema, before components
        conn.execute(
            """
            CREATE TABLE index_signal_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL,
                reason TEXT, signal REAL, raw_wobi REAL, coverage REAL, spot REAL,
                ofi REAL, aggressor REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO index_signal_log (label, ts, kind, state, spot) "
            "VALUES ('nifty:expansion', ?, 'sample', 'neutral', 24000.0)",
            (B - 60,),
        )
        conn.commit()

    shadow_log.record("nifty:expansion", _expansion_payload("bullish"), spot=24010.0, now=B, db_path=db)
    old, new = shadow_log.load_rows("nifty:expansion", B - 120, db)
    assert old["components"] is None and shadow_log._components(old) == {}
    assert shadow_log._components(new)["price_rank"] == pytest.approx(0.9)


def test_expansion_readings_csv_carries_what_the_engine_measured(tmp_path):
    db = str(tmp_path / "users_test.sqlite3")
    shadow_log.record("nifty:expansion", _expansion_payload("bullish"), spot=24000.0, now=B, db_path=db)
    rows = list(csv.DictReader(io.StringIO(
        shadow_log.readings_csv("nifty:expansion", days=5, db_path=db, now=B + 60)
    )))
    assert rows[0]["window_move_bps"] == "12.50"
    assert rows[0]["volume_rank"] == "0.8500"
    assert rows[0]["oi_change"] == "45000"
    assert rows[0]["quadrant"] == "new_longs"
    assert rows[0]["nifty:expansion_level"] == "24000.00"


def test_calls_csv_explains_each_call_and_splits_the_misses(tmp_path):
    db = str(tmp_path / "users_test.sqlite3")
    label = "nifty:expansion:backtest"

    def rec(payload, at, spot):
        shadow_log.record(label, payload, spot=spot, now=B + at, db_path=db)

    neutral = {**_payload("neutral"), "components": {"price_bps": 1.0}}
    rec(neutral, 0, 24000.0)
    # Call 1: bullish at 24000 with volume the weaker input; rises 2 bps/min for 3 min, gives it
    # all back and more, and is let go after 3 minutes.
    rec(_expansion_payload("bullish", price_rank=0.95, volume_rank=0.82), 60, 24000.0)
    for k, spot in ((2, 24004.8), (3, 24009.6), (4, 24014.4)):
        rec(_expansion_payload("bullish"), 60 * k, spot)
    rec(neutral, 60 * 4 + 1, 24014.4)
    for k in range(5, 20):
        rec(neutral, 60 * k, 23990.0)
    # Call 2: bearish at 23990, and the index drifts down under the bar.
    rec({**_expansion_payload("bearish"), "components": {**_expansion_payload("bearish")["components"],
                                                        "quadrant": "new_shorts"}}, 60 * 20, 23990.0)
    for k in range(21, 40):
        rec(_expansion_payload("bearish"), 60 * k, 23989.0)

    table = list(csv.DictReader(io.StringIO(
        shadow_log.calls_csv(label, days=1, min_move_bps=5.0, db_path=db, now=B + 3000)
    )))
    assert [c["turned"] for c in table] == ["bullish", "bearish"]  # oldest first, like a log

    up = table[0]
    assert up["from_state"] == "neutral"
    assert up["held_minutes"] == "3.0" and up["ended_as"] == "neutral"
    assert up["dropped_within_5m"] == "yes"
    assert up["weaker_side"] == "volume" and up["weaker_margin"] == "0.0200"
    assert up["price_threshold"] == "0.80"
    assert up["result_5m"] == "wrong_way"  # 24014.4 -> 23990 by +5
    assert float(up["best_15m_bps"]) == pytest.approx(6.0)  # it was right, then gave it back
    assert float(up["worst_15m_bps"]) < 0
    assert up["min_move_bps"] == "5.00"

    down = table[1]
    assert down["quadrant"] == "new_shorts"
    assert down["result_5m"] == "too_small"  # the called way, but under the bar
    assert down["ended_as"] == "session_end" and down["dropped_within_5m"] == "no"
    assert down["called_way_5m_bps"] == "0.42"  # 23990 -> 23989
    assert down["result_15m"] == "too_small"


def test_calls_csv_has_no_outcome_where_the_session_closed_first(tmp_path):
    db = str(tmp_path / "users_test.sqlite3")
    shadow_log.record("nifty", _payload("neutral"), spot=24000.0, now=B, db_path=db)
    shadow_log.record("nifty", _payload("bullish"), spot=24000.0, now=B + 60, db_path=db)
    for k in range(2, 8):
        shadow_log.record("nifty", _payload("bullish"), spot=24000.0 + k, now=B + 60 * k, db_path=db)

    (call,) = list(csv.DictReader(io.StringIO(
        shadow_log.calls_csv("nifty", days=1, min_move_bps=1.0, db_path=db, now=B + 600)
    )))
    assert call["raw_wobi"] == "0.2000"  # W-OBI's inputs, not expansion's
    assert call["result_5m"] == "right"
    assert call["result_15m"] == "close" and call["move_15m_bps"] == ""
