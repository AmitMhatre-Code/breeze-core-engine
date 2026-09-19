"""The scalper driver (docs/bots-scalping-plan.md section 5.1).

Both scalpers run end to end here. A bot in `paper` mode runs the full strategy against live
prices and places nothing; a bot in `live` mode places real orders. Reaching `live` requires a
completed paper trading day on the same material config -- enforced when the mode is saved,
not here (`scalping/evidence.py`), so by the time this loop sees `live` the evidence exists.

A daemon thread, not an asyncio task. Both design documents say "asyncio task", but
`bots/scheduler.py` and `reference_data/scheduler.py` are both threads and the tick source is
a socket-thread callback with no async I/O anywhere on this path -- so the code is the
better guide than the docs, and the docs have been corrected rather than the pattern.

Cadence is the user's **PB/SL recompute interval** (Settings -> Advanced, 1-30s, default 2s),
read fresh every pass so it stays live-adjustable. Reusing it rather than adding a second
timer means there is one latency knob for everything that watches an open position, and a
user who tightens it for their stop-losses tightens it for the bots by the same act.

The thread holds no judgement of its own: it gathers a `Snapshot`, hands it to the pure
`decide()`, and carries out the verdict. Anything that looks like a decision here is a bug.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import replace
from typing import Any, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.db.bots_migrate import (
    BOT_IRON_FLY_SCALPER,
    BOT_MOMENTUM_LONG_SCALPER,
    SCALPER_BOT_TYPES,
)
from icici_breeze_backend.app.domain.bots import ReasonCode
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots.scalping import (
    futures_feed,
    guards,
    iron_fly_bot,
    momentum_bot,
)
from icici_breeze_backend.app.services.bots.charges import load_charges
from icici_breeze_backend.app.services.bots.scalping.decide import (
    Decision,
    FeedHealth,
    Snapshot,
    decide,
    in_window,
)

_logger = logging.getLogger(__name__)

# How long the feed must stay quiet before an OPEN position is closed on staleness alone.
# Much longer than `is_tick_stream_stale`'s ~10s threshold on purpose: 10s freezes entries,
# but flattening on a blip would spend a round trip of friction -- the binding constraint --
# every time the socket hiccups.
STALE_EXIT_SECONDS = 60.0

# Bot 4 flattens when its window closes (plan section 4.4). Bot 3 does not: cutting a runner
# because the clock moved is the opposite of what its ladder exists to do, so it rides to its
# own exit or the hard square-off.
_EXIT_AT_WINDOW_END = {BOT_IRON_FLY_SCALPER: True, BOT_MOMENTUM_LONG_SCALPER: False}

# How long to wait before re-attempting a futures subscribe that failed. The feed cannot be
# subscribed before the user has a broker session, so a failure early in the day is ordinary
# and self-correcting -- but retrying it at the PB/SL cadence would spend an SDK token lookup
# and a warning line every two seconds for as long as the session is missing.
FEED_RETRY_SECONDS = 30.0

_stop = threading.Event()
_thread: Optional[threading.Thread] = None
# Monotonic timestamp of the last futures-subscribe attempt, successful or not.
_last_feed_attempt = 0.0
# (user_id, bot_type) -> last decision reason, so an unchanged verdict is logged once rather
# than every two seconds for a whole session.
_last_reason: dict[tuple[str, str], str] = {}
# (user_id, bot_type) -> monotonic time the verdict was last published. An unchanged verdict
# is re-stated on this cadence so a quiet session leaves a timeline rather than one line: the
# whole point of the record is telling a bot that stood down all day from one that stopped
# being asked, and a single line at 09:57 cannot do that.
_last_published: dict[tuple[str, str], float] = {}
PUBLISH_INTERVAL_SECONDS = 60.0
# (user_id, bot_type) -> date already finalised, so the summary is written once a day.
_finalised: dict[tuple[str, str], Any] = {}


def _interval_seconds() -> float:
    """The user's PB/SL recompute interval, falling back exactly as the P&L engine does."""
    try:
        from icici_breeze_backend.app.services.pnl_engine_settings import (
            load_pnl_engine_settings,
        )

        return float(load_pnl_engine_settings()["pnl_recompute_interval_seconds"])
    except Exception:  # noqa: BLE001
        _logger.debug("scalping: PB/SL interval lookup failed; using env default", exc_info=True)
        try:
            return max(1.0, min(30.0, float(getattr(cfg, "PNL_ENGINE_INTERVAL_SECONDS", 2.0))))
        except (TypeError, ValueError):
            return 2.0


