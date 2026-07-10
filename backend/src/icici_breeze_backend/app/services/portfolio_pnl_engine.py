"""Real-time portfolio P&L recalculation engine.

Revalues every tracked option leg on a fixed ~2s clock using only the
conflated WS-tick quote cache written by `ws_tick_pipeline`'s flush worker —
never ICICI REST — so 150-200 open legs can be repriced without touching the
broker's 100 req/min budget. Tracked legs are registered from the live
`processor.get_positions()` response (see `sync_positions_from_response`,
wired from `route_portfolio.get_portfolio_api`); this module never calls the
broker itself.

Target/stop-loss rules come in three tiers: per-leg (`set_leg_rule`) and
whole-portfolio (`set_portfolio_rule`) are in-memory-only extension points
nothing calls yet; group rules (`set_group_rule`, keyed by user+stock+expiry —
the same bucket Hedge and Square Off All group by) back the Portfolio page's
profit/loss-triggered square-off feature and are persisted via
`app.repositories.squareoff_rules` / hydrated into this module's registry at
startup by `app.services.squareoff_dispatcher`.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Callable

import numpy as np

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, parse_strike
from icici_breeze_backend.app.db.redis_client import get_redis
from icici_breeze_backend.app.services import ws_tick_pipeline
from icici_breeze_backend.app.services.reference_data.keys import pnl_quote_key
from icici_breeze_backend.app.services.reference_data.scrip_index import contract_index_key
from icici_breeze_backend.app.services.reference_data.scrip_master_sql import normalize_expiry_display

_logger = logging.getLogger(__name__)

RuleHitHandler = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class PositionLeg:
    """One tracked open option leg. Prices are never stored here — only
    identity + broker-reported quantity/average_price; `ltp` is looked up
    fresh from Redis every P&L tick."""

    user_id: str
    stock_code: str
    exchange_code: str
    expiry_display: str
    strike: Strike
    right: str  # "call" | "put"
    quantity: int
    average_price: float
    action: str  # cfg.BUY | cfg.SELL
    position_action: str = ""
    target_pnl: float | None = None
    stop_loss_pnl: float | None = None

    @property
    def scrip_key(self) -> str:
        return contract_index_key(
            self.exchange_code, self.stock_code, self.expiry_display, self.strike, self.right
        )


def leg_from_position_row(user_id: str, row: dict[str, Any]) -> PositionLeg | None:
    """Convert one raw `processor.get_positions()` row into a PositionLeg, or
    None if the row is malformed/flat (quantity 0)."""
    try:
        quantity = int(float(str(row.get("quantity") or 0)))
        average_price = float(str(row.get("average_price") or 0))
    except (TypeError, ValueError):
        return None
    if quantity == 0:
        return None
    strike = parse_strike(row.get("strike_price"))
    stock_code = str(row.get("stock_code") or "").strip().upper()
    exchange_code = str(row.get("exchange_code") or "").strip().upper()
    right_raw = str(row.get("right") or "").strip().lower()
    right = "call" if right_raw.startswith("c") else "put"
    expiry_display = normalize_expiry_display(str(row.get("expiry_date") or ""))
    action = str(row.get("action") or cfg.BUY)
    if not stock_code or not exchange_code or strike is None or not expiry_display:
        return None
    return PositionLeg(
        user_id=user_id,
        stock_code=stock_code,
        exchange_code=exchange_code,
        expiry_display=expiry_display,
        strike=strike,
        right=right,
        quantity=abs(quantity),
        average_price=average_price,
        action=action,
        position_action=str(row.get("position_action") or ""),
    )


_registry_lock = threading.RLock()
_legs_by_user: dict[str, dict[str, PositionLeg]] = {}
_portfolio_rules: dict[str, dict[str, float | None]] = {}
_rule_hit_listeners: list[RuleHitHandler] = []
_snapshot_lock = threading.RLock()
_latest_snapshots: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True)
class GroupRule:
    """A profit/loss square-off rule scoped to one (stock_code, expiry_display)
    bucket for one user — the same grouping the Portfolio page's Hedge and
    Square Off All actions use."""

    rule_id: str
    user_id: str
    stock_code: str
    expiry_display: str
    exchange_code: str
    target_pnl: float | None
    stop_loss_pnl: float | None


def _group_key(stock_code: str, expiry_display: str) -> str:
    return f"{stock_code.strip().upper()}|{expiry_display.strip()}"


_group_rules: dict[str, dict[str, GroupRule]] = {}


def register_positions(user_id: str, legs: list[PositionLeg]) -> None:
    """Replace the tracked leg set for a user, carrying forward any
    previously-set target/stop-loss rule for legs that still exist."""
    with _registry_lock:
        existing = _legs_by_user.get(user_id, {})
        merged: dict[str, PositionLeg] = {}
        for leg in legs:
            prior = existing.get(leg.scrip_key)
            if prior is not None and (prior.target_pnl is not None or prior.stop_loss_pnl is not None):
                leg = replace(leg, target_pnl=prior.target_pnl, stop_loss_pnl=prior.stop_loss_pnl)
            merged[leg.scrip_key] = leg
        _legs_by_user[user_id] = merged


def clear_positions(user_id: str) -> None:
    with _registry_lock:
        _legs_by_user.pop(user_id, None)
        _portfolio_rules.pop(user_id, None)
    with _snapshot_lock:
        _latest_snapshots.pop(user_id, None)


def sync_positions_from_response(user_id: str, response: dict[str, Any] | None) -> int:
    """Register/refresh tracked legs from a `processor.get_positions()`-shaped
    response. Best-effort: a malformed row is skipped, never raised, so a
    single bad leg can't break the portfolio API response it rides in on."""
    rows: Any = None
    if isinstance(response, dict) and response.get("Status") == 200:
        success = response.get("Success")
        rows = success.get("positions") if isinstance(success, dict) else success
    if not isinstance(rows, list):
        clear_positions(user_id)
        return 0
    legs: list[PositionLeg] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            leg = leg_from_position_row(user_id, row)
        except Exception:
            _logger.debug("Skipping malformed position row for user_id=%s", user_id, exc_info=True)
            leg = None
        if leg is not None:
            legs.append(leg)
    if legs:
        register_positions(user_id, legs)
    else:
        clear_positions(user_id)
    return len(legs)


