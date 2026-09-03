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
from icici_breeze_backend.app.db.bots_migrate import (
    BOT_EXPIRY_INDEX_WRITER,
    BOT_HOLDINGS_WRITER,
)
from icici_breeze_backend.app.domain.bots import (
    ExpiryIndexWriterConfig,
    HoldingsWriterConfig,
    ReasonCode,
)
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots import expiry_index_writer as bot2
from icici_breeze_backend.app.services.bots import hitl
from icici_breeze_backend.app.services.bots import holdings_writer as bot1

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
    """One sweep. Safe to call directly in tests.

    Iterates USERS, not bot types, because cross-bot priority is a per-user ordering: on a
    day both bots want to trade, the lower priority number sizes and places first and the
    other sizes against whatever margin is genuinely left. Sweeping by type would let
    whichever type happened to be swept first take the capital regardless of the ordering
    the user configured.
    """
    from icici_breeze_backend.app.services.deployment_license_status import (
        trading_mutations_allowed,
    )

    repo.reap_stale_runs(older_than_minutes=_STALE_RUN_MINUTES)

    by_user = repo.list_enabled_bots_by_user()
    if not by_user:
        return

    needs_expiries = any(
        bot.bot_type == BOT_EXPIRY_INDEX_WRITER for bots in by_user.values() for bot in bots
    )
    expiring = _expiring_today(proc) if needs_expiries else {}
    allowed = trading_mutations_allowed()

    for user_id, bots in by_user.items():
        has_session = _has_session(proc, user_id)
        # Margin this user's higher-priority bots have already committed in THIS sweep.
        committed = 0.0
        for bot in bots:
            try:
                if bot.bot_type == BOT_EXPIRY_INDEX_WRITER:
                    committed += _tick_index_writer(
                        proc,
                        user_id,
                        bot,
                        expiring=expiring,
                        has_session=has_session,
                        trading_allowed=allowed,
                        margin_committed=committed,
                    )
                elif bot.bot_type == BOT_HOLDINGS_WRITER:
                    committed += _tick_holdings_writer(
                        proc,
                        user_id,
                        bot,
                        has_session=has_session,
                        trading_allowed=allowed,
                        margin_committed=committed,
                    )
            except Exception:  # noqa: BLE001 -- one user's bot must not stop the sweep
                _logger.exception(
                    "bot scheduler: %s failed for user=%s", bot.bot_type, user_id
                )


def _log_skip(user_id: str, bot_type: str, reason_code: str, reason_text: str) -> None:
    run_id = repo.start_run(user_id, bot_type, "schedule")
    repo.finish_run(
        run_id, status="skipped", reason_code=reason_code, reason_text=reason_text
    )


_READ_ONLY_TEXT = "Read-only mode — the licence does not currently permit trading."


def _tick_holdings_writer(
    proc: Any,
    user_id: str,
    bot,
    *,
    has_session: bool,
    trading_allowed: bool,
    margin_committed: float,
) -> float:
    """Bot 1's unattended path. Returns the margin it committed."""
    from icici_breeze_backend.app.services.bots import holdings_runner

    try:
        config = HoldingsWriterConfig(**bot.config)
    except Exception:  # noqa: BLE001
        _logger.warning("bot1: unusable config for bot %s; skipping", bot.id, exc_info=True)
        return 0.0

    # In `telegram` mode a proposal is an ask, not an act, so the day-gate has to be the
    # narrower one -- otherwise the first proposal would count as the bot having run and
    # neither the re-ask nor the eventual placement could ever happen.
    telegram_mode = hitl.is_telegram_mode(config)
    ran_today = (
        repo.has_committed_run_today(user_id, BOT_HOLDINGS_WRITER)
        if telegram_mode
        else repo.has_terminal_run_today(user_id, BOT_HOLDINGS_WRITER)
    )
    decision = bot1.decide(
        bot1.TickContext(
            now=now_ist().replace(tzinfo=None),
            app_started_at=_app_started_at,
            config=config,
            is_firing_day=_is_holdings_firing_day(proc, user_id, config),
            has_session=has_session,
            ran_today=ran_today,
            last_nag_at=_last_nag.get(f"{user_id}:{BOT_HOLDINGS_WRITER}"),
        )
    )

    if decision.action == "idle":
        return 0.0
    # Belt and braces, as for Bot 2: anything that WRITES re-checks the day here, so a
    # second fire is structurally impossible rather than merely intended. A nag is exempt --
    # it writes nothing and must keep going until the session appears.
    if ran_today and decision.action in ("skip", "fire"):
        return 0.0

    if decision.action == "nag":
        _last_nag[f"{user_id}:{BOT_HOLDINGS_WRITER}"] = now_ist().replace(tzinfo=None)
        from icici_breeze_backend.app.services.telegram_alerts import notify_bot_needs_login

        notify_bot_needs_login(user_id, decision.reason_text or "")
        return 0.0

    if decision.action == "skip":
        _log_skip(
            user_id,
            BOT_HOLDINGS_WRITER,
            decision.reason_code or ReasonCode.NOTHING_ELIGIBLE,
            decision.reason_text or "",
        )
        return 0.0

    if not trading_allowed:
        _log_skip(user_id, BOT_HOLDINGS_WRITER, ReasonCode.TRADING_READ_ONLY, _READ_ONLY_TEXT)
        return 0.0

    if telegram_mode:
        if hitl.next_action(
            user_id, BOT_HOLDINGS_WRITER, config, now=now_ist().replace(tzinfo=None)
        ) != "propose":
            return 0.0
        return holdings_runner.fire_autonomous(
            proc, user_id, config, margin_committed=margin_committed, propose_only=True
        )

    return holdings_runner.fire_autonomous(
        proc, user_id, config, margin_committed=margin_committed
    )


