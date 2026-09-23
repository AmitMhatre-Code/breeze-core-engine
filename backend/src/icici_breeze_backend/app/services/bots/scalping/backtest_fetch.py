"""The broker-side half of the backtests: everything that spends ICICI calls (plan section 8.7).

Runs only in `scripts/scalping_backtest.py`, on the EC2 instance (the static IP), and only
outside market hours. That last rule is not politeness. The script is its own process, so the
API server's per-user limiter (`icici_api_pacing`, 90 calls a minute) cannot see its calls:
a fetch running during the session would stack its calls on top of the server's and push the
account past ICICI's ~100 a minute, and the calls that would then be throttled include a live
bot's exits. Refusing between 09:00 and 15:45 IST on a trading day is the whole defence
(decided 2026-09-13), so it is checked before a session is even opened.

Every call is spaced, counted against a per-run budget, and read for errors. The first
version of this fetcher stored "0 bars" for every chunk because the SDK refuses an NFO request
without an expiry before it reaches ICICI -- and nothing printed the refusal. Here, a response
that is not a 200 is reported, never silently stored as empty.
"""
from __future__ import annotations

import datetime
import json
import logging
import time
from typing import Any, Callable, Iterable, Optional

from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime
from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
from icici_breeze_backend.app.services.bots.scalping.backtest_store import Need, OptionKey

_logger = logging.getLogger(__name__)

MARKET_HOURS_BLOCK = (datetime.time(9, 0), datetime.time(15, 45))
# ICICI's per-call row cap, measured 2026-09-15 at both 1-minute and 1-second. A longer window
# is cut from the START -- the latest rows come back -- so a truncated chunk loses its first days.
DEFAULT_MAX_BARS_PER_CALL = 1000
# One call a second is 60 a minute, well under the ~100 ICICI allows, leaving room for
# whatever the API server itself does after hours.
CALL_SPACING_SECONDS = 1.0
DEFAULT_MAX_CALLS = 1500
VIX_CHUNK_DAYS = 28

META_MAX_BARS = "max_bars_per_call"
# "ist": ICICI reads a request's time as IST; "utc": as UTC. Measured 2026-09-15: IST.
META_CLOCK = "request_clock"


class Stopped(RuntimeError):
    """The run stopped before finishing; everything fetched so far is already stored."""


class BudgetExhausted(Stopped):
    """The run's call budget is spent."""


class AllowanceSpent(Stopped):
    """The deployment's shared ICICI allowance for today is down to its reserve.

    A sibling of `BudgetExhausted`, not the same thing: that one is this run's own budget and
    is raised by the run's own counter, while this is the whole deployment's day and is decided
    by `api_usage.advisory_budget_exhausted`. They have different remedies -- raise the backtest
    budget, versus wait for IST midnight -- so they are never reported as one
    (docs/design-decisions.md #42).
    """


def shed_refusal(response: Any) -> bool:
    """True when the app held this call back itself rather than asking ICICI.

    `icici_api_pacing.build_shed_error` marks these; they carry HTTP 429 but never left the
    process, so reading one as a broker throttle sends an operator to look for a problem at
    ICICI's end that does not exist.
    """
    return isinstance(response, dict) and bool(response.get("advisory_shed"))


def market_hours_refusal(now: datetime.datetime, *, trading_day: bool) -> Optional[str]:
    start, end = MARKET_HOURS_BLOCK
    if trading_day and start <= now.time() < end:
        return (
            f"Refusing to call ICICI at {now:%H:%M} IST on a trading day. This script's calls "
            "are invisible to the API server's rate limiter, so during the session they would "
            "compete with live orders. Run it before 09:00 or after 15:45 IST, or on a "
            "non-trading day."
        )
    return None


def resolve_sdk(user_id: Optional[str] = None) -> Any:
    """A live Breeze session for the (single) user holding one on this deployment."""
    from icici_breeze_backend.app.repositories.broker_session import list_users_with_session
    from icici_breeze_backend.app.services.processor import processor

    if user_id is None:
        users = list_users_with_session()
        if not users:
            raise SystemExit(
                "No live ICICI session on this deployment. Log in through the app (the session "
                "lasts until midnight IST), then run this again."
            )
        if len(users) > 1:
            raise SystemExit(f"Several users hold a session ({', '.join(users)}); pass --user-id.")
        user_id = users[0]
    sdk = processor().get_session_breeze(user_id)
    if sdk is None:
        raise SystemExit(f"Could not open a Breeze session for {user_id}; see the backend log.")
    return sdk


