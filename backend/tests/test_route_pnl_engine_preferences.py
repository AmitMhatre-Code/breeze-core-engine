"""Tests for GET/PUT /api/settings/pnl-engine/preferences handlers."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from icici_breeze_backend.app.api.v1.route_settings import (
    settings_pnl_engine_preferences_get,
    settings_pnl_engine_preferences_put,
)
from icici_breeze_backend.app.auth.context import RequestContext
from icici_breeze_backend.app.domain.settings_api import PnlEnginePreferencesUpdateBody
from icici_breeze_backend.app.services import pnl_engine_settings


@pytest.fixture(autouse=True)
def _isolated_settings_db(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(pnl_engine_settings, "_db_path", lambda: path)
    yield


def _ctx() -> RequestContext:
    return RequestContext(user_id="uid1", username="uid1", roles=["trader"], is_authenticated=True, broker_token="tok")


def test_get_returns_defaults_and_bounds():
    resp = asyncio.run(settings_pnl_engine_preferences_get(_ctx()))
    assert resp.quote_flush_interval_seconds == pytest.approx(2.0)
    assert resp.pnl_recompute_interval_seconds == pytest.approx(2.0)
    assert resp.quote_flush_min_seconds == 0.5
    assert resp.quote_flush_max_seconds == 10.0
    assert resp.pnl_recompute_min_seconds == 1.0
    assert resp.pnl_recompute_max_seconds == 30.0


def test_put_persists_and_get_reflects_it():
    body = PnlEnginePreferencesUpdateBody(
        quote_flush_interval_seconds=1.5, pnl_recompute_interval_seconds=3.0
    )
    put_resp = asyncio.run(settings_pnl_engine_preferences_put(body, _ctx()))
    assert put_resp.quote_flush_interval_seconds == 1.5
    assert put_resp.pnl_recompute_interval_seconds == 3.0

    get_resp = asyncio.run(settings_pnl_engine_preferences_get(_ctx()))
    assert get_resp.quote_flush_interval_seconds == 1.5
    assert get_resp.pnl_recompute_interval_seconds == 3.0


def test_pydantic_field_bounds_reject_out_of_range_before_route_runs():
    with pytest.raises(Exception):
        PnlEnginePreferencesUpdateBody(quote_flush_interval_seconds=999.0, pnl_recompute_interval_seconds=3.0)


def test_put_out_of_hard_bounds_via_service_raises_422(monkeypatch):
    """Belt-and-suspenders: even if pydantic validation were bypassed, the
    service-layer bounds check still 422s rather than persisting garbage."""
    body = PnlEnginePreferencesUpdateBody(quote_flush_interval_seconds=2.0, pnl_recompute_interval_seconds=3.0)
    monkeypatch.setattr(
        pnl_engine_settings,
        "save_pnl_engine_settings",
        lambda **kw: (_ for _ in ()).throw(ValueError("out of range")),
    )
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(settings_pnl_engine_preferences_put(body, _ctx()))
    assert exc_info.value.status_code == 422
