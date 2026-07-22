"""Tradeable option contracts (MarginPercentage > 0 in ICICI scrip master)."""
from __future__ import annotations

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike
from icici_breeze_backend.app.services.reference_data.scrip_index import (
    is_tradeable_contract_memory,
    list_tradeable_strikes_memory,
)


def is_tradeable(margin_percentage: int | None) -> bool:
    return int(margin_percentage or 0) > 0


def list_tradeable_strikes(
    stock_code: str,
    expiry_date: str,
    *,
    exchange_code: str = cfg.NFO,
) -> list[Strike]:
    """Distinct strikes with at least one CE/PE row where MarginPercentage > 0."""
    return list_tradeable_strikes_memory(
        stock_code, expiry_date, exchange_code=exchange_code
    )


def is_tradeable_contract(
    stock_code: str,
    expiry_date: str,
    strike: Strike,
    option_type: str,
    *,
    exchange_code: str = cfg.NFO,
) -> bool:
    """True when the specific CE/PE contract has MarginPercentage > 0."""
    return is_tradeable_contract_memory(
        stock_code,
        expiry_date,
        strike,
        option_type,
        exchange_code=exchange_code,
    )
