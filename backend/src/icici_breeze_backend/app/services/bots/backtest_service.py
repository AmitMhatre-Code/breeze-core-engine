"""Running a backtest end to end, for `scripts/scalping_backtest.py` and Bots -> Backtest.

Everything a caller needs between "which bot, which days" and a result: the bot's saved
settings, which indices and strategies Bot 2 is set to trade, the fetch-and-replay backfill
loop, and -- for the two bots whose live sizing is a margin ceiling -- the lot count that
today's margin implies (docs/bots-scalping-plan.md section 8.11).

**Sizing off today's margin** (decided 2026-09-13). Margin has no history, so the iron fly
and Bot 2 cannot size each replayed day the way they size live. Instead, once per run, ICICI's
margin calculator prices one lot at today's levels and the lot count follows from the saved
ceiling (fly) or the saved share of today's free margin (Bot 2). The same count then applies
to every replayed day, which is the approximation, and the run records the basis it used.
"""
from __future__ import annotations

import csv
import datetime
import io
import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.db.bots_migrate import (
    BOT_EXPIRY_INDEX_WRITER,
    BOT_IRON_FLY_SCALPER,
    BOT_MOMENTUM_LONG_SCALPER,
)
from icici_breeze_backend.app.domain.bots import (
    ExpiryIndexWriterConfig,
    IronFlyScalperConfig,
    MomentumLongScalperConfig,
)
from icici_breeze_backend.app.services.bots.backtest_expiry import (
    STRATEGY_RIGHTS,
    expiry_days,
    merge_expiry_results,
    run_expiry_backtest,
)
from icici_breeze_backend.app.services.bots.charges import load_charges
from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime
from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
from icici_breeze_backend.app.services.bots.scalping.backtest import run_backtest
from icici_breeze_backend.app.services.bots.scalping.backtest_fly import DEFAULT_LOTS, run_fly_backtest
from icici_breeze_backend.app.services.bots.scalping.backtest_options import (
    ModelPricer,
    OptionBook,
    RealPricer,
)
from icici_breeze_backend.app.services.bots.scalping.spreads import spread_stats

_logger = logging.getLogger(__name__)

BOT_TYPES = {
    "momentum": BOT_MOMENTUM_LONG_SCALPER,
    "fly": BOT_IRON_FLY_SCALPER,
    "expiry": BOT_EXPIRY_INDEX_WRITER,
}
BOT_LABELS = {
    "momentum": "Bot 3 · Momentum scalper",
    "fly": "Bot 4 · Iron fly",
    "expiry": "Bot 2 · Expiry-day writer",
}
CONFIG_MODELS = {
    "momentum": MomentumLongScalperConfig,
    "fly": IronFlyScalperConfig,
    "expiry": ExpiryIndexWriterConfig,
}
MAX_BACKFILL_ROUNDS = 25


class NoCachedData(ValueError):
    """Nothing cached for the range; fetch first."""


class LotPricingError(RuntimeError):
    """Today's margin could not size the run."""


class AwaitingData(RuntimeError):
    """The replay needs option prices that are not cached yet."""


@dataclass(frozen=True)
class Scope:
    """One Bot 2 index and the strategies to replay on it, with a lot count per strategy."""

    index: str
    strategies: tuple[str, ...]
    lots: Mapping[str, int] = field(default_factory=dict)
    margin_per_lot: Mapping[str, Optional[float]] = field(default_factory=dict)


# --------------------------------------------------------------------------------------
# Settings and scope
# --------------------------------------------------------------------------------------


def saved_config(bot: str, user_id: str) -> Any:
    from icici_breeze_backend.app.repositories import bots as repo

    return CONFIG_MODELS[bot](**repo.get_or_create_bot(user_id, BOT_TYPES[bot]).config)


def expiry_scope(config: ExpiryIndexWriterConfig) -> list[Scope]:
    """Each index enabled in Bot 2's settings with its shortlist, in the bot's priority order."""
    legs = sorted(config.indices.items(), key=lambda kv: kv[1].priority)
    return [Scope(index, tuple(leg.strategies)) for index, leg in legs if leg.enabled]


def indices_for(bot: str, config: Any, scopes: Optional[Sequence[Scope]] = None) -> list[str]:
    if bot != "expiry":
        return ["NIFTY"]
    return [s.index for s in (scopes if scopes is not None else expiry_scope(config))]


