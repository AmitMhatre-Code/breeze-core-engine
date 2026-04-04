"""ICICI mock broker mode: fixtures and direct API short-circuit."""

import importlib

import pytest


@pytest.fixture
def mock_broker_env(monkeypatch):
    monkeypatch.setenv("ICICI_BROKER_MODE", "mock")
    import icici_breeze_backend.core.config as core_config
    import icici_breeze_backend.app.core.config as app_config

    importlib.reload(core_config)
    importlib.reload(app_config)


def test_call_icici_api_direct_returns_fixture(mock_broker_env):
    from icici_breeze_backend.app.external import icici_api

    out = icici_api.call_icici_api_direct(
        "https://api.icicidirect.com/breezeapi/api/v1/margin",
        {"exchange_code": "NFO"},
        "key",
        "secret",
        "tok",
        user_id="u1",
    )
    assert out.get("Status") == 200
    assert out.get("Success", {}).get("cash_limit") is not None


def test_fetch_customerdetails_session_token_mock(mock_broker_env):
    from icici_breeze_backend.app.external import icici_api

    t = icici_api.fetch_customerdetails_session_token("k", "b", user_id="u1")
    assert t == "mock_customerdetails_session_token"


def test_call_icici_api_direct_portfolio_mock(mock_broker_env):
    from icici_breeze_backend.app.external import icici_api

    out = icici_api.call_icici_api_direct(
        "https://api.icicidirect.com/breezeapi/api/v1/portfoliopositions",
        {},
        "key",
        "secret",
        "tok",
        user_id="u1",
    )
    assert out.get("Status") == 200
    succ = out.get("Success") or []
    assert isinstance(succ, list) and len(succ) >= 1
    assert succ[0].get("stock_code") == "NIFTY"


def test_mock_breeze_sdk_historical_and_portfolio(mock_broker_env):
    from icici_breeze_backend.dev.mock_broker import MockBreezeSdk

    b = MockBreezeSdk()
    hist = b.get_historical_data_v2(
        interval="1day",
        from_date="2026-03-01T05:30:00.000Z",
        to_date="2026-03-05T15:30:00.000Z",
        stock_code="INDVIX",
        exchange_code="NSE",
        product_type="cash",
    )
    assert hist.get("Status") == 200
    assert len(hist.get("Success") or []) >= 1
    pos = b.get_portfolio_positions()
    assert pos.get("Status") == 200
    assert len(pos.get("Success") or []) >= 1
