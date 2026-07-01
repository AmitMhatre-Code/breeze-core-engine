"""Build canonical option chains from raw WS ticks (used by chain-builder worker)."""
from __future__ import annotations

import logging
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, strikes_equal
from icici_breeze_backend.app.db.redis_client import cache_get_json, cache_set_json
from icici_breeze_backend.app.services.options_chain_assembler import assemble_chain_payload
from icici_breeze_backend.app.services.reference_data.keys import (
    canonical_chain_key,
    ws_quote_key,
)
from icici_breeze_backend.app.services.reference_data.scrip_index import get_strikes
from icici_breeze_backend.app.services.reference_data.scrip_master_sql import (
    expiry_display_equivalent,
    normalize_expiry_display,
)
from icici_breeze_backend.app.services.reference_data.tradable_contracts import (
    is_tradeable_contract,
    list_tradeable_strikes,
)
from icici_breeze_backend.app.services.reference_data.ws_token_index import (
    lookup_token_for_contract,
)
from icici_breeze_backend.app.services.ws_tick_normalize import normalize_icici_tick

_logger = logging.getLogger(__name__)


def _option_type_for_right(right: str) -> str:
    return cfg.CALL if str(right).lower().startswith("c") else cfg.PUT


def _read_raw_tick(segment_code: str, token: int) -> dict[str, Any] | None:
    from icici_breeze_backend.app.services.reference_data.keys import ws_raw_quote_key

    stored = cache_get_json(ws_raw_quote_key(segment_code, token))
    if not isinstance(stored, dict):
        return None
    raw = stored.get("raw")
    return raw if isinstance(raw, dict) else None


def _parsed_matches_contract(
    parsed: Any,
    *,
    stock_code: str,
    expiry_display: str,
    strike: Strike,
    right: str,
) -> bool:
    if parsed.stock_code.upper() != stock_code.upper():
        return False
    if not expiry_display_equivalent(parsed.expiry_display, expiry_display):
        return False
    if not strikes_equal(parsed.strike, strike):
        return False
    return parsed.right == str(right).lower()


def _normalize_and_cache_cell(
    *,
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    strike: Strike,
    right: str,
    lot_size: int,
    stats: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    token = lookup_token_for_contract(
        exchange_code,
        stock_code,
        expiry_display,
        float(strike),
        _option_type_for_right(right),
    )
    if token is None:
        if stats is not None:
            stats["token_miss"] = stats.get("token_miss", 0) + 1
        return None
    raw = _read_raw_tick(exchange_code, token)
    if raw is None:
        if stats is not None:
            stats["raw_miss"] = stats.get("raw_miss", 0) + 1
        return None
    result = normalize_icici_tick(raw)
    if result is None:
        if stats is not None:
            stats["normalize_miss"] = stats.get("normalize_miss", 0) + 1
        return None
    parsed, cell = result
    if not _parsed_matches_contract(
        parsed,
        stock_code=stock_code,
        expiry_display=expiry_display,
        strike=strike,
        right=right,
    ):
        if stats is not None:
            stats["identity_miss"] = stats.get("identity_miss", 0) + 1
        return None
    if lot_size:
        cell = dict(cell)
        cell["lot_size"] = lot_size
    cache_set_json(
        ws_quote_key(exchange_code, stock_code, expiry_display, strike, right),
        cell,
        ex=cfg.WEBSOCKET_QUOTE_TTL_SECONDS,
    )
    return cell


def build_canonical_chain(
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    *,
    strikes: list[Strike] | None = None,
    lot_size: int = 0,
    freeze_quantity: int | None = None,
) -> dict[str, Any] | None:
    expiry_display = normalize_expiry_display(expiry_display)
    strike_list = strikes if strikes else get_strikes(
        stock_code, expiry_display, exchange_code=exchange_code
    )
    if not strike_list:
        strike_list = list_tradeable_strikes(
            stock_code, expiry_display, exchange_code=exchange_code
        )
    if not strike_list:
        return None

    stats: dict[str, int] = {}
    call_by: dict[Strike, dict[str, Any]] = {}
    put_by: dict[Strike, dict[str, Any]] = {}
    for strike in strike_list:
        for right, bucket in (("call", call_by), ("put", put_by)):
            if not is_tradeable_contract(
                stock_code,
                expiry_display,
                strike,
                _option_type_for_right(right),
                exchange_code=exchange_code,
            ):
                continue
            cell = _normalize_and_cache_cell(
                exchange_code=exchange_code,
                stock_code=stock_code,
                expiry_display=expiry_display,
                strike=strike,
                right=right,
                lot_size=lot_size,
                stats=stats,
            )
            if cell is not None:
                bucket[strike] = cell

    if not call_by and not put_by and stats:
        _logger.debug(
            "canonical chain empty for %s %s %s: %s",
            exchange_code,
            stock_code,
            expiry_display,
            stats,
        )

    payload = assemble_chain_payload(
        stock_code=stock_code,
        exchange_code=exchange_code,
        expiry_display=expiry_display,
        strikes=strike_list,
        call_by_strike=call_by,
        put_by_strike=put_by,
        lot_size=lot_size,
        freeze_quantity=freeze_quantity,
        quote_source="websocket",
        bhavcopy_date=None,
    )
    if payload is None:
        return None
    cache_set_json(
        canonical_chain_key(exchange_code, stock_code, expiry_display),
        payload,
        ex=int(getattr(cfg, "CANONICAL_CHAIN_TTL_SECONDS", 5) or 5),
    )
    return payload


def refresh_active_chains(
    active_chain_keys: list[str],
    *,
    resolve_lot_size: Any | None = None,
    resolve_freeze_quantity: Any | None = None,
) -> int:
    """Build canonical chains for all active registry keys. Returns count written."""
    written = 0
    for chain_key in active_chain_keys:
        from icici_breeze_backend.app.services.reference_data.active_chains import (
            parse_chain_registry_key,
        )

        parsed = parse_chain_registry_key(chain_key)
        if parsed is None:
            continue
        exchange_code, stock_code, expiry_display = parsed
        lot_size = 0
        freeze_quantity = None
        if resolve_lot_size is not None:
            try:
                ls = resolve_lot_size(stock_code, expiry_display, exchange_code)
                lot_size = int(ls or 0)
            except Exception:
                lot_size = 0
        if resolve_freeze_quantity is not None and lot_size > 0:
            try:
                freeze_quantity = resolve_freeze_quantity(
                    stock_code, expiry_display, exchange_code, lot_size
                )
            except Exception:
                freeze_quantity = None
        if build_canonical_chain(
            stock_code,
            exchange_code,
            expiry_display,
            lot_size=lot_size,
            freeze_quantity=freeze_quantity,
        ):
            written += 1
    return written
