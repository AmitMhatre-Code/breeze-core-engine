"""Tolerance-mode aggressive limit price derivation (app-side, no ICICI dependency)."""

from __future__ import annotations

import pytest

from icici_breeze_backend.app.services import aggressive_limit as al
from icici_breeze_backend.app.services.aggressive_limit import (
    AggressiveLimitError,
    clamp_tolerance_pct,
    compute_aggressive_limit_price,
    round_to_tick,
)


def test_buy_prices_above_ltp_tick_rounded():
    # 100 * 1.05 = 105.00 (already on a 0.05 tick)
    assert compute_aggressive_limit_price("Buy", 100.0, 5) == 105.0


def test_sell_prices_below_ltp():
    assert compute_aggressive_limit_price("Sell", 100.0, 5) == 95.0


def test_result_is_rounded_to_tick():
    # 150.50 * 1.10 = 165.55 -> nearest 0.05 tick
    price = compute_aggressive_limit_price("Buy", 150.5, 10)
    assert price == round_to_tick(150.5 * 1.10)
    assert abs((price / 0.05) - round(price / 0.05)) < 1e-9


def test_case_insensitive_action():
    assert compute_aggressive_limit_price("buy", 100.0, 5) == compute_aggressive_limit_price(
        "Buy", 100.0, 5
    )


def test_tolerance_clamped_to_max(monkeypatch):
    monkeypatch.setattr(al.cfg, "AGGRESSIVE_LIMIT_MAX_TOLERANCE_PCT", 25.0)
    # request 999% -> clamped to 25%
    assert compute_aggressive_limit_price("Buy", 100.0, 999) == 125.0


def test_negative_tolerance_clamps_to_zero():
    assert compute_aggressive_limit_price("Buy", 100.0, -10) == 100.0


def test_missing_tolerance_uses_default(monkeypatch):
    monkeypatch.setattr(al.cfg, "AGGRESSIVE_LIMIT_DEFAULT_TOLERANCE_PCT", 5.0)
    assert clamp_tolerance_pct(None) == 5.0
    assert clamp_tolerance_pct("") == 5.0
    assert clamp_tolerance_pct("garbage") == 5.0


@pytest.mark.parametrize("bad", [None, 0, -1, "x", float("nan")])
def test_missing_or_bad_ltp_raises(bad):
    with pytest.raises(AggressiveLimitError):
        compute_aggressive_limit_price("Buy", bad, 5)


def test_unsupported_action_raises():
    with pytest.raises(AggressiveLimitError):
        compute_aggressive_limit_price("Hold", 100.0, 5)


def test_sell_never_returns_zero_for_cheap_option(monkeypatch):
    monkeypatch.setattr(al.cfg, "AGGRESSIVE_LIMIT_MAX_TOLERANCE_PCT", 100.0)
    # 0.10 * (1 - 1.0) = 0.0 -> floored to one tick, never a zero-price order
    price = compute_aggressive_limit_price("Sell", 0.10, 100)
    assert price >= 0.05
