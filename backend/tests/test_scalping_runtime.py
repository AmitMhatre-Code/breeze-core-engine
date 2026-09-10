"""The scalper driver (services.bots.scalping.runtime).

Step 3's contract is narrow and worth pinning: the loop decides, logs and heartbeats, and
places nothing. It also must not fall over when the licence, the pacer or the feed are
unavailable -- it runs unattended for a whole session.
"""
from __future__ import annotations

import datetime
import os

import pytest

from icici_breeze_backend.app.db.bots_migrate import (
    BOT_IRON_FLY_SCALPER,
    BOT_MOMENTUM_LONG_SCALPER,
    ensure_bots_tables,
)
from icici_breeze_backend.app.domain.bots import (
    IronFlyScalperConfig,
    MomentumLongScalperConfig,
    ReasonCode,
    ScalperDayTotals,
)
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots.scalping import runtime
from icici_breeze_backend.app.services.bots.scalping.decide import FeedHealth
from icici_breeze_backend.app.services.bots.scalping.signal import SignalResult
from icici_breeze_backend.audit import bot_audit

USER = "u1"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_bots_tables(path)
    runtime.reset_state_for_tests()
    return path


@pytest.fixture
def stubbed(monkeypatch, tmp_path):
    """Isolate the driver from the broker, the licence, the clock and the data volume."""
    # The driver now appends an audit trail on every pass. Without this the suite writes
    # JSONL into the developer's real `backend/data/` -- the same class of pollution as the
    # Redis scrip index, and worse here because the files look like genuine trading days.
    monkeypatch.setattr(bot_audit.cfg, "DATA_PATH", str(tmp_path) + os.sep)
    bot_audit._last_signature.clear()
    bot_audit._last_pruned.clear()
    monkeypatch.setattr(runtime, "_trading_allowed", lambda: True)
    monkeypatch.setattr(runtime, "_api_calls_remaining", lambda uid: 90)
    monkeypatch.setattr(runtime, "_is_expiry_day", lambda cfg: False)
    monkeypatch.setattr(
        runtime, "_feed_health", lambda cfg: FeedHealth(warm=True, stale=False, stale_seconds=0.0)
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.market_calendar.is_trading_day", lambda now=None: True
    )
    monkeypatch.setattr(runtime, "now_ist", lambda: datetime.datetime(2026, 9, 8, 10, 0))
    # The signal is now the last entry gate, so these tests would otherwise stand down on a
    # feed that has no candles. Firing by default keeps each test asserting what it is about;
    # `test_no_signal_is_a_recorded_verdict` overrides this to check the other branch.
    monkeypatch.setattr(
        runtime,
        "_entry_signal",
        lambda bot_type, config: SignalResult(
            side="bullish", reason="close above EMA and VWAP on 1.8x volume", values={"volume_x": 1.8}
        ),
    )


def test_a_pass_opens_a_session_run_and_heartbeats_it(db_path, stubbed):
    decision = runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, MomentumLongScalperConfig())
    assert decision.action == "enter"  # gates clear; the bot layer would take it from here

    runs = repo.list_runs(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)
    assert len(runs) == 1 and runs[0].status == "running" and runs[0].trigger == "session"

    import sqlite3

    with sqlite3.connect(db_path) as conn:
        beat = conn.execute("SELECT heartbeat_at FROM bot_runs WHERE id = ?", (runs[0].id,)).fetchone()[0]
    assert beat is not None


def test_repeated_passes_reuse_one_session_run(db_path, stubbed):
    cfg = MomentumLongScalperConfig()
    for _ in range(5):
        runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, cfg)
    assert len(repo.list_runs(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)) == 1


