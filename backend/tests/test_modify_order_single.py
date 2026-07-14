"""ICICI's modify_order rejects a sparse patch (only order_id/exchange_code/
quantity/price) with a generic 500 error — it needs the same full field set
as place_order (order_type, stoploss, validity, disclosed_quantity,
validity_date), confirmed against a working ICICI Breeze API Playground call."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from icici_breeze_backend.app.core.timezone import today_ist_date
from icici_breeze_backend.app.services.processor import processor


@pytest.fixture
def proc(monkeypatch):
    mock_breeze = MagicMock()
    mock_breeze.modify_order.return_value = {"Status": 200, "Success": {}, "Error": None}
    p = processor()
    monkeypatch.setattr(p, "get_session_breeze", lambda _uid: mock_breeze)
    monkeypatch.setattr(p, "_maybe_evict_session", lambda *_a, **_k: None)
    return p, mock_breeze


def test_modify_order_single_sends_full_field_set(proc):
    p, mock_breeze = proc
    p.modify_order_single("user1", "202607143800026366|NFO", quantity="650", price="1.2")
    mock_breeze.modify_order.assert_called_once()
    kwargs = mock_breeze.modify_order.call_args.kwargs
    assert kwargs["order_id"] == "202607143800026366"
    assert kwargs["exchange_code"] == "NFO"
    assert kwargs["order_type"] == "limit"
    assert kwargs["stoploss"] == ""
    assert kwargs["quantity"] == "650"
    assert kwargs["price"] == "1.2"
    assert kwargs["validity"] == "day"
    assert kwargs["disclosed_quantity"] == "0"
    assert kwargs["validity_date"] == f"{today_ist_date()}T06:00:00.000Z"
