"""Live signals: twelve series built from futures ticks, published as the current reading only.

docs/signals-streamline-plan.md section 3. Per index, one futures feed (NIFTY on NFO, BSESEN on
BFO) supplies ticks; one `LiveBarBuilder` turns them into one-minute bars; six `SeriesEngine`s
(two mechanisms x three durations) read those bars. Every publish interval each engine's snapshot
goes to Redis under `signal:series:<id>` with a `valid_until`, and that is the only thing the
navbar, the Signals page and the bots read (through `reader`).

No reading is ever stored (decision 4). The live session's audit trail is a backtest of the day
once ICICI serves its bars. What IS kept is today's bars -- inputs, not signals -- in Redis until
midnight, so a restart at 13:00 rebuilds every engine's day exactly rather than starting cold.

Each trading day the engines are rebuilt from the history cache's previous sessions plus today's
bars (`warmup`), so live starts from the very bars a replay of today would start from.

The loop always runs; outside market hours every series publishes `unavailable` with its reason.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import threading
import time
from dataclasses import asdict
from typing import Any, Optional

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.db.redis_client import cache_get_json, cache_set_json
from icici_breeze_backend.app.services.index_signal import bars as bars_mod
from icici_breeze_backend.app.services.index_signal import warmup
from icici_breeze_backend.app.services.index_signal.bars import Bar, LiveBarBuilder
from icici_breeze_backend.app.services.index_signal.mechanisms import (
    INDICES,
    SeriesKey,
    all_keys,
)
from icici_breeze_backend.app.services.index_signal.series import SeriesEngine
from icici_breeze_backend.app.services.reference_data.keys import signal_bars_key, signal_series_key

_logger = logging.getLogger(__name__)

# A published payload stays valid for this many publish intervals (with a floor), so one slow
# pass does not blank a reading but a stopped loop does within seconds.
_VALIDITY_MULTIPLE = 3.0
_MIN_VALIDITY_SECONDS = 10.0
FEED_RETRY_SECONDS = 30.0
# A subscribed future that has not ticked for this long in market hours is treated as dropped
# and re-subscribed. Well inside `series.STALE_SECONDS` (180s), so a recovered feed is back
# before its readings go stale; a genuinely thin minute costs one idempotent subscribe.
FEED_QUIET_RESUBSCRIBE_SECONDS = 60.0
# A feed still silent this long after its last tick, re-subscribes notwithstanding, is an outage
# worth a Telegram message: it is `series.STALE_SECONDS`, the point every reading on that index
# goes `unavailable` and the bots stop trading. Shorter gaps heal on a re-subscribe unseen.
FEED_OUTAGE_ALERT_SECONDS = 180.0
# A warm-up fetch that found nothing to do, or failed, is retried at most this often.
WARMUP_RETRY_SECONDS = 600.0
WARMUP_MAX_ATTEMPTS = 3

_lock = threading.RLock()
_builders: dict[str, LiveBarBuilder] = {}
_engines: dict[SeriesKey, SeriesEngine] = {}
_today_bars: dict[str, list[Bar]] = {}
_bars_day: dict[str, datetime.date] = {}
_dirty: set[str] = set()
_built_for: dict[str, datetime.date] = {}
_warmup: dict[str, dict[str, Any]] = {}
_last_feed_attempt: dict[str, float] = {}
# index -> wall time the current futures outage began (the future's last tick), and whether the
# user has been told about it. One message per outage going down and one coming back.
_feed_outage: dict[str, float] = {}
_feed_outage_alerted: set[str] = set()
# series id -> (inputs it was computed from, today's replayed readings), see `today_series`.
_today_memo: dict[str, tuple[Any, list[tuple[Bar, dict[str, Any]]]]] = {}


# --------------------------------------------------------------------------------------
# Settings read each pass
# --------------------------------------------------------------------------------------


def _publish_interval_seconds() -> float:
    """The P&L recompute interval (Settings -> Advanced), the clock everything live runs on."""
    try:
        from icici_breeze_backend.app.services.pnl_engine_settings import load_pnl_engine_settings

        return max(1.0, min(30.0, float(load_pnl_engine_settings()["pnl_recompute_interval_seconds"])))
    except Exception:  # noqa: BLE001
        return 2.0


def _validity_seconds(interval: float) -> float:
    return max(_MIN_VALIDITY_SECONDS, interval * _VALIDITY_MULTIPLE)


def _keys(index: str) -> list[SeriesKey]:
    return [k for k in all_keys() if k.index == index]


def _builder(index: str) -> LiveBarBuilder:
    b = _builders.get(index)
    if b is None:
        b = _builders[index] = LiveBarBuilder()
    return b


def _engine(key: SeriesKey) -> SeriesEngine:
    eng = _engines.get(key)
    if eng is None:
        eng = _engines[key] = SeriesEngine(key)
    return eng


# --------------------------------------------------------------------------------------
# Bars in
# --------------------------------------------------------------------------------------


def _accept(index: str, bar: Bar) -> None:
    """One completed bar: today's list, then every engine of the index. Caller holds `_lock`."""
    day = bars_mod.trading_date(bar.ts)
    if _bars_day.get(index) != day:
        _today_bars[index] = []
        _bars_day[index] = day
    todays = _today_bars[index]
    if todays and bar.ts <= todays[-1].ts:
        return
    todays.append(bar)
    _dirty.add(index)
    for key in _keys(index):
        _engine(key).on_bar(bar)