def _feed_health(config: Any) -> FeedHealth:
    from icici_breeze_backend.app.services import ws_tick_pipeline
    from icici_breeze_backend.app.services.portfolio_pnl_engine import is_tick_stream_stale

    feed = futures_feed.get_feed()
    status = feed.status(
        ema_period=config.signal.ema_period if hasattr(config, "signal") else 9,
        volume_ma_period=config.signal.volume_ma_period if hasattr(config, "signal") else 20,
    )
    age = ws_tick_pipeline.last_tick_age_seconds()
    return FeedHealth(
        warm=bool(status.get("warm")),
        stale=is_tick_stream_stale(),
        stale_seconds=float(age) if age is not None else float("inf"),
        detail=status,
    )


def _trading_allowed() -> bool:
    """Licence gate. Checked directly, not via the HTTP dependency -- there is no request
    on this path (see docs/bots-mvp-plan.md section 10.4)."""
    try:
        from icici_breeze_backend.app.services.deployment_license_status import (
            trading_mutations_allowed,
        )

        return bool(trading_mutations_allowed())
    except Exception:  # noqa: BLE001 -- fail closed: unknown licence state is not a licence
        _logger.warning("scalping: licence status unavailable; treating as read-only", exc_info=True)
        return False


def _api_calls_remaining(user_id: str) -> int:
    from icici_breeze_backend.app.services.icici_api_pacing import (
        _MAX_CALLS_PER_MINUTE,
        GlobalIciciApiPacer,
    )

    try:
        return max(0, int(_MAX_CALLS_PER_MINUTE) - int(GlobalIciciApiPacer.calls_in_window(user_id)))
    except Exception:  # noqa: BLE001
        _logger.debug("scalping: API budget read failed", exc_info=True)
        return 0  # unknown budget is treated as none left, which only blocks entries


def build_snapshot(
    user_id: str,
    bot_type: str,
    config: Any,
    proc: Any = None,
    *,
    unrealized: float = 0.0,
    entries_suspended: bool = False,
) -> Snapshot:
    """Gather the world for one bot. No judgement here -- see the module docstring."""
    from icici_breeze_backend.app.services.market_calendar import is_trading_day

    now = now_ist()
    open_cycles = repo.open_cycles(user_id, bot_type)
    totals = repo.scalper_day_totals(user_id, bot_type)

    conflict = None
    if proc is not None:
        try:
            conflict = guards.find_sg_conflict(
                proc, user_id, bot_leg_count=len(open_cycles[0].legs) if open_cycles else 0
            )
        except Exception:  # noqa: BLE001 -- never let a guard lookup stop the gate stack
            _logger.exception("scalping[%s]: PB/SL conflict check failed", bot_type)

    return Snapshot(
        now_ist=now,
        trading_allowed=_trading_allowed(),
        # `is_trading_day` takes a datetime and calls .astimezone() on it -- a date raises.
        is_trading_day=bool(is_trading_day(now)),
        is_expiry_day=_is_expiry_day(config),
        feed=_feed_health(config),
        totals=totals,
        has_open_position=bool(open_cycles),
        api_calls_remaining=_api_calls_remaining(user_id),
        # Only gates ENTRY. A rule that appears while a position is already open is handled
        # by disarming the rule, not by refusing to manage the position -- see `tick_bot`.
        sg_rule_conflict=conflict is not None and not open_cycles,
        position_exit=None,
        exit_at_window_end=_EXIT_AT_WINDOW_END.get(bot_type, False),
        unrealized_pnl=float(unrealized),
        entries_suspended=bool(entries_suspended),
        signal=_entry_signal(bot_type, config),
        entry_hold=_entry_hold(
            bot_type, config, now, totals, has_open_position=bool(open_cycles),
            proc=proc, user_id=user_id,
        ),
    )


def _entry_signal(bot_type: str, config: Any) -> Any:
    """The bot's entry signal, or None for a bot that has no signal gate.

    Evaluated here rather than inside the executor so `decide` can turn a signal that did
    not fire into a recorded verdict. Cheap enough to run every pass -- an EMA and a mean
    over at most `_MAX_CANDLES` bars already in memory, with no broker call behind it.
    """
    if bot_type != BOT_MOMENTUM_LONG_SCALPER:
        return None
    try:
        feed = futures_feed.get_feed()
        return momentum_bot.current_signal(config, feed.builder.candles, feed.builder.session_vwap)
    except Exception:  # noqa: BLE001 -- a signal failure must not stop the gate stack
        _logger.exception("scalping[%s]: signal evaluation failed", bot_type)
        return None


