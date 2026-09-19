"""Signal variants (#38): the engine's split windows, the variant store, and the bots on them."""
from __future__ import annotations

import datetime

import pytest

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.domain import bots as domain
from icici_breeze_backend.app.domain.bots import (
    IronFlyEntryFilterConfig,
    MomentumLongScalperConfig,
    ReasonCode,
    TrailingLadderConfig,
)
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.bots.scalping import ladder, vix_minutes
from icici_breeze_backend.app.services.bots.scalping.backtest import run_backtest
from icici_breeze_backend.app.services.bots.scalping.backtest_store import HistCandle
from icici_breeze_backend.app.services.bots.scalping.iron_fly_bot import expansion_neutral_hold
from icici_breeze_backend.app.services.bots.scalping.signal import evaluate_variant, variant_call_unbroken
from icici_breeze_backend.app.services.bots.scalping.spreads import SpreadStats
from icici_breeze_backend.app.services.index_signal import expansion as ex
from icici_breeze_backend.app.services.index_signal import shadow_log, variants

B = 1_800_000_000.0


def _flat(n: int, *, close=100.0, volume=100.0, oi=1_000_000.0) -> list[ex.Bar]:
    return [ex.Bar(ts=B + 60 * i, close=close, volume=volume, oi=oi) for i in range(n)]


# -- the engine ----------------------------------------------------------------------------


def test_only_the_open_interest_window_has_the_fifteen_minute_floor():
    ex.ExpansionParams(window_minutes=5, oi_window_minutes=15)
    ex.ExpansionParams(window_minutes=5, require_oi=False)
    with pytest.raises(ValueError, match="open-interest window"):
        ex.ExpansionParams(window_minutes=15, oi_window_minutes=10)
    with pytest.raises(ValueError, match="open-interest window"):
        ex.ExpansionParams(window_minutes=5)  # OI would default to the 5-minute window


def test_a_short_price_window_is_confirmed_by_oi_over_its_own_window():
    p = ex.ExpansionParams(window_minutes=5, oi_window_minutes=15, hold_minutes=5)
    bars = _flat(p.min_baseline_bars + 20)
    # A heavy 5-minute rally with OI flat throughout: an expansion, but no new positions.
    bars = bars[:-5] + [
        ex.Bar(ts=b.ts, close=100.0 + 0.5 * (k + 1), volume=10_000.0, oi=b.oi)
        for k, b in enumerate(bars[-5:])
    ]
    side, _strength, parts, reason = ex.evaluate(bars, p)
    assert side is None and reason == ex.REASON_UNWIND  # price up, OI flat -> not new longs
    # Now OI builds inside the 15-minute window but *before* the 5-minute price window.
    bars2 = list(bars)
    for i in range(len(bars2) - 12, len(bars2)):
        b = bars2[i]
        bars2[i] = ex.Bar(ts=b.ts, close=b.close, volume=b.volume, oi=1_050_000.0)
    side, _strength, parts, reason = ex.evaluate(bars2, p)
    assert side == "bullish" and parts["oi_delta"] == pytest.approx(50_000.0)


def test_a_call_keeps_its_start_while_re_fired_then_lapses_to_neutral():
    p = ex.ExpansionParams(window_minutes=5, require_oi=False, hold_minutes=5)
    eng = ex.ExpansionEngine("nifty", p)
    eng.seed(_flat(p.min_baseline_bars + 10))
    t0 = B + 60 * (p.min_baseline_bars + 10)
    for k in range(3):  # three rising, heavy minutes: fired, then re-fired twice
        eng.on_bar(ex.Bar(ts=t0 + 60 * k, close=101.0 + k, volume=50_000.0))
    snap = eng.snapshot(t0 + 150, session_open=True)
    assert snap["state"] == "bullish" and snap["call_started_at"] == t0
    assert snap["hold_minutes"] == 5 and snap["oi_window_minutes"] is None
    # Quiet minutes until the call lapses...
    for k in range(3, 12):
        eng.on_bar(ex.Bar(ts=t0 + 60 * k, close=103.0, volume=100.0))
    assert eng.snapshot(t0 + 60 * 11 + 30, session_open=True)["state"] == "neutral"


# -- the store ---------------------------------------------------------------------------


def test_the_four_starting_variants_are_seeded_with_the_incumbent_first():
    ids = [v.id for v in variants.list_variants(fresh=True)]
    assert ids == [v.id for v in variants.BUILTINS]
    incumbent = variants.get_variant(variants.INCUMBENT_ID)
    assert incumbent.log_label == "nifty:expansion" and incumbent.builtin
    # It shares the engine the navbar publishes from: the same parameters exactly.
    assert incumbent.params() == ex.ExpansionParams(require_oi=True)


def test_the_domain_defaults_name_real_variants():
    assert domain.EXPANSION_FADE_15_VARIANT == variants.FADE_15_ID
    assert domain.EXPANSION_FOLLOW_15_VARIANT == variants.INCUMBENT_ID
    assert MomentumLongScalperConfig().entry_signal == variants.FADE_15_ID