def _observer(index: str):
    def on_quote(payload: Any, ts: float) -> None:
        """Runs on the socket thread for every tick: never raises, never does I/O."""
        try:
            with _lock:
                for bar in _builder(index).ingest(ts, payload):
                    _accept(index, bar)
        except Exception:  # noqa: BLE001
            _logger.debug("signals: tick handling failed for %s", index, exc_info=True)

    return on_quote


def flush(now: float) -> None:
    """Close bars the clock has left, including flat bars for a quiet contract's minutes."""
    with _lock:
        for index in INDICES:
            for bar in _builder(index).flush(now):
                _accept(index, bar)


# --------------------------------------------------------------------------------------
# Today's bars, kept until midnight for restarts
# --------------------------------------------------------------------------------------


def _seconds_to_midnight(now: float) -> int:
    t = datetime.datetime.fromtimestamp(now, IST)
    midnight = datetime.datetime.combine(t.date() + datetime.timedelta(days=1), datetime.time(), IST)
    return max(60, int(midnight.timestamp() - now) + 60)


def _persist_dirty(now: float) -> None:
    with _lock:
        pending = {i: list(_today_bars.get(i) or []) for i in _dirty}
        days = {i: _bars_day.get(i) for i in _dirty}
        _dirty.clear()
    for index, todays in pending.items():
        day = days.get(index)
        if day is None:
            continue
        try:
            cache_set_json(
                signal_bars_key(index, day.isoformat()),
                [asdict(b) for b in todays],
                ex=_seconds_to_midnight(now),
            )
        except Exception:  # noqa: BLE001 -- losing the copy only costs a colder restart
            _logger.debug("signals: could not persist today's %s bars", index, exc_info=True)


def load_today_bars(index: str, day: datetime.date) -> list[Bar]:
    raw = cache_get_json(signal_bars_key(index, day.isoformat()))
    out: list[Bar] = []
    for row in raw if isinstance(raw, list) else []:
        try:
            out.append(Bar(**row))
        except TypeError:
            continue
    return out


# --------------------------------------------------------------------------------------
# Daily rebuild and warm-up
# --------------------------------------------------------------------------------------


def rebuild(index: str, today: datetime.date, *, cache_path: Optional[str] = None) -> int:
    """Fresh engines for `index`, seeded from the cached sessions before today and today's bars.

    Seeding happens off the lock; bars that arrive meanwhile are replayed onto the new engines
    before they are installed, so nothing live is lost. Returns how many bars were seeded."""
    history = warmup.cached_bars(index, today, cache_path=cache_path)
    with _lock:
        in_memory = list(_today_bars.get(index) or []) if _bars_day.get(index) == today else []
    stored = load_today_bars(index, today)
    todays = sorted({b.ts: b for b in stored + in_memory}.values(), key=lambda b: b.ts)
    fresh = {key: SeriesEngine(key) for key in _keys(index)}
    for eng in fresh.values():
        eng.seed(history)
        eng.seed(todays)
    with _lock:
        latest = todays[-1].ts if todays else float("-inf")
        arrived = [b for b in (_today_bars.get(index) or []) if b.ts > latest]
        for eng in fresh.values():
            eng.seed(arrived)
        _engines.update(fresh)
        _today_bars[index] = todays + arrived
        _bars_day[index] = today
        _built_for[index] = today
    return len(history) + len(todays)