def _entry_hold(
    bot_type: str,
    config: Any,
    now: Any,
    totals: Any,
    *,
    has_open_position: bool,
    proc: Any = None,
    user_id: str = "",
) -> Optional[tuple[str, str]]:
    """A bot-specific hold on a fresh entry, as a verdict input, or None.

    Bot 3: the fresh-signal rule. Bot 4: the re-entry gate (cooldown, then a settled range),
    then its entry filter (#38).

    Evaluated here for the reason `_entry_signal` is: checked only inside the executor, a held
    pass was published as `enter / gates_clear` and the wait after every stop-loss looked like
    a bot failing to trade. Irrelevant while a position is open -- that pass is about exits.

    Fails closed: a check that cannot run holds the entry rather than waving it through.
    """
    if has_open_position:
        return None
    if bot_type == BOT_MOMENTUM_LONG_SCALPER:
        return _fresh_signal_hold(config, totals)
    if bot_type != BOT_IRON_FLY_SCALPER:
        return None
    try:
        held = iron_fly_bot.reentry_blocked(
            config,
            now=now,
            last_closed_at=totals.last_closed_at,
            candles=futures_feed.get_feed().builder.candles,
        )
    except Exception:  # noqa: BLE001 -- a failed check must not stop the gate stack
        _logger.exception("scalping[%s]: re-entry check failed", bot_type)
        return (ReasonCode.REENTRY_GATE_CLOSED, "Re-entry check failed; holding off.")
    if held is not None:
        return held
    return _fly_entry_filter(config, now, proc, user_id)


def _fly_entry_filter(config: Any, now: Any, proc: Any, user_id: str) -> Optional[tuple[str, str]]:
    """Bot 4's entry filter, asked last so the VIX fetch is spent only on a pass that would
    otherwise enter -- and only inside a trading window. Fails closed."""
    f = getattr(config, "entry_filter", None)
    kind = getattr(f, "kind", "none")
    if f is None or kind == "none":
        return None
    try:
        if in_window(now, config.sessions) is None:
            return None  # the window gate stands the bot down anyway; don't spend a VIX call
        if kind == "expansion_neutral":
            from icici_breeze_backend.app.services.index_signal.reader import get_variant_signal

            return iron_fly_bot.expansion_neutral_hold(get_variant_signal(f.variant))
        if kind == "vix_not_rising":
            from icici_breeze_backend.app.services.bots.scalping import vix_minutes

            return vix_minutes.filter_hold(
                f, vix_minutes.live_series(proc, user_id), time.time()
            )
    except Exception:  # noqa: BLE001 -- a failed check must not stop the gate stack
        _logger.exception("scalping: iron fly entry filter failed")
    return (ReasonCode.ENTRY_FILTER_CLOSED, "Entry filter could not be checked; holding off.")


def _fresh_signal_hold(config: Any, totals: Any) -> Optional[tuple[str, str]]:
    """Bot 3 holds while the signal run that opened its last trade is still going.

    See `signal.signal_run_unbroken`. No earlier entry today means nothing to hold.
    """
    start, side = totals.last_entry_candle_start, totals.last_entry_side
    if start is None or side is None:
        return None
    try:
        from icici_breeze_backend.app.services.bots.scalping.signal import (
            signal_run_unbroken,
            variant_call_unbroken,
        )

        if momentum_bot.uses_variant(config):
            from icici_breeze_backend.app.services.index_signal.reader import get_variant_signal

            unbroken = variant_call_unbroken(
                get_variant_signal(config.entry_signal),
                entry_candle_start=int(start),
                side=str(side),
            )
        else:
            candles = futures_feed.get_feed().builder.candles
            unbroken = signal_run_unbroken(
                candles, config.signal, entry_candle_start=int(start), side=str(side)
            )
        if not unbroken:
            return None
    except Exception:  # noqa: BLE001 -- a failed check must not stop the gate stack
        _logger.exception("scalping: fresh-signal check failed")
        return (ReasonCode.SIGNAL_NOT_FRESH, "Fresh-signal check failed; holding off.")

    import datetime as _dt

    from icici_breeze_backend.app.core.timezone import IST

    since = _dt.datetime.fromtimestamp(int(start), tz=IST).strftime("%H:%M")
    return (
        ReasonCode.SIGNAL_NOT_FRESH,
        f"Still the {side} signal run that opened the last trade (its {since} candle); "
        "waiting for the signal to switch off and fire again.",
    )