def parse_response(response: Any) -> tuple[list[dict[str, Any]], Optional[str]]:
    """(rows, error). A non-200 is an error with its text, never an empty success."""
    if not isinstance(response, dict):
        return [], f"unexpected response: {str(response)[:200]}"
    status = response.get("Status", response.get("status"))
    if status != 200:
        error = response.get("Error") or response.get("error") or f"status {status}"
        return [], str(error)[:300]
    success = response.get("Success", response.get("success"))
    return (success if isinstance(success, list) else []), None


def _rows_between(rows: list[dict[str, Any]], start: datetime.datetime, end: datetime.datetime) -> int:
    n = 0
    for row in rows:
        ts = store._parse_ts(row.get("datetime") or row.get("date"))
        if ts is not None and start <= ts <= end:
            n += 1
    return n


def _day_window(first: datetime.date, last: datetime.date) -> tuple[str, str]:
    """Whole days, midnight to midnight. Correct under either request clock, which matters
    because the clock is only known once `probe` has measured it."""
    return f"{first.isoformat()}T00:00:00.000Z", f"{last.isoformat()}T23:59:59.000Z"


class Fetcher:
    def __init__(
        self,
        sdk: Any,
        *,
        path: Optional[str] = None,
        holidays: Optional[set[datetime.date]] = None,
        max_calls: Optional[int] = DEFAULT_MAX_CALLS,
        log: Callable[[str], None] = print,
        sleep: Callable[[float], None] = time.sleep,
        stop: Optional[Callable[[], Optional[str]]] = None,
    ) -> None:
        self.sdk = sdk
        self.path = path
        self.holidays = holidays or set()
        self.max_calls = max_calls
        self.log = log
        self.sleep = sleep
        # Asked before every call; a reason means stop now. The market-hours rule is checked
        # here too, so a run started at 08:40 stops itself at 09:00 rather than running on.
        self.stop = stop
        self.calls = 0
        self._clock_warned = False

    # -- plumbing ------------------------------------------------------------------------

    def _tick(self) -> None:
        if self.stop is not None:
            reason = self.stop()
            if reason:
                raise Stopped(reason)
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise BudgetExhausted(
                f"today's backtest budget of {self.max_calls} ICICI calls ran out mid-fetch, so "
                "the gap below is only partly filled. Run it again after IST midnight for a "
                "fresh budget, or raise it now in Settings \u2192 API Usage \u2192 Backtest "
                "call budget."
            )
        if self.calls:
            self.sleep(CALL_SPACING_SECONDS)
        self.calls += 1

    def call(self, **params: Any) -> tuple[list[dict[str, Any]], Optional[str]]:
        self._tick()
        try:
            response = self.sdk.get_historical_data_v2(**params)
        except Exception as exc:  # noqa: BLE001 -- reported to the operator, never swallowed
            return [], f"request failed: {exc}"
        if shed_refusal(response):
            # Terminal, and not a property of this window: the allowance is a fact about the
            # day, so the next request cannot succeed either. Returning it as a per-window
            # error instead cost a 9-month fetch 1,631 refusals, 34 minutes of spacing sleep
            # and 1,631 units of its own budget, all of it after the answer was already known
            # (2026-09-23; docs/design-decisions.md #42).
            from icici_breeze_backend.app.services.api_usage import (
                API_CALLS_LIMIT_PER_DAY,
                AMBER_MAX,
            )

            raise AllowanceSpent(
                f"this deployment has used {AMBER_MAX} of its {API_CALLS_LIMIT_PER_DAY} ICICI "
                f"calls for today, so the app is holding the rest back for placing and "
                f"cancelling orders and will not spend any more on a backtest. Everything "
                f"fetched so far is kept; fetch again after IST midnight to carry on."
            )
        rows, error = parse_response(response)
        if not error and len(rows) >= self.max_bars():
            self.log(
                f"    warning: {len(rows)} bars is the per-call cap; this window is probably "
                "truncated. Run `probe`, or re-run `fetch` to fill the gap."
            )
        return rows, error

    def max_bars(self) -> int:
        raw = store.get_meta(META_MAX_BARS, path=self.path)
        try:
            return max(1, int(raw)) if raw else DEFAULT_MAX_BARS_PER_CALL
        except ValueError:
            return DEFAULT_MAX_BARS_PER_CALL

    def days_per_chunk(self) -> int:
        return max(1, self.max_bars() // store.SESSION_BARS)

    def clock(self) -> str:
        value = store.get_meta(META_CLOCK, path=self.path)
        if value in ("ist", "utc"):
            return value
        if not self._clock_warned:
            self.log("    note: request clock not yet measured by `probe`; assuming IST.")
            self._clock_warned = True
        return "ist"

    def stamp(self, at: datetime.datetime, clock: Optional[str] = None) -> str:
        """A request time for an intraday window, in whichever clock ICICI reads."""
        if (clock or self.clock()) == "utc":
            at = at - datetime.timedelta(hours=5, minutes=30)
        return at.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def _chunks(self, days: list[datetime.date], key: Callable[[datetime.date], Any]) -> Iterable[list[datetime.date]]:
        """Consecutive runs of `days` sharing `key` (the futures contract), cut to the cap."""
        size = self.days_per_chunk()
        run: list[datetime.date] = []
        for d in days:
            if run and (key(d) != key(run[0]) or len(run) >= size):
                yield run
                run = []
            run.append(d)
        if run:
            yield run

    # -- underlying ----------------------------------------------------------------------

    def fetch_futures(self, stock_code: str, start: datetime.date, end: datetime.date) -> int:
        """1-minute futures bars, rolling to each day's near-month contract.

        Only days short of a full session are requested, so a re-run refills gaps and
        truncations without re-spending calls on what is already complete.
        """
        counts = store.day_bar_counts(stock_code=stock_code, table="futures_candles", path=self.path)
        wanted = [
            d for d in regime.trading_days(start, end, self.holidays)
            if counts.get(d, 0) < store.COMPLETE_DAY_BARS
        ]
        total = 0
        contract = lambda d: regime.near_month_futures_expiry(d, stock_code, self.holidays)  # noqa: E731
        for chunk in self._chunks(wanted, contract):
            expiry = contract(chunk[0])
            frm, to = _day_window(chunk[0], chunk[-1])
            rows, error = self.call(
                interval=store.INTERVAL_MINUTE,
                from_date=frm,
                to_date=to,
                stock_code=stock_code,
                exchange_code=regime.OPTION_EXCHANGE[stock_code],
                product_type="futures",
                expiry_date=regime.expiry_api(expiry),
            )
            if error:
                self.log(f"  futures {chunk[0]} .. {chunk[-1]} ({expiry:%d-%b-%Y}): ERROR {error}")
                continue
            stored = store.store_candles(
                rows, stock_code=stock_code, table="futures_candles", expiry=expiry, path=self.path
            )
            total += stored
            self.log(f"  futures {chunk[0]} .. {chunk[-1]} ({expiry:%d-%b-%Y}): {stored} bars")
        return total

    def fetch_spot(self, stock_code: str, start: datetime.date, end: datetime.date) -> int:
        """1-minute bars of the cash index -- what the bots read spot from."""
        exchange, code = regime.SPOT_SOURCE[stock_code]
        counts = store.day_bar_counts(stock_code=stock_code, table="spot_candles", path=self.path)
        wanted = [
            d for d in regime.trading_days(start, end, self.holidays)
            if counts.get(d, 0) < store.COMPLETE_DAY_BARS
        ]
        total = 0
        for chunk in self._chunks(wanted, lambda d: None):
            frm, to = _day_window(chunk[0], chunk[-1])
            rows, error = self.call(
                interval=store.INTERVAL_MINUTE,
                from_date=frm,
                to_date=to,
                stock_code=code,
                exchange_code=exchange,
                product_type="cash",
            )
            if error:
                self.log(f"  {stock_code} index {chunk[0]} .. {chunk[-1]}: ERROR {error}")
                continue
            stored = store.store_candles(rows, stock_code=stock_code, table="spot_candles", path=self.path)
            total += stored
            self.log(f"  {stock_code} index {chunk[0]} .. {chunk[-1]}: {stored} bars")
        return total

    def fetch_vix(self, start: datetime.date, end: datetime.date) -> int:
        """Daily India VIX in chunks -- ICICI returns about a month of daily bars per call."""
        from icici_breeze_backend.app.services.dashboard_vix import _historical_vix

        total = 0
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + datetime.timedelta(days=VIX_CHUNK_DAYS - 1), end)
            self._tick()
            frm, to = _day_window(cursor, chunk_end)
            total += store.store_vix(_historical_vix(self.sdk, frm, to), path=self.path)
            cursor = chunk_end + datetime.timedelta(days=1)
        self.log(f"  India VIX {start} .. {end}: {total} days")
        return total

    # -- options -------------------------------------------------------------------------

    def _option_params(self, key: OptionKey) -> dict[str, Any]:
        return {
            "stock_code": key.stock_code,
            "exchange_code": regime.OPTION_EXCHANGE[key.stock_code],
            "product_type": "options",
            "expiry_date": regime.expiry_api(key.expiry),
            "right": key.right,
            "strike_price": str(int(key.strike)),
        }

    def fetch_need(self, need: Need) -> tuple[int, Optional[str]]:
        """Fetch one needed window and record that it was asked for. (rows stored, error)."""
        if need.interval == store.INTERVAL_MINUTE:
            frm, to = _day_window(need.start.date(), need.end.date())
        else:
            frm, to = self.stamp(need.start), self.stamp(need.end)
        rows, error = self.call(
            interval=need.interval, from_date=frm, to_date=to, **self._option_params(need.key)
        )
        if error and "no data" not in error.lower():
            # Left pending: a throttle or an outage is not an answer about the contract.
            return 0, error
        stored = store.store_option_candles(rows, need.key, need.interval, path=self.path)
        if need.interval == store.INTERVAL_SECOND and rows and not _rows_between(rows, need.start, need.end):
            self.log(
                f"    warning: {need.key.label()} 1-second bars all fall outside the window asked "
                "for -- the request clock is probably wrong. Run `probe`."
            )
        store.record_fetch(need, stored, path=self.path)
        return stored, None

    def fetch_needs(self, needs: Iterable[Need]) -> dict[str, int]:
        done = rows = errors = empty = 0
        for need in needs:
            stored, error = self.fetch_need(need)
            label = f"  {need.key.label()} {need.interval} {need.start:%Y-%m-%d %H:%M}"
            if error:
                errors += 1
                self.log(f"{label}: ERROR {error}")
                continue
            done += 1
            rows += stored
            empty += 0 if stored else 1
            self.log(f"{label}: {stored} bars")
        return {"fetched": done, "bars": rows, "empty": empty, "errors": errors}

    # -- probe ---------------------------------------------------------------------------

    def probe(self, today: datetime.date) -> dict[str, Any]:
        """Measure the four facts the whole design rests on, and record them.

        1. Does ICICI serve **expired** option contracts? (If not, only model pricing works.)
        2. How many candles does one call return? (Sets the chunk size.)
        3. Is a request's time read as IST or UTC? (Only 1-second windows depend on it.)
        4. How far back does option history go? (Is HISTORY_START reachable?)
        About a dozen calls.
        """
        report: dict[str, Any] = {}
        last = today - datetime.timedelta(days=1)
        while not regime.is_trading_day(last, self.holidays):
            last -= datetime.timedelta(days=1)

        # 2. The per-call cap: ask for five sessions of futures at once.
        days = regime.trading_days(last - datetime.timedelta(days=10), last, self.holidays)[-5:]
        frm, to = _day_window(days[0], days[-1])
        rows, error = self.call(
            interval=store.INTERVAL_MINUTE, from_date=frm, to_date=to, stock_code="NIFTY",
            exchange_code="NFO", product_type="futures",
            expiry_date=regime.expiry_api(regime.near_month_futures_expiry(days[-1], "NIFTY", self.holidays)),
        )
        expected = len(days) * store.SESSION_BARS
        report["futures_5_sessions"] = {"asked_for": expected, "returned": len(rows), "error": error}
        if rows and not error:
            capped = len(rows) < expected - 10
            report["per_call_cap"] = len(rows) if capped else f">= {len(rows)}"
            store.set_meta(META_MAX_BARS, str(len(rows) if capped else max(len(rows), DEFAULT_MAX_BARS_PER_CALL)), path=self.path)

        # Spot for both indices, on the last session.
        for index in ("NIFTY", "BSESEN"):
            report[f"{index}_index_bars"] = self._probe_spot(index, last)[0]

        # 1 and 3. An expired NIFTY weekly, about five weeks back.
        report["expired_nifty_weekly"] = self._probe_option("NIFTY", last - datetime.timedelta(days=35), seconds=True)
        # 4. The oldest history the replays accept.
        report["history_start_nifty_weekly"] = self._probe_option(
            "NIFTY", regime.HISTORY_START + datetime.timedelta(days=7), seconds=False
        )
        report["expired_sensex_weekly"] = self._probe_option("BSESEN", last - datetime.timedelta(days=35), seconds=False)

        store.set_meta("probe_report", json.dumps(report, default=str), path=self.path)
        store.set_meta("probed_at", datetime.datetime.now().isoformat(timespec="seconds"), path=self.path)
        return report

    def _probe_spot(self, index: str, day: datetime.date) -> tuple[dict[str, Any], Optional[float]]:
        exchange, code = regime.SPOT_SOURCE[index]
        frm, to = _day_window(day, day)
        rows, error = self.call(
            interval=store.INTERVAL_MINUTE, from_date=frm, to_date=to, stock_code=code,
            exchange_code=exchange, product_type="cash",
        )
        closes = [
            (store._parse_ts(r.get("datetime")), float(r.get("close") or 0)) for r in rows
        ]
        closes = [(ts, c) for ts, c in closes if ts is not None and c > 0]
        last_close = max(closes)[1] if closes else None
        return (
            {"day": day.isoformat(), "bars": len(rows), "error": error, "last_close": last_close},
            last_close,
        )

    def _probe_option(self, index: str, around: datetime.date, *, seconds: bool) -> dict[str, Any]:
        expiry = regime.next_expiry(around, regime.EXPIRY_WEEKDAY_MAP[index], self.holidays)
        day = expiry - datetime.timedelta(days=1)
        while not regime.is_trading_day(day, self.holidays):
            day -= datetime.timedelta(days=1)
        spot_info, spot = self._probe_spot(index, day)
        out: dict[str, Any] = {"expiry": expiry.isoformat(), "day": day.isoformat(), "spot": spot_info}
        if spot is None:
            out["verdict"] = "no index bars for the day; cannot pick a strike"
            return out
        key = OptionKey(index, expiry, regime.atm_strike(spot, index), "call")
        out["contract"] = key.label()
        frm, to = _day_window(day, day)
        rows, error = self.call(interval=store.INTERVAL_MINUTE, from_date=frm, to_date=to, **self._option_params(key))
        out["minute_bars"] = len(rows)
        out["minute_error"] = error
        out["has_open_interest"] = any("open_interest" in r for r in rows[:5])
        if seconds and rows:
            start = datetime.datetime.combine(day, datetime.time(10, 0))
            end = start + datetime.timedelta(minutes=15) - datetime.timedelta(seconds=1)
            for clock in ("ist", "utc"):
                srows, serror = self.call(
                    interval=store.INTERVAL_SECOND, from_date=self.stamp(start, clock),
                    to_date=self.stamp(end, clock), **self._option_params(key),
                )
                inside = _rows_between(srows, start, end)
                out[f"second_bars_{clock}"] = {"returned": len(srows), "inside_window": inside, "error": serror}
                if inside:
                    store.set_meta(META_CLOCK, clock, path=self.path)
                    out["request_clock"] = clock
                    break
        out["verdict"] = "served" if rows else "NOT served"
        return out
