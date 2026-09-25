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
from collections import Counter, OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.db.bots_migrate import (
    BOT_CAS_BINGO,
    BOT_EXPIRY_INDEX_WRITER,
    BOT_IRON_FLY_SCALPER,
    BOT_MOMENTUM_LONG_SCALPER,
)
from icici_breeze_backend.app.domain.bots import (
    CasBingoConfig,
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
    "cas": BOT_CAS_BINGO,
}
BOT_LABELS = {
    "momentum": "Bot 3 · Long Scalper",
    "fly": "Bot 4 · Intraday Iron Fly",
    "expiry": "Bot 2 · Expiry-day writer",
    "cas": "Bot 5 · CAS Bingo",
}
CONFIG_MODELS = {
    "momentum": MomentumLongScalperConfig,
    "fly": IronFlyScalperConfig,
    "expiry": ExpiryIndexWriterConfig,
    "cas": CasBingoConfig,
}
SLUG_FOR_BOT_TYPE = {bot_type: slug for slug, bot_type in BOT_TYPES.items()}
MAX_BACKFILL_ROUNDS = 25
# How far back to look for a run on the current settings. A user comparing variations makes a
# handful of runs, not hundreds, and this is read while a confirmation dialog opens.
EVIDENCE_RUN_SCAN = 50


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
    if bot == "cas":
        return list(config.enabled_indices())
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
    """The range to replay, defaulting to HISTORY_START..yesterday. Not floored (#36)."""
    start = from_date or regime.HISTORY_START
    end = to_date or today - datetime.timedelta(days=1)
    if start > end:
        raise ValueError(f"The range is empty: {start} is after {end}.")
    return start, end


# The four choices the card's backtest dialog offers, and nothing else (#36).
PERIODS = ("last_day", "last_week", "last_month", "custom")
PERIOD_LABELS = {
    "last_day": "Last trading day",
    "last_week": "Last trading week",
    "last_month": "Last trading month",
    "custom": "Custom range",
}
# A session is over, and replayable, once the continuous session has closed.
SESSION_CLOSE = datetime.time(15, 30)
WEEK_SESSIONS = 5


def last_completed_session(now: datetime.datetime, holidays_: set[datetime.date]) -> datetime.date:
    """The most recent trading day whose session has closed: today after 15:30 on a trading
    day, otherwise the trading day before."""
    today = now.date()
    if regime.is_trading_day(today, holidays_) and now.time() >= SESSION_CLOSE:
        return today
    day = today - datetime.timedelta(days=1)
    while not regime.is_trading_day(day, holidays_):
        day -= datetime.timedelta(days=1)
    return day


def _one_month_before(day: datetime.date) -> datetime.date:
    year, month = (day.year, day.month - 1) if day.month > 1 else (day.year - 1, 12)
    for candidate in (day.day, 30, 29, 28):
        try:
            return datetime.date(year, month, candidate)
        except ValueError:
            continue
    return datetime.date(year, month, 28)


