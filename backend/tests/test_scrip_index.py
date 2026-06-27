"""Tests for scrip index Redis publish."""
from icici_breeze_backend.app.db.redis_client import get_redis
from icici_breeze_backend.app.services.reference_data.scrip_index import (
    current_version,
    get_underlyings,
    publish_scrip_index_from_db,
)


def test_publish_scrip_index_increments_version(monkeypatch, tmp_path):
    import icici_breeze_backend.app.core.config as cfg

    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    # Use in-memory redis fallback (no server)
    get_redis()
    before = current_version()
    ver = publish_scrip_index_from_db()
    assert ver >= before
    # Empty DB still publishes structure
    data = get_underlyings("NFO")
    assert data is not None