def _is_holdings_firing_day(proc: Any, user_id: str, config: HoldingsWriterConfig) -> bool:
    """Is today N trading days before the target monthly expiry?

    The expiry comes from the scrip master rather than a rule of thumb, for the same reason
    Bot 2 reads it there: stock-option expiries have moved before, and the calendar only
    knows holidays.
    """
    from icici_breeze_backend.app.services.bots.holdings_writer import (
        _monthly_expiries,
        _parse_expiry,
        firing_date,
    )

    today = now_ist().date()
    try:
        universe = proc.fetch_stock_codes(cfg.NFO) or []
    except Exception:  # noqa: BLE001
        _logger.warning("bot1: could not read the NFO universe", exc_info=True)
        return False

    # Any F&O stock's monthly ladder will do -- they share the monthly expiry date, and this
    # only needs the DATE, not a per-scrip contract.
    for entry in universe:
        expiries = _monthly_expiries(entry.get("expiry_dates") or [])
        if not expiries:
            continue
        wanted = 1 if config.expiry_preference == "next" else 0
        if len(expiries) <= wanted:
            continue
        expiry = _parse_expiry(expiries[wanted])
        if expiry is None:
            continue
        return firing_date(expiry, config.fire_days_before_expiry) == today
    return False


def _tick_index_writer(
    proc: Any,
    user_id: str,
    bot,
    *,
    expiring: dict[str, str],
    has_session: bool,
    trading_allowed: bool,
    margin_committed: float,
) -> float:
    """Bot 2's unattended path. Returns the margin it committed."""
    try:
        config = ExpiryIndexWriterConfig(**bot.config)
    except Exception:  # noqa: BLE001
        _logger.warning("bot2: unusable config for bot %s; skipping", bot.id, exc_info=True)
        return 0.0

    # See `_tick_holdings_writer`: proposing is not acting, so semi-autonomous mode gates
    # the day on what was actually committed.
    telegram_mode = hitl.is_telegram_mode(config)
    ran_today = (
        repo.has_committed_run_today(user_id, BOT_EXPIRY_INDEX_WRITER)
        if telegram_mode
        else repo.has_terminal_run_today(user_id, BOT_EXPIRY_INDEX_WRITER)
    )
    decision = bot2.decide(
        bot2.TickContext(
            now=now_ist().replace(tzinfo=None),
            app_started_at=_app_started_at,
            config=config,
            expiring_today=expiring,
            has_session=has_session,
            ran_today=ran_today,
            last_nag_at=_last_nag.get(user_id),
        )
    )

    if decision.action == "idle":
        return 0.0

    # Belt and braces. `decide` already refuses to act twice in a day, but this bot places
    # real orders with nobody watching, and "the decision layer will remember" is exactly
    # the kind of convention that fails silently. Anything that WRITES -- a run row or an
    # order -- re-checks the day here, so a second fire is structurally impossible rather
    # than merely intended. A nag is exempt: it writes nothing and must keep going until
    # the session shows up.
    if ran_today and decision.action in ("skip", "fire"):
        return 0.0

    if decision.action == "nag":
        _last_nag[user_id] = now_ist().replace(tzinfo=None)
        from icici_breeze_backend.app.services.telegram_alerts import notify_bot_needs_login

        notify_bot_needs_login(user_id, decision.reason_text or "")
        return 0.0

    if decision.action == "skip":
        _log_skip(
            user_id,
            BOT_EXPIRY_INDEX_WRITER,
            decision.reason_code or ReasonCode.NOTHING_ELIGIBLE,
            decision.reason_text or "",
        )
        return 0.0

    # Read-only mode is checked here rather than in `decide` so that the decision layer
    # stays free of licensing, and so the skip is logged with its own reason.
    if not trading_allowed:
        _log_skip(
            user_id, BOT_EXPIRY_INDEX_WRITER, ReasonCode.TRADING_READ_ONLY, _READ_ONLY_TEXT
        )
        return 0.0

    if telegram_mode:
        if hitl.next_action(
            user_id, BOT_EXPIRY_INDEX_WRITER, config, now=now_ist().replace(tzinfo=None)
        ) != "propose":
            return 0.0
        _propose_index(proc, user_id, config, decision, expiring, margin_committed=margin_committed)
        return 0.0

    return _fire(proc, user_id, config, decision, expiring, margin_committed=margin_committed)


