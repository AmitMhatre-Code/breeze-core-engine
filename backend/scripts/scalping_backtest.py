#!/usr/bin/env python3
"""Backtest Bot 3's signal against real NIFTY futures history.

    # on the EC2 instance (needs a live broker session -- static IP only), once:
    PYTHONPATH=./src .venv/bin/python scripts/scalping_backtest.py fetch --from 2026-01-01

    # then, as often as you like -- no broker, pure CPU:
    PYTHONPATH=./src .venv/bin/python scripts/scalping_backtest.py replay
    PYTHONPATH=./src .venv/bin/python scripts/scalping_backtest.py replay --target-pts 12 --stop-loss-pts 5
    PYTHONPATH=./src .venv/bin/python scripts/scalping_backtest.py coverage

`fetch` and `replay` are split because fetching needs the broker and replaying does not.
Re-tuning a parameter should never re-spend API budget on candles already pulled.

WHAT THE RESULT MEANS: option prices here are modelled (Black-Scholes off real futures bars,
real daily VIX, observed spreads), because no historical option-price series exists locally.
This tests the SIGNAL honestly and the FILLS approximately -- see the module docstring of
`services/bots/scalping/backtest.py`. Paper mode tests the other half.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
import time

from icici_breeze_backend.app.domain.bots import MomentumLongScalperConfig
from icici_breeze_backend.app.services.bots.scalping import backtest_store
from icici_breeze_backend.app.services.bots.scalping.backtest import (
    DEFAULT_EXPIRY_WEEKDAY_MAP,
    run_backtest,
)
from icici_breeze_backend.app.services.bots.charges import load_charges
from icici_breeze_backend.app.services.bots.scalping.spreads import spread_stats

# One request per chunk. Breeze caps how much 1-minute data a single call returns, and a
# smaller window is also what makes a died-halfway fetch resumable -- the table's primary key
# makes re-running idempotent.
_FETCH_CHUNK_DAYS = 5
# The pacer serialises broker calls anyway; this is polite spacing on a bulk backfill that is
# not time-critical, so it never competes with a bot that is actually trading.
_FETCH_SLEEP_SECONDS = 1.0


def _iso(d: str) -> datetime.date:
    return datetime.date.fromisoformat(d)


def _breeze():
    from icici_breeze_backend.app.services.processor import processor

    proc = processor()
    sdk = getattr(proc, "breeze", None) or getattr(proc, "_breeze", None)
    if sdk is None:
        raise SystemExit(
            "No broker session. `fetch` must run on the production instance with a live "
            "ICICI session (see docs/bots-scalping-plan.md section 8)."
        )
    return sdk


def cmd_fetch(args: argparse.Namespace) -> int:
    backtest_store.ensure_tables()
    sdk = _breeze()
    start, end = _iso(args.from_date), _iso(args.to_date) if args.to_date else datetime.date.today()
    if start > end:
        raise SystemExit("--from must be on or before --to")

    total = 0
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + datetime.timedelta(days=_FETCH_CHUNK_DAYS - 1), end)
        try:
            response = sdk.get_historical_data_v2(
                interval="1minute",
                from_date=f"{cursor.isoformat()}T00:00:00.000Z",
                to_date=f"{chunk_end.isoformat()}T23:59:59.000Z",
                stock_code="NIFTY",
                exchange_code="NFO",
                product_type="futures",
                expiry_date=args.expiry or "",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  {cursor} .. {chunk_end}: request failed ({exc})", file=sys.stderr)
            cursor = chunk_end + datetime.timedelta(days=1)
            continue
        rows = (response or {}).get("Success") or []
        stored = backtest_store.store_candles(rows)
        total += stored
        print(f"  {cursor} .. {chunk_end}: {stored} bars")
        cursor = chunk_end + datetime.timedelta(days=1)
        time.sleep(_FETCH_SLEEP_SECONDS)

    from icici_breeze_backend.app.services.dashboard_vix import _historical_vix

    vix_rows = _historical_vix(sdk, f"{start.isoformat()}T00:00:00.000Z", f"{end.isoformat()}T23:59:59.000Z")
    vix_stored = backtest_store.store_vix(vix_rows)
    print(f"\nStored {total} candles and {vix_stored} VIX days into {backtest_store.db_path()}")
    return 0


def cmd_coverage(_args: argparse.Namespace) -> int:
    backtest_store.ensure_tables()
    print(json.dumps(backtest_store.coverage(), indent=2))
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    backtest_store.ensure_tables()
    bars = backtest_store.load_candles(
        from_date=_iso(args.from_date) if args.from_date else None,
        to_date=_iso(args.to_date) if args.to_date else None,
    )
    if not bars:
        raise SystemExit(
            "No cached candles. Run `fetch` on the production instance first, then "
            "`coverage` to see what is stored."
        )

    config = MomentumLongScalperConfig()
    exits = config.exits.model_copy(
        update={
            k: v
            for k, v in (
                ("target_pts", args.target_pts),
                ("stop_loss_pts", args.stop_loss_pts),
                ("time_invalidation_seconds", args.time_stop),
            )
            if v is not None
        }
    )
    config = config.model_copy(update={"exits": exits})

    holidays = set()
    try:
        from icici_breeze_backend.app.services.market_calendar import get_calendar_config

        holidays = {
            datetime.date.fromisoformat(d) for d in get_calendar_config().holidays
        }
    except Exception:  # noqa: BLE001 -- a missing calendar only costs holiday-shifted expiries
        print("warning: exchange calendar unavailable; expiries are not holiday-shifted", file=sys.stderr)

    result = run_backtest(
        bars,
        config=config,
        charges=load_charges(),
        spread=spread_stats(),
        vix_by_day=backtest_store.load_vix(),
        holidays=holidays,
        weekday_map=DEFAULT_EXPIRY_WEEKDAY_MAP,
    )

    summary = result.summary()
    print("\n=== Bot 3 signal backtest ===")
    print(f"  bars      : {len(bars)}  ({bars[0].ts} .. {bars[-1].ts})")
    print(f"  ladder    : target {config.exits.target_pts} / stop {config.exits.stop_loss_pts} "
          f"/ time {config.exits.time_invalidation_seconds}s")
    print(f"  IV source : {summary['iv_source']}")
    print(f"  spread    : {summary['spread_source']}")
    print()
    for key in (
        "days", "cycles", "cycles_per_day", "win_rate_pct",
        "gross_pnl", "friction", "net_pnl", "friction_pct_of_gross",
        "skipped_no_signal", "skipped_unaffordable",
    ):
        print(f"  {key:24s}: {summary[key]}")
    print(
        "\n  Modelled option prices: this tests the SIGNAL over real history and the FILLS "
        "only approximately.\n  Paper mode is what tests execution."
    )

    if args.csv:
        import csv

        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow([
                "entered_at", "exited_at", "right", "strike", "lots", "quantity",
                "entry_price", "exit_price", "gross_pnl", "friction", "net_pnl",
                "exit_reason", "spot_entry", "spot_exit", "iv",
            ])
            for c in result.cycles:
                writer.writerow([
                    c.entered_at, c.exited_at, c.right, c.strike, c.lots, c.quantity,
                    c.entry_price, c.exit_price, c.gross_pnl, c.friction, c.net_pnl,
                    c.exit_reason, c.spot_entry, c.spot_exit, round(c.iv, 4),
                ])
        print(f"\n  Wrote {len(result.cycles)} cycles to {args.csv}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    f = sub.add_parser("fetch", help="Download futures candles + VIX (production only)")
    f.add_argument("--from", dest="from_date", required=True, help="YYYY-MM-DD")
    f.add_argument("--to", dest="to_date", help="YYYY-MM-DD (default: today)")
    f.add_argument("--expiry", help="Futures expiry, DD-MMM-YYYY (default: broker's near month)")
    f.set_defaults(func=cmd_fetch)

    c = sub.add_parser("coverage", help="Show what is cached")
    c.set_defaults(func=cmd_coverage)

    r = sub.add_parser("replay", help="Replay the signal over cached candles (no broker)")
    r.add_argument("--from", dest="from_date", help="YYYY-MM-DD")
    r.add_argument("--to", dest="to_date", help="YYYY-MM-DD")
    r.add_argument("--target-pts", type=float, help="Override the runner trigger")
    r.add_argument("--stop-loss-pts", type=float, help="Override the initial stop")
    r.add_argument("--time-stop", type=int, help="Override time invalidation, seconds")
    r.add_argument("--csv", help="Write per-cycle rows to this file")
    r.set_defaults(func=cmd_replay)

    args = parser.parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
