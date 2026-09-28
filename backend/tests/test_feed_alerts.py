"""Bot feed alerts: which feed is down decides what the user is told.

The load-bearing distinction is "paused" vs "unmonitored". A dead futures feed stops the
signal bots opening trades while every open bot position is still watched on option prices;
only a stale feed under an open live position makes it unmonitored. And the recovery rule:
down alerts at once, back only after the feed has held for the recovery window.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.domain.bots import BotCycleRecord, BotRecord
from icici_breeze_backend.app.services import feed_alerts
from icici_breeze_backend.app.services.feed_alerts import RECOVERY_HOLD_SECONDS, FeedIncidents

USER = "VIKRAMMH"
OPEN = datetime(2026, 9, 28, 9, 15, tzinfo=IST).timestamp()
NOW = OPEN + 3 * 3600


# ------------------------------------------------------------------ the incident rule


def test_down_is_announced_once_per_incident():
    inc = FeedIncidents(hold_seconds=300)
    assert inc.update("k", down=True, now=0) == "down"
    assert inc.update("k", down=True, now=15) is None


def test_a_flap_inside_the_hold_is_the_same_incident():
    inc = FeedIncidents(hold_seconds=300)
    inc.update("k", down=True, now=0)
    assert inc.update("k", down=False, now=60) is None
    assert inc.update("k", down=True, now=120) is None, "not a new down: never told it was back"
    assert inc.update("k", down=False, now=180) is None
    assert inc.update("k", down=False, now=479) is None, "hold restarted at 180"
    assert inc.update("k", down=False, now=480) == "back"


def test_down_right_after_back_is_announced_with_no_cooldown():
    inc = FeedIncidents(hold_seconds=300)
    inc.update("k", down=True, now=0)
    inc.update("k", down=False, now=10)
    assert inc.update("k", down=False, now=310) == "back"
    assert inc.update("k", down=True, now=312) == "down"


def test_healthy_with_no_incident_says_nothing():
    inc = FeedIncidents(hold_seconds=300)
    assert inc.update("k", down=False, now=0) is None
    assert inc.update("k", down=False, now=10_000) is None


# ------------------------------------------------------------------ the bot pass


class _Feed:
    def __init__(self, last):
        self.last_tick_at = last


@pytest.fixture
def world(monkeypatch):
    """Every feed healthy, no bots, no positions; tests change what they need."""
    import icici_breeze_backend.app.db.redis_client as rc
    import icici_breeze_backend.app.repositories.bots as repo
    import icici_breeze_backend.app.services.portfolio_pnl_engine as engine
    import icici_breeze_backend.app.services.telegram_alerts as tg
    from icici_breeze_backend.app.services.bots.scalping import futures_feed
    from icici_breeze_backend.app.services.reference_data.keys import index_spot_key

    feed_alerts.reset_state_for_tests()
    # Freshness is an age in seconds at the current pass's clock; None means never seen.
    w = {
        "now": NOW,
        "futures": {"nifty": 1.0, "sensex": 1.0},
        "spot": {"nifty": 1.0, "sensex": 1.0},
        "quote_age": 1.0,
        "bots": [],
        "cycles": [],
        "sent": [],
    }

    def _ts(age):
        return None if age is None else w["now"] - age

    monkeypatch.setattr(
        futures_feed, "get_feed", lambda label="nifty": _Feed(_ts(w["futures"][label]))
    )
    spot_keys = {index_spot_key(label): label for label in ("nifty", "sensex")}

    def _cache(key):
        label = spot_keys.get(key)
        ts = _ts(w["spot"].get(label)) if label else None
        return {"ltp": 1.0, "updated_at": ts} if ts is not None else None

    monkeypatch.setattr(rc, "cache_get_json", _cache)
    monkeypatch.setattr(
        engine,
        "_fetch_quotes",
        lambda keys: {k: {"ltp": "1.0", "timestamp": str(_ts(w["quote_age"]))} for k in keys},
    )
    monkeypatch.setattr(repo, "list_enabled_bots_by_user", lambda: {USER: w["bots"]} if w["bots"] else {})
    monkeypatch.setattr(
        repo,
        "bots_with_open_live_cycles",
        lambda: sorted({(USER, c.bot_type) for c in w["cycles"] if not c.paper}),
    )
    monkeypatch.setattr(
        repo, "open_cycles", lambda uid, bt: [c for c in w["cycles"] if c.bot_type == bt]
    )
    monkeypatch.setattr(tg, "notify_bot_feed_down", lambda uid, **kw: w["sent"].append(("down", kw)))
    monkeypatch.setattr(tg, "notify_bot_feed_back", lambda uid, **kw: w["sent"].append(("back", kw)))
    yield w
    feed_alerts.reset_state_for_tests()


def _bot(bot_type: str, config: dict | None = None) -> BotRecord:
    return BotRecord(id=bot_type, bot_type=bot_type, enabled=True, config=config or {})


def _cycle(bot_type: str, *, paper: bool = False) -> BotCycleRecord:
    return BotCycleRecord(
        id="c1",
        run_id="r1",
        bot_type=bot_type,
        cycle_no=1,
        structure="iron_fly",
        opened_at="2026-09-28 10:00:00",
        paper=paper,
        legs=[
            {
                "stock_code": "NIFTY",
                "exchange_code": "NFO",
                "right": "call",
                "strike_price": 25000,
                "expiry_display": "30-Sep-2026",
                "action": "sell",
            }
        ],
    )


def _pass(world: dict, at: float = NOW) -> None:
    world["now"] = at
    feed_alerts.check_bot_feeds(at, OPEN)


def test_dead_futures_pauses_signal_bots_but_does_not_claim_positions_unmonitored(world):
    world["bots"] = [_bot("momentum_long_scalper")]
    world["cycles"] = [_cycle("iron_fly_scalper")]
    world["futures"]["nifty"] = feed_alerts.FUTURES_DOWN_SECONDS
    _pass(world)
    assert world["sent"] == [
        ("down", {"paused": ["Long Scalper"], "unmonitored": [], "hold_minutes": 5})
    ]


def test_a_short_futures_gap_sends_nothing(world):
    world["bots"] = [_bot("momentum_long_scalper")]
    world["futures"]["nifty"] = feed_alerts.FUTURES_DOWN_SECONDS - 5
    _pass(world)
    assert world["sent"] == []


def test_a_bot_that_does_not_read_the_dead_feed_is_not_paused(world):
    world["bots"] = [_bot("holdings_writer"), _bot("cas_bingo", {"strategy": "strangle"})]
    world["futures"]["nifty"] = world["futures"]["sensex"] = 600.0
    _pass(world)
    assert world["sent"] == [], "the strangle and the holdings writer never read the signal"


def test_dead_index_spot_pauses_the_expiry_writer_for_its_enabled_index(world):
    world["bots"] = [_bot("expiry_index_writer", {"indices": {"NIFTY": {"enabled": True}}})]
    world["spot"]["sensex"] = None
    _pass(world, NOW)
    assert world["sent"] == [], "only SENSEX spot is down and the writer trades NIFTY only"
    world["spot"]["nifty"] = None  # cache key expired; last seen at NOW - 1
    _pass(world, NOW + feed_alerts.SPOT_DOWN_SECONDS - 5)
    assert world["sent"] == []
    _pass(world, NOW + feed_alerts.SPOT_DOWN_SECONDS)
    assert world["sent"][0][1]["paused"] == ["Expiry-Day Index Writer"]


def test_stale_quotes_under_a_live_position_make_it_unmonitored(world):
    world["cycles"] = [_cycle("iron_fly_scalper")]
    world["quote_age"] = feed_alerts.POSITION_QUOTE_DOWN_SECONDS
    _pass(world)
    assert world["sent"] == [
        (
            "down",
            {
                "paused": ["Intraday Iron Fly"],
                "unmonitored": ["Intraday Iron Fly"],
                "hold_minutes": 5,
            },
        )
    ]


def test_dead_nifty_spot_leaves_an_open_iron_fly_unmonitored(world):
    """Its drift stop reads spot, not option prices."""
    world["cycles"] = [_cycle("iron_fly_scalper")]
    world["spot"]["nifty"] = None
    _pass(world, OPEN + feed_alerts.SPOT_DOWN_SECONDS)
    assert world["sent"][0][1]["unmonitored"] == ["Intraday Iron Fly"]


def test_a_paper_position_is_not_reported(world):
    world["cycles"] = [_cycle("iron_fly_scalper", paper=True)]
    world["quote_age"] = 600.0
    _pass(world)
    assert world["sent"] == []


def test_yesterdays_quote_counts_only_from_the_open(world):
    """At 09:15 every quote is yesterday's; that is not an outage yet."""
    world["cycles"] = [_cycle("iron_fly_scalper")]
    world["cycles"][0].opened_at = "2026-09-25 10:00:00"
    world["quote_age"] = 18 * 3600.0
    _pass(world, OPEN + 30)
    assert world["sent"] == []
    world["quote_age"] = 18 * 3600.0 + feed_alerts.POSITION_QUOTE_DOWN_SECONDS - 30
    _pass(world, OPEN + feed_alerts.POSITION_QUOTE_DOWN_SECONDS)
    assert [k for k, _ in world["sent"]] == ["down"]