def today_series(
    key: SeriesKey, *, now: Optional[float] = None, cache_path: Optional[str] = None
) -> list[tuple[Bar, dict[str, Any]]]:
    """Today's readings of one series so far, one per session minute, recomputed from the bars.

    Nothing is stored about a reading, so a consumer that needs the day's history -- CAS Bingo
    looking back for the flip before its window -- replays it: the cached sessions before today
    to warm, then today's bars, through a fresh engine. It is the same computation the live
    engine did, so it gives the same answer, restart or not."""
    from icici_breeze_backend.app.services.index_signal.series import replay_series, rollover_days

    ts = time.time() if now is None else now
    today = bars_mod.trading_date(ts)
    with _lock:
        in_memory = list(_today_bars.get(key.index) or []) if _bars_day.get(key.index) == today else []
    todays = sorted({b.ts: b for b in load_today_bars(key.index, today) + in_memory}.values(),
                    key=lambda b: b.ts)
    memo_key = (key.id, today, len(todays), todays[-1].ts if todays else None, cache_path)
    with _lock:
        held = _today_memo.get(key.id)
    if held is not None and held[0] == memo_key:
        return [(b, s) for b, s in held[1] if b.close_ts <= ts]
    history = warmup.cached_bars(key.index, today, cache_path=cache_path)
    excluded = rollover_days(today, today) if key.uses_oi else set()
    rows = [
        (bar, snap)
        for bar, snap in replay_series(history + todays, key, excluded_days=excluded)
        if bars_mod.trading_date(bar.ts) == today
    ]
    with _lock:
        _today_memo[key.id] = (memo_key, rows)
    return [(b, s) for b, s in rows if b.close_ts <= ts]


def today_open(index: str, *, now: Optional[float] = None) -> Optional[float]:
    """The futures' first session bar's open today, for moves measured on the futures' own scale."""
    ts = time.time() if now is None else now
    today = bars_mod.trading_date(ts)
    with _lock:
        todays = list(_today_bars.get(index) or []) if _bars_day.get(index) == today else []
    if not todays:
        todays = load_today_bars(index, today)
    for bar in todays:
        if bars_mod.in_session(bar):
            return bar.open if bar.open is not None else bar.close
    return None


def _feed_owner() -> Optional[str]:
    try:
        from icici_breeze_backend.app.repositories.broker_session import list_users_with_session

        users = list_users_with_session()
        return users[0] if users else None
    except Exception:  # noqa: BLE001
        return None


def _maybe_warm(index: str, today: datetime.date, now_m: float) -> None:
    """Rebuild once per trading day; fetch the warm-up sessions if the cache lacks them."""
    if _built_for.get(index) != today:
        try:
            n = rebuild(index, today)
            _logger.info("signals: %s engines rebuilt for %s from %d bar(s)", index, today, n)
        except Exception:  # noqa: BLE001 -- a cold engine only delays readings
            _logger.warning("signals: rebuilding %s failed", index, exc_info=True)
            _built_for[index] = today
    state = _warmup.setdefault(index, {})
    if state.get("day") != today:
        state.clear()
        state.update(day=today, attempts=0, last_attempt=None, running=False, calls=0)
    if state["running"] or state["attempts"] >= WARMUP_MAX_ATTEMPTS:
        return
    if state["last_attempt"] is not None and now_m - state["last_attempt"] < WARMUP_RETRY_SECONDS:
        return
    try:
        missing = warmup.missing_sessions(index, today)
    except Exception:  # noqa: BLE001
        _logger.debug("signals: warm-up check failed for %s", index, exc_info=True)
        return
    state["missing"] = [d.isoformat() for d in missing]
    if not missing:
        return
    user_id = _feed_owner()
    if not user_id:
        return
    state["running"] = True
    state["attempts"] += 1
    state["last_attempt"] = now_m

    def run() -> None:
        try:
            state["calls"] += warmup.fetch_missing(index, missing, user_id)
            rebuild(index, today)
        except Exception:  # noqa: BLE001
            _logger.warning("signals: warm-up fetch for %s failed", index, exc_info=True)
        finally:
            state["running"] = False

    threading.Thread(target=run, name=f"signal-warmup-{index}", daemon=True).start()