def _propose_index(
    proc,
    user_id: str,
    config,
    decision,
    expiring: dict[str, str],
    *,
    margin_committed: float = 0.0,
) -> None:
    """Bot 2's semi-autonomous path: size the trade, then ask instead of placing.

    Deliberately `plan_index` and not `fire_index` -- the latter plans *and* executes, and
    the whole point here is to stop in between. Sizing is otherwise identical to `_fire`,
    including the margin split across indices, so the proposal is exactly the trade the
    autonomous bot would have made.
    """
    run_id = repo.start_run(user_id, BOT_EXPIRY_INDEX_WRITER, "schedule")
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
        available = max(0.0, available - float(margin_committed))
        margin_source = proc.get_strategy_builder_margin_source(user_id)

        from icici_breeze_backend.app.services.bots import proposals as svc

        legs = []
        errors = []
        for index_code in decision.indices:
            plan = bot2.plan_index(
                proc,
                user_id,
                index_code,
                expiry_display=expiring[index_code],
                config=config,
                available_margin=available,
                margin_source=margin_source,
            )
            if plan.error or not plan.legs:
                errors.append(f"{index_code}: {plan.error or 'nothing sized'}")
                continue
            legs.extend(svc.plan_to_legs(plan, index_code))

        if not legs:
            repo.finish_run(
                run_id,
                status="skipped",
                reason_code=ReasonCode.NOTHING_ELIGIBLE,
                reason_text="; ".join(errors) or "Nothing could be sized today.",
            )
            return

        hitl.propose(
            user_id,
            BOT_EXPIRY_INDEX_WRITER,
            run_id=run_id,
            legs=legs,
            totals=svc.index_totals(legs),
            ttl_minutes=_index_proposal_ttl(config),
            detail={"errors": errors} if errors else None,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("bot2: proposal failed for user=%s", user_id)
        repo.finish_run(
            run_id,
            status="failed",
            reason_code=ReasonCode.INTERNAL_ERROR,
            reason_text="The bot failed unexpectedly while sizing the trade.",
        )


def _index_proposal_ttl(config) -> int:
    """Bot 2 has no `proposal_ttl_minutes` of its own -- its ask is paced by the same nag
    interval that governs everything else in its morning window, so a proposal stays valid
    exactly until the next one would be sent."""
    return int(config.nag_interval_minutes)


def _fire(
    proc,
    user_id: str,
    config,
    decision,
    expiring: dict[str, str],
    *,
    margin_committed: float = 0.0,
) -> float:
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
            return 0.0
        # Whatever a higher-priority bot already committed in this sweep is gone as far as
        # this one is concerned, so its per-index cap applies to the remainder.
        available = max(0.0, available - float(margin_committed))

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
                    "strategy": r.strategy,
                    "considered": r.considered,
                    "right": r.right,
                    "strike": r.strike_price,
                    "legs": r.legs,
                    "premium_total": r.premium_total,
                    "margin_total": r.margin_total,
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
            return 0.0
        unprotected = [r for r in ok if r.rule_id is None]
        repo.finish_run(
            run_id,
            status="completed" if not unprotected else "failed",
            reason_code=ReasonCode.ORDERS_PLACED,
            reason_text=(
                "; ".join(
                    _describe(r)
                    for r in ok
                )
                + ("" if not unprotected else " — WITHOUT a stop; see the Order Book.")
            ),
            detail=detail,
        )
        return round(sum(float(r.margin_total or 0) for r in ok), 2)
    except Exception:  # noqa: BLE001
        _logger.exception("bot2: fire failed for user=%s", user_id)
        repo.finish_run(
            run_id,
            status="failed",
            reason_code=ReasonCode.INTERNAL_ERROR,
            reason_text="The bot failed unexpectedly while trading. Check the Order Book.",
        )
        return 0.0


def _describe(result) -> str:
    """One line naming what was actually sold.

    Built from the legs rather than a single strike, because a strangle has two of them and
    a run log that said only "NIFTY" would leave the user unable to tell which shape fired.
    """
    contracts = " / ".join(
        f"{leg['strike_price']:g} {'CE' if leg['right'] == 'call' else 'PE'}"
        for leg in (result.legs or [])
    )
    if not contracts and result.strike_price is not None:
        contracts = (
            f"{result.strike_price:g} {'CE' if result.right == 'call' else 'PE'}"
        )
    index = bot2.INDEX_LABEL.get(result.index_code, result.index_code)
    return f"Sold {result.lots} lot(s) {index} {contracts}".strip()


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
