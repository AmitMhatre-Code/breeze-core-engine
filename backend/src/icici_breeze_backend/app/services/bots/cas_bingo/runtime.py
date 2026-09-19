"""CAS Bingo's driver (docs/bots-cas-bingo-plan.md sections 2, 3 and 7).

A daemon thread, like the scalper loop, at the user's PB/SL recompute interval -- the same
latency knob as everything else that watches an open position. Each pass, per user:

1. **Exits first.** Every open cycle is marked at closing prices and closed on its target or
   stop; after the close, an expired one is settled. A gate that blocks entering never blocks
   leaving, so this runs whatever the bot's mode, licence or window.
2. **Entries**, only for an armed bot, per enabled index expiring today, through the gates in
   the order section 2 lists them.

Nothing is recorded on a day neither index expires: a session run is opened only when the bot
has something to do, so the run log is not a column of "not an expiry day" rows.
"""
from __future__ import annotations

import datetime
import json
import logging
import threading
import time
from typing import Any, Optional

from icici_breeze_backend.app.core.timezone import IST, now_ist
from icici_breeze_backend.app.db.bots_migrate import BOT_CAS_BINGO
from icici_breeze_backend.app.domain.bots import CasBingoConfig, ReasonCode
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots.cas_bingo import execution, market, triggers
from icici_breeze_backend.app.services.bots.cas_bingo.plan import build_plan, structure_for

_logger = logging.getLogger(__name__)

# The auction matches 15:30-15:35; after this the last index level is the auction's close.
SETTLE_AFTER_IST = "15:40"
# How long a pricing miss (chain warming, a quote missing, a margin call that blipped) stands
# an index down before the next try. Short -- the window is ~15 minutes -- but not every pass,
# because a credit plan spends margin_calculator calls.
RETRY_SECONDS = 30.0
PUBLISH_INTERVAL_SECONDS = 60.0

_stop = threading.Event()
_thread: Optional[threading.Thread] = None
# (user_id, index_code) -> (IST date, reason_code, reason_text): a genuine "no" for the day.
_resolved: dict[tuple[str, str], tuple[datetime.date, str, str]] = {}
_retry_after: dict[tuple[str, str], float] = {}
_last_published: dict[str, tuple[str, float]] = {}
_finalised: dict[str, datetime.date] = {}


# --------------------------------------------------------------------------------------
# World reads
# --------------------------------------------------------------------------------------


def _series(config: CasBingoConfig, index_code: str) -> Any:
    from icici_breeze_backend.app.services.index_signal.mechanisms import SeriesKey

    choice = config.signal
    return SeriesKey(choice.mechanism, choice.duration, market.SIGNAL_LABEL[index_code])


def _direction(config: CasBingoConfig) -> str:
    """The debit spread trades the bot's direction; the credit spread's rule is already "a
    flip against the day's move", so it always reads the signal as published."""
    return config.signal.direction if config.strategy == "debit_spread" else "follow"


def _signal(config: CasBingoConfig, index_code: str) -> tuple[str, Optional[float], Optional[str]]:
    """(state, strength, reason) of the chosen series now. `unavailable` is never `neutral`."""
    try:
        from icici_breeze_backend.app.services.index_signal.reader import get_signal

        payload = get_signal(_series(config, index_code), direction=_direction(config))
    except Exception:  # noqa: BLE001
        return "unavailable", None, "unreadable"
    try:
        value = float(payload.get("signal")) if payload.get("signal") is not None else None
    except (TypeError, ValueError):
        value = None
    return str(payload.get("state") or "unavailable"), value, payload.get("reason")


def _today_rows(config: CasBingoConfig, index_code: str, now: datetime.datetime) -> list[dict[str, Any]]:
    """Today's readings of the chosen series as the rows `triggers` reads, oldest first.

    No reading is stored (decision 4), so the day's history is recomputed from today's bars by
    the publisher -- the same computation the live engine did, which is also what lets a
    restart at 15:18 still find the flip at 15:12. `spot` is the futures level at each reading:
    a move measured against the futures' own open keeps the basis out of it."""
    from icici_breeze_backend.app.services.index_signal import publisher
    from icici_breeze_backend.app.services.index_signal.series import apply_direction

    rows: list[dict[str, Any]] = []
    prev: Optional[str] = None
    for bar, snap in publisher.today_series(_series(config, index_code), now=now.timestamp()):
        snap = apply_direction(snap, _direction(config))
        state = str(snap.get("state") or "unavailable")
        rows.append({
            "ts": bar.close_ts,
            "kind": "transition" if state != prev else "sample",
            "state": state,
            "signal": snap.get("signal"),
            "spot": bar.close,
        })
        prev = state
    return rows


