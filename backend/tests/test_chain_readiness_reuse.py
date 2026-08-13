"""Freshness reuse and the split wait windows.

Every chain request used to rebuild the chain itself before reading it, which would
have relocated into the API process exactly the CPU the builder's cadence gate saves.
Order pricing deliberately keeps the old eager behaviour."""
from __future__ import annotations

import time
from unittest.mock import patch

import icici_breeze_backend.app.services.chain_readiness as cr


def _fresh_payload(age_seconds: float) -> dict:
    return {
        "chain_rows": [],
        "built_at": time.time() - age_seconds,
        "lot_size": 75,
    }


def _reset_cache() -> None:
    cr._freshness_cache = None


def test_chain_wait_is_much_shorter_than_the_order_pricing_wait():
    assert cr._chain_wait_timeout_ms() < cr._wait_timeout_ms()


def test_recently_built_chain_is_reused_without_rebuilding():
    _reset_cache()
    with patch.object(cr, "_chain_freshness_window_seconds", return_value=2.0):
        assert cr._payload_is_fresh(_fresh_payload(0.5)) is True


def test_stale_chain_is_not_reused():
    _reset_cache()
    with patch.object(cr, "_chain_freshness_window_seconds", return_value=2.0):
        assert cr._payload_is_fresh(_fresh_payload(5.0)) is False


def test_payload_without_a_build_stamp_is_never_treated_as_fresh():
    """Chains published before this change carry no `built_at`; treating a missing
    stamp as fresh would serve an old chain indefinitely."""
    _reset_cache()
    assert cr._payload_is_fresh({"chain_rows": []}) is False
    assert cr._payload_is_fresh({"built_at": 0}) is False
    assert cr._payload_is_fresh({"built_at": "nonsense"}) is False
    assert cr._payload_is_fresh(None) is False


def test_chain_wait_skips_the_rebuild_when_the_worker_just_published():
    _reset_cache()
    payload = _fresh_payload(0.1)
    with patch.object(cr, "cache_get_json", return_value=payload), patch.object(
        cr, "_chain_freshness_window_seconds", return_value=2.0
    ), patch(
        "icici_breeze_backend.app.services.chain_build_service.refresh_active_chains"
    ) as refresh:
        out = cr._poll_canonical_chain(
            "NFO",
            "NIFTY",
            "30-Jun-2026",
            lot_size=0,
            freeze_quantity=None,
            is_ready=lambda _p: True,
        )
    assert out is not None
    refresh.assert_not_called()


def test_order_pricing_path_rebuilds_even_when_the_chain_is_fresh():
    _reset_cache()
    payload = _fresh_payload(0.1)
    with patch.object(cr, "cache_get_json", return_value=payload), patch.object(
        cr, "_chain_freshness_window_seconds", return_value=2.0
    ), patch(
        "icici_breeze_backend.app.services.chain_build_service.refresh_active_chains"
    ) as refresh:
        cr._poll_canonical_chain(
            "NFO",
            "NIFTY",
            "30-Jun-2026",
            lot_size=0,
            freeze_quantity=None,
            is_ready=lambda _p: True,
            reuse_fresh=False,
        )
    assert refresh.call_count == 1


def test_stale_chain_triggers_a_rebuild_before_being_read():
    _reset_cache()
    with patch.object(cr, "cache_get_json", return_value=_fresh_payload(30.0)), patch.object(
        cr, "_chain_freshness_window_seconds", return_value=2.0
    ), patch(
        "icici_breeze_backend.app.services.chain_build_service.refresh_active_chains"
    ) as refresh:
        cr._poll_canonical_chain(
            "NFO",
            "NIFTY",
            "30-Jun-2026",
            lot_size=0,
            freeze_quantity=None,
            is_ready=lambda _p: True,
        )
    assert refresh.call_count == 1


def test_never_completing_chain_gives_up_inside_the_short_window():
    """A thin chain's deep-OTM strikes may never trade; the wait used to burn the
    full order-pricing window before falling back."""
    _reset_cache()
    with patch.object(cr, "cache_get_json", return_value=_fresh_payload(0.1)), patch.object(
        cr, "_chain_freshness_window_seconds", return_value=2.0
    ), patch.object(cr, "_chain_wait_timeout_ms", return_value=300), patch.object(
        cr, "_wait_poll_ms", return_value=50
    ), patch(
        "icici_breeze_backend.app.services.chain_build_service.refresh_active_chains"
    ):
        started = time.monotonic()
        out = cr.wait_for_canonical_chain("NFO", "NIFTY", "30-Jun-2026")
        elapsed = time.monotonic() - started
    assert out is None
    assert elapsed < 1.5
