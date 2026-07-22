"""Tests for canonical strike parsing."""
import pytest

from icici_breeze_backend.app.core.strike import (
    parse_strike,
    strike_for_broker,
    strike_key,
    strikes_equal,
    strikes_sorted,
)


def test_parse_strike_whole_number():
    assert parse_strike("24000") == 24000.0
    assert parse_strike(24000) == 24000.0


def test_parse_strike_fractional():
    assert parse_strike("150.35") == 150.35
    assert parse_strike("150.00") == 150.0


def test_strike_key_strips_trailing_zeros():
    assert strike_key(150.0) == "150"
    assert strike_key(150.35) == "150.35"
    assert strike_key(24000) == "24000"


def test_strike_key_distinct_fractional_pairs():
    assert strike_key(150.0) != strike_key(150.35)


def test_strike_for_broker_preserves_fraction():
    assert strike_for_broker(150.35) == "150.35"
    assert strike_for_broker(24000) == "24000"


def test_strikes_sorted_dedupes():
    assert strikes_sorted([150.35, 150.0, 150.35, 24000]) == [150.0, 150.35, 24000.0]


def test_strikes_equal():
    assert strikes_equal("150.35", 150.35)
    assert not strikes_equal("150.0", 150.35)


def test_parse_strike_rejects_invalid():
    assert parse_strike("") is None
    assert parse_strike(0) is None
    assert parse_strike(-1) is None


def test_strike_for_broker_invalid_raises():
    with pytest.raises(ValueError):
        strike_for_broker("")
