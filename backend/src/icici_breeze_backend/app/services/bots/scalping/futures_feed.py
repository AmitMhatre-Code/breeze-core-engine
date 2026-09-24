"""Index futures tick feeds: NIFTY on NFO and BSESEN (SENSEX) on BFO.

They supply the one-minute bars every signal reads (docs/signals-streamline-plan.md section 3), and
the NIFTY feed's candle builder still serves the scalpers' re-entry range and warm-up checks
(docs/bots-scalping-plan.md section 3.2).

Why futures rather than the index
---------------------------------
The signal needs a volume surge filter and a session VWAP. The NIFTY *index* has neither --
an index has no traded quantity at all -- so both are only computable against the futures
contract. The source specification said "NIFTY spot" while also requiring a volume filter;
this is that contradiction resolved.

Why this bypasses the local scrip master
----------------------------------------
`scrip_master` holds only CE/PE rows and `ws_token_index.populate_ws_token_index_from_raw`
filters to `Series = "OPTION"` with a parseable strike, so there is **no futures token in
local reference data** and `subscribe_option`'s token path cannot reach one. The way out is
the one `index_spot_feed` already uses for the cash index: ask the SDK for the token from its
own SecurityMaster (`get_stock_token_value`), then subscribe. No reference-data change, no
new ingest, nothing to keep in sync.

The consequence is that an arriving futures tick is *unidentifiable* to the normal pipeline:
`ws_tick_normalize.parse_icici_tick` requires a strike and returns None without one, so the
chain path ignores these ticks entirely. That is why this module listens on
`register_raw_tick_listener`, which fires before any parsing
(`ws_tick_pipeline.ingest_tick`), and matches ticks by the token symbol it subscribed.

The `sdk.interval` hazard
-------------------------
`subscribe_feeds(interval=...)` would give broker-computed OHLCV bars directly, and is NOT
used. It sets `self.interval` on the shared SDK, and `get_stock_token_value` reads that:

    if self.interval == "" or self.interval == None:
        exchange_code_list["BFO"] = "8."

so a non-empty interval silently flips BFO token resolution from `8.` to `2.`, while this app
hard-codes `BFO: "8.1"` in `ws_token_index.EXCHANGE_TO_WS_PREFIX`. One OHLCV subscribe would
break every SENSEX subscription and tick route in the process, with no error raised. Building
candles from plain quote ticks avoids the whole class of problem -- see `candles`.
"""
from __future__ import annotations

import datetime
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.services.bots.scalping.candles import CandleBuilder

_logger = logging.getLogger(__name__)

INDEX_STOCK_CODE = "NIFTY"
INDEX_EXCHANGE = cfg.NFO

# The SDK keys its SecurityMaster as `FUT-{underlying}-{expiry}` with the expiry copied
# verbatim from the FONSE CSV column. This app normalises the same column to `DD-MMM-YYYY`,
# but the raw file's casing/format is not observable off the production static IP, so the
# token is resolved by trying candidates in order and remembering the one that worked.
# Cheap (a dict lookup inside the SDK, no network) and self-correcting.
_EXPIRY_FORMAT_CANDIDATES = ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d%b%Y")


@dataclass(frozen=True)
class FuturesContract:
    stock_code: str
    expiry_display: str  # DD-MMM-YYYY, this app's canonical form
    expiry_date: datetime.date


def _parse_display(display: str) -> Optional[datetime.date]:
    try:
        return datetime.datetime.strptime(str(display).strip(), "%d-%b-%Y").date()
    except (TypeError, ValueError):
        return None


def monthly_expiries(option_expiries: list[str]) -> list[datetime.date]:
    """Monthly expiries derived from the option expiry list: the last one in each month.

    NIFTY futures are monthly-only and expire alongside the month's final weekly option
    expiry, so the options list -- which this app *does* hold -- yields the futures calendar
    without a futures scrip master. Deriving it beats hardcoding "last Thursday": SEBI has
    moved expiry weekdays before, which is the same reason `scheduler._expiring_today` reads
    the scrip master rather than a weekday rule.
    """
    by_month: dict[tuple[int, int], datetime.date] = {}
    for raw in option_expiries or []:
        d = _parse_display(raw)
        if d is None:
            continue
        key = (d.year, d.month)
        if key not in by_month or d > by_month[key]:
            by_month[key] = d
    return sorted(by_month.values())