def test_no_signal_is_a_recorded_verdict_not_a_silent_return(db_path, stubbed, monkeypatch):
    """A warm bot that declines to trade must say so on the run row.

    This is the gap that made a zero-cycle paper day unexplainable after the fact: the
    no-signal branch used to `return` from the executor with only a DEBUG log, so the run
    row still read `gates_clear` -- indistinguishable from a bot that was about to trade.
    """
    monkeypatch.setattr(
        runtime,
        "_entry_signal",
        lambda bot_type, config: SignalResult(
            side=None, reason="volume 0.9x the 20-bar mean", values={"volume_x": 0.9}
        ),
    )
    decision = runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, MomentumLongScalperConfig())

    assert decision.action == "idle"
    assert decision.reason_code == ReasonCode.SIGNAL_NO_TRADE

    run = repo.list_runs(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)[0]
    assert run.reason_code == ReasonCode.SIGNAL_NO_TRADE
    assert "volume 0.9x" in (run.reason_text or "")
    assert (run.detail or {}).get("decision", {}).get("volume_x") == 0.9
    assert repo.list_cycles(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER) == []


def test_feed_counters_reach_the_run_row(db_path, stubbed, monkeypatch):
    """`counter_resets`/`stale_ticks` travel with the verdict, not just the log.

    Without these on the row, a session that kept losing its candle history is
    indistinguishable from one that was simply slow to warm up.
    """
    monkeypatch.setattr(
        runtime,
        "_feed_health",
        lambda cfg: FeedHealth(
            warm=True, stale=False, stale_seconds=0.0,
            detail={"token_symbol": "4.1!68407", "candles": 47, "candles_required": 20,
                    "ticks_seen": 18691, "counter_resets": 3, "stale_ticks": 12},
        ),
    )
    runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, MomentumLongScalperConfig())

    feed = (repo.list_runs(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)[0].detail or {})["feed"]
    assert feed["counter_resets"] == 3
    assert feed["stale_ticks"] == 12


def test_step_3_places_nothing_and_opens_no_cycles(db_path, stubbed):
    """The whole point of this step: observable, and inert."""
    cfg = MomentumLongScalperConfig()
    for _ in range(3):
        assert runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, cfg).action == "enter"
    assert repo.list_cycles(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER) == []
    assert repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER) == []


def test_a_session_run_is_written_even_on_a_quiet_day(db_path, stubbed, monkeypatch):
    """An unexplained no-trade day is exactly what the run log exists to prevent."""
    monkeypatch.setattr(runtime, "now_ist", lambda: datetime.datetime(2026, 9, 8, 12, 0))
    d = runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, MomentumLongScalperConfig())
    assert d.reason_code == ReasonCode.OUTSIDE_SESSION_WINDOW
    assert len(repo.list_runs(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)) == 1


def test_an_open_cycle_is_seen_as_a_held_position(db_path, stubbed):
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    repo.open_cycle(
        USER, BOT_MOMENTUM_LONG_SCALPER, run_id,
        structure="long_ce", legs=[], lots=1, entry_value=1000.0,
    )
    d = runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, MomentumLongScalperConfig())
    assert d.action == "idle" and d.reason_code == "holding"


def test_an_unknown_licence_state_is_treated_as_read_only(monkeypatch):
    """Fail closed: an unreadable licence is not a licence."""
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.deployment_license_status.trading_mutations_allowed",
        lambda: (_ for _ in ()).throw(RuntimeError("portal unreachable")),
    )
    assert runtime._trading_allowed() is False


def test_an_unreadable_api_budget_blocks_entries_only(monkeypatch):
    """Unknown budget reads as none left, which is an entry gate and never an exit one."""
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.icici_api_pacing.GlobalIciciApiPacer.calls_in_window",
        staticmethod(lambda uid: (_ for _ in ()).throw(RuntimeError("boom"))),
    )
    assert runtime._api_calls_remaining(USER) == 0


def test_the_loop_cadence_follows_the_pb_sl_setting(monkeypatch):
    """One latency knob for everything watching an open position, not a second one."""
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.pnl_engine_settings.load_pnl_engine_settings",
        lambda: {"pnl_recompute_interval_seconds": 7.5},
    )
    assert runtime._interval_seconds() == pytest.approx(7.5)


def test_cadence_falls_back_when_the_setting_cannot_be_read(monkeypatch):
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.pnl_engine_settings.load_pnl_engine_settings",
        lambda: (_ for _ in ()).throw(RuntimeError("db locked")),
    )
    assert 1.0 <= runtime._interval_seconds() <= 30.0


