"""Parse ICICI customer details payload for portal license activation."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _success_dict(customer: dict[str, Any] | None) -> dict[str, Any] | None:
    if not customer or not isinstance(customer, dict):
        return None
    raw = customer.get("Success") or customer.get("success")
    if isinstance(raw, dict):
        return raw
    return None


def _pick_user_id(success: dict[str, Any]) -> str | None:
    for key in ("id", "user_id", "idirect_user_id", "Idirect_user_id"):
        val = success.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def _pick_display_name(success: dict[str, Any]) -> str | None:
    for key in ("idirect_user_name", "Idirect_user_name", "user_name", "name"):
        val = success.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def normalize_icici_user_id(raw: str) -> str:
    return (raw or "").strip().upper()


def parse_customer_details_identity(
    customer: dict[str, Any] | None,
    *,
    fallback_user_id: str,
) -> tuple[str, str | None]:
    """
    Return (icici_user_id, idirect_user_name) from get_customer_details response.
    Prefers API Success.id over fallback form user_id when they differ.
    """
    success = _success_dict(customer)
    api_id = _pick_user_id(success) if success else None
    fallback = normalize_icici_user_id(fallback_user_id)
    if api_id:
        normalized_api = normalize_icici_user_id(api_id)
        if fallback and normalized_api != fallback:
            logger.warning(
                "icici_user_id mismatch form=%s api=%s; using api id",
                fallback,
                normalized_api,
            )
        icici_user_id = normalized_api
    else:
        icici_user_id = fallback

    display_name = _pick_display_name(success) if success else None
    return icici_user_id, display_name
