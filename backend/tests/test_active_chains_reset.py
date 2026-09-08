"""Tests for active chain registry reset on API startup."""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

from icici_breeze_backend.app.services.reference_data import active_chains as ac


def test_reset_active_chains_registry_clears_redis_and_local():
    ac._chain_refcount.clear()
    ac._holder_chains.clear()
    with patch.object(ac, "_redis_sadd"), patch.object(ac, "_redis_srem"):
        ac.register_holder_chain("h1", "NFO", "NIFTY", "30-Jun-2026")
    assert ac._chain_refcount.get("NFO|NIFTY|30-Jun-2026") == 1

    mock_redis = MagicMock()
    with patch.object(ac, "get_redis", return_value=mock_redis):
        ac.reset_active_chains_registry()
    mock_redis.delete.assert_called_once_with(ac.CHAIN_ACTIVE_SET)
    assert not ac._chain_refcount
    assert not ac._holder_chains


def test_sweep_drops_expired_expiries_and_leaves_live_ones():
    """The order book registers chains for *past* expiries whenever an older date
    range is viewed, and nothing ever released them — they stayed in the rebuild
    set for the life of the process."""
    ac._chain_refcount.clear()
    ac._holder_chains.clear()
    expired = "NFO|NIFTY|01-Jan-2020"
    live = "NFO|NIFTY|31-Dec-2099"
    with patch.object(ac, "_redis_sadd"), patch.object(ac, "_redis_srem"):
        ac.register_holder_chain("h1", "NFO", "NIFTY", "01-Jan-2020")
        ac.register_holder_chain("h1", "NFO", "NIFTY", "31-Dec-2099")

    with patch.object(ac, "list_active_chains", return_value=[expired, live]), patch.object(
        ac, "_redis_srem"
    ) as srem:
        dropped = ac.sweep_expired_active_chains()

    assert dropped == 1
    srem.assert_called_once_with(expired)
    assert expired not in ac._chain_refcount
    assert live in ac._chain_refcount
    # Dropped from the holder's own set too, or it could never re-register.
    assert expired not in ac._holder_chains["h1"]
    assert live in ac._holder_chains["h1"]


def test_sweep_leaves_unparseable_expiry_alone():
    ac._chain_refcount.clear()
    ac._holder_chains.clear()
    with patch.object(ac, "list_active_chains", return_value=["NFO|NIFTY|not-a-date"]), patch.object(
        ac, "_redis_srem"
    ) as srem:
        assert ac.sweep_expired_active_chains() == 0
    srem.assert_not_called()


def test_daily_reset_noops_on_an_instance_that_started_today():
    """Deployments are commonly shut down overnight and booted the next working
    morning; startup already reset, so the daily reset must not fire again."""
    mock_redis = MagicMock()
    with patch.object(ac, "get_redis", return_value=mock_redis):
        ac.reset_active_chains_registry()  # stamps today, as API startup does
        mock_redis.reset_mock()
        assert ac.maybe_daily_reset_active_chains() is False
    mock_redis.delete.assert_not_called()


def test_daily_reset_fires_once_on_a_long_running_instance():
    mock_redis = MagicMock()
    with patch.object(ac, "get_redis", return_value=mock_redis):
        ac.reset_active_chains_registry()
        ac._last_full_reset_date = ac._today_ist() - dt.timedelta(days=3)
        mock_redis.reset_mock()
        assert ac.maybe_daily_reset_active_chains() is True
        mock_redis.delete.assert_called_once_with(ac.CHAIN_ACTIVE_SET)
        # Same day now -> no second reset.
        mock_redis.reset_mock()
        assert ac.maybe_daily_reset_active_chains() is False
    mock_redis.delete.assert_not_called()


def test_daily_reset_clears_retained_quotes_and_repins_live_squareoffs():
    """The daily wipe takes the SG subscription pins with it.

    Those pins are the only thing keeping an armed square-off group's chain
    subscribed when no browser holds it, and they are otherwise created only at
    process start — so on an instance left running across midnight, every armed SG
    was left visibly armed and quietly unfed.
    """
    ac._last_full_reset_date = dt.date(2020, 1, 1)
    rule = object()

    with patch.object(ac, "get_redis", return_value=MagicMock()), patch(
        "icici_breeze_backend.app.repositories.squareoff_rules.list_all_live_rules",
        return_value=[{"id": "r1", "user_id": "u1"}],
    ), patch(
        "icici_breeze_backend.app.repositories.squareoff_rules.get_rule",
        return_value=rule,
    ), patch(
        "icici_breeze_backend.app.services.strategy_group_lifecycle.pin_subscription"
    ) as pin, patch(
        "icici_breeze_backend.app.services.ws_tick_pipeline.clear_retained_pnl_quotes",
        return_value=7,
    ) as clear:
        assert ac.maybe_daily_reset_active_chains() is True

    clear.assert_called_once()
    pin.assert_called_once_with("u1", rule)


def test_daily_reset_survives_a_repin_failure():
    """A pin failure must not abort the reset half-done."""
    ac._last_full_reset_date = dt.date(2020, 1, 1)

    with patch.object(ac, "get_redis", return_value=MagicMock()), patch(
        "icici_breeze_backend.app.repositories.squareoff_rules.list_all_live_rules",
        side_effect=RuntimeError("db gone"),
    ), patch(
        "icici_breeze_backend.app.services.ws_tick_pipeline.clear_retained_pnl_quotes",
        return_value=0,
    ):
        assert ac.maybe_daily_reset_active_chains() is True