def test_one_failing_bot_does_not_kill_the_pass(db_path, monkeypatch):
    """The loop runs unattended all session; a bad bot must not take the others with it."""
    monkeypatch.setattr(
        repo, "list_enabled_bots",
        lambda bot_type: (_ for _ in ()).throw(RuntimeError("db gone")),
    )
    runtime.tick()  # must not raise


# --- the futures feed the whole gate stack depends on ---------------------------------


class _FakeFeed:
    """Stands in for the process-wide `NiftyFuturesFeed`, recording what the driver asks."""

    def __init__(self, *, subscribed: bool = False) -> None:
        self.subscribed_today = subscribed
        self.subscribes: list[tuple[str, list[str]]] = []
        self.flushes = 0
        self.raises = False

    def ensure_subscribed(self, proc, user_id, option_expiries):
        self.subscribes.append((user_id, list(option_expiries)))
        if self.raises:
            raise RuntimeError("no broker session")
        self.subscribed_today = True
        return True

    def flush(self, now_ts):
        self.flushes += 1


@pytest.fixture
def feed(monkeypatch):
    from icici_breeze_backend.app.services.bots.scalping import futures_feed, momentum_bot

    fake = _FakeFeed()
    monkeypatch.setattr(futures_feed, "get_feed", lambda: fake)
    monkeypatch.setattr(momentum_bot, "option_expiries", lambda proc: ["08-Sep-2026"])
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.processor.processor", lambda: object()
    )
    # The feed now runs on the session clock rather than as a side effect of an armed bot
    # (plan section 5.6), so a test that wants it serviced has to be inside market hours with
    # a broker session to subscribe with.
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.market_calendar.is_trading_day", lambda now=None: True
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.market_calendar.is_market_open", lambda now=None: True
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.market_calendar.has_market_opened",
        lambda now=None: True,
    )
    monkeypatch.setattr(runtime, "_feed_owner", lambda: USER)
    monkeypatch.setattr(runtime, "now_ist", lambda: datetime.datetime(2026, 9, 8, 10, 0))
    return fake


@pytest.fixture
def armed(db_path):
    """One enabled scalper, so `tick` has something to sweep."""
    repo.update_bot(USER, BOT_MOMENTUM_LONG_SCALPER, enabled=True)
    return db_path


@pytest.fixture
def decisions(monkeypatch):
    """Records `tick_bot` calls so the feed wiring can be tested on its own."""
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        runtime, "tick_bot", lambda uid, bt, cfg, **kw: seen.append((uid, bt))
    )
    return seen


def test_a_pass_subscribes_the_futures_feed(armed, feed, decisions):
    """The regression this exists for.

    `ensure_subscribed` was written, documented and unit-tested but never called, so the
    candle builder never received a tick, `is_warm` was false forever, and both scalpers
    stood down on `not_warm` for a whole session while the run log showed them running.
    """
    runtime.tick()
    assert feed.subscribes == [(USER, ["08-Sep-2026"])]
    assert decisions == [(USER, BOT_MOMENTUM_LONG_SCALPER)]


def test_the_feed_is_serviced_before_any_bot_decides(armed, feed, monkeypatch):
    """A bot must never read a builder that this same pass was about to feed."""
    order: list[str] = []
    monkeypatch.setattr(
        runtime, "tick_bot", lambda uid, bt, cfg, **kw: order.append("decide")
    )
    original = feed.ensure_subscribed

    def _record(proc, user_id, option_expiries):
        order.append("subscribe")
        return original(proc, user_id, option_expiries)

    feed.ensure_subscribed = _record
    runtime.tick()
    assert order[0] == "subscribe"


def test_bars_are_flushed_every_pass_even_when_already_subscribed(armed, feed, decisions):
    """A bar the clock has left must close even when the contract has not printed."""
    feed.subscribed_today = True
    runtime.tick()
    runtime.tick()
    assert feed.flushes == 2
    # An idempotent re-subscribe would be harmless, but building the expiry list to ask is a
    # whole-table scan on a cold cache -- not something to pay at the PB/SL cadence.
    assert feed.subscribes == []


