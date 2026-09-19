"""Bot 3 end to end in paper mode (services.bots.scalping.momentum_bot + runtime).

Drives the real driver, the real gate stack and the real ladder against a fake broker, so
what is under test is the wiring between them rather than any one part. The load-bearing
assertion is `test_a_full_paper_round_trip_is_recorded_net_of_friction`: it is the first
point at which a cycle's P&L exists at all.
"""
from __future__ import annotations

import datetime

import pytest

from icici_breeze_backend.app.db.bots_migrate import (
    BOT_MOMENTUM_LONG_SCALPER,
    ensure_bots_tables,
)
from icici_breeze_backend.app.domain.bots import MomentumLongScalperConfig, ReasonCode
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots import charges as charges_mod
from icici_breeze_backend.app.services.bots.scalping import momentum_bot, runtime
from icici_breeze_backend.app.services.bots.scalping.decide import FeedHealth

USER = "u1"
EXPIRY = "10-Sep-2026"
LOT = 75


class FakeProc:
    """Only what the bot actually asks a broker for."""

    def __init__(self, *, lot_size=LOT):
        self._lot = lot_size

    def fetch_stock_codes(self, exchange_code):
        return [{"stock_code": "NIFTY", "expiry_dates": ["2026-09-10T06:00:00.000Z"]}]

    def fetch_lot_size(self, stock_code, expiry_display, exchange_code=None):
        return self._lot


def _chain(spot=24_010.0, bid=100.0, ask=101.0, strikes=(23_900, 24_000, 24_100)):
    return {
        "Status": 200,
        "Success": [
            {
                "strike_price": s,
                "spot_price": spot,
                "best_bid_price": bid,
                "best_offer_price": ask,
                "ltp": (bid + ask) / 2,
            }
            for s in strikes
        ],
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    monkeypatch.setattr(charges_mod, "_db_path", lambda: path)
    ensure_bots_tables(path)
    runtime.reset_state_for_tests()

    monkeypatch.setattr(runtime, "_trading_allowed", lambda: True)
    monkeypatch.setattr(runtime, "_api_calls_remaining", lambda uid: 90)
    monkeypatch.setattr(runtime, "_is_expiry_day", lambda cfg: False)
    monkeypatch.setattr(
        runtime, "_feed_health", lambda bot_type, cfg: FeedHealth(warm=True, stale=False, stale_seconds=0.0)
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.market_calendar.is_trading_day", lambda now=None: True
    )
    monkeypatch.setattr(runtime, "now_ist", lambda: datetime.datetime(2026, 9, 8, 10, 0))
    # The fake scrip master lists a 10-Sep-2026 expiry; the expiry picker keeps its own clock.
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.bots.scalping.momentum_bot.now_ist",
        lambda: datetime.datetime(2026, 9, 8, 10, 0),
    )

    proc = FakeProc()
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.processor.processor", lambda: proc
    )
    return path, proc


def _set_quotes(monkeypatch, *, bid, ask, spot=24_010.0):
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.quote_source_router.fetch_chain_side_icici_response",
        lambda *a, **k: _chain(spot=spot, bid=bid, ask=ask),
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.quote_source_router.fetch_quote_icici_response",
        lambda *a, **k: {
            "Status": 200,
            "Success": [{"best_bid_price": bid, "best_offer_price": ask, "ltp": (bid + ask) / 2}],
        },
    )


def _published(monkeypatch, payload):
    """What the bot's series publishes now -- read on entry, and by the held trade's call check."""
    import time as _time

    def get_signal(key, *, direction="follow", now=None):
        from icici_breeze_backend.app.services.index_signal.series import apply_direction

        return apply_direction(dict(payload), direction)

    monkeypatch.setattr("icici_breeze_backend.app.services.index_signal.reader.get_signal", get_signal)
    return _time


FOLLOW_15 = {"mechanism": "expansion", "duration": 15, "direction": "follow"}


def _cfg(**kw) -> MomentumLongScalperConfig:
    return MomentumLongScalperConfig(signal=FOLLOW_15, **kw)


def _bullish_call(monkeypatch):
    """A bullish call that began at 10:00 and stands well past the test's passes."""
    started = datetime.datetime(2026, 9, 8, 10, 0).timestamp()
    _published(monkeypatch, {"state": "bullish", "call_started_at": started,
                             "held_until": started + 86_400, "signal": 0.92,
                             "mechanism": "expansion", "duration_minutes": 15})


