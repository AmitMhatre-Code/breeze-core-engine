"""Recently-traded scrips: top underlyings by trade frequency over the last 30 days.

Powers the quick-select on Place Order / Basket Order / Strategy Builder. Computed once
during login's dashboard-bootstrap fetch (see `dashboard_bootstrap.build_dashboard_bootstrap`)
and cached client-side for the rest of the session — never called per page-visit.
"""
from __future__ import annotations

import datetime
import logging
from collections import Counter
from typing import Any

from icici_breeze_backend.app.core.timezone import today_ist_date

_logger = logging.getLogger(__name__)

_LOOKBACK_DAYS = 30
_TOP_N = 5


def get_recently_traded_scrips(user_id: str, processor) -> list[dict[str, Any]]:
    """Top stock codes by trade count over the last 30 days, most-traded first.

    Uses `processor.get_trades` (actual fills, not order attempts) rather than
    `get_orders` — it isn't subject to ICICI's 10-day `get_order_list` window, so a
    30-day lookback is a single concurrent NFO+BFO round trip instead of several
    chunked/sequential ones. Fails soft to `[]` on any broker error.
    """
    try:
        end = today_ist_date()
        start = end - datetime.timedelta(days=_LOOKBACK_DAYS)
        trades = processor.get_trades(
            user_id,
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
        )
        if trades.get("Status") != 200:
            return []

        counts: Counter[str] = Counter()
        for row in trades.get("Success") or []:
            code = str(row.get("stock_code") or "").strip().upper()
            if code:
                counts[code] += 1

        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [
            {"stock_code": code, "trade_count": count}
            for code, count in ranked[:_TOP_N]
        ]
    except Exception as exc:
        _logger.warning(
            "get_recently_traded_scrips failed user_id=%s error=%s", user_id, exc
        )
        return []