def futures_open(index_code: str) -> Optional[float]:
    from icici_breeze_backend.app.services.index_signal import publisher

    return publisher.today_open(market.SIGNAL_LABEL[index_code])


def day_open(index_code: str) -> Optional[float]:
    from icici_breeze_backend.app.services.index_spot_feed import day_open as feed_day_open

    return feed_day_open(market.SIGNAL_LABEL[index_code])


def _trading_allowed() -> bool:
    try:
        from icici_breeze_backend.app.services.deployment_license_status import (
            trading_mutations_allowed,
        )

        return bool(trading_mutations_allowed())
    except Exception:  # noqa: BLE001 -- unknown licence state is not a licence
        return False


def _has_session(proc: Any, user_id: str) -> bool:
    try:
        return proc.get_session_breeze(user_id) is not None
    except Exception:  # noqa: BLE001
        return False


def sg_conflict(user_id: str, index_code: str, expiry_display: str) -> bool:
    """A PB/SL group rule on this expiry would absorb CAS Bingo's legs (section 8)."""
    try:
        from icici_breeze_backend.app.services.portfolio_pnl_engine import group_rule_for

        return group_rule_for(user_id, index_code, expiry_display) is not None
    except Exception:  # noqa: BLE001 -- fail closed: an unreadable rule registry blocks entry
        _logger.exception("cas bingo: PB/SL rule lookup failed")
        return True


def entered_today(user_id: str, index_code: str, today: datetime.date) -> bool:
    """One entry per index per day (section 2). Any cycle counts, aborted ones and manual
    trades included: an attempt already reached the exchange, or the user already chose."""
    stamp = today.isoformat()
    for cycle in repo.list_cycles(user_id, bot_type=BOT_CAS_BINGO, limit=100):
        if (cycle.opened_at or "")[:10] != stamp:
            continue
        if (cycle.detail or {}).get("index_code") == index_code:
            return True
    return False


def _windows(config: CasBingoConfig) -> list[tuple[str, str]]:
    return [(w.start, w.end) for w in config.windows()]


# --------------------------------------------------------------------------------------
# Decisions
# --------------------------------------------------------------------------------------


def evaluate_trigger(
    config: CasBingoConfig, index_code: str, now: datetime.datetime
) -> triggers.Verdict:
    """What the configured strategy's trigger says right now. Reads the world; `triggers`
    holds the judgement."""
    windows = _windows(config)
    ts = now.timestamp()
    if config.strategy == "long_strangle":
        return triggers.strangle_due(ts, config.strangle.entry_time_ist, windows)
    if _auction_credit(config, now):
        # Section 3.2b. The signal is not read: inside the auction it shows auction books.
        if now.strftime("%H:%M") < triggers.AUCTION_ORDER_ENTRY_IST:
            return triggers.Verdict(
                None,
                f"Auction orders open at {triggers.AUCTION_ORDER_ENTRY_IST}; the indicative "
                f"index is a settlement estimate only from then.",
            )
        return triggers.evaluate_auction_credit(
            indicative=market.index_spot(index_code), day_open=day_open(index_code), now_ts=ts
        )

    state, _value, reason = _signal(config, index_code)
    if state == "unavailable":
        return triggers.Verdict(
            None,
            f"{config.signal.label()} on {market.INDEX_LABEL[index_code]} is unavailable "
            f"({reason or 'no reading'}).",
        )
    rows = _today_rows(config, index_code, now)
    if config.strategy == "debit_spread":
        return triggers.evaluate_debit(
            rows, live_state=state, now_ts=ts, windows=windows,
            sustain_seconds=config.debit.sustain_minutes * 60.0,
        )
    return triggers.evaluate_credit(
        rows, live_state=state, now_ts=ts, windows=windows,
        day_open=futures_open(index_code), move_trigger_pct=config.credit.move_trigger_pct,
    )