def set_leg_rule(
    user_id: str, scrip_key: str, *, target_pnl: float | None = None, stop_loss_pnl: float | None = None
) -> bool:
    with _registry_lock:
        leg = _legs_by_user.get(user_id, {}).get(scrip_key)
        if leg is None:
            return False
        _legs_by_user[user_id][scrip_key] = replace(leg, target_pnl=target_pnl, stop_loss_pnl=stop_loss_pnl)
        return True


def set_portfolio_rule(user_id: str, *, target_pnl: float | None = None, stop_loss_pnl: float | None = None) -> None:
    with _registry_lock:
        _portfolio_rules[user_id] = {"target_pnl": target_pnl, "stop_loss_pnl": stop_loss_pnl}


def set_group_rule(
    user_id: str,
    rule_id: str,
    *,
    stock_code: str,
    expiry_display: str,
    exchange_code: str = "NFO",
    target_pnl: float | None = None,
    stop_loss_pnl: float | None = None,
) -> None:
    """Arm (or re-arm) a profit/loss rule for one (stock_code, expiry_display)
    group. Called both from the arm route and from startup hydration."""
    rule = GroupRule(
        rule_id=rule_id,
        user_id=user_id,
        stock_code=stock_code,
        expiry_display=expiry_display,
        exchange_code=exchange_code,
        target_pnl=target_pnl,
        stop_loss_pnl=stop_loss_pnl,
    )
    with _registry_lock:
        _group_rules.setdefault(user_id, {})[_group_key(stock_code, expiry_display)] = rule


def clear_group_rule(user_id: str, stock_code: str, expiry_display: str) -> None:
    with _registry_lock:
        user_rules = _group_rules.get(user_id)
        if user_rules is not None:
            user_rules.pop(_group_key(stock_code, expiry_display), None)


def register_rule_hit_listener(cb: RuleHitHandler) -> None:
    _rule_hit_listeners.append(cb)


