"""Weekly P&L buckets must be a strictly finer partition of the monthly ones."""

import pytest

from icici_breeze_backend.app.core import config as cfg
from icici_breeze_backend.app.services.processor import processor


def _trade(trade_date, action, quantity, average_cost, brokerage, taxes):
    return {
        "trade_date": trade_date,
        "action": action,
        "quantity": quantity,
        "average_cost": average_cost,
        "brokerage_amount": brokerage,
        "total_taxes": taxes,
    }


# Shape mirrors a real get_trade_list response: all numerics are strings and
# trade_date is date-only "%d-%b-%Y". Mon 13-Jul-2026 week holds 14-Jul + 17-Jul;
# Mon 20-Jul-2026 week holds the 20-Jul trades. Both weeks fall in Jul-26.
TRADES = [
    _trade("20-Jul-2026", "Buy", "130", "1", "38", "6.89"),
    _trade("20-Jul-2026", "Sell", "130", "1.05", "38", "6.89"),
    _trade("17-Jul-2026", "Sell", "65", "0.45", "19", "3.43"),
    _trade("14-Jul-2026", "Sell", "650", "0.15", "190", "34.24"),
    # A second month, so the monthly grouping is actually exercised.
    _trade("03-Aug-2026", "Sell", "65", "0.6", "19", "3.40"),
]


class _FakeBreeze:
    def get_trade_list(self, exchange_code=None, **_kwargs):
        # Only the NFO leg carries trades; the BFO leg comes back empty.
        if exchange_code == cfg.NFO:
            return {"Status": 200, "Success": TRADES, "Error": None}
        return {"Status": 200, "Success": [], "Error": None}


@pytest.fixture
def performance(monkeypatch):
    p = processor()
    monkeypatch.setattr(p, "get_session_breeze", lambda user_id: _FakeBreeze())
    monkeypatch.setattr(p, "_maybe_evict_session", lambda *a, **k: None)
    result = p.get_performance("u1", 1_000_000.0, "2026-04-01", "2027-03-31")
    assert result["Status"] == 200
    return result["Success"]


def test_weekly_buckets_use_monday_iso_dates(performance):
    assert [row["week"] for row in performance["weekly"]] == [
        "2026-07-13",
        "2026-07-20",
        "2026-08-03",
    ]


def test_weekly_totals_match_monthly_totals(performance):
    for field in ("pnl", "brokerage", "taxes"):
        by_month = {row["month"]: row[field] for row in performance["monthly"]}
        # Jul-26 spans the two July weeks; Aug-26 spans the single August week.
        assert by_month["Jul-26"] == pytest.approx(
            sum(
                row[field]
                for row in performance["weekly"]
                if row["week"].startswith("2026-07")
            )
        )
        assert by_month["Aug-26"] == pytest.approx(
            sum(
                row[field]
                for row in performance["weekly"]
                if row["week"].startswith("2026-08")
            )
        )


def test_weekly_pnl_sums_to_headline_net_pnl(performance):
    assert sum(row["pnl"] for row in performance["weekly"]) == pytest.approx(
        performance["net_pnl"]
    )
