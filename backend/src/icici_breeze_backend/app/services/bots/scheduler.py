"""Drives Bot 2 (docs/bots-mvp-plan.md section 4).

A daemon thread in the API process, matching `reference_data/scheduler.py` -- not a
separate OS process like `chain_builder`, because this needs the broker session cache, the
WS feed and the P&L engine, all of which live here. It is deliberately thin: every judgement
about *whether* to act belongs to `expiry_index_writer.decide()`, which is pure and tested.
"""
from __future__ import annotations

import datetime
import logging
import threading
from typing import Any, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.db.bots_migrate import BOT_EXPIRY_INDEX_WRITER
from icici_breeze_backend.app.domain.bots import ExpiryIndexWriterConfig, ReasonCode
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots import expiry_index_writer as bot2

_logger = logging.getLogger(__name__)

_TICK_SECONDS = 30
# Bound for the periodic sweep: long enough that a genuinely slow scan is never killed,
# short enough that a hung run does not sit there looking alive all session.
_STALE_RUN_MINUTES = 30

_stop = threading.Event()
_thread: Optional[threading.Thread] = None
# When the process came up. The nag cannot start before this -- a deployment powered on at
# 09:10 has not been failing to nag since 08:00.
_app_started_at = now_ist().replace(tzinfo=None)
# user_id -> last nag time. In-memory on purpose: a restart legitimately restarts the nag
# window, which is exactly what `max(app start, nag_start)` already says should happen.
_last_nag: dict[str, datetime.datetime] = {}


def _expiring_today(proc: Any) -> dict[str, str]:
    """Index code -> expiry display, for contracts expiring today.

    Read from the scrip master rather than a weekday rule: SEBI has moved these before and
    `market_calendar` only knows holidays, not expiries.
    """
    today = now_ist().date()
    out: dict[str, str] = {}
    for code, exchange in bot2.INDEX_EXCHANGE.items():
        try:
            universe = proc.fetch_stock_codes(exchange) or []
        except Exception:  # noqa: BLE001
            _logger.warning("bot2: could not read %s universe", exchange, exc_info=True)
            continue
        for entry in universe:
            if str(entry.get("stock_code") or "").strip().upper() != code:
                continue
            for raw in entry.get("expiry_dates") or []:
                display = _to_display(raw)
                if display and _parse(display) == today:
                    out[code] = display
    return out


def _to_display(raw: Any) -> str:
    from icici_breeze_backend.app.services.reference_data.scrip_master_sql import (
        _expiry_api_to_display,
    )

    try:
        return _expiry_api_to_display(str(raw))
    except (ValueError, TypeError):
        return ""


def _parse(display: str):
    try:
        return datetime.datetime.strptime(display, "%d-%b-%Y").date()
    except (TypeError, ValueError):
        return None


def _has_session(proc: Any, user_id: str) -> bool:
    try:
        return proc.get_session_breeze(user_id) is not None
    except Exception:  # noqa: BLE001
        return False


def tick(proc: Any) -> None:
    """One sweep. Safe to call directly in tests."""
    from icici_breeze_backend.app.services.deployment_license_status import (
        trading_mutations_allowed,
    )

    repo.reap_stale_runs(older_than_minutes=_STALE_RUN_MINUTES)

    bots = repo.list_enabled_bots(BOT_EXPIRY_INDEX_WRITER)
    if not bots:
        return
    expiring = _expiring_today(proc) if bots else {}

    for bot in bots:
        user_id = repo.bot_owner(bot.id)
        if not user_id:
            continue
        try:
            config = ExpiryIndexWriterConfig(**bot.config)
        except Exception:  # noqa: BLE001
            _logger.warning("bot2: unusable config for bot %s; skipping", bot.id, exc_info=True)
            continue

        ran_today = repo.has_terminal_run_today(user_id, BOT_EXPIRY_INDEX_WRITER)
        decision = bot2.decide(
            bot2.TickContext(
                now=now_ist().replace(tzinfo=None),
                app_started_at=_app_started_at,
                config=config,
                expiring_today=expiring,
                has_session=_has_session(proc, user_id),
                ran_today=ran_today,
                last_nag_at=_last_nag.get(user_id),
            )
        )

        if decision.action == "idle":
            continue

        # Belt and braces. `decide` already refuses to act twice in a day, but this bot
        # places real orders with nobody watching, and "the decision layer will remember"
        # is exactly the kind of convention that fails silently. Anything that WRITES --
        # a run row or an order -- re-checks the day here, so a second fire is structurally
        # impossible rather than merely intended. A nag is exempt: it writes nothing and
        # must keep going until the session shows up.
        if ran_today and decision.action in ("skip", "fire"):
            continue

        if decision.action == "nag":
            _last_nag[user_id] = now_ist().replace(tzinfo=None)
            from icici_breeze_backend.app.services.telegram_alerts import notify_bot_needs_login

            notify_bot_needs_login(user_id, decision.reason_text or "")
            continue

        if decision.action == "skip":
            run_id = repo.start_run(user_id, BOT_EXPIRY_INDEX_WRITER, "schedule")
            repo.finish_run(
                run_id,
                status="skipped",
                reason_code=decision.reason_code or ReasonCode.NOTHING_ELIGIBLE,
                reason_text=decision.reason_text or "",
            )
            continue

        # Read-only mode is checked here rather than in `decide` so that the decision layer
        # stays free of licensing, and so the skip is logged with its own reason.
        if not trading_mutations_allowed():
            run_id = repo.start_run(user_id, BOT_EXPIRY_INDEX_WRITER, "schedule")
            repo.finish_run(
                run_id,
                status="skipped",
                reason_code=ReasonCode.TRADING_READ_ONLY,
                reason_text="Read-only mode — the licence does not currently permit trading.",
            )
            continue

        _fire(proc, user_id, config, decision, expiring)