def test_recovery_waits_for_the_hold_and_a_redrop_alerts_at_once(world):
    world["bots"] = [_bot("momentum_long_scalper")]
    world["futures"]["nifty"] = 600.0
    _pass(world, NOW)
    world["futures"]["nifty"] = 1.0
    _pass(world, NOW + 15)
    _pass(world, NOW + 15 + RECOVERY_HOLD_SECONDS - 1)
    assert [k for k, _ in world["sent"]] == ["down"]
    _pass(world, NOW + 15 + RECOVERY_HOLD_SECONDS)
    assert world["sent"][-1] == (
        "back",
        {
            "entries_back": True,
            "positions_back": False,
            "still_paused": False,
            "still_unmonitored": False,
            "hold_minutes": 5,
        },
    )
    world["futures"]["nifty"] = feed_alerts.FUTURES_DOWN_SECONDS
    _pass(world, NOW + 15 + RECOVERY_HOLD_SECONDS + 5)
    assert [k for k, _ in world["sent"]] == ["down", "back", "down"]


def test_a_flapping_feed_sends_one_down_and_no_back(world):
    world["bots"] = [_bot("momentum_long_scalper")]
    t = NOW
    for _ in range(6):
        world["futures"]["nifty"] = 600.0
        _pass(world, t)
        world["futures"]["nifty"] = 1.0
        _pass(world, t + 60)
        t += 240
    assert [k for k, _ in world["sent"]] == ["down"]


