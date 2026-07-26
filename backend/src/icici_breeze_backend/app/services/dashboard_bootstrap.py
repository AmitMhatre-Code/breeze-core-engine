"""Orchestrated dashboard bootstrap: one ICICI pipeline for post-login load."""
from __future__ import annotations

import logging
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.api.v1.route_portfolio import _normalize_portfolio_success_for_ui
from icici_breeze_backend.app.domain.responses import HomeDataResponse
from icici_breeze_backend.app.services.broker_snapshot_cache import get_snapshot
from icici_breeze_backend.app.services.dashboard_days_pnl import compute_days_pnl
from icici_breeze_backend.app.services.dashboard_vix import fetch_vix_headline
from icici_breeze_backend.audit.logger import AuditLogger

_logger = logging.getLogger(__name__)


def _resolve_portfolio_session_token(
    user_id: str,
    processor,
    *,
    broker_token: str,
    breeze,
) -> str | None:
    snap = get_snapshot(user_id, broker_token)
    token = (snap.customerdetails_session_token if snap else None) or ""
    token = token.strip()
    if token:
        return token
    if breeze is not None:
        sk = getattr(breeze, "session_key", None)
        if sk and str(sk).strip():
            return str(sk).strip()
    bt = (broker_token or "").strip()
    return bt or None


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
        "aggressive_limit_order_enabled": cfg.AGGRESSIVE_LIMIT_ORDER_ENABLED,
        "aggressive_limit_default_tolerance_pct": float(cfg.AGGRESSIVE_LIMIT_DEFAULT_TOLERANCE_PCT),
        "aggressive_limit_max_tolerance_pct": float(cfg.AGGRESSIVE_LIMIT_MAX_TOLERANCE_PCT),
        **license_fields,
    }


def warm_dashboard_bootstrap_snapshot(
    user_id: str,
    *,
    broker_token: str,
    session_token: str | None,
    breeze,
    processor,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """
    Pre-fetch positions + VIX headline during login so /dashboard/bootstrap can return
    from the login snapshot without additional ICICI round-trips.
    """
    portfolio: dict[str, Any] | None = None
    vix: dict[str, Any] | None = None
    try:
        portfolio_raw = processor.get_positions(user_id, session_token=session_token)
        portfolio = portfolio_raw
        if isinstance(portfolio_raw, dict):
            portfolio = _normalize_portfolio_success_for_ui(portfolio_raw)
    except Exception as exc:
        _logger.warning(
            "warm_dashboard_bootstrap_snapshot: positions failed user_id=%s error=%s",
            user_id,
            exc,
        )
    try:
        vix, _nifty_quote = fetch_vix_headline(user_id, processor, breeze=breeze)
    except Exception as exc:
        _logger.warning(
            "warm_dashboard_bootstrap_snapshot: vix headline failed user_id=%s error=%s",
            user_id,
            exc,
        )
    return portfolio, vix


def build_dashboard_bootstrap(user_id: str, processor, *, broker_token: str) -> dict[str, Any]:
    """
    Fast dashboard payload: home, portfolio, vix headline only.
    vix_options and vix history load via separate lazy endpoints after first paint.
    """
    AuditLogger(None).log_portfolio_access(user_id)

    snap = get_snapshot(user_id, broker_token)
    cached_portfolio = snap.portfolio if snap and snap.portfolio is not None else None
    if isinstance(cached_portfolio, dict) and cached_portfolio.get("Status") != 200:
        cached_portfolio = None
    cached_vix = snap.vix_headline if snap and snap.vix_headline is not None else None
    needs_live_broker = cached_portfolio is None or cached_vix is None

    breeze = processor.get_session_breeze(user_id) if needs_live_broker else None

    home_fields = build_home_data_fields(user_id, processor, broker_token=broker_token)
    home = HomeDataResponse(**home_fields)

    if cached_portfolio is not None:
        portfolio = cached_portfolio
    else:
        session_token = _resolve_portfolio_session_token(
            user_id, processor, broker_token=broker_token, breeze=breeze
        )
        portfolio_raw = processor.get_positions(user_id, session_token=session_token)
        portfolio = portfolio_raw
        if isinstance(portfolio_raw, dict):
            portfolio = _normalize_portfolio_success_for_ui(portfolio_raw)

    if cached_vix is not None:
        vix = cached_vix
    else:
        vix, _nifty_quote = fetch_vix_headline(user_id, processor, breeze=breeze)

    positions = []
    if isinstance(portfolio, dict):
        success = portfolio.get("Success")
        if isinstance(success, dict) and isinstance(success.get("positions"), list):
            positions = success["positions"]
    days_pnl = compute_days_pnl(positions)

    return {
        "home": home.model_dump(),
        "portfolio": portfolio,
        "vix": vix,
        "days_pnl": days_pnl,
    }