def holidays() -> set[datetime.date]:
    try:
        from icici_breeze_backend.app.services.market_calendar import get_calendar_config

        return {datetime.date.fromisoformat(d) for d in get_calendar_config().holidays}
    except Exception:  # noqa: BLE001 -- a missing calendar only costs holiday-shifted expiries
        _logger.warning("backtest: exchange calendar unavailable; expiries are not holiday-shifted")
        return set()


def clip_range(
    from_date: Optional[datetime.date], to_date: Optional[datetime.date], today: datetime.date
) -> tuple[datetime.date, datetime.date]:
    """The range to replay, floored at HISTORY_START and defaulting to yesterday."""
    start = max(from_date or regime.HISTORY_START, regime.HISTORY_START)
    end = to_date or today - datetime.timedelta(days=1)
    if start > end:
        raise ValueError(f"The range is empty: {start} is after {end}.")
    return start, end


# --------------------------------------------------------------------------------------
# Replay and backfill
# --------------------------------------------------------------------------------------


def replay(
    bot: str,
    *,
    start: datetime.date,
    end: datetime.date,
    config: Any,
    pricer: Any,
    lots: Optional[int] = None,
    scopes: Optional[Sequence[Scope]] = None,
    holidays_: Optional[set[datetime.date]] = None,
    path: Optional[str] = None,
) -> Any:
    hol = holidays() if holidays_ is None else holidays_
    charges, spread = load_charges(), spread_stats()
    vix = store.load_vix(path=path)
    if bot == "expiry":
        scopes = list(scopes if scopes is not None else expiry_scope(config))
        results, lots_shown = [], {}
        for scope in scopes:
            lots_shown.update({f"{scope.index} {s}": scope.lots.get(s, 1) for s in scope.strategies})
            strategies = [s for s in scope.strategies if scope.lots.get(s, 1) > 0]
            if not strategies:
                continue
            results.append(
                run_expiry_backtest(
                    index=scope.index,
                    days=expiry_days(scope.index, start, end, hol),
                    spot_bars=store.load_candles(
                        stock_code=scope.index, from_date=start, to_date=end, table="spot_candles", path=path
                    ),
                    config=config,
                    charges=charges,
                    spread=spread,
                    strategies=strategies,
                    pricer=pricer,
                    lots={s: scope.lots.get(s, 1) for s in strategies},
                    vix_by_day=vix,
                )
            )
        merged = merge_expiry_results(results, price_source=pricer.source, spread_source=spread.describe())
        merged.lots = lots_shown
        return merged

    futures = store.load_candles(from_date=start, to_date=end, path=path)
    if not futures:
        raise NoCachedData(f"No NIFTY futures bars are cached for {start} to {end}. Fetch data first.")
    spot = store.load_candles(from_date=start, to_date=end, table="spot_candles", path=path)
    if bot == "fly":
        return run_fly_backtest(
            futures, config=config, charges=charges, spread=spread, vix_by_day=vix,
            spot_bars=spot, pricer=pricer, lots=lots or DEFAULT_LOTS, holidays=hol,
        )
    return run_backtest(
        futures, config=config, charges=charges, spread=spread, vix_by_day=vix,
        spot_bars=spot, pricer=pricer, holidays=hol,
    )


def fetch_underlying(
    fetcher: Any, bot: str, start: datetime.date, end: datetime.date, indices: Sequence[str]
) -> None:
    """Futures (Bot 3/4's signal and re-entry gate), each index's cash bars, and VIX."""
    if bot != "expiry":
        fetcher.fetch_futures("NIFTY", start, end)
    for index in indices:
        fetcher.fetch_spot(index, start, end)
    cached = store.load_vix(path=fetcher.path)
    missing = [d for d in regime.trading_days(start, end, fetcher.holidays) if d not in cached]
    if missing:
        fetcher.fetch_vix(min(missing), max(missing))


