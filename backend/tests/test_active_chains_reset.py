"""Tests for active chain registry reset on API startup."""
from __future__ import annotations

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
