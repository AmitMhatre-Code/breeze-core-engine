"""Carry return and DTE helpers."""

from __future__ import annotations

import datetime
from unittest.mock import patch

from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
    annualized_carry_percent_on_span,
    days_to_expiry,
)


class TestDaysToExpiry:
    @patch("icici_breeze_backend.app.core.timezone.today_ist_date")
    def test_expiry_minus_today_plus_one(self, mock_today):
        mock_today.return_value = datetime.date(2026, 6, 14)
        assert days_to_expiry("16-Jun-2026") == 3


class TestAnnualizedCarryPercent:
    def test_portfolio_carry_on_span_plus_elm(self):
        # NIFTY short call scenario: Carry on LTP, DTE=3, Span+ELM=615L
        carry = 207_067.0
        dte = 3
        total_margin = 49_900_000.0 + 11_600_000.0  # ₹499L + ₹116L SPAN + ELM
        pct = annualized_carry_percent_on_span(carry, dte, total_margin)
        assert abs(pct - 40.97) < 0.05

    def test_zero_margin_returns_zero(self):
        assert annualized_carry_percent_on_span(100.0, 5, 0.0) == 0.0