def _in_window(config: CasBingoConfig, now: datetime.datetime) -> bool:
    t = now.strftime("%H:%M")
    return any(start <= t < end for start, end in _windows(config))


def _auction_credit(config: CasBingoConfig, now: datetime.datetime) -> bool:
    """A credit spread inside the CAS window is the auction rule's alone (section 3.2b)."""
    t = now.strftime("%H:%M")
    return config.strategy == "credit_spread" and config.cas_window.start <= t < config.cas_window.end


def _entry_for_index(
    proc: Any, user_id: str, config: CasBingoConfig, run_id: str,
    index_code: str, expiry: str, now: datetime.datetime,
) -> tuple[str, str]:
    """One index's entry pass. Returns (reason_code, reason_text) for the verdict."""
    label = market.INDEX_LABEL[index_code]
    key = (user_id, index_code)
    today = now.date()

    resolved = _resolved.get(key)
    if resolved is not None and resolved[0] == today:
        return resolved[1], resolved[2]
    if entered_today(user_id, index_code, today):
        return ReasonCode.ALREADY_RAN_TODAY, f"{label}: already entered today."
    if not _in_window(config, now):
        first = config.pre_cas_window.start
        if now.strftime("%H:%M") < first:
            return ReasonCode.OUTSIDE_SESSION_WINDOW, f"{label}: waiting for the {first} window."
        return ReasonCode.OUTSIDE_SESSION_WINDOW, f"{label}: the entry windows have closed."
    if time.monotonic() < _retry_after.get(key, 0.0):
        return ReasonCode.CHAIN_NOT_READY, f"{label}: retrying shortly."
    if sg_conflict(user_id, index_code, expiry):
        return ReasonCode.SG_RULE_CONFLICT, (
            f"{label}: a PB/SL rule is armed on {expiry}; it would absorb this bot's legs, so "
            f"CAS Bingo is sitting it out."
        )

    live = config.mode == "live"
    auction = _auction_credit(config, now)
    # The 30-day backtest gate (decision 7), for Simulation and Live alike. A trigger that does
    # not read the signal -- the strangle's clock, the auction rule -- has nothing to gate.
    if config.strategy != "long_strangle" and not auction:
        from icici_breeze_backend.app.services.bots.signal_gate import refusal

        blocked = refusal(BOT_CAS_BINGO, config)
        if blocked:
            return ReasonCode.SIGNAL_NOT_READY, f"{label}: {blocked}"

    verdict = evaluate_trigger(config, index_code, now)
    if verdict.trigger is None:
        return ReasonCode.SIGNAL_NO_TRADE, f"{label}: {verdict.reason}"

    structure = structure_for(config.strategy, verdict.trigger.right)
    plan, problem = build_plan(
        proc, user_id, config, index_code=index_code, expiry_display=expiry,
        structure=structure, day_open=day_open(index_code),
        spot=verdict.trigger.level, auction=auction,
    )
    if plan is None:
        code, text = problem or (ReasonCode.INTERNAL_ERROR, "Could not plan the entry.")
        if code in (ReasonCode.OUTLAY_BELOW_ONE_LOT, ReasonCode.MARGIN_CAP_TOO_SMALL):
            _resolved[key] = (today, code, f"{label}: {text}")
        else:
            _retry_after[key] = time.monotonic() + RETRY_SECONDS
        return code, f"{label}: {text}"

    from icici_breeze_backend.app.services.bots.charges import load_charges

    outcome = execution.enter(
        proc, user_id, config, run_id, plan, live=live, charges=load_charges(),
        extra={"trigger": verdict.trigger.text, "mode": config.mode},
    )
    if not outcome.opened:
        if outcome.terminal:
            _resolved[key] = (today, outcome.reason_code, f"{label}: {outcome.reason_text}")
        else:
            _retry_after[key] = time.monotonic() + RETRY_SECONDS
    return outcome.reason_code, f"{label}: {outcome.reason_text}"


