"""The live side of the signal grid: ticks to bars to engines to Redis, and back after a restart.

docs/signals-streamline-plan.md section 3. Nothing about a reading is stored; today's bars are,
until midnight, so a restart rebuilds the day exactly.
"""
from __future__ import annotations

import datetime

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
from icici_breeze_backend.app.services.index_signal import bars as bars_mod
from icici_breeze_backend.app.services.index_signal import publisher, reader, warmup
from icici_breeze_backend.app.services.index_signal.mechanisms import SeriesKey, all_keys
from icici_breeze_backend.app.services.index_signal.series import replay_series

DAY = datetime.date(2026, 7, 15)  # a Wednesday far from any live session's keys


def at(day: datetime.date, hh: int, mm: int, ss: int = 0) -> float:
    return datetime.datetime.combine(day, datetime.time(hh, mm, ss), IST).timestamp()


def _feed_minutes(index: str, day: datetime.date, minutes: int, start=(9, 15)) -> None:
    observe = publisher._observer(index)  # noqa: SLF001 -- the socket-thread entry point
    t0 = at(day, *start)
    for m in range(minutes):
        for k in range(3):
            observe({"last": 25_000.0 + m + k, "ttq": 1000.0 * (m * 3 + k + 1), "OI": 1.8e7 + m}, t0 + 60 * m + 10 * k)


def test_ticks_become_bars_that_every_series_of_the_index_reads():
    _feed_minutes("nifty", DAY, 10)
    now = at(DAY, 9, 25, 5)
    publisher.flush(now)
    todays = publisher._today_bars["nifty"]  # noqa: SLF001
    assert len(todays) == 10 and todays[0].volume is None and todays[1].volume == 3000.0
    out = publisher.publish_once(now=now, interval=2.0, session_open=True)
    assert set(out) == {k.id for k in all_keys()}
    # Ten minutes cannot warm any series: a reading exists, and it says why it is not a call.
    payload = reader.get_signal("nifty:momentum:1m", now=now)
    assert payload["state"] == "unavailable" and payload["reason"] == "warming_up"
    assert out["sensex:momentum:1m"]["reason"] == "no_bars"  # no SENSEX ticks at all


def test_a_stopped_publisher_reads_stale_not_as_its_last_verdict():
    _feed_minutes("nifty", DAY, 3)
    now = at(DAY, 9, 18, 5)
    publisher.flush(now)
    publisher.publish_once(now=now, interval=2.0, session_open=True)
    assert reader.get_signal("nifty:expansion:15m", now=now + 60)["reason"] == "stale"


def test_the_navbar_shows_the_chosen_mechanisms_fifteen_minute_reading():
    from icici_breeze_backend.app.services.index_signal import settings

    _feed_minutes("nifty", DAY, 3)
    now = at(DAY, 9, 18, 5)
    publisher.flush(now)
    publisher.publish_once(now=now, interval=2.0, session_open=True)
    assert reader.navbar_view(now=now)["nifty"]["mechanism"] == "expansion"
    settings.set_navbar_mechanism("momentum")
    view = reader.navbar_view(now=now)
    assert view["nifty"]["mechanism"] == "momentum" and view["nifty"]["duration_minutes"] == 15


def test_todays_bars_survive_a_restart(tmp_path):
    _feed_minutes("nifty", DAY, 12)
    now = at(DAY, 9, 27, 5)
    publisher.flush(now)
    publisher._persist_dirty(now)  # noqa: SLF001
    before = list(publisher._today_bars["nifty"])  # noqa: SLF001

    publisher.reset_state_for_tests()  # the restart
    assert publisher.load_today_bars("nifty", DAY) == before
    cache = str(tmp_path / "cache.sqlite3")
    assert publisher.rebuild("nifty", DAY, cache_path=cache) == len(before)
    snap = publisher._engines[SeriesKey("expansion", 1, "nifty")].snapshot(now)  # noqa: SLF001
    assert snap["bar_ts"] == before[-1].ts


