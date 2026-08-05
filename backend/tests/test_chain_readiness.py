"""Tests for chain completeness gating."""
from __future__ import annotations

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.chain_readiness import is_chain_complete


def _payload(*, spot: float, rows: list[dict]) -> dict:
    return {
        "spot_price": spot,
        "chain_rows": rows,
        "quote_source": "websocket",
    }


def test_is_chain_complete_ignores_spot_price(monkeypatch):
    """spot_price is filled in separately by quote_source_router._apply_chain_spot()
    from a bhavcopy-fed cache; real WS option ticks never carry it at all, so
    completeness must not depend on it (previously it did, which meant a WS-built
    chain could never be considered complete no matter how many real ticks arrived)."""
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.chain_readiness.list_tradeable_strikes_memory",
        lambda *args, **kwargs: [24000.0],
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.chain_readiness.is_tradeable_contract",
        lambda *args, **kwargs: True,
    )
    rows = [{"strike_price": 24000, "call": {"ltp": 1.0}, "put": {"ltp": 1.0}}]
    assert is_chain_complete(
        _payload(spot=0, rows=rows),
        stock_code="NIFTY",
        exchange_code=cfg.NFO,
        expiry_display="30-Jun-2026",
    )
    assert is_chain_complete(
        _payload(spot=None, rows=rows),
        stock_code="NIFTY",
        exchange_code=cfg.NFO,
        expiry_display="30-Jun-2026",
    )
    assert is_chain_complete(
        _payload(spot=24050.0, rows=rows),
        stock_code="NIFTY",
        exchange_code=cfg.NFO,
        expiry_display="30-Jun-2026",
    )


def test_is_chain_complete_still_requires_real_quotes(monkeypatch):
    """Completeness hinges on per-cell quote data (ltp / bid-ask / buy-sell qty), not spot."""
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.chain_readiness.list_tradeable_strikes_memory",
        lambda *args, **kwargs: [24000.0],
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.chain_readiness.is_tradeable_contract",
        lambda *args, **kwargs: True,
    )
    rows_no_quote = [{"strike_price": 24000, "call": {"ltp": 0}, "put": {"ltp": 0}}]
    assert not is_chain_complete(
        _payload(spot=24050.0, rows=rows_no_quote),
        stock_code="NIFTY",
        exchange_code=cfg.NFO,
        expiry_display="30-Jun-2026",
    )


def test_is_chain_complete_rejects_missing_liquid_side(monkeypatch):
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.chain_readiness.list_tradeable_strikes_memory",
        lambda *args, **kwargs: [24000.0],
    )

    def tradeable(stock, expiry, strike, opt, exchange_code=None):
        return opt == cfg.CALL

    monkeypatch.setattr(
        "icici_breeze_backend.app.services.chain_readiness.is_tradeable_contract",
        tradeable,
    )
    rows = [{"strike_price": 24000, "call": {"ltp": 1.0}, "put": None}]
    assert is_chain_complete(
        _payload(spot=24050.0, rows=rows),
        stock_code="NIFTY",
        exchange_code=cfg.NFO,
        expiry_display="30-Jun-2026",
    )
    rows_missing_call = [{"strike_price": 24000, "call": None, "put": None}]
    assert not is_chain_complete(
        _payload(spot=24050.0, rows=rows_missing_call),
        stock_code="NIFTY",
        exchange_code=cfg.NFO,
        expiry_display="30-Jun-2026",
    )


def _wide_strikes(count: int, *, step: float = 50.0, center: float = 24000.0) -> list[float]:
    half = count // 2
    return [center + (i - half) * step for i in range(count)]


