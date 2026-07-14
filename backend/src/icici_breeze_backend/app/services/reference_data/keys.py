"""Redis key helpers for reference data versioning."""
from __future__ import annotations

from icici_breeze_backend.app.core.strike import Strike, strike_key

CURRENT_VERSION_KEY = "refdata:current_version"


def version_prefix(version: int) -> str:
    return f"refdata:v{version}"


def underlyings_key(version: int, exchange_code: str) -> str:
    return f"{version_prefix(version)}:underlyings:{exchange_code.upper()}"


def strikes_key(version: int, exchange_code: str, short_name: str, expiry_display: str) -> str:
    return (
        f"{version_prefix(version)}:strikes:"
        f"{exchange_code.upper()}:{short_name.upper()}:{expiry_display}"
    )


def exchange_code_map_key(version: int) -> str:
    return f"{version_prefix(version)}:exchange_code_map"


def scrip_contracts_key(version: int) -> str:
    return f"{version_prefix(version)}:scrip:contracts"


def scrip_token_map_key(version: int) -> str:
    return f"{version_prefix(version)}:scrip:ws_tokens"


def bhav_meta_key(version: int, exchange_segment: str) -> str:
    return f"{version_prefix(version)}:bhav:{exchange_segment.lower()}:meta"


def bhav_index_key(version: int, exchange_segment: str) -> str:
    return f"{version_prefix(version)}:bhav:{exchange_segment.lower()}:index"


def span_baseline_meta_key(version: int, exchange_code: str) -> str:
    return f"{version_prefix(version)}:span:{exchange_code.upper()}:meta"


def span_baseline_sheet_key(
    version: int,
    exchange_code: str,
    short_name: str,
    expiry_display: str,
) -> str:
    return (
        f"{version_prefix(version)}:span:"
        f"{exchange_code.upper()}:{short_name.upper()}:{expiry_display}"
    )


def ws_quote_key(
    exchange_code: str,
    short_name: str,
    expiry_display: str,
    strike: Strike,
    right: str,
) -> str:
    r = str(right or "").strip().lower()
    if r in {"ce", "c"}:
        r = "call"
    elif r in {"pe", "p"}:
        r = "put"
    return (
        f"quotes:ws:{exchange_code.upper()}:{short_name.upper()}:"
        f"{expiry_display}:{strike_key(strike)}:{r}"
    )


def ws_raw_quote_key(segment_code: str, token: int) -> str:
    return f"quotes:ws:raw:{segment_code.upper()}:{int(token)}"


def canonical_chain_key(exchange_code: str, stock_code: str, expiry_display: str) -> str:
    return (
        f"quotes:chain:{exchange_code.upper()}:{stock_code.upper()}:{expiry_display}"
    )


def icici_rest_chain_key(exchange_code: str, stock_code: str, expiry_display: str) -> str:
    """Shared cache for the REST-sourced full chain (post-close, pre-bhavcopy
    fallback) -- one entry per (exchange, underlying, expiry) serves every user
    until the day's bhavcopy loads or the TTL expires. See quote_source_router."""
    return (
        f"quotes:icici_api:chain:{exchange_code.upper()}:{stock_code.upper()}:{expiry_display}"
    )


def pnl_quote_key(scrip_key: str) -> str:
    """Conflated LTP/bid/ask hash for the portfolio P&L engine (contract-identity keyed)."""
    return f"quotes:pnl:{scrip_key}"


def index_spot_key(label: str) -> str:
    """Live NIFTY/SENSEX index-tick cache for the navbar ticker (see `index_spot_feed`)."""
    return f"quotes:index_spot:{label.lower()}"


WS_TICK_DIRTY_CHANNEL = "ws:tick:dirty"
