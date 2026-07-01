"""Boot-time guarantee: reference data present in Redis before serving traffic."""
from __future__ import annotations

import logging

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.redis_client import cache_get_json
from icici_breeze_backend.app.services.reference_data import bhavcopy_store, scrip_index, span_baseline_store
from icici_breeze_backend.app.services.reference_data.keys import bhav_meta_key
from icici_breeze_backend.app.services.reference_data.scrip_index import current_version, get_underlyings

_logger = logging.getLogger(__name__)


def is_scrip_cached() -> bool:
    ver = current_version()
    if ver <= 0:
        return False
    for exchange_code in (cfg.NFO, cfg.BFO):
        underlyings = get_underlyings(exchange_code)
        if underlyings:
            return True
    return False


def is_bhavcopy_cached(segment: str) -> bool:
    ver = current_version()
    if ver <= 0:
        return False
    meta = cache_get_json(bhav_meta_key(ver, segment.lower()))
    return isinstance(meta, dict) and int(meta.get("row_count") or 0) > 0


def is_span_cached(exchange_code: str) -> bool:
    return span_baseline_store.is_span_baseline_cached(exchange_code)


def is_reference_data_complete() -> bool:
    """True when scrip, bhavcopy (NFO/BFO), and SPAN baselines are present in Redis."""
    return (
        is_scrip_cached()
        and is_bhavcopy_cached("nfo")
        and is_bhavcopy_cached("bfo")
        and is_span_cached(cfg.NFO)
        and is_span_cached(cfg.BFO)
    )


def load_all_local_mirrors() -> None:
    from icici_breeze_backend.app.services.reference_data import scrip_index
    from icici_breeze_backend.app.services.reference_data.ws_token_index import load_token_map_from_redis

    scrip_index.load_local_from_redis()
    load_token_map_from_redis()
    bhavcopy_store.load_local_from_redis()
    span_baseline_store.load_local_from_redis()


def ensure_all_reference_data_cached() -> dict[str, bool]:
    """Publish scrip/SPAN/bhavcopy from SQLite when Redis is empty; hydrate local mirrors."""
    status: dict[str, bool] = {}

    need_scrip = not is_scrip_cached() and scrip_index.scrip_master_row_count() > 0
    span_any_missing = any(not is_span_cached(ex) for ex in (cfg.NFO, cfg.BFO))
    need_span = span_any_missing and span_baseline_store.span_baseline_row_count() > 0
    need_bhav_segments = [
        seg
        for seg in ("nfo", "bfo")
        if not is_bhavcopy_cached(seg) and bhavcopy_store.bhavcopy_row_count(seg) > 0
    ]

    batch_version: int | None = None
    if need_scrip or need_span or need_bhav_segments:
        batch_version = scrip_index._next_version()

    if need_scrip:
        try:
            scrip_index.publish_scrip_index_from_db(version=batch_version)
            status["scrip_published"] = True
            _logger.info("Boot: published scrip index to Redis from SQLite")
        except Exception as exc:
            _logger.warning("Boot: scrip index publish failed: %s", exc)
            status["scrip_published"] = False
    status["scrip_cached"] = is_scrip_cached()

    if need_span:
        try:
            span_baseline_store.publish_span_baseline_from_db(version=batch_version)
            status["span_published"] = True
            _logger.info("Boot: published SPAN baseline to Redis from SQLite")
        except Exception as exc:
            _logger.warning("Boot: SPAN baseline publish failed: %s", exc)
            status["span_published"] = False
    for exchange_code in (cfg.NFO, cfg.BFO):
        status[f"span_{exchange_code.lower()}_cached"] = is_span_cached(exchange_code)

    for seg in need_bhav_segments:
        try:
            bhavcopy_store.publish_bhavcopy_from_db(seg, version=batch_version)
            status[f"bhav_{seg}_published"] = True
            _logger.info("Boot: published bhavcopy %s to Redis from SQLite", seg)
        except Exception as exc:
            _logger.warning("Boot: bhavcopy %s publish failed: %s", seg, exc)
            status[f"bhav_{seg}_published"] = False
    status["bhav_nfo_cached"] = is_bhavcopy_cached("nfo")
    status["bhav_bfo_cached"] = is_bhavcopy_cached("bfo")

    load_all_local_mirrors()
    return status
