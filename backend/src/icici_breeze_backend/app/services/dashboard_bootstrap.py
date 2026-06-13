"""Orchestrated dashboard bootstrap: one ICICI pipeline for post-login load."""
from __future__ import annotations

from typing import Any

from icici_breeze_backend.app.api.v1.route_portfolio import _normalize_portfolio_success_for_ui
from icici_breeze_backend.app.domain.responses import HomeDataResponse
from icici_breeze_backend.app.services.broker_snapshot_cache import get_snapshot
from icici_breeze_backend.app.services.dashboard_vix import fetch_vix_headline, fetch_vix_options
from icici_breeze_backend.audit.logger import AuditLogger


def build_home_data_fields(user_id: str, processor, *, broker_token: str) -> dict[str, Any]:
    """HomeDataResponse fields (customer, margin, usage, license)."""
    snap = get_snapshot(user_id, broker_token)
    if snap:
        customer = snap.customer_details
        margin = snap.margin_situation
    else:
        customer = processor.get_customer_details(user_id)
        margin = processor.get_margin_situation(user_id, target_margin_ute=100)

    from icici_breeze_backend.app.services.api_usage import (
        get_usage_for_display,
        get_usage_warning,
        is_daily_limit_reached,
    )
    from icici_breeze_backend.app.services.deployment_license_status import (
        get_license_status_for_api,
    )

    usage = get_usage_for_display(user_id)
    license_fields = get_license_status_for_api() or {}
    return {
        "customer": customer or {},
        "margin": margin or {},
        "api_calls_today": usage["api_calls_today"],
        "api_calls_limit": usage["api_calls_limit"],
        "api_usage_band": usage["api_usage_band"],
        "api_usage_warning": get_usage_warning(user_id),
        "api_usage_blocked": is_daily_limit_reached(user_id),
        **license_fields,
    }


def build_dashboard_bootstrap(user_id: str, processor, *, broker_token: str) -> dict[str, Any]:
    """
    Single-request dashboard payload: home, portfolio, vix headline, vix options.
    VIX history is omitted (lazy-loaded via /dashboard/vix/history).
    """
    AuditLogger(None).log_portfolio_access(user_id)

    home_fields = build_home_data_fields(user_id, processor, broker_token=broker_token)
    home = HomeDataResponse(**home_fields)

    snap = get_snapshot(user_id, broker_token)
    session_token = snap.customerdetails_session_token if snap else None
    portfolio_raw = processor.get_positions(user_id, session_token=session_token)
    portfolio = portfolio_raw
    if isinstance(portfolio_raw, dict):
        portfolio = _normalize_portfolio_success_for_ui(portfolio_raw)

    breeze = processor.get_session_breeze(user_id)
    vix, nifty_quote = fetch_vix_headline(user_id, processor, breeze=breeze)
    vix_options = fetch_vix_options(
        user_id,
        processor,
        breeze=breeze,
        nifty_quote=nifty_quote,
    )

    return {
        "home": home.model_dump(),
        "portfolio": portfolio,
        "vix": vix,
        "vix_options": vix_options,
    }