def _is_expiry_day(config: Any) -> bool:
    """True when the traded contract expires today.

    Read from the futures feed's own contract rather than a weekday rule, for the reason
    `bots/scheduler._expiring_today` gives: SEBI has moved expiry days before.
    """
    contract = futures_feed.get_feed().contract
    if contract is None:
        return False
    return contract.expiry_date == now_ist().date()


def _audit_detail(snapshot: Snapshot, decision: Decision) -> dict[str, Any]:
    """Everything needed to tell two identical-looking stand-downs apart.

    `not_warm` on its own is ambiguous in the worst way: it reads as "give it twenty
    minutes" whether the feed is filling normally or was never subscribed at all. The
    difference is visible only in `ticks_seen` and `token_symbol`, so those travel with the
    verdict -- into the log line and onto the run row -- rather than staying inside a feed
    object nothing else can see.
    """
    feed = dict(snapshot.feed.detail or {})
    stale_seconds = snapshot.feed.stale_seconds
    return {
        "action": decision.action,
        "feed": {
            "warm": snapshot.feed.warm,
            "stale": snapshot.feed.stale,
            # inf is not JSON, and "never seen a tick" is what it means here.
            "stale_seconds": (
                None if stale_seconds == float("inf") else round(float(stale_seconds), 1)
            ),
            "subscribed": bool(feed.get("token_symbol")),
            "contract": feed.get("contract"),
            "token_symbol": feed.get("token_symbol"),
            "ticks_seen": feed.get("ticks_seen"),
            "candles": feed.get("candles"),
            "candles_required": feed.get("candles_required"),
            # Both travel with the verdict for the same reason `ticks_seen` does: a session
            # that kept losing its history reads as an ordinary slow warm-up without them,
            # and the log line that would have said so rotates away within days.
            "counter_resets": feed.get("counter_resets"),
            "stale_ticks": feed.get("stale_ticks"),
            "last_error": feed.get("last_error"),
        },
        "gates": {
            "trading_allowed": snapshot.trading_allowed,
            "is_trading_day": snapshot.is_trading_day,
            "is_expiry_day": snapshot.is_expiry_day,
            "has_open_position": snapshot.has_open_position,
            "api_calls_remaining": snapshot.api_calls_remaining,
            "sg_rule_conflict": snapshot.sg_rule_conflict,
            "realized_net_pnl": snapshot.totals.realized_net_pnl,
            "unrealized_pnl": round(float(snapshot.unrealized_pnl), 2),
        },
        # Whatever the decision itself attached (warm-up status, cooldown counts, budget).
        "decision": dict(decision.detail or {}),
    }


def _publish_verdict(
    user_id: str,
    bot_type: str,
    run_id: str,
    snapshot: Snapshot,
    decision: Decision,
) -> None:
    """Log the verdict and record it on the open run row.

    Written on change, and re-stated every `PUBLISH_INTERVAL_SECONDS` so an unchanged verdict
    still leaves a trail. At the two-second loop cadence, publishing every pass would be
    thousands of identical lines an hour and a SQLite write behind each one.

    The run row is what the Bots screen reads, so this is the difference between a user
    seeing "Indicators warming up -- 0 ticks, futures feed not subscribed" and seeing "—".
    """
    key = (user_id, bot_type)
    now = time.monotonic()
    changed = _last_reason.get(key) != decision.reason_code
    due = now - _last_published.get(key, 0.0) >= PUBLISH_INTERVAL_SECONDS
    if not changed and not due:
        return
    _last_reason[key] = decision.reason_code
    _last_published[key] = now

    detail = _audit_detail(snapshot, decision)
    _logger.info(
        "scalping[%s]: %s -> %s (%s) %s",
        bot_type,
        decision.action,
        decision.reason_code,
        decision.reason_text,
        json.dumps(detail, default=str, sort_keys=True),
    )
    try:
        repo.update_run_reason(
            run_id,
            reason_code=decision.reason_code,
            reason_text=decision.reason_text,
            detail=detail,
        )
    except Exception:  # noqa: BLE001 -- an audit write must never stop the bot
        _logger.exception("scalping[%s]: could not record the run reason", bot_type)