def backfill(
    fetcher: Any,
    bot: str,
    *,
    start: datetime.date,
    end: datetime.date,
    config: Any,
    lots: Optional[int] = None,
    scopes: Optional[Sequence[Scope]] = None,
    log: Callable[[str], None] = print,
    max_rounds: int = MAX_BACKFILL_ROUNDS,
) -> dict[str, Any]:
    """Replay on real prices, fetch what it lacked, repeat (plan section 8.7).

    Raises `Stopped` from the fetcher on a cancel, the market opening, or a spent budget --
    everything fetched until then is already stored, so running it again resumes.
    """
    path = fetcher.path
    for round_no in range(1, max_rounds + 1):
        book = OptionBook(path)
        try:
            result = replay(
                bot, start=start, end=end, config=config, pricer=RealPricer(book),
                lots=lots, scopes=scopes, holidays_=fetcher.holidays, path=path,
            )
        except NoCachedData as exc:
            return {"complete": False, "rounds": round_no, "message": str(exc)}
        store.add_needs(book.needs, path=path)
        pending = store.pending_needs(path=path)
        if not pending:
            waiting = result.summary().get("days_awaiting_data", 0)
            tail = f" {waiting} day(s) still wait for data." if waiting else ""
            return {
                "complete": not waiting,
                "rounds": round_no,
                "message": f"Every option contract the replay needs is cached.{tail}",
            }
        log(f"Round {round_no}: fetching {len(pending)} option windows")
        stats = fetcher.fetch_needs(pending)
        if stats["fetched"] == 0:
            return {
                "complete": False,
                "rounds": round_no,
                "message": "No progress this round: every request errored. See the log.",
            }
    return {
        "complete": False,
        "rounds": max_rounds,
        "message": f"Stopped after {max_rounds} rounds; fetch again to continue.",
    }


# --------------------------------------------------------------------------------------
# Sizing off today's margin
# --------------------------------------------------------------------------------------


def nearest_expiry_for(proc: Any, stock_code: str, exchange: str, today: datetime.date) -> Optional[str]:
    """The nearest listed expiry on or after `today`, as DD-MMM-YYYY -- the scrip master's."""
    from icici_breeze_backend.app.services.reference_data.scrip_master_sql import (
        _expiry_api_to_display,
    )

    best: Optional[datetime.date] = None
    for entry in proc.fetch_stock_codes(exchange) or []:
        if str(entry.get("stock_code") or "").strip().upper() != stock_code:
            continue
        for raw in entry.get("expiry_dates") or []:
            try:
                d = datetime.datetime.strptime(_expiry_api_to_display(str(raw)), "%d-%b-%Y").date()
            except (TypeError, ValueError):
                continue
            if d >= today and (best is None or d < best):
                best = d
    return best.strftime("%d-%b-%Y") if best else None


def _latest_vix(path: Optional[str]) -> Optional[float]:
    vix = store.load_vix(path=path)
    return vix[max(vix)] if vix else None


def _spot_and_contract(proc: Any, index: str, exchange: str, today: datetime.date, path: Optional[str]) -> tuple[float, str, int]:
    spot = store.latest_close(index, path=path)
    if not spot:
        raise LotPricingError(f"No cached {index} index bars to pick strikes from. Fetch data first.")
    expiry = nearest_expiry_for(proc, index, exchange, today)
    if not expiry:
        raise LotPricingError(f"No {index} expiry is listed in the scrip master.")
    lot_size = int(proc.fetch_lot_size(index, expiry, exchange_code=exchange) or 0)
    if lot_size <= 0:
        raise LotPricingError(f"No lot size for {index} {expiry} in the scrip master.")
    return spot, expiry, lot_size


def price_lots(
    bot: str,
    config: Any,
    user_id: str,
    proc: Any,
    *,
    path: Optional[str] = None,
    today: Optional[datetime.date] = None,
) -> dict[str, Any]:
    """Lots for a fly or Bot 2 run, from ICICI's margin for one lot today."""
    today = today or now_ist().date()
    if bot == "fly":
        return _price_fly(config, user_id, proc, path, today)
    if bot == "expiry":
        return _price_writer(config, user_id, proc, path, today)
    raise ValueError(f"{bot} is not sized by margin")


