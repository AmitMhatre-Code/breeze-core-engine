"""The premium gate in the scalpers' gate stack (docs/premium-gate-plan.md section 3).

The Iron Fly sells only rich premium; the Long Scalper buys only cheap premium, and only looks
once a call has fired. Neither trades without a reading.
"""
from __future__ import annotations

import datetime

import pytest

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.db.bots_migrate import BOT_IRON_FLY_SCALPER, BOT_MOMENTUM_LONG_SCALPER
from icici_breeze_backend.app.domain.bots import (
    IronFlyScalperConfig,
    MomentumLongScalperConfig,
    PremiumGateConfig,
    ReasonCode,
    ScalperDayTotals,
)
from icici_breeze_backend.app.services.bots.scalping import runtime
from icici_breeze_backend.app.services.bots.scalping.signal import SignalResult
from icici_breeze_backend.app.services.premium_gate import reading as premium

NOON = datetime.datetime(2026, 10, 8, 12, 0, tzinfo=IST)
TEN = datetime.datetime(2026, 10, 8, 10, 0, tzinfo=IST)
GATE = PremiumGateConfig(enabled=True, threshold=1.0)


@pytest.fixture
def reading(monkeypatch):
    """Stub the reading; records how often it was asked for."""
    asked = []

    def install(ratio):
        def fake(bot_type, proc, user_id, now):
            asked.append(bot_type)
            if ratio is None:
                return premium.Reading(None, None, None, premium.REASON_NO_SPOT)
            return premium.Reading(ratio, 1.0, 1.0 / ratio)

        monkeypatch.setattr(runtime, "_scalper_premium_reading", fake)
        return asked

    return install


def _fly(**kw):
    return IronFlyScalperConfig(premium_gate=GATE, **kw)


def test_the_fly_sells_only_rich_premium(reading):
    reading(0.8)
    hold = runtime._premium_hold(BOT_IRON_FLY_SCALPER, _fly(), NOON, object(), "u1")
    assert hold[0] == ReasonCode.PREMIUM_NOT_RICH and "0.80x" in hold[1]
    reading(1.3)
    assert runtime._premium_hold(BOT_IRON_FLY_SCALPER, _fly(), NOON, object(), "u1") is None


def test_the_scalper_buys_only_cheap_premium(reading):
    config = MomentumLongScalperConfig(premium_gate=GATE)
    reading(1.3)
    hold = runtime._premium_hold(BOT_MOMENTUM_LONG_SCALPER, config, TEN, object(), "u1")
    assert hold[0] == ReasonCode.PREMIUM_NOT_CHEAP
    reading(0.8)
    assert runtime._premium_hold(BOT_MOMENTUM_LONG_SCALPER, config, TEN, object(), "u1") is None


def test_no_reading_holds_both(reading):
    reading(None)
    assert runtime._premium_hold(BOT_IRON_FLY_SCALPER, _fly(), NOON, object(), "u1")[0] == \
        ReasonCode.PREMIUM_UNREADABLE
    config = MomentumLongScalperConfig(premium_gate=GATE)
    assert runtime._premium_hold(BOT_MOMENTUM_LONG_SCALPER, config, TEN, object(), "u1")[0] == \
        ReasonCode.PREMIUM_UNREADABLE


def test_a_reading_that_raises_holds_rather_than_waves_through(monkeypatch):
    def boom(*a):
        raise RuntimeError("chain gone")

    monkeypatch.setattr(runtime, "_scalper_premium_reading", boom)
    hold = runtime._premium_hold(BOT_IRON_FLY_SCALPER, _fly(), NOON, object(), "u1")
    assert hold[0] == ReasonCode.PREMIUM_UNREADABLE


def test_an_off_gate_and_a_closed_window_never_read(reading):
    asked = reading(0.1)
    assert runtime._premium_hold(BOT_IRON_FLY_SCALPER, IronFlyScalperConfig(), NOON, object(), "u1") is None
    # 10:00 is outside the fly's default 11:30-13:30 window.
    assert runtime._premium_hold(BOT_IRON_FLY_SCALPER, _fly(), TEN, object(), "u1") is None
    assert asked == []


def test_the_scalper_reads_premium_only_once_a_call_has_fired(reading):
    asked = reading(1.5)
    config = MomentumLongScalperConfig(premium_gate=GATE)
    quiet = SignalResult(None, "no call", {})
    assert runtime._entry_hold(BOT_MOMENTUM_LONG_SCALPER, config, TEN, ScalperDayTotals(),
                               has_open_position=False, proc=object(), user_id="u1",
                               signal=quiet) is None
    assert asked == []
    fired = SignalResult("bullish", "fired", {})
    hold = runtime._entry_hold(BOT_MOMENTUM_LONG_SCALPER, config, TEN, ScalperDayTotals(),
                               has_open_position=False, proc=object(), user_id="u1", signal=fired)
    assert hold[0] == ReasonCode.PREMIUM_NOT_CHEAP and asked == [BOT_MOMENTUM_LONG_SCALPER]