def unregister_rule_hit_listener(cb: RuleHitHandler) -> None:
    try:
        _rule_hit_listeners.remove(cb)
    except ValueError:
        pass


def tracked_leg_count() -> int:
    with _registry_lock:
        return sum(len(legs) for legs in _legs_by_user.values())


def latest_snapshot(user_id: str) -> dict[str, Any] | None:
    with _snapshot_lock:
        snap = _latest_snapshots.get(user_id)
    return dict(snap) if snap else None


def _stale_after_seconds() -> float:
    try:
        return max(2.0, float(getattr(cfg, "PNL_STALE_QUOTE_SECONDS", 10.0)))
    except (TypeError, ValueError):
        return 10.0


def is_tick_stream_stale() -> bool:
    """Connection-level circuit breaker: no WS tick at all recently means the
    feed is likely down. We fall back to whatever LTP is still cached in
    Redis and flag `stale=True` — we never fire a REST poll to cover the
    gap (that would blow the 100 req/min budget across 150+ legs)."""
    age = ws_tick_pipeline.last_tick_age_seconds()
    if age is None:
        return True
    return age > _stale_after_seconds()


def _parse_quote_fields(fields: dict[str, str] | None) -> tuple[float | None, float | None]:
    if not fields:
        return None, None
    ltp_raw = fields.get("ltp")
    ts_raw = fields.get("timestamp")
    ltp: float | None
    try:
        ltp = float(ltp_raw) if ltp_raw not in (None, "") else None
    except (TypeError, ValueError):
        ltp = None
    ts: float | None
    try:
        ts = float(ts_raw) if ts_raw not in (None, "") else None
    except (TypeError, ValueError):
        ts = None
    return ltp, ts


def _fetch_quotes(scrip_keys: list[str]) -> dict[str, dict[str, str]]:
    """Single pipelined round trip for every tracked contract's quote hash —
    never a sequential per-key GET."""
    if not scrip_keys:
        return {}
    redis = get_redis()
    pipe = redis.pipeline(transaction=False)
    for key in scrip_keys:
        pipe.hgetall(pnl_quote_key(key))
    results = pipe.execute()
    return {scrip_key: (fields or {}) for scrip_key, fields in zip(scrip_keys, results)}


def _evaluate_user_pnl(
    user_id: str,
    legs: list[PositionLeg],
    quotes_by_key: dict[str, dict[str, str]],
    *,
    stream_stale: bool,
) -> dict[str, Any]:
    n = len(legs)
    quantities = np.empty(n, dtype=np.float64)
    avg_prices = np.empty(n, dtype=np.float64)
    signs = np.empty(n, dtype=np.float64)
    ltps = np.empty(n, dtype=np.float64)
    has_quote = np.zeros(n, dtype=bool)
    quote_ages = [None] * n

    now = time.time()
    stale_after = _stale_after_seconds()
    for idx, leg in enumerate(legs):
        quantities[idx] = leg.quantity
        avg_prices[idx] = leg.average_price
        signs[idx] = -1.0 if leg.action == cfg.SELL else 1.0
        ltp, ts = _parse_quote_fields(quotes_by_key.get(leg.scrip_key))
        if ltp is None:
            ltps[idx] = leg.average_price
        else:
            ltps[idx] = ltp
            has_quote[idx] = True
        if ts is not None:
            quote_ages[idx] = max(0.0, now - ts)

    leg_pnl = signs * (ltps - avg_prices) * quantities
    total_pnl = float(np.sum(leg_pnl))

    leg_results = [
        {
            "scrip_key": leg.scrip_key,
            "stock_code": leg.stock_code,
            "exchange_code": leg.exchange_code,
            "expiry_display": leg.expiry_display,
            "strike": leg.strike,
            "right": leg.right,
            "action": leg.action,
            "quantity": leg.quantity,
            "average_price": leg.average_price,
            "ltp": float(ltps[idx]),
            "pnl": float(leg_pnl[idx]),
            "has_live_quote": bool(has_quote[idx]),
            "quote_age_seconds": quote_ages[idx],
            "quote_stale": quote_ages[idx] is not None and quote_ages[idx] > stale_after,
        }
        for idx, leg in enumerate(legs)
    ]

    return {
        "user_id": user_id,
        "total_pnl": total_pnl,
        "legs": leg_results,
        "stream_stale": stream_stale,
        "computed_at": now,
    }


