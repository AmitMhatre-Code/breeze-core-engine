#!/usr/bin/env python3
"""Backtest the bots against real ICICI history (docs/bots-scalping-plan.md section 8).

On the EC2 instance (a live broker session needs the static IP), and never between 09:00 and
15:45 IST on a trading day -- the script refuses, because its calls are invisible to the API
server's rate limiter and would compete with live orders:

    docker exec -it breeze-core-engine python /app/backend/scripts/scalping_backtest.py probe
    docker exec -it breeze-core-engine python /app/backend/scripts/scalping_backtest.py backfill --bot momentum
    docker exec -it breeze-core-engine python /app/backend/scripts/scalping_backtest.py backfill --bot fly
    docker exec -it breeze-core-engine python /app/backend/scripts/scalping_backtest.py backfill --bot expiry --index BSESEN

(From a checkout, `PYTHONPATH=./src .venv/bin/python scripts/scalping_backtest.py ...` from
`backend/` -- but a checkout has no static IP, so there only the replay commands are useful.)

`probe` measures what the design rests on (expired contracts served? per-call cap? request
clock?) with about a dozen calls. `backfill` fetches the underlying (futures, cash index, VIX),
then alternates replay and fetch: each replay records the option contracts it needed and did
not have, and each fetch downloads exactly those, until nothing is missing or the call budget
is spent. Re-running it resumes.

Then, as often as you like -- no broker, pure CPU:

    ... replay                     # Bot 3 on real option prices
    ... replay --model             # Bot 3 on Black-Scholes, the original harness
    ... replay --target-pts 12 --stop-loss-pts 5 --csv cycles.csv
    ... replay-fly --lots 3        # Bot 4
    ... replay-expiry --index NIFTY --strategy naked_pe --strategy short_strangle   # Bot 2
    ... compare --bot momentum --date 2026-09-11   # paper against backtest, same day
    ... coverage

WHAT THE RESULTS MEAN: on real prices the option fills come from ICICI's traded bars plus the
bid-ask spread paper mode has observed -- the book itself is not in the history. On `--model`
they are Black-Scholes. The two are never mixed. Either way this tests the strategy's rules
over real history; paper mode is what tests execution, and `compare` measures the gap.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime
import json
import os
import sqlite3
import sys
from collections import Counter
from typing import Any, Optional

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _bootstrap() -> None:
    """Load .env the way `main.py` does, before any config is read."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for path in (os.path.join(_BACKEND, "..", ".env"), os.path.join(os.getcwd(), ".env")):
        if os.path.isfile(path):
            load_dotenv(path, override=False)


_bootstrap()

from icici_breeze_backend.app.core.timezone import now_ist  # noqa: E402
from icici_breeze_backend.app.db.bots_migrate import (  # noqa: E402
    BOT_EXPIRY_INDEX_WRITER,
    BOT_IRON_FLY_SCALPER,
    BOT_MOMENTUM_LONG_SCALPER,
)
from icici_breeze_backend.app.domain.bots import (  # noqa: E402
    ExpiryIndexWriterConfig,
    IronFlyScalperConfig,
    MomentumLongScalperConfig,
)
from icici_breeze_backend.app.services.bots.backtest_expiry import (  # noqa: E402
    STRATEGY_RIGHTS,
    expiry_days,
    run_expiry_backtest,
)
from icici_breeze_backend.app.services.bots.charges import load_charges  # noqa: E402
from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime  # noqa: E402
from icici_breeze_backend.app.services.bots.scalping import backtest_store as store  # noqa: E402
from icici_breeze_backend.app.services.bots.scalping.backtest import run_backtest  # noqa: E402
from icici_breeze_backend.app.services.bots.scalping.backtest_fly import (  # noqa: E402
    DEFAULT_LOTS,
    run_fly_backtest,
)
from icici_breeze_backend.app.services.bots.scalping.backtest_options import (  # noqa: E402
    ModelPricer,
    OptionBook,
    RealPricer,
)
from icici_breeze_backend.app.services.bots.scalping.spreads import spread_stats  # noqa: E402

