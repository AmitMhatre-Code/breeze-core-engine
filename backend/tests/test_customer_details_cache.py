"""processor.get_customer_details() caches the ICICI response per (user_id, broker_token)
until evicted, instead of hitting ICICI on every call (order placement, settings screens,
admin page, etc.)."""
from __future__ import annotations

from unittest.mock import MagicMock

from icici_breeze_backend.app.services import customer_details_cache
from icici_breeze_backend.app.services.processor import processor


def setup_function(_):
    customer_details_cache._cache.clear()


def _proc_with_fake_breeze(response):
    proc = processor()
    fake_breeze = MagicMock()
    fake_breeze.get_customer_details = MagicMock(return_value=dict(response))
    proc.get_session_breeze = MagicMock(return_value=fake_breeze)
    proc.get_session_token = MagicMock(return_value="tok-123")
    return proc, fake_breeze


def test_second_call_reuses_cache_without_hitting_breeze():
    proc, fake_breeze = _proc_with_fake_breeze({"Status": 200, "Success": {"idirect_user_name": "AB1234"}})

    first = proc.get_customer_details("user-1")
    second = proc.get_customer_details("user-1")

    assert first == second
    fake_breeze.get_customer_details.assert_called_once()


def test_non_200_response_is_not_cached():
    proc, fake_breeze = _proc_with_fake_breeze({"Status": 400, "Error": "session invalid"})

    proc.get_customer_details("user-1")
    proc.get_customer_details("user-1")

    assert fake_breeze.get_customer_details.call_count == 2


def test_different_broker_token_is_a_cache_miss():
    proc, fake_breeze = _proc_with_fake_breeze({"Status": 200, "Success": {"idirect_user_name": "AB1234"}})

    proc.get_customer_details("user-1")
    proc.get_session_token = MagicMock(return_value="tok-456")
    proc.get_customer_details("user-1")

    assert fake_breeze.get_customer_details.call_count == 2


def test_evict_forces_next_call_to_hit_breeze():
    proc, fake_breeze = _proc_with_fake_breeze({"Status": 200, "Success": {"idirect_user_name": "AB1234"}})

    proc.get_customer_details("user-1")
    customer_details_cache.evict("user-1", "tok-123")
    proc.get_customer_details("user-1")

    assert fake_breeze.get_customer_details.call_count == 2