def _store_session(cache: str, day: datetime.date, base: float) -> None:
    rows = []
    start = datetime.datetime.combine(day, datetime.time(9, 0))
    for m in range(390):
        ts = start + datetime.timedelta(minutes=m)
        c = base + 8 * ((m // 17) % 5) - 3 * ((m // 7) % 3)
        rows.append({"datetime": ts.strftime("%Y-%m-%d %H:%M:%S"), "open": c, "high": c + 2, "low": c - 2,
                     "close": c, "volume": 1000 + (m * 41) % 900, "open_interest": 18_000_000 + 700 * m})
    store.store_candles(rows, stock_code="NIFTY", path=cache)


def test_todays_series_is_the_replay_of_the_warm_up_and_todays_bars(tmp_path):
    """CAS Bingo reads the day's history this way; it must equal what a backtest of today reads."""
    cache = str(tmp_path / "cache.sqlite3")
    store.ensure_tables(cache)
    for k, d in enumerate(warmup.previous_sessions(DAY)):
        _store_session(cache, d, 25_000.0 + 30 * k)
    _feed_minutes("nifty", DAY, 40)
    now = at(DAY, 9, 55, 5)
    publisher.flush(now)

    key = SeriesKey("momentum", 5, "nifty")
    got = publisher.today_series(key, now=now, cache_path=cache)
    history = warmup.cached_bars("nifty", DAY, cache_path=cache)
    todays = publisher._today_bars["nifty"]  # noqa: SLF001
    expected = [(b, s) for b, s in replay_series(history + todays, key)
                if bars_mod.trading_date(b.ts) == DAY]
    assert [s["state"] for _b, s in got] == [s["state"] for _b, s in expected]
    assert len(got) == 40
    # Cached until a new bar arrives: the same answer, without replaying again.
    assert publisher.today_series(key, now=now, cache_path=cache) == got


# --- keeping the futures feeds alive (2026-09-24) ---------------------------------------


class _Feed:
    stock_code, exchange = "NIFTY", "NFO"

    def __init__(self, subscribed: bool, quiet):
        self.subscribed_today = subscribed
        self.quiet = quiet
        self.ensures = 0
        self.invalidated: list[str] = []

    def set_quote_observer(self, observer):
        pass

    def quiet_seconds(self, now):
        return self.quiet if self.subscribed_today else None

    def invalidate_subscription(self, reason):
        self.invalidated.append(reason)
        self.subscribed_today = False

    def ensure_subscribed(self, proc, user_id, expiries):
        self.ensures += 1
        self.subscribed_today, self.quiet = True, 0.0
        return True


def _serviced(monkeypatch, feeds: dict):
    from icici_breeze_backend.app.services.bots.scalping import futures_feed, momentum_bot

    monkeypatch.setattr(futures_feed, "get_feed", lambda index="nifty": feeds[index])
    monkeypatch.setattr(
        futures_feed, "invalidate_all",
        lambda reason: [f.invalidate_subscription(reason) for f in feeds.values()],
    )
    monkeypatch.setattr(momentum_bot, "option_expiries", lambda *a, **k: [])
    monkeypatch.setattr("icici_breeze_backend.app.services.processor.processor", lambda: object())
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.market_calendar.is_market_open", lambda now=None: True
    )
    monkeypatch.setattr(publisher, "_feed_owner", lambda: "u1")
    monkeypatch.setattr(publisher, "_last_feed_attempt", {})


def test_a_ticking_feed_is_left_alone(monkeypatch):
    feeds = {"nifty": _Feed(True, 2.0), "sensex": _Feed(True, 5.0)}
    _serviced(monkeypatch, feeds)
    publisher.service_feeds(1_000.0)
    assert [f.ensures for f in feeds.values()] == [0, 0]


def test_a_subscribed_but_silent_feed_is_resubscribed(monkeypatch):
    """The latch said subscribed while NIFTY sat dead from 13:34 to the close."""
    feeds = {"nifty": _Feed(True, publisher.FEED_QUIET_RESUBSCRIBE_SECONDS + 5), "sensex": _Feed(True, 1.0)}
    _serviced(monkeypatch, feeds)
    publisher.service_feeds(1_000.0)
    assert feeds["nifty"].ensures == 1 and feeds["nifty"].invalidated
    assert feeds["sensex"].ensures == 0


def test_resubscribes_are_paced(monkeypatch):
    feeds = {"nifty": _Feed(True, 600.0), "sensex": _Feed(True, 1.0)}
    _serviced(monkeypatch, feeds)
    publisher._last_feed_attempt["nifty"] = 995.0  # noqa: SLF001 -- attempted 5s ago
    publisher.service_feeds(1_000.0)
    assert feeds["nifty"].ensures == 0 and feeds["nifty"].subscribed_today is False
    publisher.service_feeds(1_000.0 + publisher.FEED_RETRY_SECONDS)
    assert feeds["nifty"].ensures == 1


def test_force_resubscribes_every_feed_at_once(monkeypatch):
    """The socket-rebuild path: every room is gone, and waiting out the pacing or the quiet
    threshold would leave the signal blind for a minute it does not need to be."""
    feeds = {"nifty": _Feed(True, 0.5), "sensex": _Feed(True, 0.5)}
    _serviced(monkeypatch, feeds)
    publisher._last_feed_attempt.update({"nifty": 999.0, "sensex": 999.0})  # noqa: SLF001
    publisher.service_feeds(1_000.0, force=True)
    assert [f.ensures for f in feeds.values()] == [1, 1]