BOT_TYPES = {"momentum": BOT_MOMENTUM_LONG_SCALPER, "fly": BOT_IRON_FLY_SCALPER, "expiry": BOT_EXPIRY_INDEX_WRITER}
CONFIG_MODELS = {"momentum": MomentumLongScalperConfig, "fly": IronFlyScalperConfig, "expiry": ExpiryIndexWriterConfig}
MAX_BACKFILL_ROUNDS = 25


def _iso(d: str) -> datetime.date:
    return datetime.date.fromisoformat(d)


def _holidays() -> set[datetime.date]:
    try:
        from icici_breeze_backend.app.services.market_calendar import get_calendar_config

        return {datetime.date.fromisoformat(d) for d in get_calendar_config().holidays}
    except Exception:  # noqa: BLE001 -- a missing calendar only costs holiday-shifted expiries
        print("warning: exchange calendar unavailable; expiries are not holiday-shifted", file=sys.stderr)
        return set()


def _range(args: argparse.Namespace) -> tuple[datetime.date, datetime.date]:
    start = _iso(args.from_date) if getattr(args, "from_date", None) else regime.HISTORY_START
    if start < regime.HISTORY_START:
        print(f"note: --from clipped to {regime.HISTORY_START}, the start of the replayed lot-size era.")
        start = regime.HISTORY_START
    end = _iso(args.to_date) if getattr(args, "to_date", None) else now_ist().date() - datetime.timedelta(days=1)
    if start > end:
        raise SystemExit("--from must be on or before --to")
    return start, end


def _pricer(args: argparse.Namespace) -> Any:
    return ModelPricer() if getattr(args, "model", False) else RealPricer(OptionBook())


def _stored_config(bot: str, user_id: Optional[str] = None) -> Any:
    """The bot's saved settings, for the one user who has them."""
    from icici_breeze_backend.app.core import config as cfg
    from icici_breeze_backend.app.repositories.bots import normalize_config

    bot_type = BOT_TYPES[bot]
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        rows = conn.execute("SELECT user_id, config FROM bots WHERE bot_type = ?", (bot_type,)).fetchall()
    if user_id:
        rows = [r for r in rows if r[0] == user_id]
    if len(rows) != 1:
        raise SystemExit(
            f"{len(rows)} saved {bot_type} configs found; pass --user-id (or omit --stored)."
        )
    return CONFIG_MODELS[bot](**normalize_config(bot_type, rows[0][1]))


def _config(bot: str, args: argparse.Namespace) -> Any:
    config = _stored_config(bot, args.user_id) if getattr(args, "stored", False) else CONFIG_MODELS[bot]()
    if bot == "momentum":
        exits = config.exits.model_copy(
            update={
                k: v
                for k, v in (
                    ("target_pts", getattr(args, "target_pts", None)),
                    ("stop_loss_pts", getattr(args, "stop_loss_pts", None)),
                    ("time_invalidation_seconds", getattr(args, "time_stop", None)),
                )
                if v is not None
            }
        )
        config = config.model_copy(update={"exits": exits})
    return config


def _replay(bot: str, args: argparse.Namespace, pricer: Any, start: datetime.date, end: datetime.date, *, config: Any = None, lots: Optional[int] = None) -> Any:
    config = config or _config(bot, args)
    holidays = _holidays()
    common = {"charges": load_charges(), "spread": spread_stats(), "pricer": pricer}
    if bot == "expiry":
        index = args.index
        return run_expiry_backtest(
            index=index,
            days=expiry_days(index, start, end, holidays),
            spot_bars=store.load_candles(stock_code=index, from_date=start, to_date=end, table="spot_candles"),
            config=config,
            strategies=args.strategy or tuple(STRATEGY_RIGHTS),
            lots=lots or args.lots,
            vix_by_day=store.load_vix(),
            **common,
        )
    futures = store.load_candles(from_date=start, to_date=end)
    spot = store.load_candles(from_date=start, to_date=end, table="spot_candles")
    if not futures:
        raise SystemExit("No cached futures candles for that range. Run `backfill` (or `fetch`) on the instance.")
    if bot == "fly":
        return run_fly_backtest(
            futures, config=config, spot_bars=spot, vix_by_day=store.load_vix(),
            lots=lots or args.lots, holidays=holidays, **common,
        )
    return run_backtest(futures, config=config, spot_bars=spot, vix_by_day=store.load_vix(), holidays=holidays, **common)