def _manage_open_cycles(proc: Any, user_id: str, config: CasBingoConfig, now: datetime.datetime) -> None:
    from icici_breeze_backend.app.services.bots.charges import load_charges
    from icici_breeze_backend.app.services.market_calendar import is_market_open

    hhmm = now.strftime("%H:%M")
    market_open = is_market_open(now)
    for cycle in repo.open_cycles(user_id, BOT_CAS_BINGO):
        detail = cycle.detail or {}
        if detail.get("pending"):
            continue  # an unreconciled intent row is startup's question, not the exit loop's
        index_code = str(detail.get("index_code") or "NIFTY")
        expiry = market.expiry_date(str(detail.get("expiry_display") or ""))
        expired = expiry is not None and (expiry < now.date() or (expiry == now.date() and hhmm >= SETTLE_AFTER_IST))
        try:
            if expired:
                level = market.last_index_level(index_code)
                if level:
                    execution.settle(cycle, level)
                continue
            if not market_open:
                continue
            quotes = execution.leg_quotes(proc, user_id, cycle)
            verdict = execution.evaluate_exit(
                config, cycle, execution.close_value_per_unit(cycle.legs or [], quotes)
            )
            if verdict is None:
                continue
            if cycle.paper:
                execution.close_paper(cycle, quotes, verdict, load_charges())
            else:
                execution.close_live(proc, user_id, config, cycle, quotes, verdict, load_charges())
        except Exception:  # noqa: BLE001 -- one bad cycle must not stop the others
            _logger.exception("cas bingo: managing cycle %s failed", cycle.id)


def _publish(user_id: str, run_id: str, verdicts: dict[str, tuple[str, str]]) -> None:
    """Write the day's verdict on the session run -- on change, and once a minute regardless
    so a quiet window leaves a timeline (the scalpers' rule)."""
    if not verdicts:
        return
    text = " · ".join(t for _c, t in verdicts.values())
    code = next(iter(verdicts.values()))[0]
    now = time.monotonic()
    held = _last_published.get(user_id)
    if held is not None and held[0] == text and now - held[1] < PUBLISH_INTERVAL_SECONDS:
        return
    _last_published[user_id] = (text, now)
    _logger.info("cas bingo: %s", text)
    try:
        repo.update_run_reason(
            run_id, reason_code=code, reason_text=text,
            detail={"indices": {k: {"reason_code": c, "reason_text": t} for k, (c, t) in verdicts.items()}},
        )
    except Exception:  # noqa: BLE001
        _logger.exception("cas bingo: could not record the verdict")


def tick_user(user_id: str, config: CasBingoConfig, *, armed: bool, proc: Any = None) -> dict[str, tuple[str, str]]:
    """One pass for one user. Returns the per-index verdicts so tests can assert on them."""
    from icici_breeze_backend.app.services.market_calendar import is_trading_day

    if proc is None:
        from icici_breeze_backend.app.services.processor import processor

        proc = processor()
    now = now_ist()
    _manage_open_cycles(proc, user_id, config, now)

    has_open = bool(repo.open_cycles(user_id, BOT_CAS_BINGO))
    verdicts: dict[str, tuple[str, str]] = {}
    expiring: dict[str, str] = {}
    if armed and is_trading_day(now):
        try:
            expiring = {c: e for c, e in market.expiring_today(proc).items() if c in config.enabled_indices()}
        except Exception:  # noqa: BLE001
            _logger.exception("cas bingo: expiry lookup failed")
    if not expiring and not has_open:
        return verdicts

    run_id = repo.open_session_run(user_id, BOT_CAS_BINGO)
    repo.touch_run_heartbeat(run_id)

    if expiring:
        gate: Optional[tuple[str, str]] = None
        if not _trading_allowed():
            gate = (ReasonCode.TRADING_READ_ONLY, "Read-only mode — the licence does not currently permit trading.")
        elif not _has_session(proc, user_id):
            gate = (ReasonCode.NO_BROKER_SESSION, "No ICICI session; log in before the window opens.")
        for code, expiry in expiring.items():
            if gate is not None and config.mode == "live":
                verdicts[code] = gate
                continue
            try:
                verdicts[code] = _entry_for_index(proc, user_id, config, run_id, code, expiry, now)
            except Exception:  # noqa: BLE001
                _logger.exception("cas bingo: entry pass failed for %s", code)
                verdicts[code] = (ReasonCode.INTERNAL_ERROR, f"{market.INDEX_LABEL[code]}: the entry pass failed.")
    _publish(user_id, run_id, verdicts)
    _finalise_if_done(user_id, run_id, now)
    return verdicts


