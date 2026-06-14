"""In-memory TTL cache for full option chain CE+PE fetches (UI + strategy engine)."""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from icici_breeze_backend.app.core.timezone import IST

_CACHE_TTL_SECONDS = 5 * 60
_RAW_CHAIN_CACHE: Dict[str, Tuple[float, dict[str, Any]]] = {}


def make_chain_cache_key(
    user_id: str,
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
) -> str:
    return (
        f"{user_id}|{exchange_code.strip()}|{stock_code.strip().upper()}"
        f"|{expiry_display.strip()}"
    )


def get_cached_raw_chain(
    key: str,
) -> Optional[Tuple[list[dict[str, Any]], list[dict[str, Any]], float]]:
    entry = _RAW_CHAIN_CACHE.get(key)
    if not entry:
        return None
    ts, payload = entry
    if (time.time() - ts) > _CACHE_TTL_SECONDS:
        _RAW_CHAIN_CACHE.pop(key, None)
        return None
    ce_rows = payload.get("ce_rows") or []
    pe_rows = payload.get("pe_rows") or []
    return ce_rows, pe_rows, ts


def set_cached_raw_chain(
    key: str,
    ce_rows: list[dict[str, Any]],
    pe_rows: list[dict[str, Any]],
) -> float:
    ts = time.time()
    _RAW_CHAIN_CACHE[key] = (
        ts,
        {"ce_rows": ce_rows, "pe_rows": pe_rows},
    )
    return ts


def chain_metadata(fetched_at: float, served_from_cache: bool) -> dict[str, Any]:
    dt = datetime.fromtimestamp(fetched_at, tz=IST)
    return {
        "chain_fetched_at": dt.isoformat(),
        "from_cache": served_from_cache,
    }


def clear_chain_cache() -> None:
    """Test helper."""
    _RAW_CHAIN_CACHE.clear()