# --------------------------------------------------------------------------------------
# Broker commands -- the instance only, outside market hours
# --------------------------------------------------------------------------------------


def _fetcher(args: argparse.Namespace) -> Any:
    from icici_breeze_backend.app.core.requests_patch import apply_requests_patch
    from icici_breeze_backend.app.services.bots.scalping.backtest_fetch import (
        Fetcher,
        market_hours_refusal,
        resolve_sdk,
    )
    from icici_breeze_backend.app.services.market_calendar import is_trading_day

    now = now_ist()
    refusal = market_hours_refusal(now.replace(tzinfo=None), trading_day=is_trading_day(now))
    if refusal:
        raise SystemExit(refusal)
    apply_requests_patch()  # breeze_connect's session call needs GET-with-body, as in main.py
    return Fetcher(resolve_sdk(args.user_id), holidays=_holidays(), max_calls=args.max_calls)


def _fetch_underlying(fetcher: Any, index: str, start: datetime.date, end: datetime.date, *, futures: bool) -> None:
    if futures:
        fetcher.fetch_futures("NIFTY", start, end)
    fetcher.fetch_spot(index, start, end)
    cached = store.load_vix()
    missing = [d for d in regime.trading_days(start, end, fetcher.holidays) if d not in cached]
    if missing:
        fetcher.fetch_vix(min(missing), max(missing))


def cmd_probe(args: argparse.Namespace) -> int:
    store.ensure_tables()
    fetcher = _fetcher(args)
    report = fetcher.probe(now_ist().date())
    print(json.dumps(report, indent=2, default=str))
    expired = (report.get("expired_nifty_weekly") or {}).get("verdict")
    print(f"\n{fetcher.calls} ICICI calls. Expired NIFTY weekly contracts: {expired}.")
    if expired != "served":
        print("Without expired contracts only `--model` replays are possible.")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    from icici_breeze_backend.app.services.bots.scalping.backtest_fetch import BudgetExhausted

    store.ensure_tables()
    start, end = _range(args)
    fetcher = _fetcher(args)
    try:
        _fetch_underlying(fetcher, args.index, start, end, futures=args.index == "NIFTY")
    except BudgetExhausted as exc:
        print(f"Stopped: {exc}. Run again to resume.")
    print(f"\n{fetcher.calls} ICICI calls. Cache: {store.db_path()}")
    return 0


def cmd_fetch_options(args: argparse.Namespace) -> int:
    from icici_breeze_backend.app.services.bots.scalping.backtest_fetch import BudgetExhausted

    store.ensure_tables()
    pending = store.pending_needs()
    if not pending:
        print("No pending option windows. A replay on real prices queues them.")
        return 0
    fetcher = _fetcher(args)
    try:
        print(json.dumps(fetcher.fetch_needs(pending)))
    except BudgetExhausted as exc:
        print(f"Stopped: {exc}. Run again to resume.")
    print(f"{fetcher.calls} ICICI calls.")
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    from icici_breeze_backend.app.services.bots.scalping.backtest_fetch import BudgetExhausted

    store.ensure_tables()
    start, end = _range(args)
    index = args.index if args.bot == "expiry" else "NIFTY"
    fetcher = _fetcher(args)
    try:
        _fetch_underlying(fetcher, index, start, end, futures=args.bot != "expiry")
        for round_no in range(1, MAX_BACKFILL_ROUNDS + 1):
            book = OptionBook()
            result = _replay(args.bot, args, RealPricer(book), start, end)
            store.add_needs(book.needs)
            pending = store.pending_needs()
            waiting = result.summary().get("days_awaiting_data", 0)
            if not pending:
                print(f"\nComplete: every contract the replay needs is cached ({waiting} days still waiting).")
                break
            print(f"\nRound {round_no}: fetching {len(pending)} option windows")
            stats = fetcher.fetch_needs(pending)
            if stats["fetched"] == 0:
                print("No progress this round -- every request errored. Stopping; see the errors above.")
                break
        else:
            print(f"\nStopped after {MAX_BACKFILL_ROUNDS} rounds; run again to continue.")
    except BudgetExhausted as exc:
        print(f"\nStopped: {exc}. Run again to resume.")
    print(f"{fetcher.calls} ICICI calls used.")
    return 0