def _fire(proc, user_id: str, config, decision, expiring: dict[str, str]) -> None:
    trigger = "schedule"
    now = now_ist().replace(tzinfo=None)
    entry = now.replace(
        hour=int(config.entry_time_ist.split(":")[0]),
        minute=int(config.entry_time_ist.split(":")[1]),
        second=0,
        microsecond=0,
    )
    if now > entry + datetime.timedelta(minutes=2):
        # Distinguishable in the log: this fired late because the session only just turned
        # up, not because the schedule slipped.
        trigger = "session_arrival"

    run_id = repo.start_run(user_id, BOT_EXPIRY_INDEX_WRITER, trigger)
    try:
        available = bot2._available_margin(proc, user_id)
        if not available or available <= 0:
            repo.finish_run(
                run_id,
                status="failed",
                reason_code=ReasonCode.BROKER_ERROR,
                reason_text="Could not read available margin from the broker.",
            )
            return

        margin_source = proc.get_strategy_builder_margin_source(user_id)
        results = []
        for index_code in decision.indices:
            results.append(
                bot2.fire_index(
                    proc,
                    user_id,
                    index_code,
                    expiry_display=expiring[index_code],
                    config=config,
                    # Each index gets its own share of the margin read once at the start of
                    # the fire, so a same-day collision is bounded by construction rather
                    # than by whichever index happened to go first.
                    available_margin=available,
                    margin_source=margin_source,
                )
            )

        ok = [r for r in results if r.ok]
        detail = {
            "legs": [
                {
                    "index": r.index_code,
                    "right": r.right,
                    "strike": r.strike_price,
                    "lots": r.lots,
                    "quantity": r.quantity,
                    "entry_price": r.entry_price,
                    "budget": r.budget,
                    "order_ids": r.order_ids,
                    "rule_id": r.rule_id,
                    "error": r.error,
                }
                for r in results
            ]
        }
        if not ok:
            first = results[0] if results else None
            repo.finish_run(
                run_id,
                status="skipped" if (first and first.reason_code == ReasonCode.MARGIN_CAP_TOO_SMALL)
                else "failed",
                reason_code=(first.reason_code if first else ReasonCode.INTERNAL_ERROR),
                reason_text=(first.error if first else "Nothing was traded."),
                detail=detail,
            )
            return
        unprotected = [r for r in ok if r.rule_id is None]
        repo.finish_run(
            run_id,
            status="completed" if not unprotected else "failed",
            reason_code=ReasonCode.ORDERS_PLACED,
            reason_text=(
                "; ".join(
                    f"Sold {r.lots} lot(s) {bot2.INDEX_LABEL.get(r.index_code, r.index_code)} "
                    f"{r.strike_price:g} {'CE' if r.right == 'call' else 'PE'}"
                    for r in ok
                )
                + ("" if not unprotected else " — WITHOUT a stop; see the Order Book.")
            ),
            detail=detail,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("bot2: fire failed for user=%s", user_id)
        repo.finish_run(
            run_id,
            status="failed",
            reason_code=ReasonCode.INTERNAL_ERROR,
            reason_text="The bot failed unexpectedly while trading. Check the Order Book.",
        )


def _loop() -> None:
    while not _stop.is_set():
        try:
            from icici_breeze_backend.app.services.processor import processor

            tick(processor())
        except Exception:  # noqa: BLE001 -- one bad tick must never kill the scheduler
            _logger.exception("bot scheduler tick failed")
        _stop.wait(_TICK_SECONDS)


def start_bot_scheduler() -> None:
    global _thread, _app_started_at
    if _thread and _thread.is_alive():
        return
    _app_started_at = now_ist().replace(tzinfo=None)
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="bot-scheduler", daemon=True)
    _thread.start()
    _logger.info("Bot scheduler started.")


def stop_bot_scheduler() -> None:
    _stop.set()
    global _thread
    _thread = None
