"""Observed bid-ask spreads, sampled live so the backtest need not guess them.

The backtest prices a synthetic option, and the spread it assumes feeds both the fill prices
and the slippage term -- so a wrong spread biases every simulated cycle in the same
direction. Rather than pick a number, paper mode records what this deployment actually sees
and the backtest uses the median of that.

Spread is stored **alongside the premium it was observed at**, never on its own: option
spreads scale with premium, so a median in rupees across a Rs 30 option and a Rs 180 one
describes neither. The backtest asks for a *fraction of premium*, which is comparable.

Until enough samples exist there is a configured fallback, and `spread_stats()` reports which
source a run used -- a result priced off the default must never be mistaken for a calibrated
one (docs/bots-scalping-plan.md section 8).
"""
from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Optional

import icici_breeze_backend.app.core.config as cfg

_logger = logging.getLogger(__name__)

# Below this the median is noise, not a calibration.
MIN_SAMPLES_FOR_CALIBRATION = 200
# One sample per contract per minute. The bot reads a quote every pass (~5s while holding),
# and storing all of them would be tens of thousands of near-identical rows a session for a
# statistic that does not move that fast.
_SAMPLE_INTERVAL_SECONDS = 60.0
_RETENTION_DAYS = 30

# contract key -> last sample monotonic time
_last_sampled: dict[str, float] = {}


@dataclass(frozen=True)
class SpreadStats:
    source: str          # "observed" | "default"
    samples: int
    median_spread_pct: float
    median_spread_abs: Optional[float] = None

    def spread_for(self, premium: float, *, tick: float = 0.05) -> float:
        """Modelled spread at a given premium, never below one tick."""
        return max(tick, float(premium) * self.median_spread_pct / 100.0)

    def describe(self) -> str:
        if self.source == "observed":
            return (
                f"observed (n={self.samples}, median {self.median_spread_pct:.3f}% of premium"
                + (f", ~Rs {self.median_spread_abs:.2f}" if self.median_spread_abs else "")
                + ")"
            )
        return f"default ({self.median_spread_pct:.3f}% of premium, no calibration yet)"


def _db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


def record_spread_sample(
    stock_code: str,
    expiry_display: str,
    strike_price: float,
    right: str,
    bid: Optional[float],
    ask: Optional[float],
) -> bool:
    """Store one observation, throttled per contract. Returns True if written.

    Never raises: this is telemetry hanging off a trading path, and a failed insert must not
    interrupt a bot that is managing a position.
    """
    try:
        if bid is None or ask is None:
            return False
        b, a = float(bid), float(ask)
        if b <= 0 or a <= 0 or a < b:
            return False
        premium = (a + b) / 2.0
        if premium <= 0:
            return False

        key = f"{stock_code}|{expiry_display}|{strike_price}|{right}"
        now = time.monotonic()
        if now - _last_sampled.get(key, 0.0) < _SAMPLE_INTERVAL_SECONDS:
            return False
        _last_sampled[key] = now

        with sqlite3.connect(_db_path()) as conn:
            conn.execute(
                "INSERT INTO scalping_spread_samples "
                "(stock_code, expiry_display, strike_price, right, premium, spread) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (stock_code, expiry_display, float(strike_price), right, premium, a - b),
            )
            conn.commit()
        return True
    except Exception:  # noqa: BLE001 -- see docstring
        _logger.debug("scalping: spread sample not recorded", exc_info=True)
        return False


def spread_stats(*, default_pct: float = 0.5) -> SpreadStats:
    """Median observed spread as a share of premium, or the configured default."""
    try:
        with sqlite3.connect(_db_path()) as conn:
            rows = conn.execute(
                "SELECT premium, spread FROM scalping_spread_samples WHERE premium > 0"
            ).fetchall()
    except Exception:  # noqa: BLE001
        _logger.debug("scalping: spread samples unreadable", exc_info=True)
        rows = []

    pcts = sorted((s / p) * 100.0 for p, s in rows if p and p > 0)
    if len(pcts) < MIN_SAMPLES_FOR_CALIBRATION:
        return SpreadStats(source="default", samples=len(pcts), median_spread_pct=default_pct)
    abs_spreads = sorted(s for _, s in rows)
    return SpreadStats(
        source="observed",
        samples=len(pcts),
        median_spread_pct=_median(pcts),
        median_spread_abs=_median(abs_spreads),
    )


def _median(values: list[float]) -> float:
    n = len(values)
    if not n:
        return 0.0
    mid = n // 2
    return values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2.0


def prune_old_samples(*, days: int = _RETENTION_DAYS) -> int:
    with sqlite3.connect(_db_path()) as conn:
        cur = conn.execute(
            "DELETE FROM scalping_spread_samples "
            "WHERE observed_at < datetime('now', '+5 hours', '+30 minutes', ?)",
            (f"-{int(days)} days",),
        )
        conn.commit()
        return cur.rowcount or 0


def reset_throttle_for_tests() -> None:
    _last_sampled.clear()


def sample_from_quote(leg: dict[str, Any], bid: Optional[float], ask: Optional[float]) -> None:
    """Convenience for the trading path: record a leg's current two-sided quote."""
    record_spread_sample(
        str(leg.get("stock_code") or "NIFTY"),
        str(leg.get("expiry_display") or ""),
        float(leg.get("strike_price") or 0),
        str(leg.get("right") or ""),
        bid,
        ask,
    )