def near_month_contract(
    option_expiries: list[str],
    *,
    today: Optional[datetime.date] = None,
    stock_code: str = INDEX_STOCK_CODE,
) -> Optional[FuturesContract]:
    """The contract the signal reads: near-month, rolling on expiry day.

    "Roll on expiry day" means the near month stays current *through* its own expiry session
    and the next month is used from the following day, so `>= today` is the right comparison.
    The known cost, accepted when this was chosen over a days-before roll: in the final
    sessions liquidity migrates to the next series, so the volume filter is reading a
    thinning contract exactly then.
    """
    today = today or now_ist().date()
    for expiry in monthly_expiries(option_expiries):
        if expiry >= today:
            return FuturesContract(
                stock_code=stock_code,
                expiry_display=expiry.strftime("%d-%b-%Y"),
                expiry_date=expiry,
            )
    return None


def _expiry_candidates(contract: FuturesContract) -> list[str]:
    seen: list[str] = []
    for fmt in _EXPIRY_FORMAT_CANDIDATES:
        value = contract.expiry_date.strftime(fmt)
        for variant in (value, value.upper()):
            if variant not in seen:
                seen.append(variant)
    return seen


class FuturesFeed:
    """Owns the subscription, the raw-tick listener and the candle builder for one contract.

    One instance per index per process. Thread-safety matters here and not in `candles`: the
    SDK's tick callback runs on the socket thread while the bot's decision loop reads indicators
    from its own, so every touch of the builder is under `_lock`.
    """

    def __init__(self, stock_code: str = INDEX_STOCK_CODE, exchange: str = INDEX_EXCHANGE) -> None:
        self.stock_code = stock_code
        self.exchange = exchange
        self._last_tick_at: Optional[float] = None
        self._lock = threading.RLock()
        self._builder = CandleBuilder()
        self._contract: Optional[FuturesContract] = None
        self._token_symbol: Optional[str] = None
        # A second consumer of the same quote ticks: the signal publisher's bar builder
        # (`index_signal.publisher`). Called outside the lock with (payload, receive ts).
        self._quote_observer: Any = None
        self._expiry_format: Optional[str] = None
        self._listener_registered = False
        self._subscribed_date: Optional[datetime.date] = None
        # Wall-clock time of the last successful subscribe: the quiet clock's origin until the
        # first tick arrives, so a subscribe that never delivers is caught too.
        self._subscribed_at: Optional[float] = None
        # How many times today's subscription was found dead and dropped for a re-subscribe.
        self._resubscribes = 0
        self._last_error: Optional[str] = None
        self._ticks_seen = 0

    # ------------------------------------------------------------ subscription

    def _resolve_token(self, sdk: Any, contract: FuturesContract) -> Optional[str]:
        """Ask the SDK for the futures token, trying each expiry format until one resolves.

        `get_stock_token_value` reports a miss by calling `subscribe_exception`, which raises,
        so a wrong format surfaces as an exception rather than a falsy return -- both are
        treated as "try the next candidate".
        """
        # The SDK reads `self.interval` while resolving BFO tokens and never initialises it
        # in __init__ (see module docstring, and the same guard in `index_spot_feed`).
        if not hasattr(sdk, "interval"):
            sdk.interval = ""
        for candidate in _expiry_candidates(contract):
            try:
                result = sdk.get_stock_token_value(
                    exchange_code=self.exchange,
                    stock_code=contract.stock_code,
                    product_type="futures",
                    expiry_date=candidate,
                    get_exchange_quotes=True,
                    get_market_depth=False,
                )
            except Exception:  # noqa: BLE001
                _logger.debug("futures feed: expiry %r raised during lookup", candidate)
                continue
            # The SDK swallows its own failures and *returns* the exception object rather
            # than raising (`except Exception as e: return e`), so an unresolved contract
            # arrives as a non-tuple. `index_spot_feed` documents the same hazard. Check the
            # shape explicitly instead of relying on the tuple unpack to blow up.
            if not isinstance(result, tuple) or len(result) != 2:
                _logger.debug(
                    "futures feed: expiry format %r did not resolve a token (%r)",
                    candidate,
                    type(result).__name__,
                )
                continue
            exch_token, _depth = result
            if exch_token:
                self._expiry_format = candidate
                _logger.info(
                    "futures feed: resolved %s %s token=%s (expiry format %r)",
                    contract.stock_code,
                    contract.expiry_display,
                    exch_token,
                    candidate,
                )
                return str(exch_token)
        return None

    def ensure_subscribed(self, proc: Any, user_id: str, option_expiries: list[str]) -> bool:
        """Idempotent per trading day. False means no feed -- the caller must not latch.

        Returns False rather than raising when there is no session, mirroring
        `index_spot_feed.sync_index_spot_subscriptions`: a subscribe that never happened must
        not be recorded as today's success, or a session that was dead at market open leaves
        the feed cold until the process restarts.
        """
        from icici_breeze_backend.app.services.breeze_websocket_manager import (
            _ensure_ws,
            _reset_stale_auth_latch,
            _subscribe_feeds_error,
        )
        from icici_breeze_backend.app.services import ws_tick_pipeline

        today = now_ist().date()
        contract = near_month_contract(option_expiries, today=today, stock_code=self.stock_code)
        if contract is None:
            self._last_error = f"No monthly {self.stock_code} expiry available from the scrip master."
            _logger.warning("futures feed: %s", self._last_error)
            return False

        with self._lock:
            same_contract = self._contract == contract
            if self._subscribed_date == today and same_contract and self._token_symbol:
                return True
            if not same_contract and self._contract is not None:
                # Rolling to a new series: the old contract's cumulative counters have no
                # relationship to the new one's, so the history must go rather than produce
                # one absurd bar across the boundary.
                _logger.info(
                    "futures feed: rolling %s -> %s, resetting candles",
                    self._contract.expiry_display,
                    contract.expiry_display,
                )
                self._builder = CandleBuilder()
                self._token_symbol = None

        sdk = _ensure_ws(proc, user_id)
        if sdk is None:
            self._last_error = "No live broker session; futures feed not subscribed."
            return False
        _reset_stale_auth_latch(sdk)

        token = self._resolve_token(sdk, contract)
        if token is None:
            self._last_error = (
                f"Could not resolve a futures token for {contract.stock_code} "
                f"{contract.expiry_display} in any known expiry format."
            )
            _logger.warning("futures feed: %s", self._last_error)
            return False

        try:
            err = _subscribe_feeds_error(
                sdk.subscribe_feeds(
                    exchange_code=self.exchange,
                    stock_code=contract.stock_code,
                    product_type="futures",
                    expiry_date=self._expiry_format,
                    get_exchange_quotes=True,
                    get_market_depth=False,
                )
            )
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"subscribe_feeds raised: {exc}"
            _logger.warning("futures feed: %s", self._last_error, exc_info=True)
            return False
        if err is not None:
            # `subscribe_feeds` reports failure by *returning* an error string rather than
            # raising -- see the breeze_connect subscribe hazards noted in the WS manager.
            self._last_error = f"Broker rejected the futures subscribe: {err}"
            _logger.warning("futures feed: %s", self._last_error)
            return False

        with self._lock:
            self._contract = contract
            self._token_symbol = self._format_symbol(token)
            self._subscribed_date = today
            self._subscribed_at = time.time()
            self._last_error = None
            if not self._listener_registered:
                ws_tick_pipeline.register_raw_tick_listener(self._on_raw_tick)
                self._listener_registered = True
        return True

    def _format_symbol(self, token: str) -> str:
        from icici_breeze_backend.app.services.reference_data.ws_token_index import (
            EXCHANGE_TO_WS_PREFIX,
        )

        raw = str(token).strip()
        # The SDK may hand back a bare token or an already-prefixed `4.1!nnnn`.
        if "!" in raw:
            return raw
        return f"{EXCHANGE_TO_WS_PREFIX[self.exchange]}!{raw}"

    # ------------------------------------------------------------ ticks

    def _on_raw_tick(self, payload: Any) -> None:
        """Raw listener. Must never raise -- `ingest_tick` swallows, but silently."""
        try:
            if not isinstance(payload, dict):
                return
            with self._lock:
                wanted = self._token_symbol
            if wanted is None:
                return
            symbol = str(payload.get("symbol") or "").strip()
            if symbol != wanted:
                return
            now = time.time()
            with self._lock:
                self._ticks_seen += 1
                self._last_tick_at = now
                self._builder.ingest(
                    now,
                    payload.get("last"),
                    payload.get("ttq"),
                    payload.get("ttv"),
                    payload.get("avgPrice"),
                )
                observer = self._quote_observer
            if observer is not None:
                observer(payload, now)
        except Exception:  # noqa: BLE001
            _logger.debug("futures feed: raw tick handling failed", exc_info=True)

    def invalidate_subscription(self, reason: str) -> None:
        """Forget that today's subscribe succeeded, so the next `ensure_subscribed` re-issues it.

        `subscribed_today` is a latch, and a rebuilt socket (`breeze_websocket_manager.
        reconnect_ws`) does not carry this contract's room across: on 2026-09-24 the NIFTY
        future went silent at 13:34 while the latch stayed set, and nothing re-subscribed it
        before the close. The token and candles are kept -- the contract has not changed, only
        whether ICICI is still feeding it.
        """
        with self._lock:
            if self._subscribed_date is not None:
                self._resubscribes += 1
            self._subscribed_date = None
            self._last_error = reason

    def quiet_seconds(self, now: float) -> Optional[float]:
        """Seconds since this contract last ticked (or was subscribed, if it never has).

        None when there is no subscription today to judge. Deliberately this feed's own clock,
        not `ws_tick_pipeline.last_tick_age_seconds`: option-chain ticks keep that one fresh
        while the future is dead.
        """
        with self._lock:
            if not self.subscribed_today:
                return None
            marks = [t for t in (self._last_tick_at, self._subscribed_at) if t is not None]
            if not marks:
                return None
            return max(0.0, now - max(marks))

    def set_quote_observer(self, observer: Any) -> None:
        """Hand every quote tick of the traded contract to `observer(payload, ts)` as well."""
        with self._lock:
            self._quote_observer = observer

    def flush(self, now_ts: float) -> None:
        """Close a bar the clock has left even if the contract did not print."""
        with self._lock:
            self._builder.flush(now_ts)

    # ------------------------------------------------------------ read

    @property
    def builder(self) -> CandleBuilder:
        return self._builder

    @property
    def contract(self) -> Optional[FuturesContract]:
        return self._contract

    @property
    def last_tick_at(self) -> Optional[float]:
        with self._lock:
            return self._last_tick_at

    @property
    def subscribed_today(self) -> bool:
        """True when this trading day's subscribe has already succeeded.

        Public so the driver can skip the expiry lookup on the overwhelming majority of
        passes. `ensure_subscribed` is idempotent, but the caller has to *build* the option
        expiry list before it can ask, and that read falls back to a DISTINCT over the whole
        NFO scrip master whenever the reference-data cache is cold -- not something to pay
        every two seconds for an answer that changes once a day.
        """
        with self._lock:
            return self._subscribed_date == now_ist().date() and self._token_symbol is not None

    def status(self, *, ema_period: int, volume_ma_period: int) -> dict[str, Any]:
        """Everything a run-log entry needs to explain a no-signal session."""
        with self._lock:
            status = self._builder.warmup_status(
                ema_period=ema_period, volume_ma_period=volume_ma_period
            )
            status.update(
                {
                    "contract": self._contract.expiry_display if self._contract else None,
                    "token_symbol": self._token_symbol,
                    "expiry_format": self._expiry_format,
                    "ticks_seen": self._ticks_seen,
                    "subscribed_today": self.subscribed_today,
                    "quiet_seconds": self.quiet_seconds(time.time()),
                    "resubscribes": self._resubscribes,
                    "last_error": self._last_error,
                }
            )
            return status


# Kept for callers and tests written when NIFTY was the only feed.
NiftyFuturesFeed = FuturesFeed

# index -> (ICICI futures stock code, exchange segment)
FEED_CONTRACTS: dict[str, tuple[str, str]] = {
    "nifty": (INDEX_STOCK_CODE, INDEX_EXCHANGE),
    "sensex": ("BSESEN", cfg.BFO),
}

_feeds: dict[str, FuturesFeed] = {}
_feed_lock = threading.Lock()


def get_feed(index: str = "nifty") -> FuturesFeed:
    with _feed_lock:
        feed = _feeds.get(index)
        if feed is None:
            stock_code, exchange = FEED_CONTRACTS[index]
            feed = FuturesFeed(stock_code, exchange)
            _feeds[index] = feed
        return feed


def invalidate_all(reason: str) -> None:
    """Drop every feed's subscription latch -- the socket they were subscribed on is gone."""
    with _feed_lock:
        feeds = list(_feeds.values())
    for feed in feeds:
        feed.invalidate_subscription(reason)


def reset_feed_for_tests() -> None:
    with _feed_lock:
        _feeds.clear()
