"""The scalper driver (docs/bots-scalping-plan.md section 5.1).

**Bot 3 runs end to end in PAPER mode; nothing here places a real order.** Bot 4's executor
arrives in step 6, and live dispatch in step 9 -- a bot configured `live` before then logs a
warning and does nothing, rather than quietly behaving as paper.

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

import logging
import threading
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

_stop = threading.Event()
_thread: Optional[threading.Thread] = None
# (user_id, bot_type) -> last decision reason, so an unchanged verdict is logged once rather
# than every two seconds for a whole session.
_last_reason: dict[tuple[str, str], str] = {}
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


def _log_decision(user_id: str, bot_type: str, decision: Decision) -> None:
    """Log a verdict once, then only when it changes.

    A scalper spends most of a session repeating the same reason -- outside its window, not
    warm, no signal -- and at a two-second cadence that is thousands of identical lines an
    hour. Deduplicating keeps the interesting transitions findable.
    """
    key = (user_id, bot_type)
    if _last_reason.get(key) == decision.reason_code:
        return
    _last_reason[key] = decision.reason_code
    _logger.info(
        "scalping[%s]: %s -> %s (%s)",
        bot_type,
        decision.action,
        decision.reason_code,
        decision.reason_text,
    )


def tick_bot(user_id: str, bot_type: str, config: Any) -> Decision:
    """One pass for one bot. Returns the decision so tests can assert on it directly.

    The order matters: the position is inspected *before* the gate stack runs, so the
    ladder's verdict is one input the stack weighs rather than something that bypasses it.
    An obligation -- square-off, the daily stop, a dark feed -- still outranks it.
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
    snapshot = build_snapshot(user_id, bot_type, config, proc, unrealized=unrealized)
    if context is not None and context.verdict is not None:
        snapshot = replace(snapshot, position_exit=context.verdict)

    # A PB/SL rule armed on this expiry while a position is open would square the bot's legs
    # off with its own. Disarm it and tell the user, rather than manage a position that
    # something else can close underneath us (plan section 2.2).
    if snapshot.has_open_position:
        _resolve_sg_conflict(proc, user_id, bot_type, context)

    decision = decide(snapshot, config, stale_exit_seconds=STALE_EXIT_SECONDS)
    _log_decision(user_id, bot_type, decision)

    # A session run exists as soon as the bot is doing anything at all, including standing
    # down -- an unexplained quiet day is exactly what the run log is for. The heartbeat is
    # what keeps `reap_stale_runs` from mistaking an all-day session for a stalled one.
    run_id = repo.open_session_run(user_id, bot_type)
    repo.touch_run_heartbeat(run_id)

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


def tick() -> None:
    """One pass over every enabled scalper. Never raises -- the loop must survive a bad bot."""
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
            try:
                config = _config_model(bot_type, record.config)
                tick_bot(user_id, bot_type, config)
            except Exception:  # noqa: BLE001
                _logger.exception("scalping: %s tick failed for %s", bot_type, user_id)


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


def start_scalper_loop() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    reconcile_on_startup()
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="scalper-loop", daemon=True)
    _thread.start()
    _logger.info("Scalper loop started (decisions only; no orders until step 4).")


def stop_scalper_loop() -> None:
    _stop.set()
    global _thread
    _thread = None


def reset_state_for_tests() -> None:
    _last_reason.clear()
    _finalised.clear()
