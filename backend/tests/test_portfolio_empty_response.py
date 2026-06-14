"""Portfolio empty-response handling (ICICI Success: null + Status 200)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icici_breeze_backend.app.api.v1.route_portfolio import (
    _normalize_portfolio_success_for_ui,
    router,
)
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.services.processor import (
    _empty_portfolio_positions_result,
    _is_empty_portfolio_positions_response,
    processor,
)

EMPTY_ICICI_RESPONSE = {
    "Status": 200,
    "Success": None,
    "Error": "No Positions available.",
}


def test_is_empty_portfolio_positions_response():
    assert _is_empty_portfolio_positions_response(EMPTY_ICICI_RESPONSE) is True
    assert _is_empty_portfolio_positions_response({"Status": 200, "Success": []}) is True
    assert _is_empty_portfolio_positions_response({"Status": 200, "Success": [{}]}) is False
    assert _is_empty_portfolio_positions_response({"Status": 400, "Success": None}) is False


def test_empty_portfolio_positions_result():
    out = _empty_portfolio_positions_result()
    assert out == {"Status": 200, "Success": [], "Error": None}


def test_normalize_portfolio_success_for_ui_null_success():
    out = _normalize_portfolio_success_for_ui(dict(EMPTY_ICICI_RESPONSE))
    assert out["Status"] == 200
    assert out["Success"] == {"positions": []}
    assert out["Error"] is None


def test_normalize_portfolio_success_for_ui_empty_list():
    raw = {"Status": 200, "Success": [], "Error": "No Positions available."}
    out = _normalize_portfolio_success_for_ui(raw)
    assert out["Success"] == {"positions": []}
    assert out["Error"] is None


def test_get_positions_empty_portfolio_success_null(monkeypatch):
    mock_breeze = MagicMock()
    mock_breeze.get_portfolio_positions.return_value = dict(EMPTY_ICICI_RESPONSE)

    proc = processor()
    monkeypatch.setattr(proc, "get_session_breeze", lambda _uid: mock_breeze)
    monkeypatch.setattr(
        proc,
        "_get_full_secret_for_user",
        lambda _uid: ("secret", {"Status": 200, "Success": {"broker_api_key": "k"}}),
    )
    monkeypatch.setattr(proc, "_maybe_evict_session", lambda *_a, **_k: None)

    result = proc.get_positions("user1")
    assert result["Status"] == 200
    assert result["Success"] == []
    assert result["Error"] is None
    mock_breeze.get_portfolio_positions.assert_called_once()
    mock_breeze.get_quotes.assert_not_called()


@pytest.fixture
def portfolio_client():
    async def _ctx():
        return RequestContext(
            user_id="user1",
            username="user1",
            roles=["trader"],
            is_authenticated=True,
            broker_token="broker-tok",
        )

    app = FastAPI()
    app.include_router(router, prefix="/portfolio")
    app.dependency_overrides[get_request_context] = _ctx
    with TestClient(app) as client:
        yield client


def test_get_portfolio_data_empty_response(portfolio_client, monkeypatch):
    empty = _empty_portfolio_positions_result()

    class _Proc:
        def get_positions(self, user_id):
            assert user_id == "user1"
            return dict(empty)

    monkeypatch.setattr(
        "icici_breeze_backend.app.api.v1.route_portfolio.breeze",
        _Proc(),
    )
    monkeypatch.setattr(
        "icici_breeze_backend.audit.logger.AuditLogger.log_portfolio_access",
        lambda *_a, **_k: None,
    )

    r = portfolio_client.get("/portfolio/data")
    assert r.status_code == 200
    body = r.json()
    assert body["Status"] == 200
    assert body["Success"] == {"positions": []}
    assert body["Error"] is None
