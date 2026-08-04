"""Short-TTL cache shared by processor.get_positions() and get_margin_situation()
across GET /home/data, GET /dashboard/bootstrap, GET /portfolio/data, and the PB/SL
protection guard's warm loop -- see processor.py's _LIVE_SNAPSHOT_CACHE_TTL_SECONDS.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from icici_breeze_backend.app.services.processor import processor

EMPTY_ICICI_RESPONSE = {"Status": 200, "Success": None, "Error": "No Positions available."}

RAW_MARGIN_SUCCESS = {
    "Status": 200,
    "Success": {"limit_list": [{"amount": "-1000"}], "cash_limit": "50000"},
    "Error": None,
}


def _live_proc(monkeypatch, mock_breeze: MagicMock) -> processor:
    proc = processor()
    monkeypatch.setattr(proc, "get_session_breeze", lambda _uid: mock_breeze)
    monkeypatch.setattr(
        proc,
        "_get_full_secret_for_user",
        lambda _uid: ("secret", {"Status": 200, "Success": {"broker_api_key": "k"}}),
    )
    monkeypatch.setattr(proc, "_maybe_evict_session", lambda *_a, **_k: None)
    return proc


def test_get_positions_second_call_within_ttl_uses_cache(monkeypatch):
    mock_breeze = MagicMock()
    mock_breeze.get_portfolio_positions.return_value = dict(EMPTY_ICICI_RESPONSE)
    proc = _live_proc(monkeypatch, mock_breeze)

    first = proc.get_positions("user1")
    second = proc.get_positions("user1")

    assert first == second == {"Status": 200, "Success": [], "Error": None}
    mock_breeze.get_portfolio_positions.assert_called_once()


def test_get_positions_different_user_not_served_from_cache(monkeypatch):
    mock_breeze = MagicMock()
    mock_breeze.get_portfolio_positions.return_value = dict(EMPTY_ICICI_RESPONSE)
    proc = _live_proc(monkeypatch, mock_breeze)

    proc.get_positions("user1")
    proc.get_positions("user2")

    assert mock_breeze.get_portfolio_positions.call_count == 2


def test_get_positions_failure_is_never_cached(monkeypatch):
    mock_breeze = MagicMock()
    mock_breeze.get_portfolio_positions.side_effect = Exception("boom")
    proc = _live_proc(monkeypatch, mock_breeze)

    first = proc.get_positions("user1")
    second = proc.get_positions("user1")

    assert first["Status"] == 400
    assert second["Status"] == 400
    # A broker failure must always be visible live -- the PB/SL protection guard's
    # suspend/alert path depends on seeing it, not on a stale cached success.
    assert mock_breeze.get_portfolio_positions.call_count == 2


def test_get_margin_situation_second_call_within_ttl_uses_cache(monkeypatch):
    mock_breeze = MagicMock()
    mock_breeze.get_margin.return_value = dict(RAW_MARGIN_SUCCESS)
    proc = _live_proc(monkeypatch, mock_breeze)

    first = proc.get_margin_situation("user1", target_margin_ute=100)
    second = proc.get_margin_situation("user1", target_margin_ute=100)

    assert first == second
    assert first["Status"] == 200
    mock_breeze.get_margin.assert_called_once()


def test_get_margin_situation_different_target_ute_not_served_from_cache(monkeypatch):
    mock_breeze = MagicMock()
    mock_breeze.get_margin.return_value = dict(RAW_MARGIN_SUCCESS)
    proc = _live_proc(monkeypatch, mock_breeze)

    proc.get_margin_situation("user1", target_margin_ute=100)
    proc.get_margin_situation("user1", target_margin_ute=50)

    assert mock_breeze.get_margin.call_count == 2


def test_get_margin_situation_failure_is_never_cached(monkeypatch):
    mock_breeze = MagicMock()
    # Success must be truthy so the SDK response is accepted as-is (see get_margin_situation's
    # `sdk_resp.get("Status") == 200 or sdk_resp.get("Success")` check) -- otherwise it falls
    # through to the direct-API fallback path, which isn't mocked here and hits the network.
    mock_breeze.get_margin.return_value = {
        "Status": 400,
        "Error": "bad session",
        "Success": {"error": "bad session"},
    }
    proc = _live_proc(monkeypatch, mock_breeze)

    first = proc.get_margin_situation("user1", target_margin_ute=100)
    second = proc.get_margin_situation("user1", target_margin_ute=100)

    assert first["Status"] == 400
    assert second["Status"] == 400
    assert mock_breeze.get_margin.call_count == 2
