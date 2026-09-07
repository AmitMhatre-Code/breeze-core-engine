"""Round-trip cost model for EVERY bot, and for the backtest harness.

One deployment-wide record, edited in exactly one place -- Settings > Trading Costs. Not
per-bot: no two bots should disagree about what a trade costs, and the backtest must price a
trade identically to paper mode or the two describe different strategies.

For the scalpers friction is not a detail, it is the binding constraint: at roughly Rs 100 a
round trip, 80 cycles burn ~Rs 8,000 against a Rs 10,000 daily stop, so a bot can hit its
limit having been flat on the trades (docs/bots-scalping-plan.md section 6.4). For the
writers it matters less per trade but is still the difference between the premium quoted on a
proposal and the money actually received, which is the number a user is deciding on.

The rates were CALIBRATED, not looked up
----------------------------------------
Every value here was fitted to a real ICICI contract note -- 140 F&O fills across NSE and
BSE, 2026-08-03 to 2026-09-03 -- rather than taken from published summaries. That mattered:
three of the published figures this shipped with were wrong.

    brokerage      Rs 20 flat per order       confirmed, single value across all 140 fills
    STT (sell)     0.15%                      published summaries said 0.10%
    exchange txn   NSE 0.03545%, BSE 0.0325%  published said 0.0495%, and it is per-exchange
    IPFT           folded into exchange txn   billing it separately double-counted it
    SEBI           0.0001% (Rs 10 per crore)  confirmed
    stamp duty     0.003%, buy side only      confirmed
    GST            18% of brokerage+txn+SEBI  confirmed to within Rs 0.043 across all fills

`test_trading_charges.py` replays real rows from that note through this model and asserts
the predicted total matches the broker's to the paisa. If a rate changes, that test fails
with the row that disagrees.

They remain EDITABLE because they are set by regulation and change without notice. A code
edit, a build and a fleet redeploy is the wrong shape for a number the exchange can change,
and per-cycle friction is itemised into the run log so a future divergence surfaces as a
number that disagrees with a contract note rather than a silent bias in every backtest.

What is charged where (options, premium turnover = price x quantity):

  brokerage        per order, each way          flat rupees and/or % of premium, optionally capped
  STT              SELL side only               % of premium
  exchange txn     both sides                   % of premium, DIFFERENT PER EXCHANGE
  SEBI turnover    both sides                   % of premium
  IPFT             both sides                   % of premium -- 0 by default, see above
  stamp duty       BUY side only                % of premium
  GST              both sides                   % of (brokerage + exchange txn + SEBI + IPFT)

GST excludes STT and stamp duty: they are taxes, not services, and are not themselves taxed.
Confirmed against the note -- the STT-inclusive hypothesis is out by up to Rs 3.36 a fill.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional

import icici_breeze_backend.app.core.config as cfg

_logger = logging.getLogger(__name__)

_FIELDS = (
    "brokerage_per_order_inr",
    "brokerage_pct_of_premium",
    "brokerage_cap_inr",
    "stt_sell_pct",
    "exchange_txn_pct",
    "exchange_txn_pct_bse",
    "sebi_pct",
    "ipft_pct",
    "stamp_buy_pct",
    "gst_pct",
    "slippage_spread_fraction",
)


@dataclass(frozen=True)
class ChargesModel:
    brokerage_per_order_inr: float = 20.0
    brokerage_pct_of_premium: float = 0.0
    brokerage_cap_inr: Optional[float] = None
    stt_sell_pct: float = 0.15
    # NSE (NFO). BSE charges a different rate, so one field could only ever be right for one
    # exchange -- the scalpers are NIFTY/NFO today, but the model is shared.
    exchange_txn_pct: float = 0.03545
    exchange_txn_pct_bse: float = 0.0325
    sebi_pct: float = 0.0001
    # Zero by default: the contract note has no separate IPFT line, so it is already inside
    # the exchange transaction charge. Billing it again was a real double-count.
    ipft_pct: float = 0.0
    stamp_buy_pct: float = 0.003
    gst_pct: float = 18.0
    # Adverse slippage in paper mode, as a fraction of the bid-ask spread applied to EACH
    # leg. Paper mode fills at the touch, which is optimistic; this is the honest correction
    # for getting a worse price than the screen without pretending to model the book.
    slippage_spread_fraction: float = 0.5

    def brokerage_for(self, premium_turnover: float) -> float:
        """Per order. Flat, percentage, or the lower of the two when a cap is set."""
        flat = float(self.brokerage_per_order_inr)
        pct = float(self.brokerage_pct_of_premium) / 100.0 * float(premium_turnover)
        charge = flat + pct if not self.brokerage_cap_inr else min(flat + pct, float(self.brokerage_cap_inr))
        return max(0.0, charge)

    def txn_pct_for(self, exchange_code: str = "NFO") -> float:
        """Exchange transaction rate. BSE and NSE genuinely differ."""
        return (
            self.exchange_txn_pct_bse
            if str(exchange_code).upper() in ("BSE", "BFO")
            else self.exchange_txn_pct
        )

    def leg_charges(
        self, price: float, quantity: int, *, is_buy: bool, exchange_code: str = "NFO"
    ) -> float:
        """Total statutory + brokerage cost of ONE leg."""
        turnover = max(0.0, float(price)) * max(0, int(quantity))
        brokerage = self.brokerage_for(turnover)
        exchange = turnover * self.txn_pct_for(exchange_code) / 100.0
        sebi = turnover * self.sebi_pct / 100.0
        ipft = turnover * self.ipft_pct / 100.0
        stt = 0.0 if is_buy else turnover * self.stt_sell_pct / 100.0
        stamp = turnover * self.stamp_buy_pct / 100.0 if is_buy else 0.0
        gst = (brokerage + exchange + sebi + ipft) * self.gst_pct / 100.0
        return brokerage + exchange + sebi + ipft + stt + stamp + gst

    def round_trip(
        self, buy_price: float, sell_price: float, quantity: int, *, exchange_code: str = "NFO"
    ) -> float:
        """Both legs. This is the number section 6.4 is about."""
        return self.leg_charges(
            buy_price, quantity, is_buy=True, exchange_code=exchange_code
        ) + self.leg_charges(sell_price, quantity, is_buy=False, exchange_code=exchange_code)

    def breakdown(
        self, price: float, quantity: int, *, is_buy: bool, exchange_code: str = "NFO"
    ) -> dict[str, float]:
        """Itemised, for the run log -- so a disagreement with a contract note is findable."""
        turnover = max(0.0, float(price)) * max(0, int(quantity))
        brokerage = self.brokerage_for(turnover)
        exchange = turnover * self.txn_pct_for(exchange_code) / 100.0
        sebi = turnover * self.sebi_pct / 100.0
        ipft = turnover * self.ipft_pct / 100.0
        stt = 0.0 if is_buy else turnover * self.stt_sell_pct / 100.0
        stamp = turnover * self.stamp_buy_pct / 100.0 if is_buy else 0.0
        gst = (brokerage + exchange + sebi + ipft) * self.gst_pct / 100.0
        return {
            "turnover": round(turnover, 2),
            "brokerage": round(brokerage, 2),
            "exchange_txn": round(exchange, 4),
            "sebi": round(sebi, 4),
            "ipft": round(ipft, 4),
            "stt": round(stt, 2),
            "stamp_duty": round(stamp, 4),
            "gst": round(gst, 2),
            "total": round(brokerage + exchange + sebi + ipft + stt + stamp + gst, 2),
        }


def _db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


def load_charges() -> ChargesModel:
    """The configured model, falling back to shipped defaults if the row is unreadable.

    Never raises: a missing settings row must not stop a bot mid-session, and the defaults
    are the same numbers the row was seeded with.
    """
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                f"SELECT {', '.join(_FIELDS)} FROM trading_charges WHERE id = 1"
            ).fetchone()
        if row is None:
            return ChargesModel()
        data: dict[str, Any] = {k: row[k] for k in _FIELDS}
        # Only `brokerage_cap_inr` is nullable; everything else is NOT NULL with a default.
        return ChargesModel(**{k: v for k, v in data.items() if v is not None})
    except Exception:  # noqa: BLE001 -- see docstring
        _logger.warning("charges settings unreadable; using defaults", exc_info=True)
        return ChargesModel()


def save_charges(**updates: Any) -> ChargesModel:
    """Update named fields. Unknown keys are rejected rather than silently ignored."""
    unknown = set(updates) - set(_FIELDS)
    if unknown:
        raise ValueError(f"Unknown charge fields: {', '.join(sorted(unknown))}")
    if not updates:
        return load_charges()
    assignments = ", ".join(f"{k} = ?" for k in updates)
    with sqlite3.connect(_db_path()) as conn:
        conn.execute("INSERT OR IGNORE INTO trading_charges (id) VALUES (1)")
        conn.execute(
            f"UPDATE trading_charges SET {assignments}, "
            "updated_at = datetime('now', '+5 hours', '+30 minutes') WHERE id = 1",
            list(updates.values()),
        )
        conn.commit()
    return load_charges()