def test_a_failed_subscribe_is_retried_but_not_on_every_pass(armed, feed, decisions, monkeypatch):
    """Before the broker session exists, failure is ordinary -- and must stay quiet."""
    feed.raises = True
    clock = [1000.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])

    for _ in range(5):
        runtime.tick()
    assert len(feed.subscribes) == 1, "retried at the 2s loop cadence"

    clock[0] += runtime.FEED_RETRY_SECONDS + 1
    runtime.tick()
    assert len(feed.subscribes) == 2


def test_a_dead_feed_does_not_stop_the_gate_stack(armed, feed, decisions):
    """Exits still have to run when the feed is down; the stack must reach the bots."""
    feed.raises = True
    runtime.tick()  # must not raise
    assert decisions == [(USER, BOT_MOMENTUM_LONG_SCALPER)]


# --- the feed runs the whole session, armed or not (plan section 5.6) -------------------


def test_the_feed_subscribes_with_every_bot_switched_off(db_path, feed, decisions):
    """The point of section 5.6.

    As first built the feed was a side effect of iterating armed bots, so it subscribed only
    when one was armed -- and a user who armed a bot at 11:00 got one that could not act
    until ~11:20, because the EMA and volume MA build from live ticks with no backfill.
    """
    runtime.tick()
    assert feed.subscribes == [(USER, ["08-Sep-2026"])]
    assert decisions == [], "nothing armed, so nothing decided -- but the candles are building"


def test_the_feed_stays_quiet_outside_market_hours(db_path, feed, decisions, monkeypatch):
    """Nothing prints before 09:15 or after the close; subscribing then buys nothing."""
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.market_calendar.is_market_open", lambda now=None: False
    )
    runtime.tick()
    assert feed.subscribes == [] and feed.flushes == 0


def test_the_feed_stays_quiet_on_a_holiday(db_path, feed, decisions, monkeypatch):
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.market_calendar.is_trading_day", lambda now=None: False
    )
    runtime.tick()
    assert feed.subscribes == []


def test_no_bot_is_ticked_before_the_open(armed, feed, decisions, monkeypatch):
    """A deployment powered on at 08:00 showed its scalpers running from 08:00, because a
    pass opens the day's session run whatever the verdict."""
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.market_calendar.has_market_opened",
        lambda now=None: False,
    )
    runtime.tick()
    assert decisions == []


def test_bots_keep_ticking_after_the_close(armed, feed, decisions, monkeypatch):
    """The gate is "has the day started", not "is the market open": a bot finishing its day
    after 15:30 still has to finalise its run."""
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.market_calendar.is_market_open", lambda now=None: False
    )
    runtime.tick()
    assert decisions == [(USER, BOT_MOMENTUM_LONG_SCALPER)]


def test_no_broker_session_means_nothing_to_subscribe_with(db_path, feed, monkeypatch):
    """Unchanged behaviour, just reached earlier in the day: without an ICICI login there is
    no session to open a socket with, and the feed simply waits."""
    monkeypatch.setattr(runtime, "_feed_owner", lambda: None)
    runtime.tick()
    assert feed.subscribes == []


def test_read_only_mode_still_builds_candles(db_path, feed, decisions, monkeypatch):
    """Building candles is reading data, not trading. Stopping the feed when a licence
    lapses would mean a licence restored at 13:00 left the bot standing down on `not_warm`
    until 13:20 -- punishing the user twice for one lapse."""
    monkeypatch.setattr(runtime, "_trading_allowed", lambda: False)
    runtime.tick()
    assert feed.subscribes == [(USER, ["08-Sep-2026"])]


# --- a real position outlives the switch that opened it --------------------------------