def _price_fly(config: IronFlyScalperConfig, user_id: str, proc: Any, path: Optional[str], today: datetime.date) -> dict[str, Any]:
    from icici_breeze_backend.app.services.bots.scalping.iron_fly_bot import wing_width_for
    from icici_breeze_backend.app.services.bots.scalping.margin import margin_for_mixed_legs

    spot, expiry, lot_size = _spot_and_contract(proc, "NIFTY", cfg.NFO, today, path)
    width = wing_width_for(config, _latest_vix(path))
    atm = regime.atm_strike(spot, "NIFTY")
    # Rights and actions exactly as `iron_fly_bot.size_fly` sends them.
    legs = [
        ("call", atm + width, lot_size, cfg.BUY),
        ("put", atm - width, lot_size, cfg.BUY),
        ("call", atm, lot_size, cfg.SELL),
        ("put", atm, lot_size, cfg.SELL),
    ]
    per_lot = margin_for_mixed_legs(
        proc, user_id, exchange_code=cfg.NFO, stock_code="NIFTY", expiry_display=expiry, legs=legs
    )
    if per_lot is None:
        raise LotPricingError("ICICI's margin calculator did not price the fly.")
    lots = int(config.margin_ceiling_inr // per_lot)
    if lots < config.min_lots:
        raise LotPricingError(
            f"One lot of the fly needs ₹{per_lot:,.0f} today, above the "
            f"₹{config.margin_ceiling_inr:,.0f} margin ceiling."
        )
    return {
        "lots": lots,
        "margin_per_lot": round(per_lot, 2),
        "priced_on": today.isoformat(),
        "describe": (
            f"{lots} lots: one lot of today's {int(atm)} fly (wings ±{int(width)}, {expiry}) "
            f"needs ₹{per_lot:,.0f} against the ₹{config.margin_ceiling_inr:,.0f} ceiling."
        ),
    }


def _price_writer(config: ExpiryIndexWriterConfig, user_id: str, proc: Any, path: Optional[str], today: datetime.date) -> dict[str, Any]:
    from icici_breeze_backend.app.services.bots.expiry_index_writer import (
        INDEX_EXCHANGE,
        _available_margin,
        margin_for_legs,
    )

    scopes = expiry_scope(config)
    if not scopes:
        raise LotPricingError("No index is enabled in Bot 2's settings.")
    available = _available_margin(proc, user_id)
    if not available:
        raise LotPricingError("Could not read today's free margin from ICICI.")
    priced: list[Scope] = []
    lines: list[str] = []
    for scope in scopes:
        leg_cfg = config.indices[scope.index]
        exchange = INDEX_EXCHANGE.get(scope.index, cfg.NFO)
        spot, expiry, lot_size = _spot_and_contract(proc, scope.index, exchange, today, path)
        budget = available * leg_cfg.margin_pct_cap / 100.0
        strikes = {
            cfg.CALL: regime.strike_beyond(spot * (1 + leg_cfg.safety_pct_ce / 100.0), scope.index, up=True),
            cfg.PUT: regime.strike_beyond(spot * (1 - leg_cfg.safety_pct_pe / 100.0), scope.index, up=False),
        }
        lots: dict[str, int] = {}
        margins: dict[str, Optional[float]] = {}
        for strategy in scope.strategies:
            rights = [cfg.CALL if r == "call" else cfg.PUT for r in STRATEGY_RIGHTS[strategy]]
            margin = margin_for_legs(
                proc, user_id, exchange_code=exchange, stock_code=scope.index,
                expiry_display=expiry, legs=[(r, strikes[r], lot_size) for r in rights],
            )
            margins[strategy] = round(margin, 2) if margin else None
            lots[strategy] = int(budget // margin) if margin else 0
            lines.append(
                f"{scope.index} {strategy}: "
                + (f"{lots[strategy]} lots at ₹{margin:,.0f} a lot" if margin else "not priced, skipped")
            )
        priced.append(Scope(scope.index, scope.strategies, lots, margins))
    return {
        "scopes": priced,
        "priced_on": today.isoformat(),
        "describe": f"Free margin ₹{available:,.0f} today. " + "; ".join(lines) + ".",
    }


# --------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------


def trade_rows(result: Any) -> list[dict[str, Any]]:
    rows = getattr(result, "cycles", None)
    if rows is None:
        rows = getattr(result, "trades", [])
    return [json.loads(json.dumps(asdict(r), default=str)) for r in rows]


def trades_csv(trades: Sequence[Mapping[str, Any]]) -> str:
    if not trades:
        return ""
    fields = list(trades[0])
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    for row in trades:
        writer.writerow(
            {k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in row.items()}
        )
    return out.getvalue()


def coverage_summary(path: Optional[str] = None) -> dict[str, Any]:
    cov = store.coverage(stock_code="NIFTY", path=path)
    sensex = store.day_bar_counts(stock_code="BSESEN", table="spot_candles", path=path)
    nifty_index = store.day_bar_counts(stock_code="NIFTY", table="spot_candles", path=path)
    futures = store.day_bar_counts(stock_code="NIFTY", table="futures_candles", path=path)

    def span(days: Mapping[datetime.date, int]) -> dict[str, Any]:
        return {
            "days": len(days),
            "from": min(days).isoformat() if days else None,
            "to": max(days).isoformat() if days else None,
            "short_days": sum(1 for n in days.values() if n < store.COMPLETE_DAY_BARS),
        }

    return {
        "nifty_futures": span(futures),
        "nifty_index": span(nifty_index),
        "sensex_index": span(sensex),
        "vix": {"days": cov["vix_days"], "from": cov["vix_from"], "to": cov["vix_to"]},
        "option_contracts": cov["option_contracts"],
        "option_bars": cov["option_bars"],
        "option_windows_empty": cov["option_windows_empty"],
        "option_needs_pending": cov["option_needs_pending"],
    }


def saved_summary(user_id: str) -> dict[str, Any]:
    """What each bot's saved settings will test, in the words the Bots cards use."""
    momentum = saved_config("momentum", user_id)
    fly = saved_config("fly", user_id)
    writer = saved_config("expiry", user_id)

    def windows(config: Any) -> str:
        return ", ".join(f"{w.start}–{w.end}" for w in config.sessions)

    return {
        "momentum": {
            "label": BOT_LABELS["momentum"],
            "lines": [
                f"₹{momentum.premium_outlay_inr:,.0f} premium outlay, ATM option",
                f"Ladder: stop {momentum.exits.stop_loss_pts:g} pts, runner at {momentum.exits.target_pts:g} pts, "
                f"time stop {momentum.exits.time_invalidation_seconds}s",
                f"Windows {windows(momentum)}",
            ],
        },
        "fly": {
            "label": BOT_LABELS["fly"],
            "lines": [
                f"Lots from today's margin against the ₹{fly.margin_ceiling_inr:,.0f} ceiling",
                f"Wings ±{fly.structure.wing_width_points:g}, book at {fly.exits.target_decay_pct:g}% decay, "
                f"drift stop {fly.exits.max_spot_drift_pct:g}%",
                f"Windows {windows(fly)}",
            ],
        },
        "expiry": {
            "label": BOT_LABELS["expiry"],
            "lines": [
                f"{s.index}: {', '.join(s.strategies)} · safety CE {writer.indices[s.index].safety_pct_ce:g}% / "
                f"PE {writer.indices[s.index].safety_pct_pe:g}% · {writer.indices[s.index].margin_pct_cap:g}% of free margin"
                for s in expiry_scope(writer)
            ]
            or ["No index is enabled in Bot 2's settings, so there is nothing to backtest."],
            "enabled": bool(expiry_scope(writer)),
        },
    }


# --------------------------------------------------------------------------------------
# Simulation against backtest
# --------------------------------------------------------------------------------------


def compare(
    bot: str, day: datetime.date, user_id: str, *, model: bool = False, path: Optional[str] = None
) -> dict[str, Any]:
    from icici_breeze_backend.app.services.bots.scalping.backtest_compare import (
        compare_payload,
        load_paper_cycles,
        run_config_hashes,
    )
    from icici_breeze_backend.app.services.bots.scalping.evidence import material_config_hash

    bot_type = BOT_TYPES[bot]
    paper = load_paper_cycles(bot_type, day, user_id=user_id)
    if not paper:
        raise LookupError(f"No simulation cycles for {BOT_LABELS[bot]} on {day}.")
    config = saved_config(bot, user_id)
    lots = None
    if bot == "fly":
        # Sized like the simulation session was; the fly's live sizing has no history.
        counts = Counter(p.lots for p in paper if p.lots)
        lots = counts.most_common(1)[0][0] if counts else None
    pricer = ModelPricer() if model else RealPricer(OptionBook(path))
    result = replay(bot, start=day, end=day, config=config, pricer=pricer, lots=lots, path=path)
    if result.days_awaiting_data:
        store.add_needs(pricer.book.needs, path=path)
        raise AwaitingData(
            f"Option prices for {day} are not cached yet. Fetch data for {BOT_LABELS[bot]} "
            f"from {day} to {day}, then compare again."
        )
    return compare_payload(
        day,
        paper,
        result.cycles,
        config_hash_now=material_config_hash(bot_type, config.model_dump(mode="json")),
        config_hashes_then=run_config_hashes(bot_type, day),
        price_source=result.price_source,
        lots=lots,
    )