# --------------------------------------------------------------------------------------
# Feeds
# --------------------------------------------------------------------------------------


def service_feeds(now: float, *, force: bool = False) -> None:
    """Keep both futures feeds subscribed during the session and our observer attached.

    `subscribed_today` alone is not proof of a live feed. A socket rebuilt by
    `breeze_websocket_manager.reconnect_ws` loses the futures rooms while the latch stays set,
    which on 2026-09-24 left the NIFTY future silent from 13:34 to the close with every
    signal reading `stale`. So a feed quiet for FEED_QUIET_RESUBSCRIBE_SECONDS in market hours
    is re-subscribed; `force` (the rebuild path) drops every latch and re-subscribes at once.
    """
    from icici_breeze_backend.app.services.bots.scalping import futures_feed
    from icici_breeze_backend.app.services.market_calendar import is_market_open

    if force:
        futures_feed.invalidate_all("WS socket rebuilt; re-subscribing.")
    for index in INDICES:
        feed = futures_feed.get_feed(index)
        feed.set_quote_observer(_observer(index))
    if not is_market_open():
        # The close ends any outage: tomorrow starts clean, and "back" is not news at 15:30.
        _feed_outage.clear()
        _feed_outage_alerted.clear()
        return
    user_id = _feed_owner()
    if not user_id:
        return
    for index in INDICES:
        feed = futures_feed.get_feed(index)
        _track_outage(index, feed, now, user_id)
        if feed.subscribed_today:
            quiet = feed.quiet_seconds(now)
            if quiet is None or quiet < FEED_QUIET_RESUBSCRIBE_SECONDS:
                continue
            _logger.warning(
                "signals: %s futures feed silent for %.0fs; re-subscribing", index, quiet
            )
            feed.invalidate_subscription(f"No futures ticks for {quiet:.0f}s; re-subscribing.")
            last = feed.last_tick_at
            _feed_outage.setdefault(index, last if last is not None else now - quiet)
        if not force and now - _last_feed_attempt.get(index, 0.0) < FEED_RETRY_SECONDS:
            continue
        _last_feed_attempt[index] = now
        try:
            from icici_breeze_backend.app.services.bots.scalping.momentum_bot import option_expiries
            from icici_breeze_backend.app.services.processor import processor

            proc = processor()
            expiries = option_expiries(proc, feed.stock_code, feed.exchange)
            feed.ensure_subscribed(proc, user_id, expiries)
        except Exception:  # noqa: BLE001 -- a dead feed must not stop publication
            _logger.warning("signals: %s futures subscribe failed", index, exc_info=True)


def _hhmm(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts, IST).strftime("%H:%M")