def test_a_switched_off_bot_holding_a_live_position_is_still_ticked(db_path, feed, monkeypatch):
    """Section 5.5's rule, extended past the arming switch itself.

    `tick` walks *enabled* bots, so setting a bot to Off with a real position open used to
    stop the only thing evaluating its stop -- stranding it at the exchange, unmanaged.
    """
    seen: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(
        runtime, "tick_bot",
        lambda uid, bt, cfg, **kw: seen.append((uid, bt, kw.get("entries_suspended", False))),
    )
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    repo.open_cycle(
        USER, BOT_MOMENTUM_LONG_SCALPER, run_id,
        structure="long_ce", legs=[], lots=1, entry_value=1.0, paper=False,
    )
    # Deliberately NOT enabled -- this is the user having switched it off.
    runtime.tick()
    assert seen == [(USER, BOT_MOMENTUM_LONG_SCALPER, True)]


def test_an_armed_bot_is_not_also_ticked_as_exit_only(armed, feed, monkeypatch):
    """One pass per bot. A bot that is both armed and holding must not decide twice."""
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        runtime, "tick_bot", lambda uid, bt, cfg, **kw: seen.append((uid, bt))
    )
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    repo.open_cycle(
        USER, BOT_MOMENTUM_LONG_SCALPER, run_id,
        structure="long_ce", legs=[], lots=1, entry_value=1.0, paper=False,
    )
    runtime.tick()
    assert seen == [(USER, BOT_MOMENTUM_LONG_SCALPER)]


def test_an_abandoned_paper_position_is_not_chased(db_path, feed, monkeypatch):
    """A simulation has no exchange side. Ticking a switched-off bot for it would keep a
    disarmed bot running all day over a position that does not exist."""
    seen: list = []
    monkeypatch.setattr(runtime, "tick_bot", lambda uid, bt, cfg, **kw: seen.append(bt))
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    repo.open_cycle(
        USER, BOT_MOMENTUM_LONG_SCALPER, run_id,
        structure="long_ce", legs=[], lots=1, entry_value=1.0, paper=True,
    )
    runtime.tick()
    assert seen == []


# --- the audit trail ------------------------------------------------------------------
#
# A scalper holds one run row open all day. Without a live verdict on it, a bot standing
# down for a whole session is indistinguishable from one nobody ever asked -- which is the
# state that hid an unsubscribed futures feed for an entire trading day.


def _feed_detail(**kw):
    detail = {
        "candles": 0,
        "candles_required": 20,
        "ticks_seen": 0,
        "contract": None,
        "token_symbol": None,
        "last_error": None,
    }
    detail.update(kw)
    return detail


def test_the_running_row_carries_the_current_verdict(db_path, stubbed, monkeypatch):
    """The Reason column answers "why is nothing happening" while it is happening."""
    monkeypatch.setattr(
        runtime, "_feed_health",
        lambda cfg: FeedHealth(
            warm=False, stale=False, stale_seconds=1.0, detail=_feed_detail()
        ),
    )
    runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, MomentumLongScalperConfig())

    run = repo.list_runs(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)[0]
    assert run.status == "running"
    assert run.reason_code == ReasonCode.NOT_WARM
    assert run.reason_text  # the sentence a user reads, not just the code
    assert run.detail["feed"]["ticks_seen"] == 0
    # The fact that separates "warming up normally" from "never subscribed".
    assert run.detail["feed"]["subscribed"] is False


def test_the_detail_distinguishes_a_live_feed_from_an_unsubscribed_one(db_path, stubbed, monkeypatch):
    monkeypatch.setattr(
        runtime, "_feed_health",
        lambda cfg: FeedHealth(
            warm=False, stale=False, stale_seconds=2.0,
            detail=_feed_detail(ticks_seen=812, candles=6, token_symbol="4.1!35001",
                                contract="24-Sep-2026"),
        ),
    )
    runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, MomentumLongScalperConfig())
    feed = repo.list_runs(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)[0].detail["feed"]
    assert feed["subscribed"] is True and feed["ticks_seen"] == 812
    assert feed["candles"] == 6 and feed["candles_required"] == 20


