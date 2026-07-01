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


def test_is_chain_complete_requires_spot(monkeypatch):
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.chain_readiness.list_tradeable_strikes_memory",
        lambda *args, **kwargs: [24000.0],
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.chain_readiness.is_tradeable_contract",
        lambda *args, **kwargs: True,
    )
    rows = [{"strike_price": 24000, "call": {"ltp": 1.0}, "put": {"ltp": 1.0}}]
    assert not is_chain_complete(
        _payload(spot=0, rows=rows),
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