def _build_squareoff_payload(leg: PositionLeg, *, reason: str, pnl: float) -> dict[str, Any]:
    return {
        "user_id": leg.user_id,
        "action": cfg.SQUAREOFF,
        "position_action": leg.position_action or leg.action,
        "product_type": cfg.OPTIONS,
        "stock_code": leg.stock_code,
        "exchange_code": leg.exchange_code,
        "expiry_date": leg.expiry_display,
        "right": cfg.CALL if leg.right == "call" else cfg.PUT,
        "strike_price": str(leg.strike),
        "quantity": str(leg.quantity),
        "reason": reason,
        "pnl": pnl,
    }


def _build_group_leg_order(leg: PositionLeg, *, pnl: float) -> dict[str, Any]:
    """One leg's *closing* order, resolved to an actual Buy/Sell action (close
    a long with Sell, close a short with Buy — mirrors the frontend's
    `squareOffToOrderPayload`), for the dispatcher to hand straight to
    `processor.place_order(..., aggressive_limit=True)`."""
    close_action = cfg.SELL if leg.action == cfg.BUY else cfg.BUY
    return {
        "scrip_key": leg.scrip_key,
        "product_type": cfg.OPTIONS,
        "stock_code": leg.stock_code,
        "exchange_code": leg.exchange_code,
        "expiry_display": leg.expiry_display,
        "right": cfg.CALL if leg.right == "call" else cfg.PUT,
        "strike_price": str(leg.strike),
        "quantity": str(leg.quantity),
        "action": close_action,
        "pnl": pnl,
    }


def _dispatch_rule_hit(payload: dict[str, Any]) -> None:
    for listener in list(_rule_hit_listeners):
        try:
            listener(payload)
        except Exception:
            _logger.exception("PNL rule-hit listener failed for payload=%s", payload)


def _evaluate_rules(snapshot: dict[str, Any], legs_by_key: dict[str, PositionLeg]) -> None:
    user_id = snapshot["user_id"]
    for leg_result in snapshot["legs"]:
        leg = legs_by_key[leg_result["scrip_key"]]
        pnl = leg_result["pnl"]
        if leg.target_pnl is not None and pnl >= leg.target_pnl:
            _dispatch_rule_hit(_build_squareoff_payload(leg, reason="target_hit", pnl=pnl))
        elif leg.stop_loss_pnl is not None and pnl <= -abs(leg.stop_loss_pnl):
            _dispatch_rule_hit(_build_squareoff_payload(leg, reason="stop_loss_hit", pnl=pnl))

    with _registry_lock:
        rule = _portfolio_rules.get(user_id)
    if rule:
        total = snapshot["total_pnl"]
        target = rule.get("target_pnl")
        stop = rule.get("stop_loss_pnl")
        if target is not None and total >= target:
            _dispatch_rule_hit({
                "user_id": user_id,
                "reason": "portfolio_target_hit",
                "total_pnl": total,
                "legs": [r["scrip_key"] for r in snapshot["legs"]],
            })
        elif stop is not None and total <= -abs(stop):
            _dispatch_rule_hit({
                "user_id": user_id,
                "reason": "portfolio_stop_loss_hit",
                "total_pnl": total,
                "legs": [r["scrip_key"] for r in snapshot["legs"]],
            })

    with _registry_lock:
        group_rules = list(_group_rules.get(user_id, {}).values())
    for group_rule in group_rules:
        key = _group_key(group_rule.stock_code, group_rule.expiry_display)
        matching = [
            r for r in snapshot["legs"]
            if _group_key(r["stock_code"], r["expiry_display"]) == key
        ]
        if not matching:
            continue
        group_total = sum(r["pnl"] for r in matching)
        reason: str | None = None
        if group_rule.target_pnl is not None and group_total >= group_rule.target_pnl:
            reason = "group_target_hit"
        elif group_rule.stop_loss_pnl is not None and group_total <= -abs(group_rule.stop_loss_pnl):
            reason = "group_stop_loss_hit"
        if reason is None:
            continue
        # Pop before dispatch (not after) so a slow/blocking listener can't
        # let the next tick re-evaluate the same rule and double-fire orders.
        with _registry_lock:
            user_rules = _group_rules.get(user_id)
            if user_rules is None or user_rules.get(key) is not group_rule:
                continue  # already fired/cleared by a concurrent tick
            del user_rules[key]
        _dispatch_rule_hit({
            "user_id": user_id,
            "rule_id": group_rule.rule_id,
            "reason": reason,
            "stock_code": group_rule.stock_code,
            "expiry_display": group_rule.expiry_display,
            "total_pnl": group_total,
            "legs": [
                _build_group_leg_order(legs_by_key[r["scrip_key"]], pnl=r["pnl"])
                for r in matching
            ],
        })