def test_an_unchanged_verdict_is_republished_on_a_cadence_not_every_pass(db_path, stubbed, monkeypatch):
    """Two seconds apart would be a SQLite write and a log line per bot per tick; never
    re-stating would leave a whole quiet session as one line at 09:57."""
    writes: list[str] = []
    original = repo.update_run_reason
    monkeypatch.setattr(
        repo, "update_run_reason",
        lambda run_id, **kw: (writes.append(kw["reason_code"]), original(run_id, **kw))[1],
    )
    clock = [1000.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])

    cfg = MomentumLongScalperConfig()
    for _ in range(5):
        runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, cfg)
    assert len(writes) == 1

    clock[0] += runtime.PUBLISH_INTERVAL_SECONDS + 1
    runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, cfg)
    assert len(writes) == 2


def test_a_changed_verdict_publishes_immediately(db_path, stubbed, monkeypatch):
    """A transition is the interesting moment; it must not wait out the cadence."""
    clock = [1000.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    cfg = MomentumLongScalperConfig()
    runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, cfg)
    assert repo.list_runs(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)[0].reason_code == "gates_clear"

    monkeypatch.setattr(runtime, "now_ist", lambda: datetime.datetime(2026, 9, 8, 12, 0))
    runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, cfg)  # same second, new verdict
    run = repo.list_runs(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)[0]
    assert run.reason_code == ReasonCode.OUTSIDE_SESSION_WINDOW


def test_publishing_never_overwrites_a_finished_session(db_path, stubbed):
    """The loop keeps ticking after the day is closed; the last word stays the closing one."""
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    repo.finish_run(
        run_id, status="completed", reason_code="session_complete",
        reason_text="The day's last trading window has closed.",
    )
    runtime.reset_state_for_tests()
    runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, MomentumLongScalperConfig())

    run = repo.list_runs(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)[0]
    assert run.status == "completed" and run.reason_code == "session_complete"


def test_an_audit_write_failure_does_not_stop_the_bot(db_path, stubbed, monkeypatch):
    """Recording why is worth less than continuing to manage a position."""
    monkeypatch.setattr(
        repo, "update_run_reason",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db locked")),
    )
    assert runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, MomentumLongScalperConfig())


def _fly_at_noon(monkeypatch):
    """Inside Bot 4's 11:30-13:30 window, with its executor inert."""
    monkeypatch.setattr(runtime, "now_ist", lambda: datetime.datetime(2026, 9, 8, 12, 0))
    monkeypatch.setattr(runtime.iron_fly_bot, "execute", lambda *a, **k: None)


def test_the_iron_fly_reentry_wait_is_a_recorded_verdict(db_path, stubbed, monkeypatch):
    """The wait after a stop-loss must read as a wait on the run row, not as `gates_clear`.

    Checked only inside the executor, every held pass was published as `enter / gates_clear`
    -- one paper day logged 558 of them for two actual entries.
    """
    _fly_at_noon(monkeypatch)
    monkeypatch.setattr(
        repo, "scalper_day_totals",
        lambda *a, **k: ScalperDayTotals(last_closed_at="2026-09-08 11:55:00"),
    )
    decision = runtime.tick_bot(USER, BOT_IRON_FLY_SCALPER, IronFlyScalperConfig())

    assert decision.action == "idle"
    assert decision.reason_code == ReasonCode.REENTRY_GATE_CLOSED
    run = repo.list_runs(USER, bot_type=BOT_IRON_FLY_SCALPER)[0]
    assert run.reason_code == ReasonCode.REENTRY_GATE_CLOSED
    assert "cooldown" in (run.reason_text or "")


def test_a_failed_reentry_check_holds_the_entry(db_path, stubbed, monkeypatch):
    """Fail closed: a check that cannot run must not wave a fresh fly through."""
    _fly_at_noon(monkeypatch)
    monkeypatch.setattr(
        runtime.iron_fly_bot, "reentry_blocked",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("feed gone")),
    )
    decision = runtime.tick_bot(USER, BOT_IRON_FLY_SCALPER, IronFlyScalperConfig())

    assert decision.action == "idle"
    assert decision.reason_code == ReasonCode.REENTRY_GATE_CLOSED