def _record_audit(
    user_id: str,
    bot_type: str,
    config: Any,
    run_id: str,
    snapshot: Snapshot,
    decision: Decision,
) -> None:
    """Append this pass to the durable audit trail.

    Unlike `_publish_verdict` this is NOT throttled inside a session window: the run row can
    only hold the latest verdict, and the whole reason the trail exists is that "the signal
    was evaluated all afternoon and never fired" is not something the latest verdict can say.
    Outside the windows the writer collapses repeats itself.
    """
    from icici_breeze_backend.audit import bot_audit

    try:
        windows = getattr(config, "sessions", None) or []
        inside = in_window(snapshot.now_ist, windows) is not None
        bot_audit.record_pass(
            user_id,
            bot_type,
            run_id,
            detail=_audit_detail(snapshot, decision),
            reason_code=decision.reason_code,
            reason_text=decision.reason_text,
            in_window=inside,
            now=snapshot.now_ist,
        )
    except Exception:  # noqa: BLE001 -- diagnostic only; never stop the bot for it
        _logger.exception("scalping[%s]: audit record failed", bot_type)


def tick_bot(
    user_id: str, bot_type: str, config: Any, *, entries_suspended: bool = False
) -> Decision:
    """One pass for one bot. Returns the decision so tests can assert on it directly.

    The order matters: the position is inspected *before* the gate stack runs, so the
    ladder's verdict is one input the stack weighs rather than something that bypasses it.
    An obligation -- square-off, the daily stop, a dark feed -- still outranks it.

    `entries_suspended` is the exit-only pass: the bot has been switched off while holding a
    real position, so it is still ticked -- the stop has to keep being evaluated against
    something -- but nothing new may be opened.
    """
    from icici_breeze_backend.app.services.processor import processor

    proc = processor()
    handler = iron_fly_bot if bot_type == BOT_IRON_FLY_SCALPER else momentum_bot
    context = None
    try:
        context = handler.inspect_position(proc, user_id, config, bot_type)
    except Exception:  # noqa: BLE001 -- a quote failure must not stop the gate stack
        _logger.exception("scalping[%s]: position inspection failed", bot_type)

    unrealized = _unrealized_from(context)
    snapshot = build_snapshot(
        user_id, bot_type, config, proc,
        unrealized=unrealized, entries_suspended=entries_suspended,
    )
    if context is not None and context.verdict is not None:
        snapshot = replace(snapshot, position_exit=context.verdict)

    # A PB/SL rule armed on this expiry while a position is open would square the bot's legs
    # off with its own. Disarm it and tell the user, rather than manage a position that
    # something else can close underneath us (plan section 2.2).
    if snapshot.has_open_position:
        _resolve_sg_conflict(proc, user_id, bot_type, context)

    decision = decide(snapshot, config, stale_exit_seconds=STALE_EXIT_SECONDS)

    # A session run exists as soon as the bot is doing anything at all, including standing
    # down -- an unexplained quiet day is exactly what the run log is for. The heartbeat is
    # what keeps `reap_stale_runs` from mistaking an all-day session for a stalled one.
    # Opened before the verdict is published, because the verdict is written onto this row.
    run_id = repo.open_session_run(user_id, bot_type)
    repo.touch_run_heartbeat(run_id)
    _stamp_session(run_id, bot_type, config)
    _publish_verdict(user_id, bot_type, run_id, snapshot, decision)
    _record_audit(user_id, bot_type, config, run_id, snapshot, decision)

    feed = futures_feed.get_feed()
    _finalise_if_day_is_over(user_id, bot_type, config, run_id, snapshot, decision)

    if bot_type == BOT_IRON_FLY_SCALPER:
        iron_fly_bot.execute(
            proc, user_id, bot_type, config, run_id, decision, context,
            load_charges(), feed.builder.candles, snapshot.now_ist,
            _current_vix(proc, user_id, config),
        )
    else:
        momentum_bot.execute(
            proc, user_id, bot_type, config, run_id, decision, context,
            load_charges(), feed.builder.candles, feed.builder.session_vwap,
            signal=snapshot.signal,
        )
    # Disarming happens AFTER the executor has run, so the position is closed first: a bot
    # switched off with an open position would leave it unmanaged.
    if decision.reason_code == ReasonCode.TERMINATED_FOR_DAY and not repo.open_cycles(
        user_id, bot_type
    ):
        guards.disarm_bot(user_id, bot_type, decision.reason_text)
        guards.finalise_session(
            user_id, bot_type, run_id,
            reason_code=ReasonCode.TERMINATED_FOR_DAY, reason_text=decision.reason_text,
        )
    return decision


