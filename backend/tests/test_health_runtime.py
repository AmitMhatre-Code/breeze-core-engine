"""Tests for /health and /metrics/runtime Redis reporting."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from icici_breeze_backend.app.api.health import router
from fastapi import FastAPI


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_health_ok_when_redis_connected(client):
    with patch(
        "icici_breeze_backend.app.db.redis_client.redis_runtime_stats",
        return_value={
            "redis_connected": True,
            "redis_memory_fallback": False,
            "used_memory_human": "12.5M",
        },
    ):
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["redis_connected"] is True
    assert body["redis_memory_fallback"] is False
    assert body["redis_used_memory_human"] == "12.5M"


def test_health_degraded_on_memory_fallback(client):
    with patch(
        "icici_breeze_backend.app.db.redis_client.redis_runtime_stats",
        return_value={
            "redis_connected": False,
            "redis_memory_fallback": True,
        },
    ):
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["redis_memory_fallback"] is True


def test_runtime_metrics_aggregates(client):
    with patch(
        "icici_breeze_backend.app.db.redis_client.redis_runtime_stats",
        return_value={"redis_connected": True, "dbsize": 42},
    ), patch(
        "icici_breeze_backend.app.services.ws_tick_pipeline.pipeline_stats",
        return_value={"started": True, "dropped_ticks": 0},
    ), patch(
        "icici_breeze_backend.app.services.reference_data.active_chains.active_chain_stats",
        return_value={"active_chains": [], "local_refcounts": {}},
    ):
        resp = client.get("/metrics/runtime")
    assert resp.status_code == 200
    body = resp.json()
    assert body["redis"]["dbsize"] == 42
    assert body["ws_tick_pipeline"]["started"] is True
    assert body["active_chains"]["active_chains"] == []


def test_require_redis_connected_raises_when_fallback():
    from icici_breeze_backend.app.db import redis_client

    with patch.object(redis_client.cfg, "REDIS_REQUIRE_CONNECTED", True), patch.object(
        redis_client, "_init_redis_client", return_value=MagicMock()
    ), patch.object(redis_client, "redis_using_memory_fallback", return_value=True), patch.object(
        redis_client.cfg, "redis_connection_url", return_value="redis://localhost:6379/0"
    ):
        with pytest.raises(RuntimeError, match="Redis is required"):
            redis_client.require_redis_connected()


def test_require_redis_connected_skipped_when_disabled():
    from icici_breeze_backend.app.db import redis_client

    with patch.object(redis_client.cfg, "REDIS_REQUIRE_CONNECTED", False):
        redis_client.require_redis_connected()