def test_is_chain_complete_only_requires_atm_window_when_spot_given(monkeypatch):
    """Deep OTM/ITM strikes on a wide chain (NIFTY) routinely take longer to tick than
    CHAIN_WS_WAIT_TIMEOUT_MS; requiring every tradeable strike meant one illiquid strike
    could flip an otherwise-live chain to bhavcopy. When `spot` is supplied, only strikes
    within CHAIN_READY_ATM_STRIKE_WINDOW of the ATM strike must have a real quote."""
    strikes = _wide_strikes(41)  # 20 strikes each side of the 24000 ATM strike
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.chain_readiness.list_tradeable_strikes_memory",
        lambda *args, **kwargs: strikes,
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.chain_readiness.is_tradeable_contract",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(cfg, "CHAIN_READY_ATM_STRIKE_WINDOW", 20)

    # Every strike is present as a row (as chain_build_service always builds a full
    # skeleton), but only the 41 strikes within the window actually have live quotes;
    # the deep wings (far outside the window) are still null, mirroring an illiquid
    # strike that hasn't ticked yet.
    window = set(strikes)  # all 41 strikes are within +/-20 of the ATM strike here
    rows = [
        {
            "strike_price": strike,
            "call": {"ltp": 1.0} if strike in window else None,
            "put": {"ltp": 1.0} if strike in window else None,
        }
        for strike in strikes
    ]
    assert is_chain_complete(
        _payload(spot=24000.0, rows=rows),
        stock_code="NIFTY",
        exchange_code=cfg.NFO,
        expiry_display="30-Jun-2026",
        spot=24000.0,
    )

    # Without spot, falls back to requiring every tradeable strike (old behavior) --
    # the same payload with every cell present is still fine...
    assert is_chain_complete(
        _payload(spot=None, rows=rows),
        stock_code="NIFTY",
        exchange_code=cfg.NFO,
        expiry_display="30-Jun-2026",
    )

    # ...but if a strike inside the window is missing a quote, it must still fail --
    # the window narrows what's *required*, it doesn't skip cells within it.
    rows_atm_missing = [dict(r) for r in rows]
    rows_atm_missing[20] = {"strike_price": strikes[20], "call": None, "put": None}
    assert not is_chain_complete(
        _payload(spot=24000.0, rows=rows_atm_missing),
        stock_code="NIFTY",
        exchange_code=cfg.NFO,
        expiry_display="30-Jun-2026",
        spot=24000.0,
    )

    # A far-wing strike (well outside the window) missing a quote no longer blocks
    # completeness when spot is supplied -- this is the actual bug fix.
    wide_strikes = _wide_strikes(101)
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.chain_readiness.list_tradeable_strikes_memory",
        lambda *args, **kwargs: wide_strikes,
    )
    wide_window = set(wide_strikes[30:71])  # +/-20 around index 50 (the ATM strike)
    wide_rows = [
        {
            "strike_price": strike,
            "call": {"ltp": 1.0} if strike in wide_window else None,
            "put": {"ltp": 1.0} if strike in wide_window else None,
        }
        for strike in wide_strikes
    ]
    assert is_chain_complete(
        _payload(spot=24000.0, rows=wide_rows),
        stock_code="NIFTY",
        exchange_code=cfg.NFO,
        expiry_display="30-Jun-2026",
        spot=24000.0,
    )
    # ...but the same sparse payload fails without spot, since every tradeable strike
    # (including the far wings) is still required.
    assert not is_chain_complete(
        _payload(spot=None, rows=wide_rows),
        stock_code="NIFTY",
        exchange_code=cfg.NFO,
        expiry_display="30-Jun-2026",
    )


def test_is_chain_complete_falls_back_to_payload_spot(monkeypatch):
    """When the caller couldn't resolve a spot, bhavcopy/REST-sourced cells still
    carry one through to `payload["spot_price"]`. Using it keeps a thin chain on
    the ATM-window gate instead of silently widening to the all-strikes rule,
    which an illiquid chain can essentially never satisfy."""
    strikes = _wide_strikes(21)  # 24000 ATM at index 10, 100-point spacing
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.chain_readiness.list_tradeable_strikes_memory",
        lambda *args, **kwargs: strikes,
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.chain_readiness.is_tradeable_contract",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(cfg, "CHAIN_READY_ATM_STRIKE_WINDOW", 5)

    quoted = set(strikes[5:16])
    rows = [
        {
            "strike_price": strike,
            "call": {"ltp": 1.0} if strike in quoted else None,
            "put": {"ltp": 1.0} if strike in quoted else None,
        }
        for strike in strikes
    ]

    detail: dict = {}
    assert is_chain_complete(
        _payload(spot=24000.0, rows=rows),
        stock_code="NIFTY",
        exchange_code=cfg.NFO,
        expiry_display="30-Jun-2026",
        spot=None,
        detail=detail,
    )
    assert detail == {}

    # No spot from either source -> all-strikes gate, and `detail` says so.
    assert not is_chain_complete(
        _payload(spot=None, rows=rows),
        stock_code="NIFTY",
        exchange_code=cfg.NFO,
        expiry_display="30-Jun-2026",
        spot=None,
        detail=detail,
    )
    assert detail["gate"] == "all_strikes_no_spot"
    assert detail["reason"] == "unquoted contracts"
