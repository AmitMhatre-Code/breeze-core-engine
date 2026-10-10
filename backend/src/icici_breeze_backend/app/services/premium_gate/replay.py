"""The premium gate on replayed inputs (docs/premium-gate-plan.md section 4).

The same `reading` the live bots compute, from the same kind of data: the cash index's one-minute
bars before the day (fetched a month and a half before the range), and the ATM call and put at
the decision minute. History has no order book, so the pair is priced at its traded bar open
rather than a two-sided mid; each run's notes say so.
"""
from __future__ import annotations

import datetime
from typing import Any, Optional, Sequence

from icici_breeze_backend.app.services.premium_gate import reading as pg

#: Calendar days of cash-index bars a replay loads before its range, for the forecast.
WARMUP_DAYS = 45

#: Thresholds every gated bot's backtest compares, beside "off" (plan decision 2).
COMPARED_THRESHOLDS = (0.9, 1.0, 1.2)

NOTE = ("Premium gate replayed on traded ATM prices at the decision minute (history has no "
        "bid/ask), against the cash index's own sessions before each day.")


class ReplayPremium:
    """Readings for one index over a replay. Build once per run; `reading` is cheap."""

    def __init__(self, spot_bars: Sequence[pg.BarLike], holidays: Optional[set[datetime.date]] = None):
        self.sessions = pg.session_vols(spot_bars)
        self.holidays = holidays or set()

    def reading(
        self,
        now: datetime.datetime,
        expiry: datetime.date,
        spot: float,
        strike: float,
        call: Optional[float],
        put: Optional[float],
    ) -> tuple[pg.Reading, Optional[float]]:
        from icici_breeze_backend.app.services.condor.pricing import years_to_expiry_close
        from icici_breeze_backend.app.services.premium_gate.live import sessions_after

        naive = now.replace(tzinfo=None)
        fc = pg.forecast(self.sessions, naive, sessions_after(naive.date(), expiry, self.holidays))
        implied, why = pg.implied_variance(call, put, strike, spot, years_to_expiry_close(expiry, naive))
        return pg.reading(implied, fc, why), implied


def bar_price(priced: Any) -> Optional[float]:
    """The traded open of a `(status, bar)` the pricer returned, or None when it has none."""
    from icici_breeze_backend.app.services.bots.scalping.backtest_options import OK

    status, bar = priced
    return float(bar.open) if status == OK and bar is not None and bar.open > 0 else None