# --------------------------------------------------------------------------------------
# Replay commands -- anywhere, no broker
# --------------------------------------------------------------------------------------


def cmd_coverage(_args: argparse.Namespace) -> int:
    store.ensure_tables()
    print(json.dumps(store.coverage(), indent=2))
    return 0


def _print(title: str, summary: dict[str, Any]) -> None:
    print(f"\n=== {title} ===")
    for key, value in summary.items():
        if isinstance(value, dict):
            value = json.dumps(value)
        print(f"  {key:24s}: {value}")


def _queue_needs(pricer: Any) -> None:
    book = getattr(pricer, "book", None)
    if book is not None and book.needs:
        added = store.add_needs(book.needs)
        print(f"\n  {len(book.needs)} option windows are not cached ({added} newly queued). "
              "Run `backfill` (or `fetch-options`) on the instance, then replay again.")


def _write_csv(path: str, rows: list[Any]) -> None:
    if not rows:
        print(f"\n  Nothing to write to {path}")
        return
    records = [dataclasses.asdict(r) for r in rows]
    for r in records:
        for k, v in r.items():
            if isinstance(v, (list, tuple, dict)):
                r[k] = json.dumps(v, default=str)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    print(f"\n  Wrote {len(rows)} rows to {path}")


def cmd_replay(args: argparse.Namespace) -> int:
    store.ensure_tables()
    start, end = _range(args)
    pricer = _pricer(args)
    config = _config("momentum", args)
    result = _replay("momentum", args, pricer, start, end, config=config)
    print(f"  ladder    : target {config.exits.target_pts} / stop {config.exits.stop_loss_pts} "
          f"/ time {config.exits.time_invalidation_seconds}s")
    _print("Bot 3 momentum scalper backtest", result.summary())
    _queue_needs(pricer)
    if args.csv:
        _write_csv(args.csv, result.cycles)
    return 0


def cmd_replay_fly(args: argparse.Namespace) -> int:
    store.ensure_tables()
    start, end = _range(args)
    pricer = _pricer(args)
    result = _replay("fly", args, pricer, start, end)
    _print(f"Bot 4 iron fly backtest ({args.lots} lots, fixed -- no margin history)", result.summary())
    _queue_needs(pricer)
    if args.csv:
        _write_csv(args.csv, result.cycles)
    return 0