def run_pnl_tick() -> None:
    """One full engine tick: single pipelined Redis read for every tracked
    contract across every user, vectorized P&L, then rule evaluation. Called
    off the event loop via `asyncio.to_thread` by `run_pnl_loop`."""
    with _registry_lock:
        snapshot_legs = {user_id: list(legs.values()) for user_id, legs in _legs_by_user.items()}
    if not snapshot_legs:
        return

    all_keys: list[str] = []
    seen: set[str] = set()
    for legs in snapshot_legs.values():
        for leg in legs:
            if leg.scrip_key not in seen:
                seen.add(leg.scrip_key)
                all_keys.append(leg.scrip_key)

    quotes_by_key = _fetch_quotes(all_keys)
    stream_stale = is_tick_stream_stale()

    for user_id, legs in snapshot_legs.items():
        legs_by_key = {leg.scrip_key: leg for leg in legs}
        snapshot = _evaluate_user_pnl(user_id, legs, quotes_by_key, stream_stale=stream_stale)
        with _snapshot_lock:
            _latest_snapshots[user_id] = snapshot
        _evaluate_rules(snapshot, legs_by_key)


def pnl_engine_enabled() -> bool:
    return bool(getattr(cfg, "PNL_ENGINE_ENABLED", True))


def _pnl_engine_interval_seconds() -> float:
    """Read the user-configurable recompute interval (Settings → Advanced),
    fresh every call — this is what makes it live-adjustable without a
    process restart. Falls back to the env-configured default, clamped to
    the same hard bounds, if the settings table can't be read for any
    reason."""
    try:
        from icici_breeze_backend.app.services.pnl_engine_settings import load_pnl_engine_settings

        return float(load_pnl_engine_settings()["pnl_recompute_interval_seconds"])
    except Exception:
        _logger.debug("PNL recompute interval settings lookup failed; using env default", exc_info=True)
        try:
            v = float(getattr(cfg, "PNL_ENGINE_INTERVAL_SECONDS", 2.0))
        except (TypeError, ValueError):
            v = 2.0
        return max(1.0, min(30.0, v))


async def run_pnl_loop() -> None:
    """Persistent P&L evaluation loop on a user-configurable clock (Settings
    → Advanced), read fresh every iteration so changes apply within one
    cycle. Mirrors the `portal_deployment_heartbeat.run_heartbeat_loop`
    idiom exactly: infinite loop, cancelled only by the lifespan's
    `task.cancel()`, `CancelledError` always re-raised, all other errors
    logged and swallowed."""
    _logger.info("Portfolio P&L engine loop started")
    while True:
        try:
            interval = await asyncio.to_thread(_pnl_engine_interval_seconds)
            await asyncio.sleep(interval)
            await asyncio.to_thread(run_pnl_tick)
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception("Portfolio P&L tick failed")


def pnl_engine_stats() -> dict[str, Any]:
    with _registry_lock:
        tracked = sum(len(legs) for legs in _legs_by_user.values())
        users = len(_legs_by_user)
    return {
        "tracked_legs": tracked,
        "tracked_users": users,
        "tick_stream_stale": is_tick_stream_stale(),
        "last_tick_age_seconds": ws_tick_pipeline.last_tick_age_seconds(),
    }