# --- strike selection and sizing -------------------------------------------------------


def test_atm_is_the_listed_strike_nearest_spot():
    rows = _chain(spot=24_010.0)["Success"]
    assert momentum_bot.atm_strike(rows, 24_010.0) == 24_000.0
    assert momentum_bot.atm_strike(rows, 24_060.0) == 24_100.0


def test_atm_ties_go_to_the_lower_strike():
    rows = _chain(spot=24_050.0)["Success"]
    assert momentum_bot.atm_strike(rows, 24_050.0) == 24_000.0


def test_sizing_uses_the_ask_and_floors_to_whole_lots(env, monkeypatch):
    _, proc = env
    _set_quotes(monkeypatch, bid=100.0, ask=101.0)
    cfg = _cfg(premium_outlay_inr=20_000.0)
    plan, problem = momentum_bot.plan_entry(proc, USER, cfg, "call")
    assert problem is None
    # 101 x 75 = 7,575 per lot -> 2 lots fit in 20,000, not 2.6
    assert plan.lots == 2 and plan.quantity == 150


def test_an_outlay_below_one_lot_is_a_logged_skip_not_a_crash(env, monkeypatch):
    _, proc = env
    _set_quotes(monkeypatch, bid=100.0, ask=101.0)
    cfg = _cfg(premium_outlay_inr=5_000.0)
    plan, problem = momentum_bot.plan_entry(proc, USER, cfg, "call")
    assert plan is None and problem[0] == ReasonCode.OUTLAY_BELOW_ONE_LOT


def test_a_one_sided_quote_blocks_entry(env, monkeypatch):
    _, proc = env
    _set_quotes(monkeypatch, bid=0.0, ask=101.0)
    plan, problem = momentum_bot.plan_entry(proc, USER, _cfg(), "call")
    assert plan is None and problem[0] == ReasonCode.QUOTE_UNAVAILABLE


# --- the round trip --------------------------------------------------------------------


def test_a_signal_opens_a_paper_cycle_with_a_ladder(env, monkeypatch):
    _set_quotes(monkeypatch, bid=100.0, ask=101.0)
    _bullish_call(monkeypatch)
    cfg = _cfg()

    runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, cfg)

    cycles = repo.list_cycles(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)
    assert len(cycles) == 1
    c = cycles[0]
    assert c.paper is True and c.structure == "long_ce" and c.is_open
    assert c.legs[0]["strike_price"] == 24_000.0
    # Entry fills at ask + half the spread, and the ladder starts one stop below it.
    assert c.detail["entry"]["price"] == pytest.approx(101.5)
    assert c.detail["ladder"]["stop_price"] == pytest.approx(95.5)
    # The trade remembers the call that opened it, so it can exit when that call ends.
    assert c.detail["call"]["series"] == "nifty:expansion:15m"
    assert c.detail["call"]["side"] == "bullish" and c.detail["call"]["direction"] == "follow"


def test_no_second_position_is_opened_while_one_is_held(env, monkeypatch):
    """Exactly one position at a time is what makes the outlay a real ceiling."""
    _set_quotes(monkeypatch, bid=100.0, ask=101.0)
    _bullish_call(monkeypatch)
    cfg = _cfg()
    for _ in range(3):
        runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, cfg)
    assert len(repo.list_cycles(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)) == 1


def test_a_full_paper_round_trip_is_recorded_net_of_friction(env, monkeypatch):
    _set_quotes(monkeypatch, bid=100.0, ask=101.0)
    _bullish_call(monkeypatch)
    cfg = _cfg()
    runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, cfg)  # opens at 101.5, stop 95.5

    # Rally past level 2, then collapse through the trailed stop.
    _set_quotes(monkeypatch, bid=112.0, ask=113.0)
    runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, cfg)
    _set_quotes(monkeypatch, bid=104.0, ask=105.0)
    runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, cfg)

    closed = repo.list_cycles(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)[0]
    assert not closed.is_open
    assert closed.exit_reason_code == ReasonCode.TRAILING_STOP
    assert closed.gross_pnl is not None and closed.friction > 0
    assert closed.net_pnl == pytest.approx(closed.gross_pnl - closed.friction, abs=0.01)
    # Friction is real money, not a rounding term.
    assert closed.friction > 40.0