def _track_outage(index: str, feed: Any, now: float, user_id: str) -> None:
    """Tell the user when a re-subscribe could not bring a future back, and when it returns.

    Judged on the future's own last tick, not `quiet_seconds`: every re-subscribe restarts that
    clock, so a feed that "subscribes" fine every minute and never ticks would never look old.
    Never raises -- an alert must not cost the feed its re-subscribe.
    """
    started = _feed_outage.get(index)
    if started is None:
        return
    try:
        from icici_breeze_backend.app.services import telegram_alerts

        label = index.upper()
        last = feed.last_tick_at
        if last is not None and last > started:
            _feed_outage.pop(index, None)
            if index in _feed_outage_alerted:
                _feed_outage_alerted.discard(index)
                _logger.warning("signals: %s futures feed back after %.0fs", index, last - started)
                telegram_alerts.notify_futures_feed_restored(
                    user_id, label, _hhmm(last), max(1, round((last - started) / 60))
                )
            return
        if index not in _feed_outage_alerted and now - started >= FEED_OUTAGE_ALERT_SECONDS:
            _feed_outage_alerted.add(index)
            _logger.warning(
                "signals: %s futures feed silent for %.0fs after re-subscribing; alerting",
                index, now - started,
            )
            telegram_alerts.notify_futures_feed_down(
                user_id, label, _hhmm(started), int((now - started) // 60)
            )
    except Exception:  # noqa: BLE001
        _logger.warning("signals: %s futures outage alert failed", index, exc_info=True)


# --------------------------------------------------------------------------------------
# Publish
# --------------------------------------------------------------------------------------


def _in_rollover_window(ts: float) -> bool:
    """NIFTY near-month futures rolling: OI moves mechanically, so OI readings stand down (#34)."""
    try:
        from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime
        from icici_breeze_backend.app.services.index_signal.expansion import in_rollover_window

        today = bars_mod.trading_date(ts)
        return in_rollover_window(today, regime.near_month_futures_expiry(today, "NIFTY", set()))
    except Exception:  # noqa: BLE001
        return False


def publish_once(
    *, now: Optional[float] = None, interval: Optional[float] = None, session_open: Optional[bool] = None
) -> dict[str, dict[str, Any]]:
    ts = time.time() if now is None else now
    interval = _publish_interval_seconds() if interval is None else interval
    if session_open is None:
        from icici_breeze_backend.app.services.market_calendar import is_market_open

        session_open = is_market_open()
    valid_for = _validity_seconds(interval)
    excluded = _in_rollover_window(ts)
    out: dict[str, dict[str, Any]] = {}
    with _lock:
        engines = [(key, _engine(key)) for key in all_keys()]
    for key, eng in engines:
        payload = eng.snapshot(ts, session_open=session_open, excluded=excluded)
        payload["published_at"] = ts
        payload["valid_until"] = ts + valid_for
        payload["publish_interval_seconds"] = interval
        try:
            cache_set_json(signal_series_key(key.id), payload, ex=int(valid_for) + 60)
        except Exception:  # noqa: BLE001
            _logger.debug("signals: publish failed for %s", key.id, exc_info=True)
        out[key.id] = payload
    return out


def _loop_tick(interval: float) -> None:
    now = time.time()
    today = bars_mod.trading_date(now)
    now_m = time.monotonic()
    for index in INDICES:
        _maybe_warm(index, today, now_m)
    try:
        service_feeds(now)
    except Exception:  # noqa: BLE001
        _logger.debug("signals: feed servicing failed", exc_info=True)
    flush(now)
    _persist_dirty(now)
    publish_once(now=now, interval=interval)


async def run_index_signal_loop() -> None:
    """Cancelled only via the FastAPI lifespan; one bad pass never kills it."""
    _logger.info("Signal loop started (%d series).", len(all_keys()))
    while True:
        interval = _publish_interval_seconds()
        try:
            await asyncio.to_thread(_loop_tick, interval)
        except Exception:  # noqa: BLE001
            _logger.exception("signals: loop pass failed")
        await asyncio.sleep(interval)


def status() -> dict[str, Any]:
    """What the Signals page shows about the live side: feeds, today's bars, warm-up."""
    from icici_breeze_backend.app.services.bots.scalping import futures_feed

    out: dict[str, Any] = {}
    with _lock:
        for index in INDICES:
            feed = futures_feed.get_feed(index)
            todays = _today_bars.get(index) or []
            out[index] = {
                "contract": feed.contract.expiry_display if feed.contract else None,
                "subscribed_today": feed.subscribed_today,
                "last_tick_at": feed.last_tick_at,
                "quiet_seconds": feed.quiet_seconds(time.time()),
                "bars_today": len(todays),
                "last_bar_ts": todays[-1].ts if todays else None,
                "built_for": _built_for[index].isoformat() if index in _built_for else None,
                "warmup": {
                    k: (v.isoformat() if isinstance(v, datetime.date) else v)
                    for k, v in (_warmup.get(index) or {}).items()
                    if k != "last_attempt"
                },
            }
    return out


def reset_state_for_tests() -> None:
    with _lock:
        _builders.clear()
        _engines.clear()
        _today_bars.clear()
        _bars_day.clear()
        _dirty.clear()
        _built_for.clear()
        _warmup.clear()
        _last_feed_attempt.clear()
        _feed_outage.clear()
        _feed_outage_alerted.clear()
        _today_memo.clear()
