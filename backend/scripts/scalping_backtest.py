#!/usr/bin/env python3
"""Backtest the bots against real ICICI history (docs/bots-scalping-plan.md section 8).

The same engine as Bots -> Backtest in the app (section 8.11); this is the SSH route to it. On
the EC2 instance (a live broker session needs the static IP), and never between 09:00 and
15:45 IST on a trading day -- the script refuses, and a run already going stops itself at 09:00:

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
    ... compare --bot momentum --date 2026-09-11   # simulation against backtest, same day
    ... coverage

Unlike the app, the command line takes lot counts as flags (`--lots`) rather than pricing
today's margin, and accepts setting overrides for Bot 3's ladder.

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
from icici_breeze_backend.app.services.bots import backtest_service as service  # noqa: E402
from icici_breeze_backend.app.services.bots.backtest_expiry import STRATEGY_RIGHTS  # noqa: E402
from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime  # noqa: E402
from icici_breeze_backend.app.services.bots.scalping import backtest_store as store  # noqa: E402
from icici_breeze_backend.app.services.bots.scalping.backtest_fetch import Stopped  # noqa: E402
from icici_breeze_backend.app.services.bots.scalping.backtest_fly import DEFAULT_LOTS  # noqa: E402
from icici_breeze_backend.app.services.bots.scalping.backtest_options import (  # noqa: E402
    ModelPricer,
    OptionBook,
    RealPricer,
)


def _iso(d: str) -> datetime.date:
    return datetime.date.fromisoformat(d)


def _range(args: argparse.Namespace) -> tuple[datetime.date, datetime.date]:
    frm = _iso(args.from_date) if getattr(args, "from_date", None) else None
    to = _iso(args.to_date) if getattr(args, "to_date", None) else None
    if frm and frm < regime.HISTORY_START:
        print(f"note: --from clipped to {regime.HISTORY_START}, the start of the replayed lot-size era.")
    try:
        return service.clip_range(frm, to, now_ist().date())
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _pricer(args: argparse.Namespace) -> Any:
    return ModelPricer() if getattr(args, "model", False) else RealPricer(OptionBook())


def _stored_config(bot: str, user_id: Optional[str] = None) -> Any:
    """The bot's saved settings, for the one user who has them."""
    from icici_breeze_backend.app.core import config as cfg
    from icici_breeze_backend.app.repositories.bots import normalize_config

    bot_type = service.BOT_TYPES[bot]
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        rows = conn.execute("SELECT user_id, config FROM bots WHERE bot_type = ?", (bot_type,)).fetchall()
    if user_id:
        rows = [r for r in rows if r[0] == user_id]
    if len(rows) != 1:
        raise SystemExit(f"{len(rows)} saved {bot_type} configs found; pass --user-id (or omit --stored).")
    return service.CONFIG_MODELS[bot](**normalize_config(bot_type, rows[0][1]))


def _config(bot: str, args: argparse.Namespace) -> Any:
    if getattr(args, "stored", False):
        config = _stored_config(bot, args.user_id)
    else:
        config = service.CONFIG_MODELS[bot]()
    if bot == "momentum":
        exits = config.exits.model_copy(
            update={
                k: v
                for k, v in (
                    ("target_pts", getattr(args, "target_pts", None)),
                    ("stop_loss_pts", getattr(args, "stop_loss_pts", None)),
                )
                if v is not None
            }
        )
        config = config.model_copy(update={"exits": exits})
    return config


def _scopes(args: argparse.Namespace, lots: Optional[int] = None) -> list[service.Scope]:
    strategies = tuple(args.strategy or STRATEGY_RIGHTS)
    n = lots or args.lots
    return [service.Scope(args.index, strategies, {s: n for s in strategies})]


def _replay(bot: str, args: argparse.Namespace, pricer: Any, start: datetime.date, end: datetime.date,
            *, config: Any = None, lots: Optional[int] = None) -> Any:
    try:
        return service.replay(
            bot,
            start=start,
            end=end,
            config=config or _config(bot, args),
            pricer=pricer,
            lots=lots or getattr(args, "lots", None),
            scopes=_scopes(args, lots) if bot == "expiry" else None,
        )
    except service.NoCachedData as exc:
        raise SystemExit(f"{exc} Run `backfill` (or `fetch`) on the instance.") from exc