def test_a_ratcheted_stop_is_persisted_so_a_restart_resumes_it(env, monkeypatch):
    """The reason ladder state is written at all -- portal upgrades recreate the container."""
    _set_quotes(monkeypatch, bid=100.0, ask=101.0)
    _bullish_call(monkeypatch)
    cfg = _cfg()
    runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, cfg)
    assert repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)[0].detail["ladder"][
        "stop_price"
    ] == pytest.approx(95.5)

    _set_quotes(monkeypatch, bid=112.0, ask=113.0)
    runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, cfg)

    # +10.5 on a 101.5 entry clears the runner target outright, so the ladder promotes
    # through every rung it has earned in one step and trails the peak: 112 - 3.
    reloaded = repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)[0]
    assert reloaded.detail["ladder"]["stop_price"] == pytest.approx(109.0)
    assert reloaded.detail["ladder"]["level"] == 3
    assert reloaded.detail["ladder"]["peak_price"] == pytest.approx(112.0)


def test_the_hard_square_off_closes_an_open_paper_position(env, monkeypatch):
    _set_quotes(monkeypatch, bid=100.0, ask=101.0)
    _bullish_call(monkeypatch)
    cfg = _cfg()
    runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, cfg)

    monkeypatch.setattr(runtime, "now_ist", lambda: datetime.datetime(2026, 9, 8, 15, 20))
    runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, cfg)

    closed = repo.list_cycles(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)[0]
    assert not closed.is_open and closed.exit_reason_code == ReasonCode.SQUARE_OFF


def test_an_open_live_position_is_never_closed_at_a_simulated_price(env, monkeypatch):
    """An open position is managed the way it was OPENED, not the way the bot is set now.

    The hazard this pins: a user moves a bot holding a real position from Live back to Paper
    (or switches it off entirely, which is the `entries_suspended` tick). Routing the exit on
    `config.mode` would send it to `close_paper_cycle`, marking the cycle closed at a
    simulated price while the actual position sits at the exchange with nothing managing it
    -- the row would say "done" and the money would still be at risk.
    """
    _set_quotes(monkeypatch, bid=100.0, ask=101.0)
    _bullish_call(monkeypatch)

    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    repo.open_cycle(
        USER, BOT_MOMENTUM_LONG_SCALPER, run_id,
        structure="long_ce",
        legs=[{"right": "call", "strike_price": 24000.0, "quantity": 75,
               "stock_code": "NIFTY", "expiry_date": "2026-09-10", "action": "buy"}],
        lots=1, entry_value=7500.0, paper=False,
    )

    dispatched: list[str] = []
    monkeypatch.setattr(
        momentum_bot, "_close_live",
        lambda *a, **k: dispatched.append("live"),
    )
    monkeypatch.setattr(
        momentum_bot, "_close",
        lambda *a, **k: dispatched.append("paper"),
    )

    # The bot is set to paper and switched off -- the exact state that used to strand it.
    cfg = _cfg(mode="paper")
    monkeypatch.setattr(runtime, "now_ist", lambda: datetime.datetime(2026, 9, 8, 15, 20))
    runtime.tick_bot(
        USER, BOT_MOMENTUM_LONG_SCALPER, cfg, entries_suspended=True
    )

    assert dispatched == ["live"], "a real position must exit through the live path"


def test_a_suspended_bot_opens_nothing_once_it_is_flat(env, monkeypatch):
    """The other half of exit-only: entries are refused the instant the user switches off."""
    _set_quotes(monkeypatch, bid=100.0, ask=101.0)
    _bullish_call(monkeypatch)
    cfg = _cfg(mode="paper")
    decision = runtime.tick_bot(
        USER, BOT_MOMENTUM_LONG_SCALPER, cfg, entries_suspended=True
    )
    assert decision.action == "idle"
    assert decision.reason_code == ReasonCode.ENTRIES_SUSPENDED
    assert repo.list_cycles(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER) == []


def test_no_signal_means_no_cycle_and_no_noise(env, monkeypatch):
    _set_quotes(monkeypatch, bid=100.0, ask=101.0)
    _published(monkeypatch, {"state": "neutral", "reason": "no_expansion"})
    runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, _cfg())
    assert repo.list_cycles(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER) == []