def resolve_period(
    period: str,
    from_date: Optional[datetime.date],
    to_date: Optional[datetime.date],
    now: datetime.datetime,
    holidays_: Optional[set[datetime.date]] = None,
) -> tuple[datetime.date, datetime.date]:
    """The (start, end) a dialog choice means, in trading days on the exchange calendar.

    Not floored: a custom range may reach as far back as the user likes (#36). A range that
    runs into a session still open is clipped to the last completed one, because a partial day
    replayed as if it were whole would report a day the bot never finished.
    """
    hol = holidays() if holidays_ is None else holidays_
    end = last_completed_session(now, hol)
    if period == "last_day":
        return end, end
    if period == "last_week":
        sessions = [end]
        day = end
        while len(sessions) < WEEK_SESSIONS:
            day -= datetime.timedelta(days=1)
            if regime.is_trading_day(day, hol):
                sessions.append(day)
        return sessions[-1], end
    if period == "last_month":
        return _one_month_before(end) + datetime.timedelta(days=1), end
    if period == "custom":
        if from_date is None or to_date is None:
            raise ValueError("A custom range needs both a start and an end date.")
        clipped_end = min(to_date, end)
        if from_date > clipped_end:
            raise ValueError(
                f"The range is empty: {from_date} is after {clipped_end}, the last completed session."
            )
        return from_date, clipped_end
    raise ValueError(f"Unknown period {period!r}; expected one of {', '.join(PERIODS)}.")


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
    readings_cache: Optional[ReadingsCache] = None,
    record_decisions: bool = False,
    on_day: Optional[Callable[[datetime.date], None]] = None,
) -> Any:
    """Replay one bot over a range. `readings_cache` (series id -> readings) lets a run that
    compares signal settings build each series' readings once, whichever combinations share it.
    It holds only the last `KEEP_SERIES`, which the combo order makes free -- see `ReadingsCache`.

    `on_day` is called as each session starts replaying, so a job can say where it is."""
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
                    on_day=on_day,
                )
            )
        merged = merge_expiry_results(results, price_source=pricer.source, spread_source=spread.describe())
        merged.lots = lots_shown
        return merged

    if bot == "cas":
        return _replay_cas(config, start=start, end=end, pricer=pricer, hol=hol, path=path,
                           charges=charges, spread=spread, readings_cache=readings_cache,
                           record_decisions=record_decisions, on_day=on_day)
    futures = store.load_candles(from_date=start, to_date=end, path=path)
    if not futures:
        raise NoCachedData(f"No NIFTY futures bars are cached for {start} to {end}. Fetch data first.")
    spot = store.load_candles(from_date=start, to_date=end, table="spot_candles", path=path)
    if bot == "fly":
        entry_filter = getattr(config, "entry_filter", None)
        kind = getattr(entry_filter, "kind", "none")
        filter_readings, vix_series = None, None
        if kind == "signal_quiet":
            filter_readings = _series_readings(
                _series_key(entry_filter.signal, "nifty"), start, end, hol, path, readings_cache
            )
        elif kind == "vix_not_rising":
            from icici_breeze_backend.app.services.bots.scalping import vix_minutes

            vix_series = vix_minutes.series_from_candles(
                store.load_candles(
                    stock_code=vix_minutes.STOCK_CODE, from_date=start, to_date=end,
                    table="spot_candles", path=path,
                )
            )
        return run_fly_backtest(
            futures, config=config, charges=charges, spread=spread, vix_by_day=vix,
            spot_bars=spot, pricer=pricer, lots=lots or DEFAULT_LOTS, holidays=hol,
            filter_readings=filter_readings, vix_series=vix_series,
            record_decisions=record_decisions, on_day=on_day,
        )
    readings = _series_readings(_series_key(config.signal, "nifty"), start, end, hol, path, readings_cache)
    return run_backtest(
        futures, config=config, charges=charges, spread=spread, vix_by_day=vix,
        spot_bars=spot, pricer=pricer, holidays=hol, readings=readings,
        record_decisions=record_decisions, on_day=on_day,
    )