def test_a_variant_is_created_once_and_a_duplicate_definition_is_refused():
    v = variants.create_variant(
        name="10m fade", window_minutes=10, oi_window_minutes=20, hold_minutes=10, direction="fade"
    )
    assert v.id == "nifty-w10-oi20-h10-fade" and not v.builtin
    assert v.log_label == "nifty:expansion:nifty-w10-oi20-h10-fade"
    with pytest.raises(ValueError, match="already reads"):
        variants.create_variant(
            name="again", window_minutes=10, oi_window_minutes=20, hold_minutes=10, direction="fade"
        )
    with pytest.raises(ValueError, match="open-interest window"):
        variants.create_variant(
            name="thin", window_minutes=5, oi_window_minutes=10, hold_minutes=5, direction="follow"
        )


def test_deleting_a_variant_deletes_its_evidence_but_a_builtin_cannot_go(tmp_path, monkeypatch):
    db = str(tmp_path / "log.sqlite3")
    monkeypatch.setattr(shadow_log, "_db_path", lambda: db)
    v = variants.create_variant(
        name="x", window_minutes=20, oi_window_minutes=None, hold_minutes=20, direction="follow"
    )
    shadow_log.record(v.log_label, {"state": "bullish"}, spot=24000.0, now=B, db_path=db)
    shadow_log.record("nifty:expansion", {"state": "bullish"}, spot=24000.0, now=B, db_path=db)
    variants.delete_variant(v.id)
    assert shadow_log.load_rows(v.log_label, 0, db) == []
    assert shadow_log.load_rows("nifty:expansion", 0, db)  # the incumbent's record is untouched
    with pytest.raises(ValueError, match="Built-in"):
        variants.delete_variant(variants.FADE_15_ID)


def test_only_a_variant_label_may_be_purged_this_way():
    with pytest.raises(ValueError):
        shadow_log.purge_variant_label("nifty:expansion")
    with pytest.raises(ValueError):
        shadow_log.purge_variant_label("nifty:flow")


def test_a_fade_swaps_the_call_and_negates_the_strength():
    fade = variants.get_variant(variants.FADE_15_ID)
    out = variants.apply_direction({"state": "bullish", "signal": 0.9}, fade)
    assert out["state"] == "bearish" and out["signal"] == -0.9 and out["source_state"] == "bullish"
    # Anything that is not a call passes through: unavailable is never turned into a trade.
    assert variants.apply_direction({"state": "unavailable", "reason": "stale"}, fade)["state"] == "unavailable"


# -- Bot 3 on a variant --------------------------------------------------------------------


def test_a_variant_call_is_an_entry_carrying_when_the_call_began():
    r = evaluate_variant({"state": "bearish", "call_started_at": B, "hold_minutes": 15}, "v")
    assert r.fired and r.right == "put" and r.values["candle_start"] == int(B)
    assert not evaluate_variant({"state": "neutral"}, "v").fired
    unavailable = evaluate_variant({"state": "unavailable", "reason": "stale"}, "v")
    assert not unavailable.fired and unavailable.reason == "signal_unavailable:stale"


def test_one_trade_per_call():
    live = {"state": "bullish", "call_started_at": B}
    assert variant_call_unbroken(live, entry_candle_start=int(B), side="bullish")
    assert not variant_call_unbroken({**live, "call_started_at": B + 600}, entry_candle_start=int(B), side="bullish")
    assert not variant_call_unbroken({"state": "neutral"}, entry_candle_start=int(B), side="bullish")


def test_a_variant_trade_exits_when_its_window_ends_not_at_ninety_seconds():
    cfg = TrailingLadderConfig()
    state = ladder.open_ladder(100.0, B, cfg)
    # Going nowhere at 90s would time a momentum trade out; a variant trade holds.
    assert ladder.exit_decision(state, 100.5, B + 120, cfg) is not None
    assert ladder.exit_decision(state, 100.5, B + 120, cfg, hold_seconds=900) is None
    code, _ = ladder.exit_decision(state, 100.5, B + 900, cfg, hold_seconds=900)
    assert code == ReasonCode.SIGNAL_WINDOW_ENDED
    # The stop still applies throughout.
    code, _ = ladder.exit_decision(state, 90.0, B + 60, cfg, hold_seconds=900)
    assert code == ReasonCode.STOP_LOSS


_DAY = datetime.date(2026, 3, 9)  # a Monday, one day before a Tuesday expiry


def _day_bars() -> list[HistCandle]:
    base = datetime.datetime.combine(_DAY, datetime.time(9, 15))
    return [
        HistCandle(base + datetime.timedelta(minutes=i), 24_000.0, 24_000.0, 24_000.0, 24_000.0, 1_000)
        for i in range(120)
    ]