def _unrealized_from(context: Any) -> float:
    """Mark-to-market on the open position, for the cumulative stop."""
    if context is None:
        return 0.0
    cycle = getattr(context, "cycle", None)
    quantity = 0
    if cycle is not None and cycle.legs:
        quantity = int((cycle.legs[0] or {}).get("quantity") or 0)
    close_cost = getattr(context, "close_cost", None)
    if close_cost is not None:  # iron fly: cost to buy the structure back
        return guards.unrealized_pnl(cycle, float(close_cost) * quantity)
    quote = getattr(context, "quote", None)
    if quote is not None and getattr(quote, "bid", None):
        return guards.unrealized_pnl(cycle, float(quote.bid) * quantity)
    return 0.0


def _resolve_sg_conflict(proc: Any, user_id: str, bot_type: str, context: Any) -> None:
    cycle = getattr(context, "cycle", None)
    legs = len(cycle.legs) if cycle is not None and cycle.legs else 0
    try:
        conflict = guards.find_sg_conflict(proc, user_id, bot_leg_count=legs)
    except Exception:  # noqa: BLE001
        _logger.exception("scalping[%s]: PB/SL conflict check failed", bot_type)
        return
    if conflict is not None:
        guards.disarm_conflicting_rule(user_id, conflict)


def _finalise_if_day_is_over(
    user_id: str, bot_type: str, config: Any, run_id: str, snapshot: Any, decision: Any
) -> None:
    """Close the session run once the day is genuinely done.

    Without this the row stays `running`, its heartbeat stops at end of day, and
    `reap_stale_runs` marks an ordinary trading day as "Interrupted before it finished" about
    half an hour later.
    """
    if snapshot.has_open_position or decision.action == "exit":
        return
    if not guards.session_is_over(config, snapshot.now_ist):
        return
    if _finalised.get((user_id, bot_type)) == snapshot.now_ist.date():
        return
    _finalised[(user_id, bot_type)] = snapshot.now_ist.date()
    guards.finalise_session(
        user_id, bot_type, run_id,
        reason_code="session_complete",
        reason_text="The day's last trading window has closed.",
    )


def _current_vix(proc: Any, user_id: str, config: Any) -> Optional[float]:
    """Latest India VIX, and **only when something actually needs it**.

    The one consumer is Bot 4's optional wing-widening rule, which ships off. Fetching VIX
    costs a broker quote, so a bot that is not using the rule must not pay for it every pass.
    Returning None leaves the configured wing width untouched.
    """
    structure = getattr(config, "structure", None)
    if structure is None or getattr(structure, "widen_above_vix", None) is None:
        return None
    try:
        from icici_breeze_backend.app.services.dashboard_vix import fetch_vix_headline

        payload, _ = fetch_vix_headline(user_id, proc)
        value = (payload or {}).get("current_vix")
        return float(value) if value else None
    except Exception:  # noqa: BLE001 -- an unavailable reading simply means "do not widen"
        _logger.debug("scalping: VIX unavailable; wing width left as configured", exc_info=True)
        return None


def _ensure_feed(user_id: str) -> None:
    """Keep the NIFTY futures feed subscribed and its bars closing. Never raises.

    Nothing else in the process subscribes futures: `index_spot_feed` covers the cash index
    only, and the chain path drops futures ticks outright because they carry no strike. So
    this call is the *whole* supply of candles -- without it `is_warm` is false forever and
    every scalper stands down on `not_warm` for an entire session while looking healthy in
    the run log.

    Called once a pass rather than once a bot: the feed is a single process-wide
    subscription to one contract, and any enabled bot's owner has the broker session it
    needs. The flush is unconditional because a bar the clock has left must close even when
    the contract has not printed -- otherwise a quiet minute stalls the indicators.
    """
    from icici_breeze_backend.app.services.processor import processor

    global _last_feed_attempt
    feed = futures_feed.get_feed()
    if not feed.subscribed_today:
        now = time.monotonic()
        if now - _last_feed_attempt >= FEED_RETRY_SECONDS:
            _last_feed_attempt = now
            try:
                proc = processor()
                feed.ensure_subscribed(proc, user_id, momentum_bot.option_expiries(proc))
            except Exception:  # noqa: BLE001 -- a dead feed must not stop the gate stack
                _logger.exception("scalping: futures feed subscribe failed")
    try:
        feed.flush(time.time())
    except Exception:  # noqa: BLE001
        _logger.debug("scalping: candle flush failed", exc_info=True)