def _replay_cas(
    config: Any, *, start: datetime.date, end: datetime.date, pricer: Any, hol: set[datetime.date],
    path: Optional[str], charges: Any, spread: Any, readings_cache: Optional[ReadingsCache],
    record_decisions: bool, on_day: Optional[Callable[[datetime.date], None]] = None,
) -> Any:
    """CAS Bingo over each enabled index's expiry days, merged into one result."""
    from icici_breeze_backend.app.services.bots.cas_bingo.backtest import CasResult, run_cas_backtest
    from icici_breeze_backend.app.services.bots.cas_bingo.market import SIGNAL_LABEL

    merged = CasResult(price_source=getattr(pricer, "source", ""))
    reads_signal = config.strategy in ("debit_spread", "credit_spread")
    for index in config.enabled_indices():
        days = expiry_days(index, start, end, hol)
        if not days:
            continue
        readings = (
            _series_readings(_series_key(config.signal, SIGNAL_LABEL[index]), start, end, hol, path,
                             readings_cache)
            if reads_signal else {}
        )
        one = run_cas_backtest(
            config=config, index=index, days=days,
            index_bars=store.load_candles(stock_code=index, from_date=start, to_date=end,
                                          table="spot_candles", path=path),
            futures_bars=store.load_candles(stock_code=index, from_date=start, to_date=end, path=path),
            readings=readings, charges=charges, spread=spread, pricer=pricer,
            record_decisions=record_decisions, on_day=on_day,
        )
        merged.cycles.extend(one.cycles)
        merged.decisions.extend(one.decisions)
        for counter in ("days", "days_awaiting_data", "days_without_index", "no_trigger",
                        "skipped_no_data", "skipped_unaffordable"):
            setattr(merged, counter, getattr(merged, counter) + getattr(one, counter))
    return merged


# Bars before the range that only warm a series' baselines, as the live warm-up does.
_SIGNAL_WARMUP_DAYS = 10


def _series_key(choice: Any, index: str) -> Any:
    from icici_breeze_backend.app.services.index_signal.mechanisms import SeriesKey

    return SeriesKey(choice.mechanism, int(choice.duration), index)


#: How many signal series a run's readings cache holds at once.
#:
#: Two is not a guess, and not a safety margin either -- it is the widest any bot's replay
#: reaches. A backtest replays its settings in `backtest_combos` order, which loops direction
#: innermost, and direction is *not* part of a `SeriesKey`, so the two settings sharing a series
#: (follow and fade) always run back to back. Bot 3 and the fly therefore need one series live
#: at a time; **CAS Bingo needs two**, because one of its replays walks both enabled indices and
#: asks for that setting's series once per index. At two, every bot rebuilds nothing at all.
#:
#: Holding all six instead was the run's largest term by far once a period got long: a series is
#: roughly 80 MB over nine months, so six is ~490 MB carried to the end where two is ~160 MB
#: (docs/design-decisions.md #41). `rebuilt` is the tripwire if this reasoning ever stops
#: holding.
KEEP_SERIES = 2


class ReadingsCache:
    """The signal series a run has built, bounded to the last `keep`.

    Deliberately a mapping with the three operations `_series_readings` already used on the
    plain dict it replaces, so the caching logic stayed where it was. Reading a series marks it
    most recent; building one past the bound drops the oldest.
    """

    def __init__(self, keep: int = KEEP_SERIES) -> None:
        self.keep = max(1, int(keep))
        self._series: "OrderedDict[str, dict[datetime.datetime, dict[str, Any]]]" = OrderedDict()
        #: Series ids ever built, so a rebuild can be told from a first build.
        self._built: set[str] = set()
        #: Series dropped and then built again. The adjacency above should keep this at zero; it
        #: is here so a bot whose combo order stops being adjacent says so rather than quietly
        #: paying for it.
        self.rebuilt = 0

    def __contains__(self, key_id: object) -> bool:
        return key_id in self._series

    def reserve(self) -> None:
        """Drop what has to go to make room for a series about to be built.

        Called *before* the build rather than leaving it to `__setitem__` afterwards. A series
        is built whole before it can be stored, so evicting after the fact means `keep` + 1 are
        live at exactly the moment the run is at its fullest -- which at nine months is another
        ~85 MB at the only instant it matters.
        """
        while len(self._series) >= self.keep:
            self._series.popitem(last=False)

    def __getitem__(self, key_id: str) -> dict[datetime.datetime, dict[str, Any]]:
        self._series.move_to_end(key_id)
        return self._series[key_id]

    def __setitem__(self, key_id: str, readings: dict[datetime.datetime, dict[str, Any]]) -> None:
        if key_id in self._built:
            self.rebuilt += 1
        self._built.add(key_id)
        self._series[key_id] = readings
        self._series.move_to_end(key_id)
        while len(self._series) > self.keep:
            self._series.popitem(last=False)

    def __len__(self) -> int:
        return len(self._series)


