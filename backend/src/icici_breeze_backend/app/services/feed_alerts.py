"""Telling the user when an ICICI feed that bots depend on has stopped, and when it is back.

The rule every feed alert here follows (the PB/SL one in `squareoff_protection_guard` too):
  * **Down is never delayed.** Once an outage is detected the user is told, even seconds after
    a "back" message -- they may need to start watching positions by hand.
  * **Back is held.** "Back" goes out only after the feed has worked continuously for
    `RECOVERY_HOLD_SECONDS`. A feed that flaps inside that window is still the same outage, so
    a flapping feed costs one message pair, not dozens (the futures feed on 2026-09-28).

Feeds do fail one at a time -- the futures rooms died on 2026-09-24 while option chains kept
ticking -- so each is judged on its own ticks, never on the socket as a whole.

Which feed matters for what (verified per bot, 2026-09-28):
  * entries: NIFTY/SENSEX futures (signals: Long Scalper, Iron Fly, CAS spreads) and index spot
    (every index bot);
  * open positions: the option quotes of live Long Scalper / Iron Fly / CAS positions -- their
    exit loops hold without a stop, or mark against stand-in prices, once those go stale -- and
    NIFTY spot for an open Iron Fly (its drift stop). Expiry Writer positions are protected by
    PB/SL rules and alerted there; Holdings Writer positions have no automatic exit at all.
The order-notification feed is not watched: it has no heartbeat, so silence there is
indistinguishable from having no orders.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime
from typing import Any, Iterable, Optional

from icici_breeze_backend.app.core.timezone import IST

_logger = logging.getLogger(__name__)

RECOVERY_HOLD_SECONDS = 300.0

# `series.STALE_SECONDS`: the point every signal on that index reads `unavailable`.
FUTURES_DOWN_SECONDS = 180.0
# Past the price-feed watchdog's 45s re-subscribe, so only an outage it could not heal.
SPOT_DOWN_SECONDS = 60.0
# The scalpers' own socket-stale exit point; a position quote this old is not being judged.
POSITION_QUOTE_DOWN_SECONDS = 60.0
_INTERVAL_SECONDS = 15.0

_INDEX_LABEL = {"NIFTY": "nifty", "BSESEN": "sensex"}


class FeedIncidents:
    """Per-key outage state: alert on down at once, announce back only after a held recovery.

    `update` returns "down", "back" or None. A key goes "down" once per incident; while it
    is down, a brief recovery only restarts the hold, so it neither announces "back" nor
    re-announces "down". After "back" the next down is a new incident and alerts at once.
    """

    def __init__(self, hold_seconds: float = RECOVERY_HOLD_SECONDS) -> None:
        self._hold = hold_seconds
        self._down: dict[str, Optional[float]] = {}  # key -> healthy since (None: still down)

    def update(
        self, key: str, *, down: bool, now: float, healthy_since: Optional[float] = None
    ) -> Optional[str]:
        if down:
            fresh = key not in self._down
            self._down[key] = None
            return "down" if fresh else None
        if key not in self._down:
            return None
        since = healthy_since if healthy_since is not None else (self._down[key] or now)
        self._down[key] = since
        if now - since >= self._hold:
            del self._down[key]
            return "back"
        return None

    def is_down(self, key: str) -> bool:
        return key in self._down

    def forget(self, key: str) -> None:
        self._down.pop(key, None)

    def keys(self) -> list[str]:
        return list(self._down)

    def clear(self) -> None:
        self._down.clear()


_lock = threading.Lock()
_incidents = FeedIncidents()
# label -> last time index spot was seen live; the cache key itself expires after 15s
_spot_seen: dict[str, float] = {}


# --------------------------------------------------------------------------------------
# What is down
# --------------------------------------------------------------------------------------


def _futures_down(floor: float, now: float) -> dict[str, bool]:
    from icici_breeze_backend.app.services.bots.scalping import futures_feed

    out: dict[str, bool] = {}
    for label in _INDEX_LABEL.values():
        last = futures_feed.get_feed(label).last_tick_at
        out[label] = now - max(last or floor, floor) >= FUTURES_DOWN_SECONDS
    return out


def _spot_down(floor: float, now: float) -> dict[str, bool]:
    from icici_breeze_backend.app.db.redis_client import cache_get_json
    from icici_breeze_backend.app.services.reference_data.keys import index_spot_key

    out: dict[str, bool] = {}
    for label in _INDEX_LABEL.values():
        payload = cache_get_json(index_spot_key(label))
        if isinstance(payload, dict) and isinstance(payload.get("updated_at"), (int, float)):
            _spot_seen[label] = max(_spot_seen.get(label, 0.0), float(payload["updated_at"]))
        out[label] = now - max(_spot_seen.get(label, floor), floor) >= SPOT_DOWN_SECONDS
    return out


def _entry_feeds(bot: Any) -> set[tuple[str, str]]:
    """(kind, index label) feeds this enabled bot needs to open a trade."""
    from icici_breeze_backend.app.db.bots_migrate import (
        BOT_CAS_BINGO,
        BOT_EXPIRY_INDEX_WRITER,
        BOT_IRON_FLY_SCALPER,
        BOT_MOMENTUM_LONG_SCALPER,
    )

    config = bot.config or {}
    if bot.bot_type in (BOT_MOMENTUM_LONG_SCALPER, BOT_IRON_FLY_SCALPER):
        return {("futures", "nifty"), ("spot", "nifty")}
    if bot.bot_type == BOT_EXPIRY_INDEX_WRITER:
        indices = config.get("indices") or {}
        return {
            ("spot", _INDEX_LABEL[code])
            for code, leg in indices.items()
            if code in _INDEX_LABEL and isinstance(leg, dict) and leg.get("enabled")
        }
    if bot.bot_type == BOT_CAS_BINGO:
        indices = config.get("indices") or {code: {} for code in _INDEX_LABEL}
        labels = [
            _INDEX_LABEL[code]
            for code, idx in indices.items()
            if code in _INDEX_LABEL and (not isinstance(idx, dict) or idx.get("enabled", True))
        ]
        # The strangle buys at a fixed time; only the spreads read the signal.
        needs_signal = config.get("strategy", "debit_spread") != "strangle"
        feeds = {("spot", label) for label in labels}
        if needs_signal:
            feeds |= {("futures", label) for label in labels}
        return feeds
    return set()


def _leg_quote_ages(legs: list[dict[str, Any]], now: float) -> list[Optional[float]]:
    from icici_breeze_backend.app.core.strike import parse_strike
    from icici_breeze_backend.app.services.portfolio_pnl_engine import (
        _fetch_quotes,
        _parse_quote_fields,
    )
    from icici_breeze_backend.app.services.reference_data.scrip_index import contract_index_key
    from icici_breeze_backend.app.services.reference_data.scrip_master_sql import (
        normalize_expiry_display,
    )

    keys: list[str] = []
    for leg in legs:
        right = "call" if str(leg.get("right") or "").lower().startswith("c") else "put"
        keys.append(
            contract_index_key(
                str(leg.get("exchange_code") or ""),
                str(leg.get("stock_code") or ""),
                normalize_expiry_display(str(leg.get("expiry_display") or "")),
                parse_strike(leg.get("strike_price")),
                right,
            )
        )
    quotes = _fetch_quotes(keys)
    ages: list[Optional[float]] = []
    for key in keys:
        _ltp, ts = _parse_quote_fields(quotes.get(key))
        ages.append(None if ts is None else max(0.0, now - ts))
    return ages


def _live_cycles_by_user() -> dict[str, list[Any]]:
    from icici_breeze_backend.app.repositories import bots as repo

    out: dict[str, list[Any]] = {}
    for user_id, bot_type in repo.bots_with_open_live_cycles():
        live = [c for c in repo.open_cycles(user_id, bot_type) if not c.paper]
        if live:
            out.setdefault(user_id, []).extend(live)
    return out


def _unmonitored_bots(
    cycles: Iterable[Any], spot_down: dict[str, bool], floor: float, now: float
) -> set[str]:
    from icici_breeze_backend.app.db.bots_migrate import BOT_IRON_FLY_SCALPER

    out: set[str] = set()
    for cycle in cycles:
        if cycle.bot_type == BOT_IRON_FLY_SCALPER and spot_down.get("nifty"):
            out.add(cycle.bot_type)
            continue
        legs = list(cycle.legs or [])
        if not legs:
            continue
        opened = _parse_ist(cycle.opened_at)
        base = max(floor, opened) if opened else floor
        for age in _leg_quote_ages(legs, now):
            last = now - age if age is not None else base
            if now - max(last, base) >= POSITION_QUOTE_DOWN_SECONDS:
                out.add(cycle.bot_type)
                break
    return out


def _parse_ist(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.timestamp()


# --------------------------------------------------------------------------------------
# The pass
# --------------------------------------------------------------------------------------


def _bot_mode_tag(bot_type: str, by_type: dict[str, Any]) -> str:
    """"Simulation" for a paper/simulation-mode bot, "Live" otherwise -- including a bot with
    no paper axis at all (Expiry Writer), which always places real orders, and a bot_type not
    found here at all, which only happens for `unmonitored` and is live by construction
    (`_live_cycles_by_user` only ever returns non-paper cycles)."""
    bot = by_type.get(bot_type)
    mode = (bot.config or {}).get("mode") if bot is not None else None
    return "Simulation" if mode in ("paper", "simulation") else "Live"


def _bot_names(bot_types: Iterable[str], by_type: dict[str, Any]) -> list[str]:
    from icici_breeze_backend.app.services.telegram_alerts import bot_label

    return sorted({f"{bot_label(t)} ({_bot_mode_tag(t, by_type)})" for t in bot_types})


def check_bot_feeds(now: float, floor: float) -> None:
    """One pass over every user with an enabled bot or an open live bot position."""
    from icici_breeze_backend.app.repositories import bots as repo
    from icici_breeze_backend.app.services import telegram_alerts

    futures = _futures_down(floor, now)
    spot = _spot_down(floor, now)
    down_feeds = {("futures", l) for l, d in futures.items() if d} | {
        ("spot", l) for l, d in spot.items() if d
    }
    enabled = repo.list_enabled_bots_by_user()
    cycles = _live_cycles_by_user()

    for user_id in sorted(set(enabled) | set(cycles) | _users_with_incidents()):
        entries_key, positions_key = f"{user_id}|entries", f"{user_id}|positions"
        bots = enabled.get(user_id, [])
        by_type = {b.bot_type: b for b in bots}
        paused = {b.bot_type for b in bots if _entry_feeds(b) & down_feeds}
        user_cycles = cycles.get(user_id, [])
        unmonitored = _unmonitored_bots(user_cycles, spot, floor, now)

        # Nothing left to watch: the bot's own messages (disabled, position closed) cover it.
        if not bots:
            _incidents.forget(entries_key)
        if not user_cycles:
            _incidents.forget(positions_key)

        entries = _incidents.update(entries_key, down=bool(paused), now=now) if bots else None
        positions = (
            _incidents.update(positions_key, down=bool(unmonitored), now=now)
            if user_cycles
            else None
        )
        hold_minutes = int(RECOVERY_HOLD_SECONDS // 60)

        if positions == "down":
            _logger.warning("feed alerts: bot positions unmonitored for user=%s: %s", user_id, sorted(unmonitored))
            telegram_alerts.notify_bot_feed_down(
                user_id,
                paused=_bot_names(paused | unmonitored, by_type),
                unmonitored=_bot_names(unmonitored, by_type),
                hold_minutes=hold_minutes,
            )
        elif entries == "down" and not _incidents.is_down(positions_key):
            _logger.warning("feed alerts: bots paused for user=%s: %s", user_id, sorted(paused))
            telegram_alerts.notify_bot_feed_down(
                user_id, paused=_bot_names(paused, by_type), unmonitored=[], hold_minutes=hold_minutes
            )

        if positions == "back" or entries == "back":
            _logger.info("feed alerts: feed back for user=%s (entries=%s positions=%s)", user_id, entries, positions)
            telegram_alerts.notify_bot_feed_back(
                user_id,
                entries_back=entries == "back",
                positions_back=positions == "back",
                still_paused=_incidents.is_down(entries_key),
                still_unmonitored=_incidents.is_down(positions_key),
                hold_minutes=hold_minutes,
            )


def _users_with_incidents() -> set[str]:
    return {key.split("|", 1)[0] for key in _incidents.keys()}


def _open_floor(now_dt: datetime) -> Optional[float]:
    from icici_breeze_backend.app.services.market_calendar import get_calendar_config

    try:
        return get_calendar_config().open_time(now_dt).timestamp()
    except Exception:  # noqa: BLE001
        _logger.debug("feed alerts: could not resolve market open", exc_info=True)
        return None


def bot_feed_alert_tick(now_dt: Optional[datetime] = None) -> None:
    """Safe to call directly (tests); never raises."""
    from icici_breeze_backend.app.services.breeze_websocket_manager import current_ws_user_id
    from icici_breeze_backend.app.services.market_calendar import is_market_open

    now_dt = now_dt or datetime.now(IST)
    with _lock:
        try:
            if not is_market_open(now_dt):
                # Every feed goes quiet at the close; an open outage ends with the session.
                _incidents.clear()
                _spot_seen.clear()
                return
            if current_ws_user_id() is None:
                return  # no broker session: nothing is subscribed, and the login nags cover it
            floor = _open_floor(now_dt)
            now = now_dt.timestamp()
            check_bot_feeds(now, floor if floor is not None else now)
        except Exception:  # noqa: BLE001
            _logger.exception("feed alerts: bot feed pass failed")


async def run_bot_feed_alert_loop() -> None:
    """Cancelled only via the FastAPI lifespan's `task.cancel()`."""
    _logger.info("Bot feed alert loop started (interval=%.0fs)", _INTERVAL_SECONDS)
    while True:
        try:
            await asyncio.sleep(_INTERVAL_SECONDS)
            await asyncio.to_thread(bot_feed_alert_tick)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _logger.exception("Bot feed alert tick failed")


def reset_state_for_tests() -> None:
    with _lock:
        _incidents.clear()
        _spot_seen.clear()
