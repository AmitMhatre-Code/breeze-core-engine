"""Which quote tier may set a position's LTP, and what happens when none may.

Mid-session the router's per-cell walk answers a not-yet-warm websocket cell from
the *previous* session's snapshot/bhavcopy tier. Those are settlement prices, and
letting one override a live broker ltp made a profitable book report a loss on the
Portfolio summary tiles for a whole session (the tiles never refetch; only the
table's websocket overlay self-corrected, so the two disagreed on screen).

So: while the market is open only a websocket cell may set the ltp. Once it closes
the offline tiers are authoritative again.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services import processor as processor_module
from icici_breeze_backend.app.services import quote_source_router
from icici_breeze_backend.app.services.processor import processor


def _uid() -> str:
    """Fresh id per test — get_positions memoises its response for 15s per user."""
    return "quoteguard-test-" + uuid.uuid4().hex


def _broker_position(exchange_code: str = cfg.NFO, *, broker_ltp: str = "0.55") -> dict:
    """One short NIFTY put, shaped as ICICI's get_portfolio_positions() returns it
    (all strings, including the broker's own ltp)."""
    return {
        "stock_code": "NIFTY",
        "exchange_code": exchange_code,
        "expiry_date": "31-Dec-2026",
        "product_type": cfg.OPTIONS,
        "right": cfg.PUT,
        "action": cfg.SELL,
        "strike_price": "22500",
        "quantity": "16640",
        "average_price": "0.80",
        "ltp": broker_ltp,
        "stock_index_indicator": cfg.INDEX,
    }


def _router_quote(ltp: float, source: str) -> dict:
    return {
        "Status": 200,
        "Error": None,
        "Success": [{"ltp": ltp, "spot_price": 24305.2, "strike_price": 22500}],
        "quote_source": source,
    }


def _run_get_positions(monkeypatch, *, position: dict, quote: dict, market_open: bool) -> dict:
    proc = processor()
    mock_breeze = MagicMock()
    mock_breeze.get_portfolio_positions.return_value = {
        "Status": 200,
        "Success": [position],
        "Error": None,
    }
    monkeypatch.setattr(proc, "get_session_breeze", lambda _uid: mock_breeze)
    monkeypatch.setattr(
        proc,
        "_get_full_secret_for_user",
        lambda _uid: ("secret", {"Status": 200, "Success": {"broker_api_key": "k"}}),
    )
    monkeypatch.setattr(proc, "_maybe_evict_session", lambda *_a, **_k: None)
    # Margin netting makes its own broker calls and is not under test here.
    monkeypatch.setattr(
        proc, "_compute_netted_margins", lambda *_a, **_k: {"groups": [], "portfolio": {}}
    )
    monkeypatch.setattr(processor_module, "is_market_open", lambda *_a, **_k: market_open)
    monkeypatch.setattr(
        quote_source_router, "fetch_quote_icici_response", lambda *_a, **_k: quote
    )
    monkeypatch.setattr(quote_source_router, "cached_chain_spot", lambda *_a, **_k: 24305.2)

    result = proc.get_positions(_uid())
    assert result["Status"] == 200
    return result["Success"][0]


def test_prev_session_price_cannot_override_a_live_broker_ltp(monkeypatch):
    """The reported bug: bhavcopy's 0.90 beat the broker's live 0.55 and flipped
    a +4,160 position into a -1,664 loss on the summary tiles."""
    row = _run_get_positions(
        monkeypatch,
        position=_broker_position(broker_ltp="0.55"),
        quote=_router_quote(0.90, "bhavcopy"),
        market_open=True,
    )
    assert row["ltp"] == "0.55"
    assert row["quote_source"] == "broker"
    # (0.80 - 0.55) * 16640 — a profit, not the -1664 the stale price produced.
    assert row["current_profit"] == 4160.0


def test_snapshot_tier_is_rejected_mid_session_too(monkeypatch):
    """`offline_source_order` leads with the previous session's ws snapshot, not
    bhavcopy — excluding only bhavcopy would have left the bug reachable."""
    row = _run_get_positions(
        monkeypatch,
        position=_broker_position(broker_ltp="0.55"),
        quote=_router_quote(0.90, "snapshot"),
        market_open=True,
    )
    assert row["ltp"] == "0.55"
    assert row["quote_source"] == "broker"


def test_websocket_tier_overrides_the_broker_mid_session(monkeypatch):
    """The router preference still holds for a real tick — that is the whole point
    of preferring it over ICICI's positions ltp."""
    row = _run_get_positions(
        monkeypatch,
        position=_broker_position(broker_ltp="0.90"),
        quote=_router_quote(0.55, "websocket"),
        market_open=True,
    )
    assert row["ltp"] == 0.55
    assert row["quote_source"] == "websocket"
    assert row["current_profit"] == 4160.0


def test_offline_tiers_are_authoritative_once_the_market_closes(monkeypatch):
    """Post-close, bhavcopy is the correct mark and the broker's ltp is the one
    that goes stale — the guard must not invert that."""
    row = _run_get_positions(
        monkeypatch,
        position=_broker_position(broker_ltp="0.55"),
        quote=_router_quote(0.90, "bhavcopy"),
        market_open=False,
    )
    assert row["ltp"] == 0.90
    assert row["quote_source"] == "bhavcopy"


def test_bfo_leg_is_blanked_rather_than_marked_at_an_untrusted_price(monkeypatch):
    """ICICI's positions ltp is known-unreliable for BFO index options, so a BFO
    leg with no live cell gets no price at all instead of a plausible wrong one."""
    row = _run_get_positions(
        monkeypatch,
        position=_broker_position(cfg.BFO, broker_ltp="0.55"),
        quote=_router_quote(0.90, "bhavcopy"),
        market_open=True,
    )
    assert row["ltp"] is None
    assert row["quote_source"] == "unavailable"
    assert row["current_profit"] is None
    assert row["carry_profit"] is None
    # Margin inputs key off spot/quantity, so they survive an unpriced leg.
    assert row["elm_margin_required"] is not None
    assert row["days_to_expiry"] >= 1


def test_group_carry_return_is_blank_when_any_leg_is_unpriced():
    """A None carry summed as 0.0 would hand the UI a partial figure that looks
    whole; the group's carry-return goes blank instead."""
    fake_breeze = MagicMock()
    fake_breeze.margin_calculator.return_value = {
        "Status": 200,
        "Success": {"span_margin_required": 50000.0},
        "Error": None,
    }
    legs = [
        {
            "stock_code": "BSESEN", "exchange_code": cfg.BFO, "expiry_date": "31-Dec-2026",
            "product_type": cfg.OPTIONS, "right": cfg.PUT, "action": cfg.SELL,
            "strike_price": "74300", "quantity": 20, "elm_margin_required": 1000.0,
            "carry_profit": 5000.0,
        },
        {
            "stock_code": "BSESEN", "exchange_code": cfg.BFO, "expiry_date": "31-Dec-2026",
            "product_type": cfg.OPTIONS, "right": cfg.CALL, "action": cfg.SELL,
            "strike_price": "79800", "quantity": 20, "elm_margin_required": 1500.0,
            "carry_profit": None,  # unpriced leg
        },
    ]
    out = processor()._compute_netted_margins(fake_breeze, _uid(), legs)
    assert out["groups"][0]["carry_margin_returns"] is None
    # ELM is additive and independent of price, so it still reports.
    assert out["groups"][0]["elm_margin_required"] == 2500.0