def cmd_replay_expiry(args: argparse.Namespace) -> int:
    store.ensure_tables()
    start, end = _range(args)
    pricer = _pricer(args)
    result = _replay("expiry", args, pricer, start, end)
    _print(f"Bot 2 expiry writer backtest -- {args.index}, each strategy side by side", result.summary())
    _queue_needs(pricer)
    if args.csv:
        _write_csv(args.csv, result.trades)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    from icici_breeze_backend.app.services.bots.scalping.backtest_compare import (
        load_paper_cycles,
        render,
        run_config_hashes,
    )
    from icici_breeze_backend.app.services.bots.scalping.evidence import material_config_hash

    store.ensure_tables()
    day = _iso(args.date)
    bot_type = BOT_TYPES[args.bot]
    paper = load_paper_cycles(bot_type, day, user_id=args.user_id)
    if not paper:
        raise SystemExit(f"No paper {bot_type} cycles on {day}.")
    config = _stored_config(args.bot, paper[0].user_id)
    lots = None
    if args.bot == "fly":
        # Size the replay like the paper session did; the fly's sizing has no history.
        lots = Counter(p.lots for p in paper if p.lots).most_common(1)[0][0]
    pricer = _pricer(args)
    result = _replay(args.bot, args, pricer, day, day, config=config, lots=lots)
    if result.summary().get("days_awaiting_data"):
        _queue_needs(pricer)
        raise SystemExit(f"Run `backfill --bot {args.bot} --from {day} --to {day}` on the instance first.")
    lines = render(
        day,
        paper,
        result.cycles,
        config_hash_now=material_config_hash(bot_type, config.model_dump(mode="json")),
        config_hashes_then=run_config_hashes(bot_type, day),
        price_source=result.price_source,
    )
    if lots:
        lines.insert(1, f"  replayed at {lots} lots, the paper session's size")
    print("\n".join(lines))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def broker(p: argparse.ArgumentParser) -> None:
        p.add_argument("--user-id", help="Whose session to use (default: the only one)")
        p.add_argument("--max-calls", type=int, default=1500, help="ICICI call budget for this run")

    def span(p: argparse.ArgumentParser) -> None:
        p.add_argument("--from", dest="from_date", help=f"YYYY-MM-DD (default and floor: {regime.HISTORY_START})")
        p.add_argument("--to", dest="to_date", help="YYYY-MM-DD (default: yesterday)")

    def replay_opts(p: argparse.ArgumentParser) -> None:
        span(p)
        p.add_argument("--model", action="store_true", help="Price options with Black-Scholes instead of real bars")
        p.add_argument("--stored", action="store_true", help="Use the bot's saved settings, not the defaults")
        p.add_argument("--user-id", help="Whose saved settings (with --stored)")
        p.add_argument("--csv", help="Write per-trade rows to this file")

    p = sub.add_parser("probe", help="Measure ICICI's history API (instance only)")
    broker(p)
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("fetch", help="Futures, cash index and VIX (instance only)")
    broker(p)
    span(p)
    p.add_argument("--index", choices=("NIFTY", "BSESEN"), default="NIFTY")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("fetch-options", help="Download the queued option windows (instance only)")
    broker(p)
    p.set_defaults(func=cmd_fetch_options)

    p = sub.add_parser("backfill", help="Fetch everything a bot's replay needs (instance only)")
    broker(p)
    span(p)
    p.add_argument("--bot", choices=tuple(BOT_TYPES), required=True)
    p.add_argument("--index", choices=("NIFTY", "BSESEN"), default="NIFTY", help="Bot 2 only")
    p.add_argument("--strategy", action="append", choices=tuple(STRATEGY_RIGHTS), help="Bot 2 only")
    p.add_argument("--lots", type=int, default=DEFAULT_LOTS)
    p.add_argument("--stored", action="store_true", help="Use the bot's saved settings")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("coverage", help="Show what is cached")
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("replay", help="Bot 3, the momentum scalper")
    replay_opts(p)
    p.add_argument("--target-pts", type=float, help="Override the runner trigger")
    p.add_argument("--stop-loss-pts", type=float, help="Override the initial stop")
    p.add_argument("--time-stop", type=int, help="Override time invalidation, seconds")
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("replay-fly", help="Bot 4, the iron fly")
    replay_opts(p)
    p.add_argument("--lots", type=int, default=DEFAULT_LOTS, help="Fixed size; margin has no history")
    p.set_defaults(func=cmd_replay_fly)

    p = sub.add_parser("replay-expiry", help="Bot 2, the expiry-day writer")
    replay_opts(p)
    p.add_argument("--index", choices=("NIFTY", "BSESEN"), default="NIFTY")
    p.add_argument("--strategy", action="append", choices=tuple(STRATEGY_RIGHTS), help="Repeatable; default all")
    p.add_argument("--lots", type=int, default=1)
    p.set_defaults(func=cmd_replay_expiry)

    p = sub.add_parser("compare", help="Paper against backtest for one day")
    p.add_argument("--bot", choices=("momentum", "fly"), required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--model", action="store_true")
    p.add_argument("--user-id")
    p.set_defaults(func=cmd_compare)

    args = parser.parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