# --------------------------------------------------------------------------------------
# Broker commands -- the instance only, outside market hours
# --------------------------------------------------------------------------------------


def _market_hours() -> Optional[str]:
    from icici_breeze_backend.app.services.bots.scalping.backtest_fetch import market_hours_refusal
    from icici_breeze_backend.app.services.market_calendar import is_trading_day

    now = now_ist()
    return market_hours_refusal(now.replace(tzinfo=None), trading_day=is_trading_day(now))


def _fetcher(args: argparse.Namespace) -> Any:
    from icici_breeze_backend.app.core.requests_patch import apply_requests_patch
    from icici_breeze_backend.app.services.bots.scalping.backtest_fetch import Fetcher, resolve_sdk

    refusal = _market_hours()
    if refusal:
        raise SystemExit(refusal)
    apply_requests_patch()  # breeze_connect's session call needs GET-with-body, as in main.py
    return Fetcher(
        resolve_sdk(args.user_id), holidays=service.holidays(), max_calls=args.max_calls, stop=_market_hours
    )


def cmd_probe(args: argparse.Namespace) -> int:
    store.ensure_tables()
    fetcher = _fetcher(args)
    try:
        report = fetcher.probe(now_ist().date())
    except Stopped as exc:
        raise SystemExit(f"Stopped: {exc}") from exc
    print(json.dumps(report, indent=2, default=str))
    expired = (report.get("expired_nifty_weekly") or {}).get("verdict")
    print(f"\n{fetcher.calls} ICICI calls. Expired NIFTY weekly contracts: {expired}.")
    if expired != "served":
        print("Without expired contracts only `--model` replays are possible.")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    store.ensure_tables()
    start, end = _range(args)
    fetcher = _fetcher(args)
    try:
        service.fetch_underlying(fetcher, "expiry" if args.index != "NIFTY" else "momentum", start, end, [args.index])
    except Stopped as exc:
        print(f"Stopped: {exc}. Run again to resume.")
    print(f"\n{fetcher.calls} ICICI calls. Cache: {store.db_path()}")
    return 0


def cmd_fetch_options(args: argparse.Namespace) -> int:
    store.ensure_tables()
    pending = store.pending_needs()
    if not pending:
        print("No pending option windows. A replay on real prices queues them.")
        return 0
    fetcher = _fetcher(args)
    try:
        print(json.dumps(fetcher.fetch_needs(pending)))
    except Stopped as exc:
        print(f"Stopped: {exc}. Run again to resume.")
    print(f"{fetcher.calls} ICICI calls.")
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    store.ensure_tables()
    start, end = _range(args)
    scopes = _scopes(args) if args.bot == "expiry" else None
    fetcher = _fetcher(args)
    try:
        service.fetch_underlying(fetcher, args.bot, start, end, service.indices_for(args.bot, None, scopes))
        outcome = service.backfill(
            fetcher, args.bot, start=start, end=end, config=_config(args.bot, args),
            lots=args.lots, scopes=scopes, log=print,
        )
        print(f"\n{outcome['message']}")
    except Stopped as exc:
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
    print(f"  signal    : {config.signal.label()}, held until the call ends")
    print(f"  ladder    : target {config.exits.target_pts} / stop {config.exits.stop_loss_pts}")
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
    from collections import Counter

    store.ensure_tables()
    day = _iso(args.date)
    bot_type = service.BOT_TYPES[args.bot]
    paper = load_paper_cycles(bot_type, day, user_id=args.user_id)
    if not paper:
        raise SystemExit(f"No simulation {bot_type} cycles on {day}.")
    config = _stored_config(args.bot, paper[0].user_id)
    lots = None
    if args.bot == "fly":
        # Size the replay like the simulation session did; the fly's sizing has no history.
        lots = Counter(p.lots for p in paper if p.lots).most_common(1)[0][0]
    pricer = _pricer(args)
    result = _replay(args.bot, args, pricer, day, day, config=config, lots=lots)
    if result.days_awaiting_data:
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
        lines.insert(1, f"  replayed at {lots} lots, the simulation session's size")
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
    p.add_argument("--bot", choices=tuple(service.BOT_TYPES), required=True)
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

    p = sub.add_parser("compare", help="Simulation against backtest for one day")
    p.add_argument("--bot", choices=("momentum", "fly"), required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--model", action="store_true")
    p.add_argument("--user-id")
    p.set_defaults(func=cmd_compare)

    args = parser.parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