def _finalise_if_done(user_id: str, run_id: str, now: datetime.datetime) -> None:
    """Close the day's session once everything has settled, so the reaper never mistakes an
    ordinary expiry day for an interrupted one."""
    if now.strftime("%H:%M") < SETTLE_AFTER_IST or repo.open_cycles(user_id, BOT_CAS_BINGO):
        return
    if _finalised.get(user_id) == now.date():
        return
    _finalised[user_id] = now.date()
    from icici_breeze_backend.app.services.bots.scalping import guards

    guards.finalise_session(
        user_id, BOT_CAS_BINGO, run_id,
        reason_code="session_complete", reason_text="The expiry day is done and every position has closed.",
    )


def _config(raw: dict[str, Any]) -> CasBingoConfig:
    try:
        return CasBingoConfig(**(raw or {}))
    except Exception:  # noqa: BLE001 -- a bad stored blob falls back to policy defaults
        _logger.warning("cas bingo: stored config invalid; using defaults: %s", json.dumps(raw, default=str)[:200])
        return CasBingoConfig()


def tick() -> None:
    from icici_breeze_backend.app.services.market_calendar import has_market_opened

    if not has_market_opened(now_ist()):
        return
    armed: set[str] = set()
    try:
        bots = repo.list_enabled_bots(BOT_CAS_BINGO)
    except Exception:  # noqa: BLE001
        _logger.exception("cas bingo: could not list enabled bots")
        bots = []
    for record in bots:
        user_id = repo.bot_owner(record.id)
        if not user_id:
            continue
        armed.add(user_id)
        try:
            tick_user(user_id, _config(record.config), armed=True)
        except Exception:  # noqa: BLE001
            _logger.exception("cas bingo: tick failed for %s", user_id)
    # Positions outlive the switch that opened them: a bot set to Manual (or a manual trade)
    # still has its exits evaluated until it is flat.
    try:
        holding = repo.users_with_open_cycles(BOT_CAS_BINGO)
    except Exception:  # noqa: BLE001
        holding = []
    for user_id in holding:
        if user_id in armed:
            continue
        try:
            record = repo.get_or_create_bot(user_id, BOT_CAS_BINGO)
            tick_user(user_id, _config(record.config), armed=False)
        except Exception:  # noqa: BLE001
            _logger.exception("cas bingo: exit-only tick failed for %s", user_id)


def _loop() -> None:
    from icici_breeze_backend.app.services.bots.scalping.runtime import _interval_seconds

    while not _stop.is_set():
        try:
            tick()
        except Exception:  # noqa: BLE001 -- one bad pass must never kill the loop
            _logger.exception("cas bingo loop tick failed")
        _stop.wait(_interval_seconds())


def reconcile_on_startup() -> None:
    """Resolve intent rows a crash left mid-placement before trading resumes."""
    try:
        from icici_breeze_backend.app.services.bots.scalping import guards
        from icici_breeze_backend.app.services.processor import processor

        proc = processor()
        users = {repo.bot_owner(r.id) for r in repo.list_enabled_bots(BOT_CAS_BINGO)}
        users.update(repo.users_with_open_cycles(BOT_CAS_BINGO))
        for user_id in filter(None, users):
            guards.reconcile_pending_cycles(proc, user_id, BOT_CAS_BINGO)
    except Exception:  # noqa: BLE001
        _logger.exception("cas bingo: startup reconciliation failed")


def start_cas_bingo_loop() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    reconcile_on_startup()
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="cas-bingo-loop", daemon=True)
    _thread.start()
    _logger.info("CAS Bingo loop started.")


def stop_cas_bingo_loop() -> None:
    global _thread
    _stop.set()
    _thread = None


def reset_state_for_tests() -> None:
    _resolved.clear()
    _retry_after.clear()
    _last_published.clear()
    _finalised.clear()