def test_entries_going_down_during_a_positions_incident_adds_no_message(world):
    """The positions message already said the bots are paused; an "open positions are still
    monitored" follow-up would contradict it."""
    world["bots"] = [_bot("momentum_long_scalper")]
    world["cycles"] = [_cycle("iron_fly_scalper")]
    world["quote_age"] = 600.0
    _pass(world, NOW)
    world["futures"]["nifty"] = 600.0
    _pass(world, NOW + 15)
    assert [k for k, _ in world["sent"]] == ["down"]
    assert world["sent"][0][1]["unmonitored"] == ["Intraday Iron Fly"]


def test_a_position_closed_mid_outage_is_dropped_silently(world):
    world["cycles"] = [_cycle("iron_fly_scalper")]
    world["quote_age"] = 600.0
    _pass(world, NOW)
    world["cycles"] = []
    _pass(world, NOW + 15 + RECOVERY_HOLD_SECONDS)
    assert [k for k, _ in world["sent"]] == ["down"]
    assert feed_alerts._incidents.keys() == []


def test_the_close_ends_every_incident_without_a_back_message(world, monkeypatch):
    import icici_breeze_backend.app.services.market_calendar as mc

    world["bots"] = [_bot("momentum_long_scalper")]
    world["futures"]["nifty"] = 600.0
    _pass(world)
    monkeypatch.setattr(mc, "is_market_open", lambda *a, **k: False)
    feed_alerts.bot_feed_alert_tick(datetime(2026, 9, 28, 15, 31, tzinfo=IST))
    assert feed_alerts._incidents.keys() == []
    assert [k for k, _ in world["sent"]] == ["down"]


def test_no_broker_session_means_no_feed_alerts(world, monkeypatch):
    import icici_breeze_backend.app.services.breeze_websocket_manager as ws
    import icici_breeze_backend.app.services.market_calendar as mc

    world["bots"] = [_bot("momentum_long_scalper")]
    world["futures"]["nifty"] = None
    monkeypatch.setattr(mc, "is_market_open", lambda *a, **k: True)
    monkeypatch.setattr(ws, "current_ws_user_id", lambda: None)
    feed_alerts.bot_feed_alert_tick(datetime(2026, 9, 28, 12, 0, tzinfo=IST))
    assert world["sent"] == []


# ------------------------------------------------------------------ the words


def test_the_two_down_messages_say_different_things():
    import icici_breeze_backend.app.services.telegram_alerts as tg

    unmonitored = tg._format_bot_feed_down_message(
        ["Intraday Iron Fly"], ["Intraday Iron Fly"], hold_minutes=5
    )
    assert (
        "Feed from ICICI is stopped and therefore the bots have been paused and any open "
        "positions created by the bots are currently unmonitored." in unmonitored
    )
    paused = tg._format_bot_feed_down_message(["Long Scalper"], [], hold_minutes=5)
    assert "these bots have been paused: Long Scalper" in paused
    assert "Their open positions are still monitored." in paused
    assert "unmonitored" not in paused


def test_back_message_says_what_is_still_down():
    import icici_breeze_backend.app.services.telegram_alerts as tg

    text = tg._format_bot_feed_back_message(
        entries_back=True,
        positions_back=False,
        still_paused=False,
        still_unmonitored=True,
        hold_minutes=5,
    )
    assert "worked for 5 min" in text and "still unmonitored" in text