def _series_readings(
    key: Any,
    start: datetime.date,
    end: datetime.date,
    hol: set[datetime.date],
    path: Optional[str],
    cache: Optional[ReadingsCache] = None,
) -> dict[datetime.datetime, dict[str, Any]]:
    from icici_breeze_backend.app.services.bots.scalping.backtest_common import series_readings
    from icici_breeze_backend.app.services.index_signal.mechanisms import STOCK_CODES
    from icici_breeze_backend.app.services.index_signal.series import rollover_days

    if cache is not None and key.id in cache:
        return cache[key.id]
    warm_from = start - datetime.timedelta(days=_SIGNAL_WARMUP_DAYS)
    if cache is not None:
        cache.reserve()
    bars = store.load_candles(stock_code=STOCK_CODES[key.index], from_date=warm_from, to_date=end, path=path)
    readings = series_readings(bars, key, rollover_days=rollover_days(warm_from, end, hol))
    if cache is not None:
        cache[key.id] = readings
    return readings


def fetch_underlying(
    fetcher: Any, bot: str, start: datetime.date, end: datetime.date, indices: Sequence[str]
) -> None:
    """Futures (the signals, and Bot 4's re-entry gate), each index's cash bars, and VIX.
    Futures reach back over the signal warm-up, which every setting a backtest compares reads."""
    warm_from = start - datetime.timedelta(days=_SIGNAL_WARMUP_DAYS)
    if bot == "cas":
        for index in indices:
            fetcher.fetch_futures(index, warm_from, end)
    elif bot != "expiry":
        fetcher.fetch_futures("NIFTY", warm_from, end)
    for index in indices:
        fetcher.fetch_spot(index, start, end)
    if bot == "fly":
        # India VIX minute bars, for the fly's `vix_not_rising` entry filter (#38). A handful of
        # calls a month; fetched whatever the filter is set to, so switching it on later needs
        # no second fetch.
        fetcher.fetch_spot("INDVIX", start, end)
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
    configs: Optional[Sequence[Any]] = None,
) -> dict[str, Any]:
    """Replay on real prices, fetch what it lacked, repeat (plan section 8.7).

    `configs` replays several settings each round -- the signal combinations a bot backtest
    compares -- so one loop fetches the option windows every one of them needs.

    Raises `Stopped` from the fetcher on a cancel, the market opening, or a spent budget --
    everything fetched until then is already stored, so running it again resumes.
    """
    path = fetcher.path
    variants = list(configs) if configs else [config]
    # Bounded for the same reason the replay's is: this loop runs every setting too.
    cache = ReadingsCache()
    for round_no in range(1, max_rounds + 1):
        book = OptionBook(path)
        waiting = 0
        try:
            for one in variants:
                result = replay(
                    bot, start=start, end=end, config=one, pricer=RealPricer(book),
                    lots=lots, scopes=scopes, holidays_=fetcher.holidays, path=path,
                    readings_cache=cache,
                )
                waiting = max(waiting, int(result.summary().get("days_awaiting_data", 0) or 0))
        except NoCachedData as exc:
            return {"complete": False, "rounds": round_no, "message": str(exc)}
        store.add_needs(book.needs, path=path)
        pending = store.pending_needs(path=path)
        if not pending:
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
                f"Signal: {momentum.signal.label()}, held until the call ends",
                f"Ladder: stop {momentum.exits.stop_loss_pts:g} pts, runner at {momentum.exits.target_pts:g} pts",
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
    config_hash_now = material_config_hash(bot_type, config.model_dump(mode="json"))
    payload = compare_payload(
        day,
        paper,
        result.cycles,
        config_hash_now=config_hash_now,
        config_hashes_then=run_config_hashes(bot_type, day),
        price_source=result.price_source,
        lots=lots,
    )
    # Kept so the Live confirmation can show it without replaying a day while a dialog opens.
    # The answer cannot change for a fixed day and a fixed set of settings, so a stored one is
    # as good as a fresh one -- and it is keyed by the settings, so editing any of them drops it.
    try:
        record_compare(bot, config_hash_now, payload, path=path)
    except Exception:  # noqa: BLE001 -- a compare the user asked for must not fail on bookkeeping
        _logger.debug("compare: could not record the fill check for %s", bot, exc_info=True)
    return payload