def test_the_backtest_trades_the_replayed_call_once_and_holds_for_its_window():
    fade = variants.get_variant(variants.FADE_15_ID)
    bars = _day_bars()
    call_start = bars[40].ts
    started = call_start.replace(tzinfo=IST).timestamp()
    readings = {
        b.ts: {"state": "bearish", "call_started_at": started, "hold_minutes": 15, "direction": "fade"}
        for b in bars[40:60]
    }
    result = run_backtest(
        bars,
        config=MomentumLongScalperConfig(),
        charges=ChargesModel(),
        spread=SpreadStats(source="default", samples=0, median_spread_pct=0.5),
        vix_by_day={_DAY: 13.0},
        variant=fade,
        readings=readings,
    )
    assert len(result.cycles) == 1  # one call, one trade -- however long it stays live
    c = result.cycles[0]
    assert c.right == "put"
    assert c.exit_reason == ReasonCode.SIGNAL_WINDOW_ENDED
    assert (c.exited_at - c.entered_at).total_seconds() >= 15 * 60
    assert result.skipped_same_signal >= 1


def test_a_backtest_refuses_to_replay_momentum_for_a_bot_set_to_a_variant():
    with pytest.raises(ValueError, match="signal variant"):
        run_backtest(
            _day_bars(),
            config=MomentumLongScalperConfig(),
            charges=ChargesModel(),
            spread=SpreadStats(source="default", samples=0, median_spread_pct=0.5),
            vix_by_day={},
        )


# -- Bot 4's entry filter ------------------------------------------------------------------


def test_the_expansion_filter_waits_out_either_call_and_holds_when_blind():
    assert expansion_neutral_hold({"state": "neutral"}) is None
    assert expansion_neutral_hold({"state": "bullish"})[0] == ReasonCode.ENTRY_FILTER_CLOSED
    assert expansion_neutral_hold({"state": "bearish"})[0] == ReasonCode.ENTRY_FILTER_CLOSED
    assert expansion_neutral_hold({"state": "unavailable", "reason": "stale"})[0] == ReasonCode.ENTRY_FILTER_CLOSED
    assert expansion_neutral_hold(None)[0] == ReasonCode.ENTRY_FILTER_CLOSED


def _vix(values: list[float]) -> list[tuple[float, float]]:
    return [(B + 60 * i, v) for i, v in enumerate(values)]


def test_vix_change_is_measured_over_the_lookback_from_closed_bars():
    series = _vix([13.0] * 10 + [13.5] * 10)
    now = B + 60 * 20  # bar 19 has just closed
    pct, detail = vix_minutes.change_pct(series, now, 15)
    # Bar 19 (13.5) against bar 4 (13.0).
    assert pct == pytest.approx(100 * 0.5 / 13.0)
    assert detail["vix_ref"] == 13.0


def test_the_vix_filter_holds_on_a_rise_and_on_no_series():
    f = IronFlyEntryFilterConfig(kind="vix_not_rising", vix_lookback_minutes=15, vix_max_rise_pct=2.0)
    now = B + 60 * 20
    assert vix_minutes.filter_hold(f, _vix([13.0] * 20), now) is None
    assert vix_minutes.filter_hold(f, _vix([13.0] * 10 + [13.5] * 10), now)[0] == ReasonCode.ENTRY_FILTER_CLOSED
    assert vix_minutes.filter_hold(f, [], now)[0] == ReasonCode.ENTRY_FILTER_CLOSED
    # A series that stopped updating is not a current reading.
    assert vix_minutes.filter_hold(f, _vix([13.0] * 20), now + 3600)[0] == ReasonCode.ENTRY_FILTER_CLOSED


# -- publication ---------------------------------------------------------------------------


def test_variants_that_differ_only_in_direction_share_an_engine_and_publish_their_own_way(monkeypatch):
    from icici_breeze_backend.app.services.index_signal import publisher

    publisher.reset_state_for_tests()
    monkeypatch.setattr(publisher, "_seed_engine", lambda eng, **kw: 0)
    # The four built-ins need two engines beyond the incumbent's: both 15-minute variants
    # read the incumbent's own, and the two 5-minute ones one each.
    assert publisher.sync_variant_engines() == 2

    written: dict[str, dict] = {}
    logged: list[str] = []
    monkeypatch.setattr(publisher, "_write_payload", lambda key, payload, valid: written.__setitem__(key, payload))
    monkeypatch.setattr(publisher, "_index_spot", lambda label, ts=None: 24_000.0)
    monkeypatch.setattr(shadow_log, "record", lambda label, payload, **kw: logged.append(label))
    incumbent = publisher._expansion_engine("nifty")
    incumbent._held, incumbent._held_until, incumbent._call_started = "bullish", B + 900, B
    incumbent._bars.append(ex.Bar(ts=B, close=100.0))

    publisher._publish_variants(B + 60, session_open=True, excluded=False, valid_for=10.0, interval=2.0)
    follow = written[f"variant:{variants.INCUMBENT_ID}"]
    fade = written[f"variant:{variants.FADE_15_ID}"]
    assert follow["state"] == "bullish" and fade["state"] == "bearish"
    assert fade["call_started_at"] == follow["call_started_at"] == B
    # The incumbent's readings are already logged as the mechanism's; every other variant logs its own.
    assert "nifty:expansion" not in logged and f"nifty:expansion:{variants.FADE_15_ID}" in logged
    publisher.reset_state_for_tests()