_stamped: dict[str, tuple[str, str]] = {}


def _stamp_session(run_id: str, bot_type: str, config: Any) -> None:
    """Record which settings today's session is running on, for the paper-evidence gate.

    Cached in-process because the loop calls this every two seconds and the answer changes at
    most a handful of times a day; without the cache this is a read-and-write per bot per
    pass for a value that is almost always identical.

    The cache is keyed on the run, so a restart simply re-reads once and agrees with itself.
    """
    from icici_breeze_backend.app.services.bots.scalping import evidence as evidence_mod

    try:
        stamp = (
            evidence_mod.material_config_hash(bot_type, config),
            str(getattr(config, "mode", "paper") or "paper"),
        )
        if _stamped.get(run_id) == stamp:
            return
        repo.stamp_session_config(run_id, stamp[0], stamp[1])
        _stamped[run_id] = stamp
    except Exception:  # noqa: BLE001 -- an evidence write must never stop the bot trading
        _logger.exception("scalping[%s]: could not stamp the session config", bot_type)


def _feed_owner() -> Optional[str]:
    """Whose broker session the always-on candle feed subscribes with.

    Resolved from the persisted broker session rather than from the enabled-bot list, which
    is the whole point of plan section 5.6: the feed has to be running from 09:15 *before*
    anyone arms a bot, so that a bot armed at 11:00 reads warm indicators instead of standing
    down on `not_warm` through the move that prompted it.

    One trader per deployment (docs/bots-mvp-plan.md section 5), so "the" session is
    unambiguous; if a deployment ever held more, the first is as good as any -- the feed is
    one process-wide subscription to one contract, not per-user state.
    """
    try:
        from icici_breeze_backend.app.repositories.broker_session import (
            list_users_with_session,
        )

        users = list_users_with_session()
        return users[0] if users else None
    except Exception:  # noqa: BLE001 -- no session simply means nothing to subscribe with
        _logger.debug("scalping: could not resolve a feed owner", exc_info=True)
        return None


def _service_feed_for_the_session() -> None:
    """Keep candles building for the whole trading day, armed bots or not (section 5.6).

    Deliberately outside the enabled-bot loop. As first built this ran only as a side effect
    of iterating armed bots, so the feed subscribed when a bot was armed -- and a user who
    armed one at 11:00 got a bot that could not act until ~11:20, because the EMA and volume
    MA build from live ticks with no historical backfill.

    Read-only licence mode does NOT stop this: building candles is reading data, not trading.
    The entry gates still refuse every trade (section 5.5); what they no longer also do is
    throw away the warm-up, so a licence restored at 13:00 leaves the bot able to trade its
    afternoon window immediately.
    """
    from icici_breeze_backend.app.services.market_calendar import is_market_open, is_trading_day

    now = now_ist()
    if not is_trading_day(now) or not is_market_open(now):
        return
    user_id = _feed_owner()
    if not user_id:
        return
    _ensure_feed(user_id)


def _market_has_opened() -> bool:
    """Has today's trading session started? Stays true after the close, so a bot still
    finishing its day -- finalising the run, a square-off set at the close -- is not cut off."""
    from icici_breeze_backend.app.services.market_calendar import has_market_opened

    return has_market_opened(now_ist())


def _exit_only_bots(armed: set[tuple[str, str]]) -> list[tuple[str, str]]:
    """Bots holding a real position that the user has since switched off.

    Without this the position is stranded: `tick()` walks enabled bots, so setting a bot to
    Off with a live position open would stop the only thing evaluating its stop. Section 5.5
    says a gate that blocks entering never blocks leaving; this extends that past the arming
    switch itself, which is the one "gate" that used to escape it.
    """
    try:
        holding = repo.bots_with_open_live_cycles()
    except Exception:  # noqa: BLE001
        _logger.exception("scalping: could not list bots holding live positions")
        return []
    return [pair for pair in holding if pair not in armed and pair[1] in SCALPER_BOT_TYPES]