def _compare_key(slug: str, config_hash: str) -> str:
    return f"compare:{slug}:{config_hash}"


def record_compare(
    slug: str, config_hash: str, payload: Mapping[str, Any], *, path: Optional[str] = None
) -> None:
    """Remember a fill check: the median gap between backtest and Simulation entries."""
    store.set_meta(
        _compare_key(slug, config_hash),
        json.dumps(
            {
                "day": str(payload.get("day") or ""),
                "median_abs_entry_diff": payload.get("median_abs_entry_diff"),
                "pairs": len(payload.get("pairs") or []),
                "settings_changed": bool(payload.get("settings_changed")),
                "computed_at": now_ist().isoformat(timespec="seconds"),
            },
            default=str,
        ),
        path=path,
    )


def _last_compare(slug: str, config_hash: str, *, path: Optional[str] = None) -> Optional[dict[str, Any]]:
    raw = store.get_meta(_compare_key(slug, config_hash), path=path)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def backtest_evidence(
    user_id: str, bot_type: str, config: Any, *, path: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """The most recent completed backtest on *these exact settings*, for the Live dialog.

    Matched by the same materiality rule as the paper gate (`evidence.material_config_hash`):
    a run whose settings differ in anything that moves the P&L describes a different bot, so it
    is not offered as evidence for this one. The hash is recomputed from the config each run
    stored rather than read from a column, so runs made before this existed still match.

    This is never a gate and never blocks anything — `evidence.gather` remains the only thing
    that decides whether Live may be armed.
    """
    from icici_breeze_backend.app.services.bots.scalping.evidence import material_config_hash

    slug = SLUG_FOR_BOT_TYPE.get(bot_type)
    if slug is None:
        return None
    raw = config.model_dump(mode="json") if hasattr(config, "model_dump") else dict(config or {})
    wanted = material_config_hash(bot_type, raw)

    for run in store.list_runs(user_id, limit=EVIDENCE_RUN_SCAN, path=path):
        if run.get("bot") != slug or run.get("status") != "completed":
            continue
        params = run.get("params") or {}
        stored = params.get("config")
        if not isinstance(stored, dict) or material_config_hash(bot_type, stored) != wanted:
            continue
        summary = run.get("summary") or {}
        checked = _last_compare(slug, wanted, path=path)
        return {
            "run_id": str(run.get("id") or ""),
            "created_at": str(run.get("created_at") or ""),
            "from_date": params.get("from"),
            "to_date": params.get("to"),
            "price_source": str(summary.get("price_source") or ("model" if params.get("model") else "real")),
            "days_replayed": int(summary.get("days_replayed") or 0),
            "days_awaiting_data": int(summary.get("days_awaiting_data") or 0),
            "cycles": int(summary.get("cycles") or 0),
            "win_rate_pct": summary.get("win_rate_pct"),
            "net_pnl": float(summary.get("net_pnl") or 0.0),
            "friction": float(summary.get("friction") or 0.0),
            "compare_day": (checked or {}).get("day") or None,
            "compare_median_entry_gap": (checked or {}).get("median_abs_entry_diff"),
            "compare_pairs": (checked or {}).get("pairs"),
        }
    return None
