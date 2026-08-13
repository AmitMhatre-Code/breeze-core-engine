"""The rebuild cadence gate: a live tick feed publishes one dirty-notification per
tick (hundreds a second), and the builder used to rebuild every active chain for
each burst. These pin the throttle that bounds that to the P&L recalc interval."""
from __future__ import annotations

import time
from unittest.mock import patch

import icici_breeze_backend.workers.chain_builder as cb


def _reset_gate() -> None:
    cb._last_refresh_monotonic = 0.0
    cb._interval_cache = None


def test_burst_of_ticks_rebuilds_once_not_once_per_tick():
    _reset_gate()
    with patch.object(cb, "_refresh_interval_seconds", return_value=60.0), patch.object(
        cb, "list_active_chains", return_value=["NFO|NIFTY|30-Jun-2026"]
    ), patch.object(cb, "refresh_active_chains") as refresh:
        for _ in range(200):
            cb._maybe_refresh()
    assert refresh.call_count == 1


def test_rebuild_allowed_again_after_the_interval_elapses():
    _reset_gate()
    with patch.object(cb, "_refresh_interval_seconds", return_value=0.05), patch.object(
        cb, "list_active_chains", return_value=["NFO|NIFTY|30-Jun-2026"]
    ), patch.object(cb, "refresh_active_chains") as refresh:
        cb._maybe_refresh()
        time.sleep(0.06)
        cb._maybe_refresh()
    assert refresh.call_count == 2


def test_gate_advances_even_when_a_rebuild_raises():
    """A failing rebuild must not leave the gate open, or one bad chain would
    restore the unthrottled hot loop."""
    _reset_gate()
    with patch.object(cb, "_refresh_interval_seconds", return_value=60.0), patch.object(
        cb, "list_active_chains", return_value=["NFO|NIFTY|30-Jun-2026"]
    ), patch.object(cb, "refresh_active_chains", side_effect=RuntimeError("boom")) as refresh:
        cb._maybe_refresh()
        cb._maybe_refresh()
    assert refresh.call_count == 1


def test_no_active_chains_still_closes_the_gate():
    _reset_gate()
    with patch.object(cb, "_refresh_interval_seconds", return_value=60.0), patch.object(
        cb, "list_active_chains", return_value=[]
    ), patch.object(cb, "refresh_active_chains") as refresh:
        cb._maybe_refresh()
        cb._maybe_refresh()
    refresh.assert_not_called()


def test_interval_follows_the_pnl_recalc_setting():
    _reset_gate()
    with patch(
        "icici_breeze_backend.app.services.pnl_engine_settings.load_pnl_engine_settings",
        return_value={"pnl_recompute_interval_seconds": 7.0},
    ):
        assert cb._refresh_interval_seconds() == 7.0


def test_interval_falls_back_when_the_setting_cannot_be_read():
    _reset_gate()
    with patch(
        "icici_breeze_backend.app.services.pnl_engine_settings.load_pnl_engine_settings",
        side_effect=RuntimeError("no db"),
    ):
        assert cb._refresh_interval_seconds() == 2.0