def tick() -> None:
    """One pass over every scalper that needs one. Never raises -- the loop must survive a
    bad bot.

    Three groups, in order: the feed (always, so nothing reads a builder this pass was about
    to fill), the armed bots, and then any bot still holding a real position after being
    switched off, which is ticked for its exits alone.
    """
    _service_feed_for_the_session()

    # Nothing is ticked before the open. A pass opens the day's session run whatever the
    # verdict, so a deployment powered on at 08:00 showed its scalpers running from 08:00
    # with an hour and a half of "outside every session window" behind them. Exit-only bots
    # wait too: no exit can be placed before the open anyway.
    if not _market_has_opened():
        return

    armed: set[tuple[str, str]] = set()
    for bot_type in SCALPER_BOT_TYPES:
        try:
            bots = repo.list_enabled_bots(bot_type)
        except Exception:  # noqa: BLE001
            _logger.exception("scalping: could not list enabled %s bots", bot_type)
            continue
        for record in bots:
            user_id = repo.bot_owner(record.id)
            if not user_id:
                continue
            armed.add((user_id, bot_type))
            try:
                config = _config_model(bot_type, record.config)
                tick_bot(user_id, bot_type, config)
            except Exception:  # noqa: BLE001
                _logger.exception("scalping: %s tick failed for %s", bot_type, user_id)

    for user_id, bot_type in _exit_only_bots(armed):
        try:
            record = repo.get_or_create_bot(user_id, bot_type)
            config = _config_model(bot_type, record.config)
            tick_bot(user_id, bot_type, config, entries_suspended=True)
        except Exception:  # noqa: BLE001
            _logger.exception(
                "scalping: exit-only %s tick failed for %s", bot_type, user_id
            )


def _config_model(bot_type: str, raw: dict[str, Any]) -> Any:
    from icici_breeze_backend.app.domain.bots import (
        IronFlyScalperConfig,
        MomentumLongScalperConfig,
    )

    model = {
        BOT_MOMENTUM_LONG_SCALPER: MomentumLongScalperConfig,
        BOT_IRON_FLY_SCALPER: IronFlyScalperConfig,
    }[bot_type]
    return model(**(raw or {}))


def _loop() -> None:
    while not _stop.is_set():
        try:
            tick()
        except Exception:  # noqa: BLE001 -- one bad pass must never kill the loop
            _logger.exception("scalping loop tick failed")
        _stop.wait(_interval_seconds())


def reconcile_on_startup() -> None:
    """Resolve any intent rows left by a crash mid-placement, before trading resumes.

    Runs once at start, ahead of the loop: an unreconciled row blocks new cycles, so
    resolving them first is what lets a clean restart pick up where it left off instead of
    standing down all session.
    """
    from icici_breeze_backend.app.services.processor import processor

    try:
        proc = processor()
    except Exception:  # noqa: BLE001
        _logger.warning("scalping: no processor at startup; skipping reconciliation")
        return
    for bot_type in SCALPER_BOT_TYPES:
        try:
            for record in repo.list_enabled_bots(bot_type):
                user_id = repo.bot_owner(record.id)
                if user_id:
                    guards.reconcile_pending_cycles(proc, user_id, bot_type)
        except Exception:  # noqa: BLE001
            _logger.exception("scalping: startup reconciliation failed for %s", bot_type)


def _armed_summary() -> str:
    """What is actually armed, for the startup log.

    A static sentence cannot answer the only question an operator reads this line for -- can
    this deployment place real orders right now? Naming each enabled scalper and its mode
    can, and it is the one moment where saying so costs nothing.
    """
    try:
        armed = [
            f"{bot_type}={str((record.config or {}).get('mode') or 'paper')}"
            for bot_type in SCALPER_BOT_TYPES
            for record in repo.list_enabled_bots(bot_type)
        ]
    except Exception:  # noqa: BLE001 -- a log line must never stop the loop starting
        _logger.debug("scalping: could not summarise armed bots", exc_info=True)
        return "armed bots unknown"
    return ", ".join(armed) if armed else "no scalper enabled"


def start_scalper_loop() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    reconcile_on_startup()
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="scalper-loop", daemon=True)
    _thread.start()
    _logger.info("Scalper loop started (%s).", _armed_summary())


def stop_scalper_loop() -> None:
    _stop.set()
    global _thread
    _thread = None


def reset_state_for_tests() -> None:
    global _last_feed_attempt
    _last_reason.clear()
    _last_published.clear()
    _finalised.clear()
    _last_feed_attempt = 0.0
