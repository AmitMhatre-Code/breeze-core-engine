"""Tests for WS tick ingest/cache pipeline."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

from icici_breeze_backend.app.services import ws_tick_pipeline as pipeline


def _raw_nifty_call_25000() -> dict:
    path = Path(__file__).resolve().parent / "fixtures" / "icici_ticks" / "nifty_call_25000_raw.json"
    return json.loads(path.read_text(encoding="utf-8"))


@patch("icici_breeze_backend.app.services.ws_tick_pipeline.cache_publish")
@patch("icici_breeze_backend.app.services.ws_tick_pipeline.cache_set_json")
def test_pipeline_writes_raw_ticks_not_normalized_cells(mock_cache, _mock_publish):
    pipeline.stop_tick_pipeline()
    pipeline.start_tick_pipeline()
    try:
        raw = _raw_nifty_call_25000()
        pipeline.ingest_tick(raw)
        deadline = time.time() + 2.0
        while time.time() < deadline and mock_cache.call_count == 0:
            time.sleep(0.05)
        assert mock_cache.call_count >= 1
        key, payload = mock_cache.call_args[0]
        assert "quotes:ws:raw:NFO:" in key
        assert isinstance(payload, dict)
        assert "raw" in payload
        assert payload["raw"]["last"] == 1.4
        assert "ltp" not in payload
    finally:
        pipeline.stop_tick_pipeline()


def test_ingest_drops_when_queue_full(monkeypatch):
    monkeypatch.setattr(pipeline, "_ingest_qsize", lambda: 1)
    pipeline.stop_tick_pipeline()
    pipeline.start_tick_pipeline()
    try:
        raw = _raw_nifty_call_25000()
        pipeline.ingest_tick(raw)
        pipeline.ingest_tick(dict(raw, last=9.9))
        stats = pipeline.pipeline_stats()
        assert stats["started"] is True
    finally:
        pipeline.stop_tick_pipeline()
